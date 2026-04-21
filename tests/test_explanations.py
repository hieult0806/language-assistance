from __future__ import annotations

from app.analysis.explanations import explain_issue


def test_explain_issue_for_infinitive_problem() -> None:
    issue = {
        "category": "grammar",
        "message": "Missing 'to' before verb.",
        "replacement": "want to make",
        "suggestion": "Use 'want to make'.",
    }
    explanation = explain_issue(
        issue=issue,
        original_text="i want make a tool",
        corrected_text="I want to make a tool.",
    )
    assert "infinitive" in explanation.lower()
    assert "to make" in explanation


def test_explain_issue_for_capitalization_problem() -> None:
    issue = {
        "category": "capitalization",
        "message": "Product names need capitalization.",
        "replacement": "Codex and Claude Code",
        "suggestion": "Capitalize Codex and Claude Code.",
    }
    explanation = explain_issue(
        issue=issue,
        original_text="tool for codex and claude code",
        corrected_text="tool for Codex and Claude Code",
    )
    assert "product" in explanation.lower() or "names" in explanation.lower()
    assert "Codex and Claude Code" in explanation
