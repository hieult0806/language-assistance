from __future__ import annotations

from app.text_diff import build_prompt_diff


def test_build_prompt_diff_marks_replacements() -> None:
    diff = build_prompt_diff(
        original_text="i want make a tool",
        rewritten_text="I want to make a tool.",
    )

    assert diff.has_changes is True
    assert 'class="diff-token-remove"' in str(diff.original_html)
    assert 'class="diff-token-add"' in str(diff.rewritten_html)


def test_build_prompt_diff_leaves_identical_text_unmarked() -> None:
    diff = build_prompt_diff(
        original_text="I just reloaded this Codex session.",
        rewritten_text="I just reloaded this Codex session.",
    )

    assert diff.has_changes is False
    assert "diff-token-remove" not in str(diff.original_html)
    assert "diff-token-add" not in str(diff.rewritten_html)
