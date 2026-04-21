from __future__ import annotations

import json

import httpx
import pytest

from app.analysis.base import AnalysisResult
from app.analysis.llm import LLMAnalyzer, LLMConfig
from app.analysis.service import AnalyzerService


def test_llm_analyzer_parses_chat_completion_response() -> None:
    captured: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["authorization"] = request.headers.get("Authorization")
        captured["payload"] = json.loads(request.content.decode("utf-8"))
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {
                            "content": json.dumps(
                                {
                                    "grammar_score": 84,
                                    "corrected_text": "I want to make a tool for Codex.",
                                    "summary": "Found 2 grammar issues.",
                                    "issues": [
                                        {
                                            "category": "grammar",
                                            "severity": "high",
                                            "message": "Missing infinitive after 'want'.",
                                            "suggestion": "Use 'want to make'.",
                                            "replacement": "want to make",
                                            "start_offset": 2,
                                            "end_offset": 11,
                                        },
                                        {
                                            "category": "capitalization",
                                            "severity": "low",
                                            "message": "Proper noun should be capitalized.",
                                            "suggestion": "Capitalize 'Codex'.",
                                            "replacement": "Codex",
                                            "start_offset": 25,
                                            "end_offset": 30,
                                        },
                                    ],
                                }
                            )
                        }
                    }
                ],
                "usage": {"total_tokens": 123},
            },
        )

    analyzer = LLMAnalyzer(
        config=LLMConfig(
            base_url="https://llm.example/v1",
            api_key="test-key",
            model="grammar-model",
            timeout_seconds=5,
        ),
        transport=httpx.MockTransport(handler),
    )

    try:
        result = analyzer.analyze("i want make a tool for codex")
    finally:
        analyzer.close()

    assert result.engine == "llm"
    assert result.grammar_score == 81
    assert result.corrected_text == "I want to make a tool for Codex."
    assert result.summary == "Found 2 grammar issues."
    assert len(result.issues) == 2
    assert result.issues[0].category == "grammar"
    assert result.issues[1].category == "capitalization"
    assert result.raw["model"] == "grammar-model"
    assert result.raw["provider_grammar_score"] == 84
    assert captured["url"] == "https://llm.example/v1/chat/completions"
    assert captured["authorization"] == "Bearer test-key"
    assert captured["payload"]["model"] == "grammar-model"
    assert captured["payload"]["temperature"] == 0
    assert captured["payload"]["max_tokens"] == 384
    assert captured["payload"]["response_format"]["type"] == "json_object"
    assert "reasoning_effort" not in captured["payload"]
    assert "Do not include offsets." in captured["payload"]["messages"][1]["content"]
    assert "Codex and Claude Code" in captured["payload"]["messages"][1]["content"]


def test_llm_analyzer_allows_missing_api_key_for_local_endpoint() -> None:
    captured: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["authorization"] = request.headers.get("Authorization")
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {
                            "content": json.dumps(
                                {
                                    "grammar_score": 90,
                                    "corrected_text": "This is valid.",
                                    "summary": "No major grammar issues found.",
                                    "issues": [],
                                }
                            )
                        }
                    }
                ]
            },
        )

    analyzer = LLMAnalyzer(
        config=LLMConfig(
            base_url="https://ollama.lan/v1",
            model="gemma4:e4b",
            api_key="",
        ),
        transport=httpx.MockTransport(handler),
    )

    try:
        result = analyzer.analyze("This is valid.")
    finally:
        analyzer.close()

    assert result.engine == "llm"
    assert result.grammar_score == 100
    assert result.raw["provider_grammar_score"] == 90
    assert captured["authorization"] is None


def test_llm_analyzer_sends_reasoning_effort_and_seed_when_configured() -> None:
    captured: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["payload"] = json.loads(request.content.decode("utf-8"))
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {
                            "content": json.dumps(
                                {
                                    "corrected_text": "This is valid.",
                                    "summary": "No issues found.",
                                    "issues": [],
                                }
                            )
                        },
                        "finish_reason": "stop",
                    }
                ]
            },
        )

    analyzer = LLMAnalyzer(
        config=LLMConfig(
            base_url="https://ollama.lan/v1",
            model="gemma4:31b",
            api_key="",
            max_tokens=256,
            reasoning_effort="none",
            seed=7,
        ),
        transport=httpx.MockTransport(handler),
    )

    try:
        result = analyzer.analyze("This is valid.")
    finally:
        analyzer.close()

    assert result.raw["reasoning_effort"] == "none"
    assert result.raw["seed"] == 7
    assert captured["payload"]["reasoning_effort"] == "none"
    assert captured["payload"]["seed"] == 7
    assert captured["payload"]["max_tokens"] == 256


def test_llm_analyzer_accepts_fenced_json_response() -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {
                            "content": """```json
{"grammar_score": 96, "corrected_text": "This prompt is fine.", "summary": "No issues found.", "issues": []}
```"""
                        }
                    }
                ]
            },
        )

    analyzer = LLMAnalyzer(
        config=LLMConfig(
            base_url="https://llm.example/v1",
            api_key="test-key",
            model="grammar-model",
        ),
        transport=httpx.MockTransport(handler),
    )

    try:
        result = analyzer.analyze("This prompt is fine.")
    finally:
        analyzer.close()

    assert result.grammar_score == 100
    assert result.raw["provider_grammar_score"] == 96
    assert result.issues == []
    assert result.corrected_text == "This prompt is fine."


def test_llm_analyzer_adds_fallback_issue_when_text_changes_without_issues() -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {
                            "content": json.dumps(
                                {
                                    "corrected_text": "I want to make a tool.",
                                    "summary": "Grammar fixed.",
                                    "issues": [],
                                }
                            )
                        }
                    }
                ]
            },
        )

    analyzer = LLMAnalyzer(
        config=LLMConfig(
            base_url="https://llm.example/v1",
            api_key="test-key",
            model="grammar-model",
        ),
        transport=httpx.MockTransport(handler),
    )

    try:
        result = analyzer.analyze("i want make a tool")
    finally:
        analyzer.close()

    assert len(result.issues) == 1
    assert result.issues[0].message == "Corrected text differs from original."
    assert result.grammar_score == 96


def test_llm_analyzer_normalizes_known_product_names() -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {
                            "content": json.dumps(
                                {
                                    "corrected_text": "I want to make a tool for codex and claude code.",
                                    "summary": "Capitalization fixed.",
                                    "issues": [
                                        {
                                            "category": "capitalization",
                                            "severity": "medium",
                                            "message": "Proper nouns need capitalization.",
                                            "suggestion": "Capitalize codex and claude code.",
                                            "replacement": "codex and claude code",
                                        }
                                    ],
                                }
                            )
                        }
                    }
                ]
            },
        )

    analyzer = LLMAnalyzer(
        config=LLMConfig(
            base_url="https://ollama.lan/v1",
            model="gemma4:e4b",
        ),
        transport=httpx.MockTransport(handler),
    )

    try:
        result = analyzer.analyze("i want make a tool for codex and claude code")
    finally:
        analyzer.close()

    assert result.corrected_text == "I want to make a tool for Codex and Claude Code."
    assert result.issues[0].suggestion == "Capitalize Codex and Claude Code."
    assert result.issues[0].replacement == "Codex and Claude Code"


def test_llm_analyzer_retries_after_empty_response() -> None:
    attempts = {"count": 0}

    def handler(_: httpx.Request) -> httpx.Response:
        attempts["count"] += 1
        if attempts["count"] == 1:
            return httpx.Response(
                200,
                json={
                    "choices": [
                        {
                            "message": {
                                "content": "",
                            }
                        }
                    ]
                },
            )
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {
                            "content": json.dumps(
                                {
                                    "corrected_text": "I want to make a tool.",
                                    "summary": "Fixed infinitive form.",
                                    "issues": [
                                        {
                                            "category": "grammar",
                                            "severity": "high",
                                            "message": "Missing 'to' before verb.",
                                            "suggestion": "Use infinitive form.",
                                            "replacement": "want to make",
                                        }
                                    ],
                                }
                            )
                        }
                    }
                ]
            },
        )

    analyzer = LLMAnalyzer(
        config=LLMConfig(
            base_url="https://ollama.lan/v1",
            model="gemma4:31b",
            max_tokens=256,
            reasoning_effort="none",
            seed=7,
        ),
        transport=httpx.MockTransport(handler),
    )

    try:
        result = analyzer.analyze("i want make a tool")
    finally:
        analyzer.close()

    assert attempts["count"] == 2
    assert result.corrected_text == "I want to make a tool."
    assert result.raw["attempt"] == 2
    assert result.raw["reasoning_effort"] is None
    assert result.raw["seed"] is None
    assert result.raw["max_tokens"] == 384


def test_analyzer_service_auto_prefers_llm_when_configured(monkeypatch) -> None:
    class FakeLLMAnalyzer:
        def __init__(self, config, language="en-US", transport=None) -> None:
            self.config = config

        def analyze(self, text: str) -> AnalysisResult:
            return AnalysisResult(
                engine="llm",
                grammar_score=91,
                clarity_score=100,
                corrected_text=text,
                summary="LLM analyzed grammar.",
                issues=[],
                raw={"mode": "llm"},
            )

        def close(self) -> None:
            return None

    monkeypatch.setattr("app.analysis.service.LLMAnalyzer", FakeLLMAnalyzer)

    service = AnalyzerService(
        mode="auto",
        llm_api_base_url="https://llm.example/v1",
        llm_api_key="test-key",
        llm_model="grammar-model",
    )
    try:
        result = service.analyze("sample prompt")
    finally:
        service.close()

    assert service.active_engine == "llm"
    assert result.engine == "llm"
    assert result.summary == "LLM analyzed grammar."


def test_analyzer_service_llm_mode_requires_model() -> None:
    with pytest.raises(RuntimeError):
        AnalyzerService(
            mode="llm",
            llm_api_base_url="https://llm.example/v1",
            llm_model="",
        )
