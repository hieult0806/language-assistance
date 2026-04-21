from __future__ import annotations

import re

from app.analysis.base import AnalysisResult, Analyzer, Issue


COMMON_REPLACEMENTS = {
    "becaus": "because",
    "dont": "don't",
    "doesnt": "doesn't",
    "cant": "can't",
    "wont": "won't",
    "im": "I'm",
    "ive": "I've",
    "i": "I",
    "alot": "a lot",
    "teh": "the",
    "enviroment": "environment",
    "grammer": "grammar",
}


def normalize_whitespace(text: str) -> str:
    return re.sub(r"[ \t]+", " ", text).strip()


def split_sentences(text: str) -> list[str]:
    sentences = [
        sentence.strip()
        for sentence in re.split(r"(?<=[.!?])\s+|\n+", text)
        if sentence.strip()
    ]
    return sentences or ([text.strip()] if text.strip() else [])


def score_from_issues(issues: list[Issue], base: int = 100) -> int:
    penalties = {"high": 15, "medium": 8, "low": 4}
    score = base
    for issue in issues:
        score -= penalties.get(issue.severity, 6)
    return max(0, min(100, score))


def estimate_clarity(text: str, issues: list[Issue]) -> int:
    score = 100
    sentences = split_sentences(text)
    for sentence in sentences:
        word_count = len(sentence.split())
        if word_count > 35:
            score -= 12
        elif word_count > 24:
            score -= 6
    low_clarity_count = sum(1 for issue in issues if issue.category in {"clarity", "style"})
    score -= low_clarity_count * 5
    if "\n\n\n" in text:
        score -= 4
    return max(0, min(100, score))


class HeuristicAnalyzer(Analyzer):
    engine_name = "heuristic"

    def analyze(self, text: str) -> AnalysisResult:
        issues: list[Issue] = []
        corrected = normalize_whitespace(text)

        if not corrected:
            return AnalysisResult(
                engine=self.engine_name,
                grammar_score=100,
                clarity_score=100,
                corrected_text="",
                summary="No text to analyze.",
                issues=[],
                raw={"mode": self.engine_name},
            )

        if re.search(r"\s{2,}", text):
            issues.append(
                Issue(
                    category="style",
                    severity="low",
                    message="Prompt contains repeated spaces.",
                    suggestion="Use single spaces between words.",
                )
            )

        if corrected[0].islower():
            issues.append(
                Issue(
                    category="grammar",
                    severity="medium",
                    message="Prompt starts with a lowercase letter.",
                    suggestion="Capitalize the first word.",
                    start_offset=0,
                    end_offset=1,
                    replacement=corrected[0].upper(),
                )
            )
            corrected = corrected[0].upper() + corrected[1:]

        if corrected[-1] not in ".!?":
            issues.append(
                Issue(
                    category="punctuation",
                    severity="low",
                    message="Prompt does not end with terminal punctuation.",
                    suggestion="Add a period or question mark to finish the sentence.",
                )
            )
            corrected = corrected + "."

        for match in re.finditer(r"\b(\w+)\s+\1\b", corrected, flags=re.IGNORECASE):
            duplicate = match.group(1)
            issues.append(
                Issue(
                    category="grammar",
                    severity="medium",
                    message=f"Repeated word detected: '{duplicate}'.",
                    suggestion=f"Remove the duplicated '{duplicate}'.",
                    start_offset=match.start(),
                    end_offset=match.end(),
                )
            )

        for wrong, right in COMMON_REPLACEMENTS.items():
            pattern = re.compile(rf"\b{re.escape(wrong)}\b", flags=re.IGNORECASE)
            if pattern.search(corrected):
                issues.append(
                    Issue(
                        category="spelling",
                        severity="medium",
                        message=f"Possible misspelling: '{wrong}'.",
                        suggestion=f"Use '{right}'.",
                    )
                )
                corrected = pattern.sub(right, corrected)

        for sentence in split_sentences(corrected):
            if len(sentence.split()) > 24:
                issues.append(
                    Issue(
                        category="clarity",
                        severity="low",
                        message="Long sentence may be harder to parse quickly.",
                        suggestion="Break this thought into shorter statements.",
                    )
                )
                break

        if re.search(r"\b(?:kind of|sort of|maybe|perhaps)\b", corrected, flags=re.IGNORECASE):
            issues.append(
                Issue(
                    category="style",
                    severity="low",
                    message="Prompt uses soft qualifiers that may weaken intent.",
                    suggestion="Use direct wording when the instruction is firm.",
                )
            )

        corrected = re.sub(r"\bi want make\b", "I want to make", corrected, flags=re.IGNORECASE)
        corrected = re.sub(
            r"\bi want create\b", "I want to create", corrected, flags=re.IGNORECASE
        )
        corrected = re.sub(r"\benglish\b", "English", corrected)
        corrected = re.sub(r"\bcodex\b", "Codex", corrected)
        corrected = re.sub(r"\bclaude code\b", "Claude Code", corrected, flags=re.IGNORECASE)

        grammar_score = score_from_issues(
            [issue for issue in issues if issue.category != "clarity"], base=100
        )
        clarity_score = estimate_clarity(corrected, issues)

        summary = (
            "No obvious issues found."
            if not issues
            else f"Found {len(issues)} issue(s) across {len({issue.category for issue in issues})} categories."
        )

        return AnalysisResult(
            engine=self.engine_name,
            grammar_score=grammar_score,
            clarity_score=clarity_score,
            corrected_text=corrected,
            summary=summary,
            issues=issues,
            raw={"mode": self.engine_name},
        )
