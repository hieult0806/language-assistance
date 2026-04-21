from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sys


def repo_root() -> Path:
    return Path(__file__).resolve().parent.parent


def user_home() -> Path:
    return Path.home()


def merge_hook_config(target: Path, incoming: dict) -> tuple[Path, str]:
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists():
        current = json.loads(target.read_text(encoding="utf-8"))
        current_hooks = current.setdefault("hooks", {})
        for event_name, groups in incoming.get("hooks", {}).items():
            merged = current_hooks.setdefault(event_name, [])
            serialized = {json.dumps(group, sort_keys=True) for group in merged}
            for group in groups:
                encoded = json.dumps(group, sort_keys=True)
                if encoded not in serialized:
                    merged.append(group)
                    serialized.add(encoded)
        target.write_text(json.dumps(current, indent=2) + "\n", encoding="utf-8")
        return target, "merged"

    target.write_text(json.dumps(incoming, indent=2) + "\n", encoding="utf-8")
    return target, "created"


def hydrate_tracker_url(payload: dict) -> dict:
    tracker_url = os.getenv("PROMPT_TRACKER_URL", "").strip()
    if not tracker_url:
        return payload
    cloned = json.loads(json.dumps(payload))
    hooks = cloned.get("hooks", {})
    for groups in hooks.values():
        if not isinstance(groups, list):
            continue
        for group in groups:
            if not isinstance(group, dict):
                continue
            for hook in group.get("hooks", []):
                if isinstance(hook, dict) and isinstance(hook.get("url"), str):
                    hook["url"] = hook["url"].replace("__TRACKER_BASE_URL__", tracker_url.rstrip("/"))
    return cloned


def install_claude(repo: Path, scope: str = "local") -> tuple[Path, str]:
    source = repo / ".claude" / "settings.example.json"
    incoming = hydrate_tracker_url(json.loads(source.read_text(encoding="utf-8")))

    if scope == "local":
        target = repo / ".claude" / "settings.local.json"
    elif scope == "project":
        target = repo / ".claude" / "settings.json"
    elif scope == "global":
        target = user_home() / ".claude" / "settings.json"
    else:
        raise ValueError(f"Unsupported Claude scope: {scope}")

    return merge_hook_config(target, incoming)


def install_codex(repo: Path) -> tuple[Path, str]:
    source = repo / ".codex" / "hooks.example.json"
    target = repo / ".codex" / "hooks.json"
    incoming = json.loads(source.read_text(encoding="utf-8"))
    return merge_hook_config(target, incoming)


def main() -> int:
    parser = argparse.ArgumentParser(description="Install Prompt Grammar Tracker hook config templates.")
    parser.add_argument(
        "--client",
        action="append",
        choices=["claude", "codex"],
        required=True,
        help="Install hook config for one or more clients.",
    )
    parser.add_argument(
        "--scope",
        choices=["local", "project", "global"],
        default=None,
        help="Hook scope. Claude defaults to local; Codex currently supports only project scope.",
    )
    args = parser.parse_args()

    root = repo_root()
    installed: list[str] = []

    if "codex" in args.client and args.scope not in {None, "project"}:
        parser.error("Codex currently supports only project scope.")

    if "claude" in args.client:
        claude_scope = args.scope or "local"
        target, mode = install_claude(root, scope=claude_scope)
        installed.append(f"Claude Code ({claude_scope}): {mode} {target}")

    if "codex" in args.client:
        target, mode = install_codex(root)
        installed.append(f"Codex (project): {mode} {target}")
        installed.append("Codex reminder: enable [features] codex_hooks = true in ~/.codex/config.toml.")
        installed.append("Codex reminder: official docs currently mark Windows hook support as disabled.")

    sys.stdout.write("\n".join(installed) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
