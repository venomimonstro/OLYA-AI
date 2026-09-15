#!/usr/bin/env python3
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
import re

_CYR = re.compile(r"[А-Яа-яЁё]")
_LAT = re.compile(r"[A-Za-z]")
_CONTROL = re.compile(
    r"(?:<\|(?:assistant|user|system|im_start|im_end|endoftext)[^>]*\|>|</?think>|"
    r"###\s*(?:system|assistant|user)\s*:|\[/?INST\])",
    re.I,
)
_AI_SELF_REFERENCE = re.compile(
    r"(?:\bкак\s+(?:ии|искусственный\s+интеллект|языковая\s+модель)\b|"
    r"\bя\s+(?:являюсь|—|-)\s+(?:ии|языковой\s+моделью)\b|"
    r"\bas\s+an?\s+(?:ai|language\s+model)\b)",
    re.I,
)
_TRANSLATE_TO_EN = re.compile(r"(?:переведи\s+(?:на\s+)?английск|in\s+english|translate\s+to\s+english)", re.I)
_IDENTITY_ASK = re.compile(r"(?:кто\s+тебя\s+создал|кто\s+ты|what\s+are\s+you|who\s+made\s+you)", re.I)


@dataclass(frozen=True)
class QualityLint:
    score: int
    issues: tuple[str, ...]


def _normalized_words(text: str) -> list[str]:
    return re.findall(r"[a-zа-яё0-9]+", str(text or "").casefold())


def _sentence_repetition(answer: str) -> bool:
    chunks = [" ".join(part.casefold().split()) for part in re.split(r"(?:[.!?]+\s+|\n+)", answer)]
    chunks = [part for part in chunks if len(part) >= 45]
    counts = Counter(chunks)
    return any(count >= 2 for count in counts.values())


def _phrase_loop(answer: str) -> bool:
    words = _normalized_words(answer)
    if len(words) < 48:
        return False
    n = 8
    grams = Counter(tuple(words[i:i + n]) for i in range(0, len(words) - n + 1))
    return any(count >= 3 for count in grams.values())


def _prompt_echo(prompt: str, answer: str) -> bool:
    p = " ".join(str(prompt or "").casefold().split())
    a = " ".join(str(answer or "").casefold().split())
    if len(p) < 45 or len(a) < 45:
        return False
    head = p[: min(120, len(p))]
    return a.startswith(head) and len(a) <= max(len(p) + 80, int(len(p) * 1.35))


def lint_answer(prompt: str, answer: str) -> QualityLint:
    text = str(answer or "").strip()
    issues: list[str] = []
    penalties = 0
    if not text:
        return QualityLint(score=0, issues=("quality_empty",))

    if _CONTROL.search(text):
        issues.append("quality_control_token_leak")
        penalties += 40

    if not _IDENTITY_ASK.search(prompt or "") and _AI_SELF_REFERENCE.search(text):
        issues.append("quality_generic_ai_self_reference")
        penalties += 18

    if len(_CYR.findall(prompt or "")) >= 5 and not _TRANSLATE_TO_EN.search(prompt or "") and len(text) >= 100:
        cyr = len(_CYR.findall(text))
        lat = len(_LAT.findall(text))
        if cyr < 8 and lat >= 40:
            issues.append("quality_language_mismatch")
            penalties += 30

    if text.count("```") % 2:
        issues.append("quality_broken_code_fence")
        penalties += 14

    if _sentence_repetition(text):
        issues.append("quality_repeated_sentence")
        penalties += 16

    if _phrase_loop(text):
        issues.append("quality_repetition_loop")
        penalties += 30

    if _prompt_echo(prompt, text):
        issues.append("quality_prompt_echo")
        penalties += 18

    if len(text) >= 320 and text.rstrip().endswith((",", ":", ";", "—", "-", "(")):
        issues.append("quality_probably_truncated")
        penalties += 12

    return QualityLint(score=max(0, 100 - penalties), issues=tuple(issues))


if __name__ == "__main__":
    samples = (
        ("Объясни HTTP", "HTTP — протокол передачи данных в интернете."),
        ("Объясни HTTP", "<think>hidden</think> HTTP is a protocol."),
    )
    for prompt, answer in samples:
        print(prompt, lint_answer(prompt, answer))
