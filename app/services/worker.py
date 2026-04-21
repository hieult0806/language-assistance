from __future__ import annotations

import asyncio
import contextlib
import logging

from app.analysis.service import AnalyzerService
from app.services.repository import Repository


logger = logging.getLogger(__name__)


class AnalysisWorker:
    def __init__(self, repository: Repository, analyzer: AnalyzerService) -> None:
        self.repository = repository
        self.analyzer = analyzer
        self.queue: asyncio.Queue[int] = asyncio.Queue()
        self._task: asyncio.Task | None = None
        self._running = False

    async def start(self) -> None:
        if self._running:
            return
        self._running = True
        self._task = asyncio.create_task(self._run(), name="analysis-worker")
        for analysis_id in self.repository.get_pending_analysis_ids():
            await self.queue.put(analysis_id)

    async def stop(self) -> None:
        self._running = False
        if self._task is not None:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task

    async def submit(self, analysis_id: int) -> None:
        await self.queue.put(analysis_id)

    async def _run(self) -> None:
        while self._running:
            analysis_id = await self.queue.get()
            try:
                claimed = self.repository.claim_analysis(analysis_id, engine=self.analyzer.active_engine)
                if not claimed or claimed["status"] not in {"processing", "queued", "failed"}:
                    continue
                result = await self.analyzer.analyze_async(claimed["text"])
                self.repository.complete_analysis(analysis_id, result)
            except Exception as exc:  # pragma: no cover - defensive path
                logger.exception("Analysis failed for %s", analysis_id)
                self.repository.fail_analysis(analysis_id, str(exc))
            finally:
                self.queue.task_done()
