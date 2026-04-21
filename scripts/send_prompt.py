from __future__ import annotations

import argparse
import json
import sys
from urllib import request


def main() -> int:
    parser = argparse.ArgumentParser(description="Send a prompt copy to Prompt Grammar Tracker.")
    parser.add_argument("--url", default="")
    parser.add_argument("--source", default="manual")
    parser.add_argument("--session-id", default=None)
    parser.add_argument("--external-id", default=None)
    parser.add_argument("--token", default=None)
    parser.add_argument("--text", default=None)
    args = parser.parse_args()

    text = args.text or sys.stdin.read().strip()
    if not text:
        print("Prompt text is required.", file=sys.stderr)
        return 1
    if not args.url.strip():
        print("Tracker URL is required.", file=sys.stderr)
        return 1

    payload = json.dumps(
        {
            "text": text,
            "source": args.source,
            "session_id": args.session_id,
            "external_id": args.external_id,
            "metadata": {"captured_from": "send_prompt.py"},
        }
    ).encode("utf-8")

    req = request.Request(
        url=f"{args.url.rstrip('/')}/api/prompts",
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    if args.token:
        req.add_header("X-API-Token", args.token)

    with request.urlopen(req) as response:
        body = response.read().decode("utf-8")
        print(body)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
