from __future__ import annotations

import json
from pathlib import Path

from scripts import install_hooks


def write_template(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def test_install_claude_creates_local_settings(tmp_path) -> None:
    repo = tmp_path
    example = {
        "hooks": {
            "UserPromptSubmit": [
                {"hooks": [{"type": "http", "url": "http://localhost:8080/hooks/claude/user-prompt-submit"}]}
            ]
        }
    }
    write_template(repo / ".claude" / "settings.example.json", example)

    target, mode = install_hooks.install_claude(repo)

    assert mode == "created"
    assert target == repo / ".claude" / "settings.local.json"
    assert json.loads(target.read_text(encoding="utf-8")) == example


def test_install_claude_global_merges_into_user_settings(monkeypatch, tmp_path) -> None:
    repo = tmp_path / "repo"
    home = tmp_path / "home"
    incoming = {
        "hooks": {
            "UserPromptSubmit": [
                {"hooks": [{"type": "http", "url": "http://localhost:8080/hooks/claude/user-prompt-submit"}]}
            ]
        }
    }
    existing = {
        "hooks": {
            "Notification": [
                {"hooks": [{"type": "command", "command": "echo notify"}]}
            ]
        },
        "theme": "dark",
    }
    write_template(repo / ".claude" / "settings.example.json", incoming)
    write_template(home / ".claude" / "settings.json", existing)
    monkeypatch.setattr(install_hooks, "user_home", lambda: home)

    target, mode = install_hooks.install_claude(repo, scope="global")
    merged = json.loads(target.read_text(encoding="utf-8"))

    assert mode == "merged"
    assert target == home / ".claude" / "settings.json"
    assert merged["theme"] == "dark"
    assert "Notification" in merged["hooks"]
    assert "UserPromptSubmit" in merged["hooks"]


def test_install_codex_merges_without_duplicate_groups(tmp_path) -> None:
    repo = tmp_path
    incoming = {
        "hooks": {
            "UserPromptSubmit": [
                {"hooks": [{"type": "command", "command": "python scripts/hook_capture_prompt.py --source codex"}]}
            ]
        }
    }
    existing = {
        "hooks": {
            "UserPromptSubmit": [
                {"hooks": [{"type": "command", "command": "echo existing"}]},
                incoming["hooks"]["UserPromptSubmit"][0],
            ]
        }
    }
    write_template(repo / ".codex" / "hooks.example.json", incoming)
    target = repo / ".codex" / "hooks.json"
    write_template(target, existing)

    result_target, mode = install_hooks.install_codex(repo)
    merged = json.loads(result_target.read_text(encoding="utf-8"))

    assert mode == "merged"
    assert result_target == target
    assert len(merged["hooks"]["UserPromptSubmit"]) == 2
    commands = [group["hooks"][0]["command"] for group in merged["hooks"]["UserPromptSubmit"]]
    assert commands == ["echo existing", "python scripts/hook_capture_prompt.py --source codex"]


def test_main_reports_installed_clients(monkeypatch, tmp_path, capsys) -> None:
    repo = tmp_path
    write_template(
        repo / ".claude" / "settings.example.json",
        {"hooks": {"UserPromptSubmit": [{"hooks": [{"type": "http", "url": "http://localhost"}]}]}},
    )
    write_template(
        repo / ".codex" / "hooks.example.json",
        {"hooks": {"UserPromptSubmit": [{"hooks": [{"type": "command", "command": "python hook.py"}]}]}},
    )

    monkeypatch.setattr(install_hooks, "repo_root", lambda: repo)
    monkeypatch.setattr(
        install_hooks.sys,
        "argv",
        ["install_hooks.py", "--client", "claude", "--client", "codex"],
    )

    result = install_hooks.main()
    out = capsys.readouterr().out

    assert result == 0
    assert "Claude Code (local): created" in out
    assert "Codex (project): created" in out
    assert "codex_hooks = true" in out


def test_main_installs_global_claude_when_scope_requested(monkeypatch, tmp_path, capsys) -> None:
    repo = tmp_path / "repo"
    home = tmp_path / "home"
    write_template(
        repo / ".claude" / "settings.example.json",
        {"hooks": {"UserPromptSubmit": [{"hooks": [{"type": "http", "url": "http://localhost"}]}]}},
    )

    monkeypatch.setattr(install_hooks, "repo_root", lambda: repo)
    monkeypatch.setattr(install_hooks, "user_home", lambda: home)
    monkeypatch.setattr(
        install_hooks.sys,
        "argv",
        ["install_hooks.py", "--client", "claude", "--scope", "global"],
    )

    result = install_hooks.main()
    out = capsys.readouterr().out

    assert result == 0
    assert "Claude Code (global): created" in out
    assert json.loads((home / ".claude" / "settings.json").read_text(encoding="utf-8"))["hooks"]
