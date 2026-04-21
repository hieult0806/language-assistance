from __future__ import annotations

from dataclasses import dataclass
import asyncio
import json
import re
import sys
from typing import Any

import httpx

from app.analysis.base import AnalysisResult, Analyzer, Issue
from app.analysis.heuristic import estimate_clarity, score_from_issues


SYSTEM_PROMPT = (
    "You are an English grammar reviewer. "
    "Analyze English grammar only. "
    "Do not optimize for coding, style, tone, or prompt strategy. "
    "Return one concise JSON object only. No markdown."
)


def _user_prompt(text: str, language: str) -> str:
    return (
        f"Language: {language}\n"
        "Return a JSON object with keys corrected_text, summary, and issues.\n"
        "Each issue must have category, severity, message, suggestion, and replacement.\n"
        "Do not include offsets.\n"
        "Keep the summary under 14 words.\n"
        "Keep each issue message and suggestion under 8 words.\n"
        "Report only distinct grammar, spelling, punctuation, capitalization, tense, agreement, "
        "article, and word-form issues.\n"
        "If corrected_text changes capitalization in more than one place, include one capitalization "
        "issue that mentions all affected words.\n"
        "Use official capitalization for known product names when obvious from context, especially "
        "Codex and Claude Code.\n"
        "Every correction in corrected_text must be reflected in issues.\n"
        f"Prompt:\n{text}"
    )


@dataclass(slots=True)
class LLMConfig:
    base_url: str
    model: str
    api_key: str = ""
    timeout_seconds: float = 30.0
    max_tokens: int = 384
    reasoning_effort: str = ""
    seed: int | None = None

    def validate(self) -> None:
        if not self.base_url.strip():
            raise RuntimeError("LLM_API_BASE_URL is required for llm analyzer mode.")
        if not self.model.strip():
            raise RuntimeError("LLM_MODEL is required for llm analyzer mode.")
        if self.max_tokens < 64:
            raise RuntimeError("LLM_MAX_TOKENS must be at least 64.")


def _extract_json_object(payload: str) -> dict[str, Any]:
    text = (payload or "").strip()
    if not text:
        raise RuntimeError("LLM returned an empty response.")

    if text.startswith("```"):
        lines = text.splitlines()
        if len(lines) >= 3:
            text = "\n".join(lines[1:-1]).strip()

    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end == -1 or end < start:
        raise RuntimeError("LLM response did not contain a JSON object.")

    try:
        data = json.loads(text[start : end + 1])
    except json.JSONDecodeError as exc:  # pragma: no cover - depends on provider output
        raise RuntimeError(f"LLM response was not valid JSON: {exc}") from exc

    if not isinstance(data, dict):
        raise RuntimeError("LLM response root must be a JSON object.")
    return data


def _message_payload(response_json: dict[str, Any]) -> dict[str, Any]:
    choices = response_json.get("choices")
    if not isinstance(choices, list) or not choices:
        raise RuntimeError("LLM response did not include choices.")

    message = choices[0].get("message") if isinstance(choices[0], dict) else None
    if not isinstance(message, dict):
        raise RuntimeError("LLM response did not include a message payload.")
    return message


def _message_text(response_json: dict[str, Any]) -> str:
    message = _message_payload(response_json)
    content = message.get("content")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for item in content:
            if isinstance(item, dict):
                if isinstance(item.get("text"), str):
                    parts.append(item["text"])
                elif item.get("type") == "text" and isinstance(item.get("content"), str):
                    parts.append(item["content"])
            elif isinstance(item, str):
                parts.append(item)
        if parts:
            return "\n".join(parts)
    raise RuntimeError("LLM response content was empty or in an unsupported format.")


def _normalize_severity(value: object) -> str:
    severity = str(value or "medium").strip().lower()
    if severity not in {"low", "medium", "high"}:
        return "medium"
    return severity


def _normalize_category(value: object) -> str:
    category = str(value or "grammar").strip().lower().replace(" ", "_")
    return category or "grammar"


def _coerce_optional_int(value: object) -> int | None:
    if value is None or value == "":
        return None
    if isinstance(value, bool):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _normalize_known_terms(text: str | None) -> str | None:
    if text is None:
        return None
    value = text
    replacements = (
        (r"\benglish\b", "English"),
        (r"\bcodex\b", "Codex"),
        (r"\bclaude code\b", "Claude Code"),
        (r"\bclaude\b", "Claude"),
    )
    for pattern, replacement in replacements:
        value = re.sub(pattern, replacement, value, flags=re.IGNORECASE)
    return value


class LLMAnalyzer(Analyzer):
    engine_name = "llm"

    def __init__(
        self,
        config: LLMConfig,
        language: str = "en-US",
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        config.validate()
        self.config = config
        self.language = language
        self.transport = transport

    def close(self) -> None:
        return None

    def _payload(
        self,
        text: str,
        *,
        max_tokens: int | None = None,
        include_reasoning: bool = True,
        include_seed: bool = True,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "model": self.config.model,
            "temperature": 0,
            "max_tokens": max_tokens or self.config.max_tokens,
            "response_format": {"type": "json_object"},
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": _user_prompt(text, self.language)},
            ],
        }
        if include_reasoning and self.config.reasoning_effort.strip():
            payload["reasoning_effort"] = self.config.reasoning_effort
        if include_seed and self.config.seed is not None:
            payload["seed"] = self.config.seed
        return payload

    def _parse_issues(self, payload: object) -> list[Issue]:
        if not isinstance(payload, list):
            return []

        issues: list[Issue] = []
        seen: set[tuple[str, str, str | None]] = set()
        for item in payload:
            if not isinstance(item, dict):
                continue
            issue = Issue(
                category=_normalize_category(item.get("category")),
                severity=_normalize_severity(item.get("severity")),
                message=str(item.get("message") or "Grammar issue detected.").strip(),
                suggestion=_normalize_known_terms(
                    str(item["suggestion"]).strip() if item.get("suggestion") is not None else None
                ),
                replacement=_normalize_known_terms(
                    str(item["replacement"]).strip() if item.get("replacement") is not None else None
                ),
                start_offset=_coerce_optional_int(item.get("start_offset")),
                end_offset=_coerce_optional_int(item.get("end_offset")),
            )
            if not issue.message:
                continue
            dedupe_key = (issue.category, issue.message, issue.replacement)
            if dedupe_key in seen:
                continue
            seen.add(dedupe_key)
            issues.append(issue)
        return issues

    def _post(self, payload: dict[str, Any]) -> dict[str, Any]:
        headers = self._headers()
        with httpx.Client(
            base_url=self.config.base_url.rstrip("/"),
            timeout=self.config.timeout_seconds,
            transport=self.transport,
        ) as client:
            response = client.post(
                "/chat/completions",
                headers=headers,
                json=payload,
            )
            response.raise_for_status()
            return response.json()

    async def _post_async(self, payload: dict[str, Any]) -> dict[str, Any]:
        headers = self._headers()
        async with httpx.AsyncClient(
            base_url=self.config.base_url.rstrip("/"),
            timeout=self.config.timeout_seconds,
            transport=self.transport,
        ) as client:
            response = await client.post(
                "/chat/completions",
                headers=headers,
                json=payload,
            )
            response.raise_for_status()
            return response.json()

    def _headers(self) -> dict[str, str]:
        headers = {"Content-Type": "application/json"}
        if self.config.api_key.strip():
            headers["Authorization"] = f"Bearer {self.config.api_key}"
        return headers

    def _build_result(self, response_json: dict[str, Any], text: str, payload: dict[str, Any], attempt: int) -> AnalysisResult:
        content = _message_text(response_json)
        parsed = _extract_json_object(content)

        corrected_text = _normalize_known_terms(str(parsed.get("corrected_text") or text).strip()) or text
        issues = self._parse_issues(parsed.get("issues"))
        if corrected_text != text and not issues:
            issues.append(
                Issue(
                    category="grammar",
                    severity="low",
                    message="Corrected text differs from original.",
                    suggestion="Review the suggested rewrite.",
                    replacement=corrected_text,
                )
            )

        grammar_score = score_from_issues(issues)
        clarity_score = estimate_clarity(corrected_text, [])
        summary = str(parsed.get("summary") or f"Found {len(issues)} grammar issue(s).").strip()
        if not summary:
            summary = f"Found {len(issues)} grammar issue(s)."

        raw_message = _message_payload(response_json)
        choice = response_json.get("choices", [{}])[0]
        finish_reason = choice.get("finish_reason") if isinstance(choice, dict) else None

        return AnalysisResult(
            engine=self.engine_name,
            grammar_score=grammar_score,
            clarity_score=clarity_score,
            corrected_text=corrected_text,
            summary=summary,
            issues=issues,
            raw={
                "mode": self.engine_name,
                "model": self.config.model,
                "base_url": self.config.base_url,
                "usage": response_json.get("usage", {}),
                "finish_reason": finish_reason,
                "provider_summary": parsed.get("summary"),
                "provider_grammar_score": parsed.get("grammar_score"),
                "reasoning": raw_message.get("reasoning"),
                "max_tokens": payload.get("max_tokens"),
                "reasoning_effort": payload.get("reasoning_effort"),
                "seed": payload.get("seed"),
                "response_format": "json_object",
                "attempt": attempt,
            },
        )

    def _result_from_dict(self, payload: dict[str, Any]) -> AnalysisResult:
        issues = [
            Issue(
                category=str(item.get("category") or "grammar"),
                severity=str(item.get("severity") or "medium"),
                message=str(item.get("message") or "Grammar issue detected."),
                suggestion=item.get("suggestion"),
                replacement=item.get("replacement"),
                start_offset=item.get("start_offset"),
                end_offset=item.get("end_offset"),
            )
            for item in payload.get("issues", [])
            if isinstance(item, dict)
        ]
        return AnalysisResult(
            engine=str(payload.get("engine") or "llm"),
            grammar_score=int(payload.get("grammar_score") or 0),
            clarity_score=int(payload.get("clarity_score") or 0),
            corrected_text=str(payload.get("corrected_text") or ""),
            summary=str(payload.get("summary") or ""),
            issues=issues,
            raw=payload.get("raw", {}) if isinstance(payload.get("raw"), dict) else {},
        )

    async def _analyze_via_subprocess(self, text: str) -> AnalysisResult:
        payload = {
            "base_url": self.config.base_url,
            "model": self.config.model,
            "api_key": self.config.api_key,
            "timeout_seconds": self.config.timeout_seconds,
            "max_tokens": self.config.max_tokens,
            "reasoning_effort": self.config.reasoning_effort,
            "seed": self.config.seed,
            "language": self.language,
            "text": text,
        }
        process = await asyncio.create_subprocess_exec(
            sys.executable,
            "-m",
            "app.analysis.llm_subprocess",
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        timeout_seconds = max((self.config.timeout_seconds * 2) + 30, 120)
        try:
            stdout, stderr = await asyncio.wait_for(
                process.communicate(json.dumps(payload).encode("utf-8")),
                timeout=timeout_seconds,
            )
        except TimeoutError as exc:
            process.kill()
            await process.wait()
            raise RuntimeError("LLM subprocess timed out.") from exc
        if process.returncode != 0:
            message = stderr.decode("utf-8", errors="replace").strip() or "LLM subprocess failed."
            raise RuntimeError(message)
        try:
            data = json.loads(stdout.decode("utf-8"))
        except json.JSONDecodeError as exc:
            raise RuntimeError("LLM subprocess returned invalid JSON.") from exc
        return self._result_from_dict(data)

    def analyze(self, text: str) -> AnalysisResult:
        primary_payload = self._payload(text)
        attempts = [
            (1, primary_payload),
            (
                2,
                self._payload(
                    text,
                    max_tokens=max(self.config.max_tokens + 128, 384),
                    include_reasoning=False,
                    include_seed=False,
                ),
            ),
        ]

        last_error: Exception | None = None
        for attempt, payload in attempts:
            try:
                response_json = self._post(payload)
                return self._build_result(response_json, text, payload, attempt)
            except RuntimeError as exc:
                last_error = exc
                message = str(exc).lower()
                if not any(
                    pattern in message
                    for pattern in (
                        "empty response",
                        "did not contain a json object",
                        "not valid json",
                        "unsupported format",
                    )
                ):
                    raise
            except httpx.ReadTimeout as exc:
                last_error = exc
            except httpx.HTTPError:
                raise

        if last_error is not None:
            raise RuntimeError(str(last_error)) from last_error
        raise RuntimeError("LLM analysis failed.")

    async def analyze_async(self, text: str) -> AnalysisResult:
        if self.transport is None:
            return await self._analyze_via_subprocess(text)

        primary_payload = self._payload(text)
        attempts = [
            (1, primary_payload),
            (
                2,
                self._payload(
                    text,
                    max_tokens=max(self.config.max_tokens + 128, 384),
                    include_reasoning=False,
                    include_seed=False,
                ),
            ),
        ]

        last_error: Exception | None = None
        for attempt, payload in attempts:
            try:
                response_json = await self._post_async(payload)
                return self._build_result(response_json, text, payload, attempt)
            except RuntimeError as exc:
                last_error = exc
                message = str(exc).lower()
                if not any(
                    pattern in message
                    for pattern in (
                        "empty response",
                        "did not contain a json object",
                        "not valid json",
                        "unsupported format",
                    )
                ):
                    raise
            except httpx.ReadTimeout as exc:
                last_error = exc
                await asyncio.sleep(0.25)
            except httpx.HTTPError:
                raise

        if last_error is not None:
            raise RuntimeError(str(last_error)) from last_error
        raise RuntimeError("LLM analysis failed.")
