from __future__ import annotations

import io
from urllib import error

from scripts import hook_capture_prompt


def test_build_payload_includes_external_id_and_metadata() -> None:
    payload = hook_capture_prompt.build_payload(
        {
            "prompt": "  fix this sentence  ",
            "session_id": "session-1",
            "turn_id": "turn-9",
            "model": "gpt-5",
            "cwd": "/workspace",
            "transcript_path": "/logs/session.jsonl",
            "hook_event_name": "UserPromptSubmit",
        },
        source="codex",
    )

    assert payload["text"] == "fix this sentence"
    assert payload["source"] == "codex"
    assert payload["session_id"] == "session-1"
    assert payload["external_id"] == "codex:session-1:turn-9"
    assert payload["metadata"] == {
        "captured_from": "command_hook",
        "hook_event_name": "UserPromptSubmit",
        "cwd": "/workspace",
        "transcript_path": "/logs/session.jsonl",
        "turn_id": "turn-9",
        "model": "gpt-5",
    }


def test_build_payload_ignores_empty_prompt() -> None:
    payload = hook_capture_prompt.build_payload({"prompt": "   "}, source="codex")
    assert payload == {}


def test_main_posts_payload_using_env_defaults(monkeypatch) -> None:
    captured: dict[str, object] = {}

    def fake_post(url: str, token: str | None, payload: dict) -> None:
        captured["url"] = url
        captured["token"] = token
        captured["payload"] = payload

    monkeypatch.setenv("PROMPT_TRACKER_URL", "http://tracker.local")
    monkeypatch.setenv("PROMPT_TRACKER_TOKEN", "secret-token")
    monkeypatch.setattr(hook_capture_prompt, "post_prompt", fake_post)
    monkeypatch.setattr(
        hook_capture_prompt.sys,
        "argv",
        ["hook_capture_prompt.py", "--source", "codex"],
    )
    monkeypatch.setattr(
        hook_capture_prompt.sys,
        "stdin",
        io.StringIO('{"prompt":"fix this","session_id":"s1","turn_id":"t1"}'),
    )

    result = hook_capture_prompt.main()

    assert result == 0
    assert captured["url"] == "http://tracker.local"
    assert captured["token"] == "secret-token"
    assert captured["payload"] == {
        "text": "fix this",
        "source": "codex",
        "session_id": "s1",
        "external_id": "codex:s1:t1",
        "metadata": {
            "captured_from": "command_hook",
            "hook_event_name": None,
            "cwd": None,
            "transcript_path": None,
            "turn_id": "t1",
        },
    }


def test_main_swallows_network_errors(monkeypatch) -> None:
    def fake_post(url: str, token: str | None, payload: dict) -> None:
        raise error.URLError("tracker down")

    monkeypatch.setattr(hook_capture_prompt, "post_prompt", fake_post)
    monkeypatch.setattr(
        hook_capture_prompt.sys,
        "argv",
        ["hook_capture_prompt.py", "--source", "codex", "--url", "http://tracker.local"],
    )
    monkeypatch.setattr(
        hook_capture_prompt.sys,
        "stdin",
        io.StringIO('{"prompt":"fix this","session_id":"s1"}'),
    )

    assert hook_capture_prompt.main() == 0


def test_main_ignores_invalid_json(monkeypatch) -> None:
    called = {"post": False}

    def fake_post(url: str, token: str | None, payload: dict) -> None:
        called["post"] = True

    monkeypatch.setattr(hook_capture_prompt, "post_prompt", fake_post)
    monkeypatch.setattr(
        hook_capture_prompt.sys,
        "argv",
        ["hook_capture_prompt.py", "--source", "codex"],
    )
    monkeypatch.setattr(hook_capture_prompt.sys, "stdin", io.StringIO("{not json"))

    assert hook_capture_prompt.main() == 0
    assert called["post"] is False
