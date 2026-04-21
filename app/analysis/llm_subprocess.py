from __future__ import annotations

import json
import sys

from app.analysis.base import AnalysisResult, Issue
from app.analysis.llm import LLMAnalyzer, LLMConfig


def _result_to_dict(result: AnalysisResult) -> dict:
    return {
        "engine": result.engine,
        "grammar_score": result.grammar_score,
        "clarity_score": result.clarity_score,
        "corrected_text": result.corrected_text,
        "summary": result.summary,
        "issues": [
            {
                "category": issue.category,
                "severity": issue.severity,
                "message": issue.message,
                "suggestion": issue.suggestion,
                "replacement": issue.replacement,
                "start_offset": issue.start_offset,
                "end_offset": issue.end_offset,
            }
            for issue in result.issues
        ],
        "raw": result.raw,
    }


def _result_from_dict(payload: dict) -> AnalysisResult:
    issues = [
        Issue(
            category=str(item.get("category") or "grammar"),
            severity=str(item.get("severity") or "medium"),
            message=str(item.get("message") or "Grammar issue detected."),
            suggestion=item.get("suggestion"),
            replacement=item.get("replacement"),
            start_offset=item.get("start_offset"),
            end_offset=item.get("end_offset"),
        )
        for item in payload.get("issues", [])
        if isinstance(item, dict)
    ]
    return AnalysisResult(
        engine=str(payload.get("engine") or "llm"),
        grammar_score=int(payload.get("grammar_score") or 0),
        clarity_score=int(payload.get("clarity_score") or 0),
        corrected_text=str(payload.get("corrected_text") or ""),
        summary=str(payload.get("summary") or ""),
        issues=issues,
        raw=payload.get("raw", {}) if isinstance(payload.get("raw"), dict) else {},
    )


def main() -> int:
    payload = json.loads(sys.stdin.read())
    config = LLMConfig(
        base_url=payload["base_url"],
        model=payload["model"],
        api_key=payload.get("api_key", ""),
        timeout_seconds=float(payload.get("timeout_seconds", 30)),
        max_tokens=int(payload.get("max_tokens", 384)),
        reasoning_effort=str(payload.get("reasoning_effort") or ""),
        seed=payload.get("seed"),
    )
    analyzer = LLMAnalyzer(config=config, language=str(payload.get("language") or "en-US"))
    try:
        result = analyzer.analyze(str(payload["text"]))
    finally:
        analyzer.close()
    sys.stdout.write(json.dumps(_result_to_dict(result), sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
