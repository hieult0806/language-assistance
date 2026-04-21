from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any


@dataclass(slots=True)
class Issue:
    category: str
    severity: str
    message: str
    suggestion: str | None = None
    replacement: str | None = None
    start_offset: int | None = None
    end_offset: int | None = None


@dataclass(slots=True)
class AnalysisResult:
    engine: str
    grammar_score: int
    clarity_score: int
    corrected_text: str
    summary: str
    issues: list[Issue] = field(default_factory=list)
    raw: dict[str, Any] = field(default_factory=dict)


class Analyzer:
    engine_name = "unknown"

    def analyze(self, text: str) -> AnalysisResult:
        raise NotImplementedError

    async def analyze_async(self, text: str) -> AnalysisResult:
        return await asyncio.to_thread(self.analyze, text)
