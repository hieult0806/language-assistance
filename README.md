# Prompt Grammar Tracker

Passive grammar tracking for prompts sent to AI tools. The app never edits or blocks your prompt. It stores a copy, analyzes it asynchronously, and gives you a dashboard with history, suggested rewrites, and recurring grammar patterns.

For the operator guide, see [docs/INSTRUCTIONS.md](docs/INSTRUCTIONS.md).

## Features

- FastAPI backend with a built-in dashboard
- SQLite storage
- Background grammar analysis
- Manual prompt capture
- Import scanning for `.txt` and `.jsonl`
- Hook scaffolding for Claude Code and Codex
- Optional LLM-backed grammar analysis
- Live prompt review page with diff highlighting

## Repository Safety

This public repository does not ship with any real service endpoints.

- Tracker host values must be provided by your environment
- LLM host values must be provided by your environment
- Hook templates use placeholders unless you inject values during install

## Quick Start

1. Copy `.env.example` and fill in the values you want to use.
2. Start the app with:

```bash
docker compose up --build
```

3. Open the tracker in your browser using the host and port you configured.

By default, the public repo starts in `auto` analyzer mode with no built-in LLM endpoint configured. If you want LLM analysis, set:

- `LLM_API_BASE_URL`
- `LLM_MODEL`
- optionally `LLM_API_KEY`

## Environment Variables

- `APP_ENV`
- `APP_HOST`
- `APP_PORT`
- `DATA_DIR`
- `IMPORT_DIR`
- `IMPORT_POLL_INTERVAL_SECONDS`
- `ANALYZER_MODE`
- `ANALYZER_LANGUAGE`
- `LLM_API_BASE_URL`
- `LLM_API_KEY`
- `LLM_MODEL`
- `LLM_TIMEOUT_SECONDS`
- `LLM_MAX_TOKENS`
- `LLM_REASONING_EFFORT`
- `LLM_SEED`
- `APP_API_TOKEN`
- `PROMPT_TRACKER_URL`
- `PROMPT_TRACKER_TOKEN`

## Hook Setup

### Claude Code

Project-local install:

```bash
python scripts/install_hooks.py --client claude
```

Global install:

```bash
python scripts/install_hooks.py --client claude --scope global
```

If you want the installer to write a real tracker endpoint into the generated Claude settings, set `PROMPT_TRACKER_URL` before running it. Otherwise the installed hook will keep the placeholder URL and you must edit it manually.

### Codex

Project install:

```bash
python scripts/install_hooks.py --client codex
```

Codex also requires `codex_hooks = true` in your Codex user config.

## Manual Prompt Sender

The repo includes helper scripts:

- `scripts/send_prompt.py`
- `scripts/send_prompt.ps1`

Both require an explicit tracker URL. No default endpoint is baked into the public repo.

## Development

```bash
python -m venv .venv
pip install -r requirements-dev.txt
pytest
```

## Structure

- `app/`: backend application
- `app/templates/`: server-rendered UI
- `app/static/`: CSS and JavaScript
- `docs/`: operator documentation
- `scripts/`: hook installers and sender utilities
- `tests/`: automated tests

## Notes

- `auto` mode prefers a configured LLM analyzer, then LanguageTool, then the built-in heuristic analyzer
- The Docker image includes Java so LanguageTool can run inside the container when available
- The dashboard is server-rendered and does not require a separate frontend build
