from __future__ import annotations

from typing import Any
import re


def _quoted_fragment(message: str) -> str | None:
    match = re.search(r"'([^']+)'", message)
    if match:
        return match.group(1)
    return None


def explain_issue(issue: dict[str, Any], original_text: str, corrected_text: str) -> str:
    category = str(issue.get("category") or "grammar").lower()
    message = str(issue.get("message") or "").strip()
    replacement = str(issue.get("replacement") or "").strip()
    suggestion = str(issue.get("suggestion") or "").strip()
    fragment = _quoted_fragment(message)

    if category == "capitalization":
        targets = replacement or fragment or "the affected words"
        return f"Standard English capitalizes names, product titles, and the pronoun 'I'. Use {targets} in uppercase form."

    if category == "article":
        noun = fragment or replacement or "the noun"
        return f"Singular countable nouns usually need an article such as 'a', 'an', or 'the' before {noun}."

    if category in {"word-form", "word_form"}:
        token = replacement or suggestion or "the corrected form"
        return f"The sentence needs a different word form here. Use {token} so the phrase is grammatically complete."

    if category == "agreement":
        return "The subject and verb need matching singular or plural forms so the sentence reads naturally."

    if category == "tense":
        return "The verb tense here should match the rest of the sentence and the time you are describing."

    if category == "punctuation":
        return "Complete sentences usually need terminal punctuation so the request reads as a finished statement."

    if category == "spelling":
        token = fragment or replacement or "the corrected spelling"
        return f"This word is spelled incorrectly. Use {token} so the prompt matches standard written English."

    if category == "grammar":
        if "infinitive" in message.lower() or replacement == "to" or "before verb" in message.lower():
            return "After verbs like 'want', English normally uses the infinitive form, such as 'to make'."
        if "duplicate" in message.lower():
            return "The repeated word does not add meaning and makes the sentence look unedited."
        if replacement:
            return f"This phrase is grammatically awkward in the original prompt. Replacing it with {replacement} fixes the structure."
        return "This part of the sentence does not follow standard English grammar and needs the suggested correction."

    if category == "clarity":
        return "The prompt is understandable, but this wording makes it harder to read quickly."

    if category == "style":
        return "The wording works, but the suggested change makes the sentence cleaner and easier to scan."

    if corrected_text and corrected_text != original_text:
        return "The suggested rewrite follows standard English more closely than the original wording."

    return "This change improves the sentence so it better matches standard English usage."
