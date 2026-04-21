from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import datetime
import hashlib
import json
from pathlib import Path
from urllib.parse import quote, urlencode
from typing import Any

from fastapi import Depends, FastAPI, Form, Header, HTTPException, Query, Request, status
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, Field, field_validator

from app.analysis.service import AnalyzerService
from app.config import Settings, get_settings
from app.database import init_db
from app.services.import_watcher import ImportWatcher
from app.services.repository import Repository
from app.services.worker import AnalysisWorker
from app.text_diff import build_prompt_diff


BASE_DIR = Path(__file__).resolve().parent
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))
UI_AUTH_COOKIE = "prompt_tracker_auth"


def _format_timestamp(value: str | None) -> str:
    if not value:
        return "Pending"
    try:
        parsed = datetime.fromisoformat(value)
        return parsed.strftime("%Y-%m-%d %H:%M UTC")
    except ValueError:
        return value


def _score_tone(value: int | float | None) -> str:
    if value is None:
        return "muted"
    if value >= 85:
        return "strong"
    if value >= 70:
        return "steady"
    return "warning"


templates.env.filters["timestamp"] = _format_timestamp
templates.env.filters["score_tone"] = _score_tone


class PromptCreatePayload(BaseModel):
    text: str = Field(..., min_length=1)
    source: str = "api"
    session_id: str | None = None
    external_id: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("text")
    @classmethod
    def validate_text(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("Prompt text cannot be empty.")
        return cleaned


class PromptBulkPayload(BaseModel):
    prompts: list[PromptCreatePayload]


class ClaudePromptHookPayload(BaseModel):
    session_id: str | None = None
    transcript_path: str | None = None
    cwd: str | None = None
    hook_event_name: str | None = None
    prompt: str = Field(..., min_length=1)

    @field_validator("prompt")
    @classmethod
    def validate_prompt(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("Prompt text cannot be empty.")
        return cleaned


def create_app(settings: Settings | None = None) -> FastAPI:
    active_settings = settings or get_settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        active_settings.ensure_directories()
        init_db(active_settings.database_path)
        repository = Repository(database_path=active_settings.database_path)
        analyzer = AnalyzerService(
            mode=active_settings.analyzer_mode,
            language=active_settings.analyzer_language,
            llm_api_base_url=active_settings.llm_api_base_url,
            llm_api_key=active_settings.llm_api_key,
            llm_model=active_settings.llm_model,
            llm_timeout_seconds=active_settings.llm_timeout_seconds,
            llm_max_tokens=active_settings.llm_max_tokens,
            llm_reasoning_effort=active_settings.llm_reasoning_effort,
            llm_seed=active_settings.llm_seed,
        )
        worker = AnalysisWorker(repository=repository, analyzer=analyzer)
        watcher = ImportWatcher(
            import_dir=active_settings.import_dir,
            repository=repository,
            worker=worker,
            poll_interval_seconds=active_settings.import_poll_interval_seconds,
        )

        app.state.settings = active_settings
        app.state.repository = repository
        app.state.analyzer = analyzer
        app.state.worker = worker
        app.state.watcher = watcher

        await worker.start()
        await watcher.start()
        yield
        await watcher.stop()
        await worker.stop()
        analyzer.close()

    app = FastAPI(title=active_settings.app_name, lifespan=lifespan)
    app.mount("/static", StaticFiles(directory=str(BASE_DIR / "static")), name="static")

    def common_context(request: Request, **extra: Any) -> dict[str, Any]:
        settings_snapshot = request.app.state.settings
        return {
            "request": request,
            "app_name": settings_snapshot.app_name,
            "settings": settings_snapshot,
            "ui_auth_enabled": bool(settings_snapshot.api_token),
            "ui_authenticated": has_ui_access(request),
            **extra,
        }

    def build_prompt_history_context(
        request: Request,
        *,
        source: str | None,
        status_filter: str | None,
        page: int,
        page_size: int,
    ) -> dict[str, Any]:
        repository: Repository = request.app.state.repository
        selected_source = source or ""
        selected_status = status_filter or ""
        featured_listing = repository.list_prompts(
            source=source or None,
            status=status_filter or None,
            page=1,
            page_size=1,
        )
        featured_prompt = None
        if featured_listing["items"]:
            featured_prompt = repository.fetch_prompt_detail(int(featured_listing["items"][0]["id"]))
            if featured_prompt:
                corrected_text = featured_prompt["text"]
                if featured_prompt.get("latest_analysis"):
                    corrected_text = featured_prompt["latest_analysis"].get("corrected_text") or featured_prompt["text"]
                featured_prompt["live_diff"] = build_prompt_diff(
                    original_text=featured_prompt["text"],
                    rewritten_text=corrected_text,
                )

        prompts = repository.list_prompts(
            source=source or None,
            status=status_filter or None,
            page=page,
            page_size=page_size,
        )
        history_items = list(prompts["items"])
        if page == 1 and featured_prompt and history_items and int(history_items[0]["id"]) == int(featured_prompt["id"]):
            history_items = history_items[1:]

        refresh_query = {
            "page": page,
            "page_size": page_size,
        }
        if selected_source:
            refresh_query["source"] = selected_source
        if selected_status:
            refresh_query["status"] = selected_status

        return {
            "featured_prompt": featured_prompt,
            "prompts": prompts,
            "history_prompts": history_items,
            "sources": repository.get_source_breakdown(),
            "selected_source": selected_source,
            "selected_status": selected_status,
            "refresh_path": str(request.app.url_path_for("prompt_history_table")) + "?" + urlencode(refresh_query),
            "refresh_interval_ms": 3000,
        }

    def token_hash(token: str) -> str:
        return hashlib.sha256(token.encode("utf-8")).hexdigest()

    def has_ui_access(request: Request) -> bool:
        configured = request.app.state.settings.api_token
        if not configured:
            return True
        return request.cookies.get(UI_AUTH_COOKIE) == token_hash(configured)

    def require_ui_access(request: Request) -> Response | None:
        if has_ui_access(request):
            return None
        target = quote(normalize_next_path(request.url.path), safe="/")
        return RedirectResponse(url=f"/login?next={target}", status_code=status.HTTP_303_SEE_OTHER)

    def normalize_next_path(value: str | None) -> str:
        if value and value.startswith("/") and not value.startswith("//"):
            return value
        return "/"

    def require_api_auth(
        request: Request,
        x_api_token: str | None = Header(default=None, alias="X-API-Token"),
        authorization: str | None = Header(default=None, alias="Authorization"),
    ) -> None:
        configured = request.app.state.settings.api_token
        if not configured:
            return
        candidate = x_api_token
        if authorization and authorization.lower().startswith("bearer "):
            candidate = authorization.split(" ", 1)[1].strip()
        if candidate != configured:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid API token.")

    @app.get("/login", response_class=HTMLResponse)
    async def login_page(request: Request, next: str = Query(default="/"), error: str = Query(default="")) -> Response:
        next_path = normalize_next_path(next)
        if not request.app.state.settings.api_token:
            return RedirectResponse(url="/", status_code=status.HTTP_303_SEE_OTHER)
        if has_ui_access(request):
            return RedirectResponse(url=next_path, status_code=status.HTTP_303_SEE_OTHER)
        return templates.TemplateResponse(
            request,
            "login.html",
            common_context(
                request,
                page_title="Login",
                next_path=next_path,
                login_error=error,
            ),
        )

    @app.post("/login")
    async def login_submit(
        request: Request,
        token: str = Form(...),
        next: str = Form(default="/"),
    ) -> RedirectResponse:
        next_path = normalize_next_path(next)
        configured = request.app.state.settings.api_token
        if not configured:
            return RedirectResponse(url="/", status_code=status.HTTP_303_SEE_OTHER)
        if token != configured:
            target = quote(next_path, safe="/")
            return RedirectResponse(
                url=f"/login?next={target}&error=invalid",
                status_code=status.HTTP_303_SEE_OTHER,
            )
        response = RedirectResponse(url=next_path, status_code=status.HTTP_303_SEE_OTHER)
        response.set_cookie(
            key=UI_AUTH_COOKIE,
            value=token_hash(configured),
            httponly=True,
            samesite="lax",
            max_age=60 * 60 * 12,
        )
        return response

    @app.post("/logout")
    async def logout(request: Request) -> RedirectResponse:
        response = RedirectResponse(url="/login", status_code=status.HTTP_303_SEE_OTHER)
        response.delete_cookie(UI_AUTH_COOKIE)
        return response

    @app.get("/health")
    async def health(request: Request) -> dict[str, Any]:
        stats = request.app.state.repository.get_dashboard_stats()
        return {
            "status": "ok",
            "engine": request.app.state.analyzer.active_engine,
            "queued_prompts": stats["queued_prompts"],
            "processing_prompts": stats["processing_prompts"],
        }

    @app.get("/", response_class=HTMLResponse)
    async def dashboard(request: Request) -> Response:
        gated = require_ui_access(request)
        if gated:
            return gated
        repository: Repository = request.app.state.repository
        stats = repository.get_dashboard_stats()
        trends = repository.get_trend_series(days=21)
        recurring = repository.get_recurring_patterns(limit=6)
        recent = repository.get_recent_prompts(limit=8)
        return templates.TemplateResponse(
            request,
            "dashboard.html",
            common_context(
                request,
                page_title="Overview",
                stats=stats,
                trends=trends,
                trends_json=json.dumps(trends),
                recurring=recurring,
                recent_prompts=recent,
            ),
        )

    @app.get("/prompts", response_class=HTMLResponse)
    async def prompt_history(
        request: Request,
        source: str | None = Query(default=None),
        status_filter: str | None = Query(default=None, alias="status"),
        page: int = Query(default=1, ge=1),
        page_size: int = Query(default=20, ge=1, le=100),
    ) -> Response:
        gated = require_ui_access(request)
        if gated:
            return gated
        return templates.TemplateResponse(
            request,
            "prompts.html",
            common_context(
                request,
                page_title="Prompt History",
                **build_prompt_history_context(
                    request,
                    source=source,
                    status_filter=status_filter,
                    page=page,
                    page_size=page_size,
                ),
            ),
        )

    @app.get("/prompts/table", response_class=HTMLResponse, name="prompt_history_table")
    async def prompt_history_table(
        request: Request,
        source: str | None = Query(default=None),
        status_filter: str | None = Query(default=None, alias="status"),
        page: int = Query(default=1, ge=1),
        page_size: int = Query(default=20, ge=1, le=100),
    ) -> Response:
        gated = require_ui_access(request)
        if gated:
            return gated
        return templates.TemplateResponse(
            request,
            "partials/prompts_live_feed.html",
            common_context(
                request,
                **build_prompt_history_context(
                    request,
                    source=source,
                    status_filter=status_filter,
                    page=page,
                    page_size=page_size,
                ),
            ),
        )

    @app.get("/prompts/{prompt_id}", response_class=HTMLResponse)
    async def prompt_detail_page(request: Request, prompt_id: int) -> Response:
        gated = require_ui_access(request)
        if gated:
            return gated
        repository: Repository = request.app.state.repository
        prompt = repository.fetch_prompt_detail(prompt_id)
        if not prompt:
            raise HTTPException(status_code=404, detail="Prompt not found.")
        return templates.TemplateResponse(
            request,
            "prompt_detail.html",
            common_context(
                request,
                page_title=f"Prompt {prompt_id}",
                prompt=prompt,
            ),
        )

    @app.post("/prompts/{prompt_id}/reanalyze")
    async def reanalyze_prompt(request: Request, prompt_id: int) -> RedirectResponse:
        gated = require_ui_access(request)
        if gated:
            return gated
        repository: Repository = request.app.state.repository
        prompt = repository.fetch_prompt(prompt_id)
        if not prompt:
            raise HTTPException(status_code=404, detail="Prompt not found.")
        analysis_id = repository.queue_reanalysis(prompt_id)
        await request.app.state.worker.submit(analysis_id)
        return RedirectResponse(url=f"/prompts/{prompt_id}", status_code=status.HTTP_303_SEE_OTHER)

    @app.get("/capture", response_class=HTMLResponse)
    async def manual_capture_page(request: Request) -> Response:
        gated = require_ui_access(request)
        if gated:
            return gated
        return templates.TemplateResponse(
            request,
            "capture.html",
            common_context(request, page_title="Manual Capture"),
        )

    @app.get("/instructions", response_class=HTMLResponse)
    async def instructions_page(request: Request) -> Response:
        gated = require_ui_access(request)
        if gated:
            return gated
        return templates.TemplateResponse(
            request,
            "instructions.html",
            common_context(request, page_title="Instructions"),
        )

    @app.post("/capture")
    async def manual_capture_submit(
        request: Request,
        text: str = Form(...),
        source: str = Form(default="manual"),
        session_id: str = Form(default=""),
    ) -> RedirectResponse:
        gated = require_ui_access(request)
        if gated:
            return gated
        cleaned_text = text.strip()
        if not cleaned_text:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="Prompt text cannot be empty.",
            )
        repository: Repository = request.app.state.repository
        record = repository.create_prompt(
            text=cleaned_text,
            source=source or "manual",
            session_id=session_id or None,
            metadata={"captured_from": "manual_form"},
        )
        if record.get("analysis_id") and record.get("analysis_status") == "queued":
            await request.app.state.worker.submit(int(record["analysis_id"]))
        return RedirectResponse(
            url=f"/prompts/{record['id']}",
            status_code=status.HTTP_303_SEE_OTHER,
        )

    @app.get("/settings", response_class=HTMLResponse)
    async def settings_page(request: Request) -> Response:
        gated = require_ui_access(request)
        if gated:
            return gated
        repository: Repository = request.app.state.repository
        snapshot = repository.get_settings_snapshot()
        return templates.TemplateResponse(
            request,
            "settings.html",
            common_context(
                request,
                page_title="Settings",
                snapshot=snapshot,
                analyzer_mode=request.app.state.settings.analyzer_mode,
                active_engine=request.app.state.analyzer.active_engine,
                llm_error=request.app.state.analyzer.llm_error,
                language_tool_error=request.app.state.analyzer.language_tool_error,
            ),
        )

    @app.post("/settings/imports/scan")
    async def scan_imports(request: Request) -> RedirectResponse:
        gated = require_ui_access(request)
        if gated:
            return gated
        await request.app.state.watcher.scan_once()
        return RedirectResponse(url="/settings", status_code=status.HTTP_303_SEE_OTHER)

    @app.post("/api/prompts", dependencies=[Depends(require_api_auth)])
    async def create_prompt_api(request: Request, payload: PromptCreatePayload) -> JSONResponse:
        repository: Repository = request.app.state.repository
        record = repository.create_prompt(
            text=payload.text,
            source=payload.source,
            session_id=payload.session_id,
            external_id=payload.external_id,
            metadata=payload.metadata,
        )
        if record.get("analysis_id") and record.get("analysis_status") == "queued":
            await request.app.state.worker.submit(int(record["analysis_id"]))
        return JSONResponse(
            {
                "prompt_id": record["id"],
                "analysis_id": record["analysis_id"],
                "status": record.get("analysis_status") or "unknown",
                "source": record["source"],
                "deduplicated": record.get("deduplicated", False),
            },
            status_code=status.HTTP_202_ACCEPTED,
        )

    @app.post("/api/prompts/bulk", dependencies=[Depends(require_api_auth)])
    async def create_prompt_bulk_api(request: Request, payload: PromptBulkPayload) -> dict[str, Any]:
        created: list[dict[str, Any]] = []
        repository: Repository = request.app.state.repository
        for item in payload.prompts:
            record = repository.create_prompt(
                text=item.text,
                source=item.source,
                session_id=item.session_id,
                external_id=item.external_id,
                metadata=item.metadata,
            )
            if record.get("analysis_id") and record.get("analysis_status") == "queued":
                await request.app.state.worker.submit(int(record["analysis_id"]))
            created.append(
                {
                    "prompt_id": record["id"],
                    "analysis_id": record["analysis_id"],
                    "status": record.get("analysis_status"),
                    "deduplicated": record.get("deduplicated", False),
                }
            )
        return {"queued": created, "count": len(created)}

    @app.post("/hooks/claude/user-prompt-submit", dependencies=[Depends(require_api_auth)])
    async def claude_user_prompt_submit_hook(
        request: Request,
        payload: ClaudePromptHookPayload,
    ) -> dict[str, Any]:
        repository: Repository = request.app.state.repository
        record = repository.create_prompt(
            text=payload.prompt,
            source="claude-code",
            session_id=payload.session_id,
            metadata={
                "captured_from": "claude_http_hook",
                "hook_event_name": payload.hook_event_name or "UserPromptSubmit",
                "cwd": payload.cwd,
                "transcript_path": payload.transcript_path,
            },
        )
        if record.get("analysis_id") and record.get("analysis_status") == "queued":
            await request.app.state.worker.submit(int(record["analysis_id"]))
        return {}

    @app.get("/api/prompts", dependencies=[Depends(require_api_auth)])
    async def list_prompts_api(
        request: Request,
        source: str | None = Query(default=None),
        status_filter: str | None = Query(default=None, alias="status"),
        page: int = Query(default=1, ge=1),
        page_size: int = Query(default=20, ge=1, le=100),
    ) -> dict[str, Any]:
        return request.app.state.repository.list_prompts(
            source=source or None,
            status=status_filter or None,
            page=page,
            page_size=page_size,
        )

    @app.get("/api/prompts/{prompt_id}", dependencies=[Depends(require_api_auth)])
    async def prompt_detail_api(request: Request, prompt_id: int) -> dict[str, Any]:
        prompt = request.app.state.repository.fetch_prompt_detail(prompt_id)
        if not prompt:
            raise HTTPException(status_code=404, detail="Prompt not found.")
        return prompt

    @app.post("/api/prompts/{prompt_id}/reanalyze", dependencies=[Depends(require_api_auth)])
    async def prompt_reanalyze_api(request: Request, prompt_id: int) -> dict[str, Any]:
        repository: Repository = request.app.state.repository
        prompt = repository.fetch_prompt(prompt_id)
        if not prompt:
            raise HTTPException(status_code=404, detail="Prompt not found.")
        analysis_id = repository.queue_reanalysis(prompt_id)
        await request.app.state.worker.submit(analysis_id)
        return {"prompt_id": prompt_id, "analysis_id": analysis_id, "status": "queued"}

    @app.get("/api/stats/summary", dependencies=[Depends(require_api_auth)])
    async def stats_summary_api(request: Request) -> dict[str, Any]:
        repository: Repository = request.app.state.repository
        return {
            **repository.get_dashboard_stats(),
            "source_breakdown": repository.get_source_breakdown(),
            "recurring_patterns": repository.get_recurring_patterns(),
        }

    @app.get("/api/stats/trends", dependencies=[Depends(require_api_auth)])
    async def stats_trends_api(request: Request, days: int = Query(default=21, ge=1, le=90)) -> dict[str, Any]:
        return {"items": request.app.state.repository.get_trend_series(days=days)}

    return app


app = create_app()
