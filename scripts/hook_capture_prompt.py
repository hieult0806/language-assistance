from __future__ import annotations

import argparse
import json
import os
import sys
from urllib import error, request


def build_payload(hook_input: dict, source: str) -> dict:
    prompt = str(hook_input.get("prompt", "")).strip()
    if not prompt:
        return {}

    metadata = {
        "captured_from": "command_hook",
        "hook_event_name": hook_input.get("hook_event_name"),
        "cwd": hook_input.get("cwd"),
        "transcript_path": hook_input.get("transcript_path"),
    }
    if "turn_id" in hook_input:
        metadata["turn_id"] = hook_input.get("turn_id")
    if "model" in hook_input:
        metadata["model"] = hook_input.get("model")

    payload: dict[str, object] = {
        "text": prompt,
        "source": source,
        "session_id": hook_input.get("session_id"),
        "metadata": metadata,
    }

    turn_id = hook_input.get("turn_id")
    if hook_input.get("session_id") and turn_id:
        payload["external_id"] = f"{source}:{hook_input['session_id']}:{turn_id}"

    return payload


def post_prompt(url: str, token: str | None, payload: dict) -> None:
    req = request.Request(
        url=f"{url.rstrip('/')}/api/prompts",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    if token:
        req.add_header("X-API-Token", token)

    with request.urlopen(req, timeout=5):
        return


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Read hook JSON from stdin and forward the prompt to Prompt Grammar Tracker."
    )
    parser.add_argument("--source", required=True, help="Prompt source label, for example codex.")
    parser.add_argument(
        "--url",
        default=os.getenv("PROMPT_TRACKER_URL", ""),
        help="Tracker base URL. Defaults to PROMPT_TRACKER_URL.",
    )
    parser.add_argument(
        "--token",
        default=os.getenv("PROMPT_TRACKER_TOKEN"),
        help="Optional tracker token. Defaults to PROMPT_TRACKER_TOKEN.",
    )
    args = parser.parse_args()

    try:
        raw = sys.stdin.read()
        hook_input = json.loads(raw or "{}")
    except json.JSONDecodeError:
        return 0

    payload = build_payload(hook_input, source=args.source)
    if not payload:
        return 0
    if not args.url.strip():
        return 0

    try:
        post_prompt(args.url, args.token, payload)
    except (TimeoutError, OSError, error.URLError, error.HTTPError):
        # Passive logging must never block the prompt flow.
        return 0
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
