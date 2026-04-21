from __future__ import annotations

from contextlib import contextmanager
import sqlite3
from pathlib import Path
from typing import Iterator


def init_db(database_path: Path) -> None:
    database_path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(database_path) as conn:
        conn.executescript(
            """
            PRAGMA journal_mode=WAL;
            PRAGMA foreign_keys=ON;

            CREATE TABLE IF NOT EXISTS prompts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                source TEXT NOT NULL,
                session_id TEXT,
                external_id TEXT,
                text TEXT NOT NULL,
                text_hash TEXT NOT NULL,
                metadata_json TEXT NOT NULL DEFAULT '{}',
                created_at TEXT NOT NULL
            );

            CREATE INDEX IF NOT EXISTS idx_prompts_created_at
                ON prompts (created_at DESC);
            CREATE INDEX IF NOT EXISTS idx_prompts_text_hash
                ON prompts (text_hash);
            CREATE INDEX IF NOT EXISTS idx_prompts_source_external_id
                ON prompts (source, external_id);

            CREATE TABLE IF NOT EXISTS analyses (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                prompt_id INTEGER NOT NULL,
                engine TEXT NOT NULL,
                status TEXT NOT NULL,
                grammar_score INTEGER,
                clarity_score INTEGER,
                corrected_text TEXT,
                summary TEXT,
                raw_json TEXT NOT NULL DEFAULT '{}',
                error_message TEXT,
                created_at TEXT NOT NULL,
                completed_at TEXT,
                FOREIGN KEY(prompt_id) REFERENCES prompts(id) ON DELETE CASCADE
            );

            CREATE INDEX IF NOT EXISTS idx_analyses_prompt_id
                ON analyses (prompt_id, created_at DESC);
            CREATE INDEX IF NOT EXISTS idx_analyses_status
                ON analyses (status, created_at ASC);

            CREATE TABLE IF NOT EXISTS issues (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                analysis_id INTEGER NOT NULL,
                category TEXT NOT NULL,
                severity TEXT NOT NULL,
                message TEXT NOT NULL,
                suggestion TEXT,
                replacement TEXT,
                start_offset INTEGER,
                end_offset INTEGER,
                FOREIGN KEY(analysis_id) REFERENCES analyses(id) ON DELETE CASCADE
            );

            CREATE INDEX IF NOT EXISTS idx_issues_analysis_id
                ON issues (analysis_id);

            CREATE TABLE IF NOT EXISTS events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                prompt_id INTEGER,
                event_type TEXT NOT NULL,
                payload_json TEXT NOT NULL DEFAULT '{}',
                created_at TEXT NOT NULL,
                FOREIGN KEY(prompt_id) REFERENCES prompts(id) ON DELETE CASCADE
            );

            CREATE INDEX IF NOT EXISTS idx_events_type
                ON events (event_type, created_at DESC);

            CREATE TABLE IF NOT EXISTS imports (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                path TEXT NOT NULL,
                fingerprint TEXT NOT NULL UNIQUE,
                status TEXT NOT NULL,
                prompt_count INTEGER NOT NULL DEFAULT 0,
                error_message TEXT,
                processed_at TEXT NOT NULL
            );
            """
        )


@contextmanager
def get_connection(database_path: Path) -> Iterator[sqlite3.Connection]:
    conn = sqlite3.connect(database_path, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON;")
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()
