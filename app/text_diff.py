from __future__ import annotations

from dataclasses import dataclass
from difflib import SequenceMatcher
import re

from markupsafe import Markup, escape


TOKEN_PATTERN = re.compile(r"\s+|[A-Za-z0-9_]+|[^\w\s]", re.UNICODE)


@dataclass(slots=True)
class PromptDiff:
    original_html: Markup
    rewritten_html: Markup
    has_changes: bool


def _tokenize(text: str) -> list[str]:
    return TOKEN_PATTERN.findall(text)


def _append_segment(parts: list[Markup], segment: str, css_class: str | None = None) -> None:
    if not segment:
        return

    escaped = escape(segment)
    if css_class:
        parts.append(Markup(f'<mark class="{css_class}">') + escaped + Markup("</mark>"))
        return
    parts.append(Markup(escaped))


def build_prompt_diff(original_text: str, rewritten_text: str) -> PromptDiff:
    original_tokens = _tokenize(original_text)
    rewritten_tokens = _tokenize(rewritten_text)

    matcher = SequenceMatcher(a=original_tokens, b=rewritten_tokens, autojunk=False)
    original_parts: list[Markup] = []
    rewritten_parts: list[Markup] = []
    has_changes = False

    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        original_segment = "".join(original_tokens[i1:i2])
        rewritten_segment = "".join(rewritten_tokens[j1:j2])

        if tag == "equal":
            _append_segment(original_parts, original_segment)
            _append_segment(rewritten_parts, rewritten_segment)
            continue

        has_changes = True
        if tag in {"replace", "delete"}:
            _append_segment(original_parts, original_segment, "diff-token-remove")
        if tag in {"replace", "insert"}:
            _append_segment(rewritten_parts, rewritten_segment, "diff-token-add")

    return PromptDiff(
        original_html=Markup("").join(original_parts),
        rewritten_html=Markup("").join(rewritten_parts),
        has_changes=has_changes,
    )
