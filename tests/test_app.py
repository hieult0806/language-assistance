from __future__ import annotations

import sqlite3
import time

from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app


def wait_for_completion(client: TestClient, prompt_id: int, timeout: float = 5.0) -> dict:
    deadline = time.time() + timeout
    while time.time() < deadline:
        response = client.get(f"/api/prompts/{prompt_id}")
        assert response.status_code == 200
        payload = response.json()
        latest = payload.get("latest_analysis") or {}
        if latest.get("status") == "completed":
            return payload
        time.sleep(0.1)
    raise AssertionError("Analysis did not complete in time.")


def create_prompt_and_wait(client: TestClient, text: str, source: str = "codex", **extra) -> dict:
    response = client.post(
        "/api/prompts",
        json={"text": text, "source": source, **extra},
    )
    assert response.status_code == 202
    body = response.json()
    return wait_for_completion(client, body["prompt_id"])


def build_settings(tmp_path) -> Settings:
    return Settings(
        app_name="Prompt Grammar Tracker",
        app_env="test",
        app_host="127.0.0.1",
        app_port=8081,
        data_dir=(tmp_path / "data").resolve(),
        import_dir=(tmp_path / "imports").resolve(),
        import_poll_interval_seconds=3600,
        analyzer_mode="heuristic",
        analyzer_language="en-US",
        api_token="",
    )


def test_api_ingest_and_analysis(tmp_path) -> None:
    settings = build_settings(tmp_path)
    app = create_app(settings)
    with TestClient(app) as client:
        detail = create_prompt_and_wait(client, "i want make a tool for codex")
        latest = detail["latest_analysis"]

        assert latest["status"] == "completed"
        assert latest["grammar_score"] < 100
        assert "I want to make" in latest["corrected_text"]


def test_api_ingest_keeps_repeated_prompt_text(tmp_path) -> None:
    settings = build_settings(tmp_path)
    app = create_app(settings)
    with TestClient(app) as client:
        first = client.post("/api/prompts", json={"text": "same prompt", "source": "codex"})
        second = client.post("/api/prompts", json={"text": "same prompt", "source": "codex"})

        assert first.status_code == 202
        assert second.status_code == 202
        assert first.json()["prompt_id"] != second.json()["prompt_id"]

        items = client.get("/api/prompts").json()["items"]
        assert len(items) == 2


def test_api_external_id_deduplicates_prompt(tmp_path) -> None:
    settings = build_settings(tmp_path)
    app = create_app(settings)
    with TestClient(app) as client:
        first = client.post(
            "/api/prompts",
            json={"text": "same event", "source": "codex", "external_id": "event-1"},
        )
        second = client.post(
            "/api/prompts",
            json={"text": "same event", "source": "codex", "external_id": "event-1"},
        )

        assert first.status_code == 202
        assert second.status_code == 202
        assert first.json()["prompt_id"] == second.json()["prompt_id"]
        assert second.json()["deduplicated"] is True
        assert len(client.get("/api/prompts").json()["items"]) == 1


def test_whitespace_only_prompt_returns_422(tmp_path) -> None:
    settings = build_settings(tmp_path)
    app = create_app(settings)
    with TestClient(app) as client:
        response = client.post("/api/prompts", json={"text": "   ", "source": "codex"})
        assert response.status_code == 422


def test_api_bulk_ingest_and_source_filter(tmp_path) -> None:
    settings = build_settings(tmp_path)
    app = create_app(settings)
    with TestClient(app) as client:
        response = client.post(
            "/api/prompts/bulk",
            json={
                "prompts": [
                    {"text": "first bulk prompt", "source": "codex"},
                    {"text": "second bulk prompt", "source": "claude-code"},
                ]
            },
        )
        assert response.status_code == 200
        assert response.json()["count"] == 2

        codex_items = client.get("/api/prompts?source=codex").json()["items"]
        claude_items = client.get("/api/prompts?source=claude-code").json()["items"]
        assert len(codex_items) == 1
        assert len(claude_items) == 1
        assert codex_items[0]["text"] == "first bulk prompt"
        assert claude_items[0]["text"] == "second bulk prompt"


def test_import_scan_processes_jsonl_without_replaying_old_lines(tmp_path) -> None:
    settings = build_settings(tmp_path)
    settings.ensure_directories()
    batch = settings.import_dir / "batch.jsonl"
    batch.write_text(
        '{"text":"i dont know if this prompt is clear","source":"claude-code"}\n',
        encoding="utf-8",
    )
    app = create_app(settings)
    with TestClient(app) as client:
        response = client.post("/settings/imports/scan", follow_redirects=False)
        assert response.status_code == 303
        listing = client.get("/api/prompts")
        assert listing.status_code == 200
        items = listing.json()["items"]
        assert len(items) == 1
        detail = wait_for_completion(client, items[0]["id"])
        assert detail["latest_analysis"]["status"] == "completed"

        batch.write_text(
            '{"text":"i dont know if this prompt is clear","source":"claude-code"}\n'
            '{"text":"this is the second prompt","source":"claude-code"}\n',
            encoding="utf-8",
        )
        response = client.post("/settings/imports/scan", follow_redirects=False)
        assert response.status_code == 303
        items = client.get("/api/prompts").json()["items"]
        assert len(items) == 2
        texts = sorted(item["text"] for item in items)
        assert texts == ["i dont know if this prompt is clear", "this is the second prompt"]


def test_import_scan_same_file_twice_does_not_duplicate(tmp_path) -> None:
    settings = build_settings(tmp_path)
    settings.ensure_directories()
    batch = settings.import_dir / "batch.jsonl"
    batch.write_text(
        '{"text":"first prompt","source":"claude-code"}\n',
        encoding="utf-8",
    )
    app = create_app(settings)
    with TestClient(app) as client:
        first = client.post("/settings/imports/scan", follow_redirects=False)
        second = client.post("/settings/imports/scan", follow_redirects=False)

        assert first.status_code == 303
        assert second.status_code == 303
        items = client.get("/api/prompts").json()["items"]
        assert len(items) == 1


def test_text_import_creates_new_prompt_when_file_changes(tmp_path) -> None:
    settings = build_settings(tmp_path)
    settings.ensure_directories()
    note = settings.import_dir / "prompt.txt"
    note.write_text("first version", encoding="utf-8")
    app = create_app(settings)
    with TestClient(app) as client:
        first = client.post("/settings/imports/scan", follow_redirects=False)
        assert first.status_code == 303
        assert len(client.get("/api/prompts").json()["items"]) == 1

        note.write_text("second version", encoding="utf-8")
        second = client.post("/settings/imports/scan", follow_redirects=False)
        assert second.status_code == 303
        items = client.get("/api/prompts").json()["items"]
        assert len(items) == 2
        assert sorted(item["text"] for item in items) == ["first version", "second version"]


def test_import_scan_malformed_jsonl_records_failure(tmp_path) -> None:
    settings = build_settings(tmp_path)
    settings.ensure_directories()
    broken = settings.import_dir / "broken.jsonl"
    broken.write_text('{"text":"valid line"}\n{not json}\n', encoding="utf-8")
    app = create_app(settings)
    with TestClient(app) as client:
        response = client.post("/settings/imports/scan", follow_redirects=False)
        assert response.status_code == 303
        items = client.get("/api/prompts").json()["items"]
        assert len(items) == 1
        assert items[0]["text"] == "valid line"

    with sqlite3.connect(settings.database_path) as conn:
        row = conn.execute(
            "SELECT status, prompt_count, error_message FROM imports WHERE path = ?",
            (str(broken),),
        ).fetchone()

    assert row is not None
    assert row[0] == "failed"
    assert row[1] == 1
    assert "Expecting property name enclosed in double quotes" in row[2]


def test_dashboard_requires_login_when_token_is_enabled(tmp_path) -> None:
    settings = build_settings(tmp_path)
    settings.api_token = "secret-token"
    app = create_app(settings)
    with TestClient(app) as client:
        dashboard = client.get("/", follow_redirects=False)
        assert dashboard.status_code == 303
        assert dashboard.headers["location"].startswith("/login")

        api = client.get("/api/prompts")
        assert api.status_code == 401

        login = client.post(
            "/login",
            data={"token": "secret-token", "next": "/"},
            follow_redirects=False,
        )
        assert login.status_code == 303
        assert login.headers["location"] == "/"

        dashboard_after_login = client.get("/")
        assert dashboard_after_login.status_code == 200


def test_instructions_page_renders(tmp_path) -> None:
    settings = build_settings(tmp_path)
    app = create_app(settings)
    with TestClient(app) as client:
        response = client.get("/instructions")
        assert response.status_code == 200
        assert "How To Run And Use The Tracker" in response.text
        assert "Project Hook Setup" in response.text


def test_prompt_history_page_renders_with_prompt_rows(tmp_path) -> None:
    settings = build_settings(tmp_path)
    app = create_app(settings)
    with TestClient(app) as client:
        create_prompt_and_wait(client, "i want make a tool for codex")
        response = client.get("/prompts")
        assert response.status_code == 200
        assert "Prompt History" in response.text
        assert "Latest Prompt" in response.text
        assert "What You Sent" in response.text
        assert "Suggested Rewrite" in response.text
        assert "Refine Feed" in response.text
        assert "All sources" in response.text
        assert "diff-token-remove" in response.text
        assert "diff-token-add" in response.text
        assert 'id="prompt-history-panel"' in response.text
        assert 'data-refresh-url="/prompts/table?page=1&amp;page_size=20"' in response.text


def test_prompt_history_table_fragment_updates_with_new_prompts(tmp_path) -> None:
    settings = build_settings(tmp_path)
    app = create_app(settings)
    with TestClient(app) as client:
        create_prompt_and_wait(client, "first tracked prompt", source="codex")

        initial = client.get("/prompts/table")
        assert initial.status_code == 200
        assert "Latest Prompt" in initial.text
        assert 'href="/prompts/1"' in initial.text
        assert "second tracked prompt" not in initial.text
        assert "No earlier prompts match the current filters." in initial.text

        create_prompt_and_wait(client, "second tracked prompt", source="codex")

        refreshed = client.get("/prompts/table")
        assert refreshed.status_code == 200
        assert 'href="/prompts/2"' in refreshed.text
        assert "first tracked prompt" in refreshed.text
        assert "diff-token-remove" in refreshed.text
        assert refreshed.text.index('href="/prompts/2"') < refreshed.text.index("first tracked prompt")


def test_prompt_detail_includes_enriched_issue_explanation(tmp_path) -> None:
    settings = build_settings(tmp_path)
    app = create_app(settings)
    with TestClient(app) as client:
        detail = create_prompt_and_wait(client, "i want make a tool for codex")
        response = client.get(f"/api/prompts/{detail['id']}")
        assert response.status_code == 200
        issues = response.json()["latest_analysis"]["issues"]
        assert issues
        assert "explanation" in issues[0]
        assert issues[0]["explanation"]
        explanation = issues[0]["explanation"]

        page = client.get(f"/prompts/{detail['id']}")
        assert page.status_code == 200
        assert explanation in page.text


def test_login_normalizes_external_next_path(tmp_path) -> None:
    settings = build_settings(tmp_path)
    settings.api_token = "secret-token"
    app = create_app(settings)
    with TestClient(app) as client:
        page = client.get("/login?next=https://evil.example/path")
        assert page.status_code == 200
        assert 'value="/"' in page.text

        login = client.post(
            "/login",
            data={"token": "secret-token", "next": "https://evil.example/path"},
            follow_redirects=False,
        )
        assert login.status_code == 303
        assert login.headers["location"] == "/"


def test_instructions_page_requires_login_when_token_enabled(tmp_path) -> None:
    settings = build_settings(tmp_path)
    settings.api_token = "secret-token"
    app = create_app(settings)
    with TestClient(app) as client:
        gated = client.get("/instructions", follow_redirects=False)
        assert gated.status_code == 303
        assert gated.headers["location"].startswith("/login")

        client.post(
            "/login",
            data={"token": "secret-token", "next": "/instructions"},
            follow_redirects=False,
        )
        unlocked = client.get("/instructions")
        assert unlocked.status_code == 200
        assert "Daily Use" in unlocked.text


def test_logout_revokes_dashboard_access(tmp_path) -> None:
    settings = build_settings(tmp_path)
    settings.api_token = "secret-token"
    app = create_app(settings)
    with TestClient(app) as client:
        login = client.post(
            "/login",
            data={"token": "secret-token", "next": "/"},
            follow_redirects=False,
        )
        assert login.status_code == 303
        assert client.get("/").status_code == 200

        logout = client.post("/logout", follow_redirects=False)
        assert logout.status_code == 303
        assert logout.headers["location"] == "/login"

        redirected = client.get("/", follow_redirects=False)
        assert redirected.status_code == 303
        assert redirected.headers["location"].startswith("/login")


def test_claude_http_hook_ingests_prompt(tmp_path) -> None:
    settings = build_settings(tmp_path)
    app = create_app(settings)
    with TestClient(app) as client:
        response = client.post(
            "/hooks/claude/user-prompt-submit",
            json={
                "session_id": "claude-session-1",
                "hook_event_name": "UserPromptSubmit",
                "cwd": str(tmp_path),
                "prompt": "i need help fix this sentence",
            },
        )
        assert response.status_code == 200
        items = client.get("/api/prompts").json()["items"]
        assert len(items) == 1
        assert items[0]["source"] == "claude-code"
        detail = wait_for_completion(client, items[0]["id"])
        assert detail["latest_analysis"]["status"] == "completed"


def test_claude_http_hook_requires_token_when_enabled(tmp_path) -> None:
    settings = build_settings(tmp_path)
    settings.api_token = "secret-token"
    app = create_app(settings)
    with TestClient(app) as client:
        unauthorized = client.post(
            "/hooks/claude/user-prompt-submit",
            json={"prompt": "fix this sentence"},
        )
        assert unauthorized.status_code == 401

        authorized = client.post(
            "/hooks/claude/user-prompt-submit",
            headers={"X-API-Token": "secret-token"},
            json={"prompt": "fix this sentence"},
        )
        assert authorized.status_code == 200


def test_api_accepts_bearer_token_auth(tmp_path) -> None:
    settings = build_settings(tmp_path)
    settings.api_token = "secret-token"
    app = create_app(settings)
    with TestClient(app) as client:
        response = client.post(
            "/api/prompts",
            headers={"Authorization": "Bearer secret-token"},
            json={"text": "authorized request", "source": "codex"},
        )
        assert response.status_code == 202


def test_manual_capture_form_creates_prompt_and_detail_page_renders(tmp_path) -> None:
    settings = build_settings(tmp_path)
    app = create_app(settings)
    with TestClient(app) as client:
        submit = client.post(
            "/capture",
            data={"text": "i need help improve this", "source": "manual", "session_id": "form-1"},
            follow_redirects=False,
        )
        assert submit.status_code == 303
        location = submit.headers["location"]
        assert location.startswith("/prompts/")

        prompt_id = int(location.rsplit("/", 1)[1])
        wait_for_completion(client, prompt_id)

        page = client.get(location)
        assert page.status_code == 200
        assert "Suggested Rewrite" in page.text
        assert "manual" in page.text


def test_api_reanalyze_creates_second_analysis_entry(tmp_path) -> None:
    settings = build_settings(tmp_path)
    app = create_app(settings)
    with TestClient(app) as client:
        detail = create_prompt_and_wait(client, "i want make a tool for codex")
        prompt_id = detail["id"]
        assert len(detail["analyses"]) == 1

        reanalyze = client.post(f"/api/prompts/{prompt_id}/reanalyze")
        assert reanalyze.status_code == 200
        updated = wait_for_completion(client, prompt_id)
        assert len(updated["analyses"]) == 2
        assert updated["latest_analysis"]["status"] == "completed"


def test_stats_endpoints_return_expected_shapes(tmp_path) -> None:
    settings = build_settings(tmp_path)
    app = create_app(settings)
    with TestClient(app) as client:
        create_prompt_and_wait(client, "i want make a tool for codex", source="codex")
        summary = client.get("/api/stats/summary")
        trends = client.get("/api/stats/trends?days=7")

        assert summary.status_code == 200
        assert trends.status_code == 200
        summary_payload = summary.json()
        trend_payload = trends.json()
        assert summary_payload["total_prompts"] == 1
        assert "source_breakdown" in summary_payload
        assert isinstance(trend_payload["items"], list)


def test_settings_page_shows_llm_runtime_when_configured(tmp_path) -> None:
    settings = build_settings(tmp_path)
    settings.analyzer_mode = "llm"
    settings.llm_api_base_url = "https://llm.example/v1"
    settings.llm_api_key = "test-key"
    settings.llm_model = "grammar-model"
    settings.llm_max_tokens = 256
    settings.llm_reasoning_effort = "none"
    app = create_app(settings)
    with TestClient(app) as client:
        page = client.get("/settings")
        assert page.status_code == 200
        assert "grammar-model" in page.text
        assert "https://llm.example/v1" in page.text
        assert "256" in page.text
        assert "none" in page.text

        health = client.get("/health")
        assert health.status_code == 200
        assert health.json()["engine"] == "llm"


def test_login_rejects_invalid_token(tmp_path) -> None:
    settings = build_settings(tmp_path)
    settings.api_token = "secret-token"
    app = create_app(settings)
    with TestClient(app) as client:
        response = client.post(
            "/login",
            data={"token": "wrong-token", "next": "/settings"},
            follow_redirects=False,
        )
        assert response.status_code == 303
        assert response.headers["location"].startswith("/login?next=/settings&error=invalid")

        dashboard = client.get("/", follow_redirects=False)
        assert dashboard.status_code == 303
