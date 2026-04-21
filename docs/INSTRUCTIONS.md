# Instructions

This document is the operator guide for Prompt Grammar Tracker. Use it when you want to install the app, connect a client, confirm prompt capture, and troubleshoot common problems.

## What The App Does

- captures a copy of prompts after you send them to an AI client
- analyzes grammar asynchronously and keeps the existing clarity score for dashboard trends
- stores prompt history in SQLite
- shows trends and recurring issues in a browser dashboard

The app does not modify, delay, or block the prompt that goes to Codex or Claude Code.

## Start The App

### Docker Compose

```bash
docker compose up --build
```

Then open the tracker in your browser using the host and port configured for your environment.

The public repo does not include a built-in LLM endpoint. If you want LLM analysis, set your own values for:

- `LLM_API_BASE_URL`
- `LLM_MODEL`
- `LLM_API_KEY` if your provider requires one

### Plain Docker

```bash
docker build -t prompt-grammar-tracker .
docker run --rm -p 8080:8080 -v ${PWD}/data:/data -v ${PWD}/imports:/imports prompt-grammar-tracker
```

## First Verification

Before installing hooks, verify the app itself:

1. Open the tracker dashboard in your browser.
2. Navigate to `/capture`.
3. Paste a test prompt such as `i want make a tool for codex`.
4. Submit it.
5. Open the prompt detail page and confirm analysis completes.
6. Check `/health` if anything looks stalled.

Expected result:

- the prompt appears in Prompt History
- grammar and clarity scores are populated
- a suggested rewrite is shown on the prompt detail page

If you are using `ANALYZER_MODE=llm`, confirm on the Settings page that:

- Configured Analyzer shows `llm`
- Active Engine shows `llm`
- the LLM model field shows the model you configured

## Authentication

If `APP_API_TOKEN` is set:

- JSON API routes require either `X-API-Token` or `Authorization: Bearer <token>`
- the browser dashboard redirects to `/login`
- you must log in with the same token once per browser session

## Capture Methods

### Claude Code

Install the bundled project-local hook:

```bash
python scripts/install_hooks.py --client claude
```

Install the same hook globally for all Claude Code projects on your machine:

```bash
python scripts/install_hooks.py --client claude --scope global
```

What this does:

- project-local mode creates or updates `.claude/settings.local.json`
- global mode creates or updates `~/.claude/settings.json`
- wires Claude Code's `UserPromptSubmit` event to `POST /hooks/claude/user-prompt-submit`

If you want the installer to write a real tracker endpoint into the generated Claude settings, set `PROMPT_TRACKER_URL` before running it. Otherwise the placeholder URL remains and you must edit the installed file manually.

What to verify:

1. `.claude/settings.local.json` exists for project-local installs, or `~/.claude/settings.json` exists for global installs.
2. `PROMPT_TRACKER_TOKEN` is set if the app requires auth.
3. Run `/hooks` in Claude Code and confirm the hook source matches the scope you installed:
   `Local` for `.claude/settings.local.json`
   `User` for `~/.claude/settings.json`
4. A prompt submitted in Claude Code appears in the dashboard with source `claude-code`.

### Codex

Install the bundled Codex hook config:

```bash
python scripts/install_hooks.py --client codex
```

Additional requirement:

- enable `codex_hooks = true` under `[features]` in `~/.codex/config.toml`

What this does:

- creates or updates `.codex/hooks.json`
- runs `scripts/hook_capture_prompt.py` as a command hook
- forwards `UserPromptSubmit` prompt text to `POST /api/prompts`

Recommended verification flow:

1. Install the hook.
2. Confirm `.codex/hooks.json` exists in this repo.
3. Confirm `[features] codex_hooks = true` is present in `~/.codex/config.toml`.
4. Set `PROMPT_TRACKER_TOKEN` if the tracker uses `APP_API_TOKEN`.
5. Set `PROMPT_TRACKER_URL` if the tracker is not running at the default address.
6. Restart Codex or open a fresh Codex session in this repo.
7. Send a test prompt.
8. Open the prompt history page and confirm the newest prompt appears in the live review section.

What success looks like:

- the newest prompt is pinned at the top of `/prompts`
- the original prompt is on the left
- the suggested rewrite is on the right
- changed wording is highlighted with diff styling
- older prompts move into the history list underneath

Current note:

- this repo's Codex hook path has been verified on the current Windows setup
- upstream Codex hook support may still vary across Windows installations

### Custom Scripts Or Wrappers

Send a copy of the prompt to:

```text
POST /api/prompts
```

Example request body:

```json
{
  "text": "i want make a tool for codex",
  "source": "codex",
  "session_id": "session-123",
  "external_id": "event-456",
  "metadata": {
    "workspace": "language-assistance"
  }
}
```

### Import Folder

Drop files into the mounted import directory, then scan from the Settings page.

Supported files:

- `.jsonl`
- `.txt`

Import behavior:

- unchanged files are not re-imported
- appended JSONL files add only new lines
- changed TXT files create a new prompt entry
- malformed JSONL stops the import at the broken line and records a failed import event

## Navigation

### Overview

Use this page to monitor:

- total prompts captured
- average grammar score
- average clarity score
- recent trends
- recurring issue categories

### Prompt History

Use filters for:

- source
- latest analysis status

The page now has a live review layout:

- the newest matching prompt is pinned at the top
- the original prompt is shown on the left
- the suggested rewrite is shown on the right
- changed wording is highlighted
- earlier prompts are listed underneath and refresh automatically while the page is open

### Prompt Detail

Use this page when you want:

- the original prompt
- the suggested rewrite
- issue-by-issue explanations
- a rerun of analysis

### Settings

Use this page to:

- confirm analyzer mode
- confirm import directory
- scan imports manually
- review source counts

## Troubleshooting

### No prompts appear

Check these in order:

1. `GET /health` returns `status: ok`
2. the client hook file was installed where the client expects it
3. the token matches the app configuration
4. the tracker host value points to the running app

### LLM analyses fail or stay failed

Check:

- `ANALYZER_MODE=llm`
- `LLM_MODEL` is set in the container
- `LLM_API_BASE_URL` points to a compatible `/v1` API
- `LLM_MAX_TOKENS` and `LLM_REASONING_EFFORT` match the model profile you intend to run
- the Settings page does not show an LLM fallback error

### Claude prompts do not arrive

Check:

- `.claude/settings.local.json`
- the hook host value
- `PROMPT_TRACKER_TOKEN`

### Codex prompts do not arrive

Check:

- `.codex/hooks.json`
- `~/.codex/config.toml`
- whether Codex was restarted after the hook was installed
- whether `PROMPT_TRACKER_TOKEN` or `PROMPT_TRACKER_URL` need to be set in the shell that launches Codex
- whether your Windows setup differs from the current verified setup for this repo

### Import scan says failed

Check:

- malformed JSON in `.jsonl`
- whether the imported file was updated mid-scan
- file encoding is UTF-8

### Dashboard keeps redirecting to login

Check:

- the token you entered matches `APP_API_TOKEN`
- the browser accepted cookies
- you are not mixing multiple tracker instances with different tokens

## Useful Paths

- app database: `/data/app.db`
- import directory: `/imports`
- Claude hook template: `.claude/settings.example.json`
- Codex hook template: `.codex/hooks.example.json`
- forwarder script: `scripts/hook_capture_prompt.py`
- installer: `scripts/install_hooks.py`
