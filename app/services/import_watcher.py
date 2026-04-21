from __future__ import annotations

import asyncio
import contextlib
import hashlib
import json
import logging
from pathlib import Path

from app.services.repository import Repository
from app.services.worker import AnalysisWorker


logger = logging.getLogger(__name__)


class ImportProcessingError(Exception):
    def __init__(self, message: str, prompt_count: int) -> None:
        super().__init__(message)
        self.prompt_count = prompt_count


class ImportWatcher:
    def __init__(
        self,
        import_dir: Path,
        repository: Repository,
        worker: AnalysisWorker,
        poll_interval_seconds: int = 20,
    ) -> None:
        self.import_dir = import_dir
        self.repository = repository
        self.worker = worker
        self.poll_interval_seconds = poll_interval_seconds
        self._task: asyncio.Task | None = None
        self._running = False

    async def start(self) -> None:
        if self._running:
            return
        self._running = True
        self._task = asyncio.create_task(self._run(), name="import-watcher")

    async def stop(self) -> None:
        self._running = False
        if self._task is not None:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task

    async def _run(self) -> None:
        while self._running:
            try:
                await self.scan_once()
            except Exception:  # pragma: no cover - defensive logging
                logger.exception("Import scan failed")
            await asyncio.sleep(self.poll_interval_seconds)

    async def scan_once(self) -> None:
        if not self.import_dir.exists():
            return

        for path in sorted(self.import_dir.iterdir()):
            if not path.is_file() or path.suffix.lower() not in {".jsonl", ".txt"}:
                continue
            fingerprint = self._fingerprint(path)
            if self.repository.is_import_processed(fingerprint):
                continue

            prompt_count = 0
            try:
                if path.suffix.lower() == ".jsonl":
                    prompt_count = await self._process_jsonl(path)
                else:
                    prompt_count = await self._process_text(path)
                self.repository.record_import(str(path), fingerprint, "processed", prompt_count)
            except ImportProcessingError as exc:
                logger.exception("Import failed for %s", path)
                self.repository.record_import(str(path), fingerprint, "failed", exc.prompt_count, str(exc))
            except Exception as exc:
                logger.exception("Import failed for %s", path)
                self.repository.record_import(str(path), fingerprint, "failed", prompt_count, str(exc))

    async def _process_jsonl(self, path: Path) -> int:
        count = 0
        resolved_path = str(path.resolve())
        for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
            payload = line.strip()
            if not payload:
                continue
            try:
                data = json.loads(payload)
            except json.JSONDecodeError as exc:
                raise ImportProcessingError(str(exc), prompt_count=count) from exc
            line_hash = hashlib.sha256(payload.encode("utf-8")).hexdigest()
            prompt = self.repository.create_prompt(
                text=str(data["text"]),
                source=str(data.get("source", "import")),
                session_id=data.get("session_id"),
                external_id=str(
                    data.get("external_id")
                    or f"jsonl:{resolved_path}:{line_number}:{line_hash}"
                ),
                metadata={
                    **data.get("metadata", {}),
                    "import_filename": path.name,
                    "import_line_number": line_number,
                },
            )
            if prompt.get("analysis_id") and prompt.get("analysis_status") == "queued":
                await self.worker.submit(int(prompt["analysis_id"]))
            if not prompt.get("deduplicated"):
                count += 1
        return count

    async def _process_text(self, path: Path) -> int:
        text = path.read_text(encoding="utf-8").strip()
        if not text:
            return 0
        text_hash = hashlib.sha256(text.encode("utf-8")).hexdigest()
        prompt = self.repository.create_prompt(
            text=text,
            source="import",
            external_id=f"text:{path.resolve()}:{text_hash}",
            metadata={"filename": path.name, "import_format": "text"},
        )
        if prompt.get("analysis_id") and prompt.get("analysis_status") == "queued":
            await self.worker.submit(int(prompt["analysis_id"]))
        return 0 if prompt.get("deduplicated") else 1

    def _fingerprint(self, path: Path) -> str:
        stat = path.stat()
        digest = hashlib.sha256()
        digest.update(str(path.resolve()).encode("utf-8"))
        digest.update(str(stat.st_mtime_ns).encode("utf-8"))
        digest.update(str(stat.st_size).encode("utf-8"))
        return digest.hexdigest()
