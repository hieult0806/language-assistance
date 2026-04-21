from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
from typing import Any

from app.analysis.base import AnalysisResult
from app.analysis.explanations import explain_issue
from app.database import get_connection


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


class Repository:
    def __init__(self, database_path: Path) -> None:
        self.database_path = database_path

    def create_prompt(
        self,
        text: str,
        source: str = "manual",
        session_id: str | None = None,
        external_id: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        payload = (text or "").strip()
        if not payload:
            raise ValueError("Prompt text cannot be empty.")

        created_at = utc_now()
        text_hash = hashlib.sha256(payload.encode("utf-8")).hexdigest()
        metadata_json = json.dumps(metadata or {}, sort_keys=True)

        with get_connection(self.database_path) as conn:
            if external_id:
                existing = conn.execute(
                    """
                    SELECT * FROM prompts
                    WHERE source = ? AND external_id = ?
                    ORDER BY id DESC
                    LIMIT 1
                    """,
                    (source, external_id),
                ).fetchone()
                if existing:
                    return self._serialize_prompt_row(conn, existing, deduplicated=True)

            cursor = conn.execute(
                """
                INSERT INTO prompts (source, session_id, external_id, text, text_hash, metadata_json, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (source, session_id, external_id, payload, text_hash, metadata_json, created_at),
            )
            prompt_id = int(cursor.lastrowid)
            conn.execute(
                """
                INSERT INTO events (prompt_id, event_type, payload_json, created_at)
                VALUES (?, 'prompt_ingested', ?, ?)
                """,
                (prompt_id, metadata_json, created_at),
            )
            analysis_id = self.create_analysis(conn, prompt_id=prompt_id, engine="queued", status="queued")
            prompt = conn.execute("SELECT * FROM prompts WHERE id = ?", (prompt_id,)).fetchone()
            result = self._serialize_prompt_row(conn, prompt)
            result["analysis_id"] = analysis_id
            result["analysis_status"] = "queued"
            result["deduplicated"] = False
            return result

    def _serialize_prompt_row(self, conn, row, deduplicated: bool = False) -> dict[str, Any]:
        prompt = dict(row)
        latest_analysis = conn.execute(
            """
            SELECT id, status
            FROM analyses
            WHERE prompt_id = ?
            ORDER BY id DESC
            LIMIT 1
            """,
            (prompt["id"],),
        ).fetchone()
        prompt["analysis_id"] = int(latest_analysis["id"]) if latest_analysis else None
        prompt["analysis_status"] = latest_analysis["status"] if latest_analysis else None
        prompt["deduplicated"] = deduplicated
        return prompt

    def create_analysis(
        self,
        conn,
        prompt_id: int,
        engine: str,
        status: str = "queued",
    ) -> int:
        cursor = conn.execute(
            """
            INSERT INTO analyses (prompt_id, engine, status, raw_json, created_at)
            VALUES (?, ?, ?, '{}', ?)
            """,
            (prompt_id, engine, status, utc_now()),
        )
        return int(cursor.lastrowid)

    def queue_reanalysis(self, prompt_id: int) -> int:
        with get_connection(self.database_path) as conn:
            analysis_id = self.create_analysis(conn, prompt_id=prompt_id, engine="queued", status="queued")
            conn.execute(
                """
                INSERT INTO events (prompt_id, event_type, payload_json, created_at)
                VALUES (?, 'analysis_queued', '{}', ?)
                """,
                (prompt_id, utc_now()),
            )
            return analysis_id

    def claim_analysis(self, analysis_id: int, engine: str) -> dict[str, Any] | None:
        with get_connection(self.database_path) as conn:
            row = conn.execute(
                """
                SELECT analyses.*, prompts.text, prompts.source, prompts.session_id
                FROM analyses
                JOIN prompts ON prompts.id = analyses.prompt_id
                WHERE analyses.id = ?
                """,
                (analysis_id,),
            ).fetchone()
            if not row or row["status"] not in {"queued", "failed"}:
                return dict(row) if row else None

            conn.execute(
                """
                UPDATE analyses
                SET status = 'processing', engine = ?, error_message = NULL
                WHERE id = ?
                """,
                (engine, analysis_id),
            )
            claimed = conn.execute(
                """
                SELECT analyses.*, prompts.text, prompts.source, prompts.session_id
                FROM analyses
                JOIN prompts ON prompts.id = analyses.prompt_id
                WHERE analyses.id = ?
                """,
                (analysis_id,),
            ).fetchone()
            return dict(claimed) if claimed else None

    def complete_analysis(self, analysis_id: int, result: AnalysisResult) -> None:
        completed_at = utc_now()
        with get_connection(self.database_path) as conn:
            row = conn.execute("SELECT prompt_id FROM analyses WHERE id = ?", (analysis_id,)).fetchone()
            if not row:
                return
            conn.execute(
                """
                UPDATE analyses
                SET engine = ?, status = 'completed', grammar_score = ?, clarity_score = ?,
                    corrected_text = ?, summary = ?, raw_json = ?, completed_at = ?
                WHERE id = ?
                """,
                (
                    result.engine,
                    result.grammar_score,
                    result.clarity_score,
                    result.corrected_text,
                    result.summary,
                    json.dumps(result.raw, sort_keys=True),
                    completed_at,
                    analysis_id,
                ),
            )
            conn.execute("DELETE FROM issues WHERE analysis_id = ?", (analysis_id,))
            for issue in result.issues:
                conn.execute(
                    """
                    INSERT INTO issues (
                        analysis_id, category, severity, message, suggestion,
                        replacement, start_offset, end_offset
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        analysis_id,
                        issue.category,
                        issue.severity,
                        issue.message,
                        issue.suggestion,
                        issue.replacement,
                        issue.start_offset,
                        issue.end_offset,
                    ),
                )
            conn.execute(
                """
                INSERT INTO events (prompt_id, event_type, payload_json, created_at)
                VALUES (?, 'analysis_completed', ?, ?)
                """,
                (
                    int(row["prompt_id"]),
                    json.dumps(
                        {
                            "analysis_id": analysis_id,
                            "engine": result.engine,
                            "issue_count": len(result.issues),
                        },
                        sort_keys=True,
                    ),
                    completed_at,
                ),
            )

    def fail_analysis(self, analysis_id: int, error_message: str) -> None:
        failed_at = utc_now()
        with get_connection(self.database_path) as conn:
            row = conn.execute("SELECT prompt_id FROM analyses WHERE id = ?", (analysis_id,)).fetchone()
            conn.execute(
                """
                UPDATE analyses
                SET status = 'failed', error_message = ?, completed_at = ?
                WHERE id = ?
                """,
                (error_message, failed_at, analysis_id),
            )
            if row:
                conn.execute(
                    """
                    INSERT INTO events (prompt_id, event_type, payload_json, created_at)
                    VALUES (?, 'analysis_failed', ?, ?)
                    """,
                    (
                        int(row["prompt_id"]),
                        json.dumps({"analysis_id": analysis_id, "error": error_message}, sort_keys=True),
                        failed_at,
                    ),
                )

    def get_pending_analysis_ids(self) -> list[int]:
        with get_connection(self.database_path) as conn:
            rows = conn.execute(
                """
                SELECT id
                FROM analyses
                WHERE status IN ('queued', 'processing')
                ORDER BY created_at ASC
                """
            ).fetchall()
            return [int(row["id"]) for row in rows]

    def fetch_prompt(self, prompt_id: int) -> dict[str, Any] | None:
        with get_connection(self.database_path) as conn:
            row = conn.execute(
                """
                SELECT * FROM prompts WHERE id = ?
                """,
                (prompt_id,),
            ).fetchone()
            if not row:
                return None
            prompt = dict(row)
            prompt["metadata"] = json.loads(prompt.pop("metadata_json", "{}") or "{}")
            return prompt

    def fetch_prompt_detail(self, prompt_id: int) -> dict[str, Any] | None:
        prompt = self.fetch_prompt(prompt_id)
        if not prompt:
            return None

        with get_connection(self.database_path) as conn:
            analyses = [dict(row) for row in conn.execute(
                """
                SELECT *
                FROM analyses
                WHERE prompt_id = ?
                ORDER BY created_at DESC, id DESC
                """,
                (prompt_id,),
            ).fetchall()]

            for analysis in analyses:
                analysis["raw"] = json.loads(analysis.get("raw_json") or "{}")
                analysis["issues"] = []
                for issue_row in conn.execute(
                        """
                        SELECT *
                        FROM issues
                        WHERE analysis_id = ?
                        ORDER BY id ASC
                        """,
                        (analysis["id"],),
                    ).fetchall():
                    issue = dict(issue_row)
                    issue["explanation"] = explain_issue(
                        issue=issue,
                        original_text=prompt["text"],
                        corrected_text=analysis.get("corrected_text") or prompt["text"],
                    )
                    analysis["issues"].append(issue)

            prompt["analyses"] = analyses
            prompt["latest_analysis"] = analyses[0] if analyses else None
            return prompt

    def list_prompts(
        self,
        source: str | None = None,
        status: str | None = None,
        page: int = 1,
        page_size: int = 20,
    ) -> dict[str, Any]:
        clauses: list[str] = []
        values: list[Any] = []
        if source:
            clauses.append("prompts.source = ?")
            values.append(source)
        if status:
            clauses.append("latest.status = ?")
            values.append(status)

        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        offset = (max(page, 1) - 1) * page_size

        with get_connection(self.database_path) as conn:
            total_row = conn.execute(
                f"""
                SELECT COUNT(*)
                FROM prompts
                LEFT JOIN (
                    SELECT a1.*
                    FROM analyses a1
                    INNER JOIN (
                        SELECT prompt_id, MAX(id) AS max_id
                        FROM analyses
                        GROUP BY prompt_id
                    ) latest ON latest.max_id = a1.id
                ) latest ON latest.prompt_id = prompts.id
                {where}
                """,
                values,
            ).fetchone()
            rows = conn.execute(
                f"""
                SELECT prompts.*, latest.id AS latest_analysis_id, latest.status AS latest_status,
                       latest.engine AS latest_engine, latest.grammar_score, latest.clarity_score,
                       latest.summary, latest.corrected_text, latest.completed_at
                FROM prompts
                LEFT JOIN (
                    SELECT a1.*
                    FROM analyses a1
                    INNER JOIN (
                        SELECT prompt_id, MAX(id) AS max_id
                        FROM analyses
                        GROUP BY prompt_id
                    ) latest ON latest.max_id = a1.id
                ) latest ON latest.prompt_id = prompts.id
                {where}
                ORDER BY prompts.created_at DESC, prompts.id DESC
                LIMIT ? OFFSET ?
                """,
                [*values, page_size, offset],
            ).fetchall()

        items = []
        for row in rows:
            record = dict(row)
            record["metadata"] = json.loads(record.pop("metadata_json", "{}") or "{}")
            items.append(record)

        total = int(total_row[0]) if total_row else 0
        return {
            "items": items,
            "page": page,
            "page_size": page_size,
            "total": total,
            "pages": max(1, (total + page_size - 1) // page_size),
        }

    def get_dashboard_stats(self) -> dict[str, Any]:
        with get_connection(self.database_path) as conn:
            counts = conn.execute(
                """
                SELECT
                    COUNT(*) AS total_prompts,
                    SUM(CASE WHEN latest.status = 'queued' THEN 1 ELSE 0 END) AS queued_prompts,
                    SUM(CASE WHEN latest.status = 'processing' THEN 1 ELSE 0 END) AS processing_prompts,
                    SUM(CASE WHEN latest.status = 'failed' THEN 1 ELSE 0 END) AS failed_prompts,
                    AVG(CASE WHEN latest.status = 'completed' THEN latest.grammar_score END) AS avg_grammar_score,
                    AVG(CASE WHEN latest.status = 'completed' THEN latest.clarity_score END) AS avg_clarity_score
                FROM prompts
                LEFT JOIN (
                    SELECT a1.*
                    FROM analyses a1
                    INNER JOIN (
                        SELECT prompt_id, MAX(id) AS max_id
                        FROM analyses
                        GROUP BY prompt_id
                    ) latest ON latest.max_id = a1.id
                ) latest ON latest.prompt_id = prompts.id
                """
            ).fetchone()

            recent_issues = conn.execute(
                """
                SELECT issues.category, COUNT(*) AS count
                FROM issues
                JOIN (
                    SELECT a1.*
                    FROM analyses a1
                    INNER JOIN (
                        SELECT prompt_id, MAX(id) AS max_id
                        FROM analyses
                        WHERE status = 'completed'
                        GROUP BY prompt_id
                    ) latest ON latest.max_id = a1.id
                ) latest_completed ON latest_completed.id = issues.analysis_id
                GROUP BY issues.category
                ORDER BY count DESC, issues.category ASC
                LIMIT 5
                """
            ).fetchall()

        return {
            "total_prompts": int(counts["total_prompts"] or 0),
            "queued_prompts": int(counts["queued_prompts"] or 0),
            "processing_prompts": int(counts["processing_prompts"] or 0),
            "failed_prompts": int(counts["failed_prompts"] or 0),
            "avg_grammar_score": round(float(counts["avg_grammar_score"] or 0), 1),
            "avg_clarity_score": round(float(counts["avg_clarity_score"] or 0), 1),
            "top_issue_categories": [dict(row) for row in recent_issues],
        }

    def get_trend_series(self, days: int = 14) -> list[dict[str, Any]]:
        with get_connection(self.database_path) as conn:
            rows = conn.execute(
                """
                SELECT
                    substr(prompts.created_at, 1, 10) AS day,
                    AVG(latest_completed.grammar_score) AS avg_grammar_score,
                    AVG(latest_completed.clarity_score) AS avg_clarity_score,
                    COUNT(*) AS prompt_count
                FROM prompts
                JOIN (
                    SELECT a1.*
                    FROM analyses a1
                    INNER JOIN (
                        SELECT prompt_id, MAX(id) AS max_id
                        FROM analyses
                        WHERE status = 'completed'
                        GROUP BY prompt_id
                    ) latest ON latest.max_id = a1.id
                ) latest_completed ON latest_completed.prompt_id = prompts.id
                GROUP BY day
                ORDER BY day DESC
                LIMIT ?
                """,
                (days,),
            ).fetchall()
        series = [
            {
                "day": row["day"],
                "avg_grammar_score": round(float(row["avg_grammar_score"] or 0), 1),
                "avg_clarity_score": round(float(row["avg_clarity_score"] or 0), 1),
                "prompt_count": int(row["prompt_count"] or 0),
            }
            for row in reversed(rows)
        ]
        return series

    def get_source_breakdown(self) -> list[dict[str, Any]]:
        with get_connection(self.database_path) as conn:
            rows = conn.execute(
                """
                SELECT source, COUNT(*) AS count
                FROM prompts
                GROUP BY source
                ORDER BY count DESC, source ASC
                """
            ).fetchall()
        return [dict(row) for row in rows]

    def get_recent_prompts(self, limit: int = 8) -> list[dict[str, Any]]:
        return self.list_prompts(page=1, page_size=limit)["items"]

    def get_recurring_patterns(self, limit: int = 8) -> list[dict[str, Any]]:
        with get_connection(self.database_path) as conn:
            rows = conn.execute(
                """
                SELECT message, category, COUNT(*) AS count
                FROM issues
                JOIN (
                    SELECT a1.*
                    FROM analyses a1
                    INNER JOIN (
                        SELECT prompt_id, MAX(id) AS max_id
                        FROM analyses
                        WHERE status = 'completed'
                        GROUP BY prompt_id
                    ) latest ON latest.max_id = a1.id
                ) latest_completed ON latest_completed.id = issues.analysis_id
                GROUP BY message, category
                ORDER BY count DESC, category ASC, message ASC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
        return [dict(row) for row in rows]

    def record_import(self, path: str, fingerprint: str, status: str, prompt_count: int, error_message: str | None = None) -> None:
        with get_connection(self.database_path) as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO imports (path, fingerprint, status, prompt_count, error_message, processed_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (path, fingerprint, status, prompt_count, error_message, utc_now()),
            )

    def is_import_processed(self, fingerprint: str) -> bool:
        with get_connection(self.database_path) as conn:
            row = conn.execute(
                """
                SELECT 1 FROM imports WHERE fingerprint = ?
                """,
                (fingerprint,),
            ).fetchone()
            return row is not None

    def get_settings_snapshot(self) -> dict[str, Any]:
        stats = self.get_dashboard_stats()
        return {
            "sources": self.get_source_breakdown(),
            "pending_analyses": stats["queued_prompts"] + stats["processing_prompts"],
            "top_issue_categories": stats["top_issue_categories"],
        }
