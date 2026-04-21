from __future__ import annotations

import asyncio

from app.analysis.base import AnalysisResult
from app.analysis.heuristic import HeuristicAnalyzer
from app.analysis.llm import LLMAnalyzer, LLMConfig
from app.analysis.languagetool import LanguageToolAnalyzer


class AnalyzerService:
    def __init__(
        self,
        mode: str = "auto",
        language: str = "en-US",
        llm_api_base_url: str = "",
        llm_api_key: str = "",
        llm_model: str = "",
        llm_timeout_seconds: float = 30.0,
        llm_max_tokens: int = 384,
        llm_reasoning_effort: str = "",
        llm_seed: int | None = None,
        llm_transport = None,
    ) -> None:
        self.mode = mode
        self.language = language
        self.heuristic = HeuristicAnalyzer()
        self.language_tool: LanguageToolAnalyzer | None = None
        self.language_tool_error: str | None = None
        self.llm: LLMAnalyzer | None = None
        self.llm_error: str | None = None

        llm_config = LLMConfig(
            base_url=llm_api_base_url,
            api_key=llm_api_key,
            model=llm_model,
            timeout_seconds=llm_timeout_seconds,
            max_tokens=llm_max_tokens,
            reasoning_effort=llm_reasoning_effort,
            seed=llm_seed,
        )
        llm_requested = mode == "llm"
        llm_available = bool(llm_api_base_url.strip() and llm_model.strip())

        if llm_requested or (mode == "auto" and llm_available):
            try:
                self.llm = LLMAnalyzer(
                    config=llm_config,
                    language=language,
                    transport=llm_transport,
                )
            except Exception as exc:
                self.llm_error = str(exc)
                if llm_requested:
                    raise

        if mode in {"auto", "languagetool", "hybrid"}:
            try:
                self.language_tool = LanguageToolAnalyzer(language=language)
            except Exception as exc:  # pragma: no cover - depends on Java/runtime
                self.language_tool_error = str(exc)
                if mode == "languagetool":
                    raise

    @property
    def active_engine(self) -> str:
        if self.mode == "heuristic":
            return "heuristic"
        if self.mode == "llm" and self.llm is not None:
            return "llm"
        if self.mode == "languagetool" and self.language_tool is not None:
            return "languagetool"
        if self.mode == "hybrid" and self.language_tool is not None:
            return "hybrid"
        if self.llm is not None:
            return "llm"
        if self.language_tool is not None:
            return "languagetool"
        return "heuristic"

    def analyze(self, text: str) -> AnalysisResult:
        if self.mode == "heuristic":
            return self.heuristic.analyze(text)

        if self.mode == "llm":
            return self.llm.analyze(text) if self.llm else self.heuristic.analyze(text)

        if self.mode == "languagetool":
            return self.language_tool.analyze(text) if self.language_tool else self.heuristic.analyze(text)

        if self.mode == "hybrid":
            return self._analyze_hybrid(text)

        if self.llm is not None:
            try:
                return self.llm.analyze(text)
            except Exception:
                if self.language_tool is not None:
                    try:
                        return self.language_tool.analyze(text)
                    except Exception:
                        return self.heuristic.analyze(text)
                return self.heuristic.analyze(text)

        if self.language_tool is not None:
            try:
                return self.language_tool.analyze(text)
            except Exception:
                return self.heuristic.analyze(text)
        return self.heuristic.analyze(text)

    async def analyze_async(self, text: str) -> AnalysisResult:
        if self.mode == "heuristic":
            return await asyncio.to_thread(self.heuristic.analyze, text)

        if self.mode == "llm":
            if self.llm:
                return await self.llm.analyze_async(text)
            return await asyncio.to_thread(self.heuristic.analyze, text)

        if self.mode == "languagetool":
            if self.language_tool:
                return await asyncio.to_thread(self.language_tool.analyze, text)
            return await asyncio.to_thread(self.heuristic.analyze, text)

        if self.mode == "hybrid":
            return await asyncio.to_thread(self._analyze_hybrid, text)

        if self.llm is not None:
            try:
                return await self.llm.analyze_async(text)
            except Exception:
                if self.language_tool is not None:
                    try:
                        return await asyncio.to_thread(self.language_tool.analyze, text)
                    except Exception:
                        return await asyncio.to_thread(self.heuristic.analyze, text)
                return await asyncio.to_thread(self.heuristic.analyze, text)

        if self.language_tool is not None:
            try:
                return await asyncio.to_thread(self.language_tool.analyze, text)
            except Exception:
                return await asyncio.to_thread(self.heuristic.analyze, text)
        return await asyncio.to_thread(self.heuristic.analyze, text)

    def close(self) -> None:
        if self.llm is not None:
            self.llm.close()

    def _analyze_hybrid(self, text: str) -> AnalysisResult:
        baseline = self.language_tool.analyze(text) if self.language_tool else self.heuristic.analyze(text)
        heuristic = self.heuristic.analyze(text)

        merged_issues = baseline.issues[:]
        seen = {
            (issue.category, issue.message, issue.start_offset, issue.end_offset)
            for issue in merged_issues
        }
        for issue in heuristic.issues:
            key = (issue.category, issue.message, issue.start_offset, issue.end_offset)
            if key not in seen:
                merged_issues.append(issue)
                seen.add(key)

        corrected_text = (
            baseline.corrected_text
            if baseline.corrected_text and baseline.corrected_text != text
            else heuristic.corrected_text
        )

        return AnalysisResult(
            engine="hybrid",
            grammar_score=max(0, min(100, round((baseline.grammar_score * 0.7) + (heuristic.grammar_score * 0.3)))),
            clarity_score=max(0, min(100, round((baseline.clarity_score * 0.5) + (heuristic.clarity_score * 0.5)))),
            corrected_text=corrected_text,
            summary=f"{baseline.summary} Hybrid mode added {max(0, len(merged_issues) - len(baseline.issues))} extra heuristic checks.",
            issues=merged_issues,
            raw={
                "mode": "hybrid",
                "baseline_engine": baseline.engine,
                "language_tool_available": self.language_tool is not None,
                "fallback_reason": self.language_tool_error,
            },
        )
