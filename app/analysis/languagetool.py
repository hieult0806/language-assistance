from __future__ import annotations

from collections.abc import Iterable

from app.analysis.base import AnalysisResult, Analyzer, Issue
from app.analysis.heuristic import HeuristicAnalyzer, estimate_clarity

try:
    import language_tool_python  # type: ignore
except Exception:  # pragma: no cover - optional dependency may fail to import
    language_tool_python = None


def _rule_issue_type(match: object) -> str:
    value = getattr(match, "ruleIssueType", None) or getattr(match, "rule_issue_type", None)
    return str(value or "grammar")


def _category(match: object) -> str:
    category = getattr(match, "category", None)
    if isinstance(category, dict):
        return str(category.get("id") or category.get("name") or "grammar").lower()
    name = getattr(category, "id", None) or getattr(category, "name", None)
    if name:
        return str(name).lower()
    return _rule_issue_type(match).lower()


def _severity_for_type(issue_type: str) -> str:
    mapping = {
        "misspelling": "medium",
        "typographical": "low",
        "whitespace": "low",
        "style": "low",
        "grammar": "medium",
    }
    return mapping.get(issue_type.lower(), "medium")


class LanguageToolAnalyzer(Analyzer):
    engine_name = "languagetool"

    def __init__(self, language: str = "en-US") -> None:
        if language_tool_python is None:
            raise RuntimeError("language_tool_python is not available.")
        self.language = language
        self.tool = language_tool_python.LanguageTool(language)
        self.heuristic = HeuristicAnalyzer()

    def _matches_to_issues(self, matches: Iterable[object]) -> list[Issue]:
        issues: list[Issue] = []
        for match in matches:
            replacements = getattr(match, "replacements", None) or []
            issue_type = _rule_issue_type(match)
            offset = getattr(match, "offset", None)
            length = getattr(match, "errorLength", None) or getattr(match, "error_length", None)
            issues.append(
                Issue(
                    category=_category(match),
                    severity=_severity_for_type(issue_type),
                    message=str(getattr(match, "message", "Grammar issue detected.")),
                    suggestion=str(getattr(match, "shortMessage", "")) or None,
                    replacement=str(replacements[0]) if replacements else None,
                    start_offset=int(offset) if offset is not None else None,
                    end_offset=int(offset + length) if offset is not None and length else None,
                )
            )
        return issues

    def analyze(self, text: str) -> AnalysisResult:
        matches = self.tool.check(text)
        lt_issues = self._matches_to_issues(matches)
        corrected = self.tool.correct(text)
        heuristic_result = self.heuristic.analyze(text)

        merged = lt_issues[:]
        seen = {(issue.category, issue.message, issue.start_offset, issue.end_offset) for issue in merged}
        for issue in heuristic_result.issues:
            key = (issue.category, issue.message, issue.start_offset, issue.end_offset)
            if key not in seen:
                merged.append(issue)
                seen.add(key)

        grammar_penalty = sum(
            12 if issue.severity == "high" else 7 if issue.severity == "medium" else 4
            for issue in merged
            if issue.category != "clarity"
        )
        grammar_score = max(0, min(100, 100 - grammar_penalty))
        clarity_score = estimate_clarity(corrected, merged)
        summary = (
            "No obvious issues found."
            if not merged
            else f"Found {len(merged)} issue(s) with LanguageTool."
        )
        return AnalysisResult(
            engine=self.engine_name,
            grammar_score=grammar_score,
            clarity_score=clarity_score,
            corrected_text=corrected,
            summary=summary,
            issues=merged,
            raw={"mode": self.engine_name, "match_count": len(lt_issues)},
        )
