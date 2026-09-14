from __future__ import annotations

import re
from collections import Counter

from app.inference.client import LlamaClient, LlamaGeneration
from app.schemas.chat import ChatMessage
from app.services.freshness import classify_freshness

_INTERNAL_PREFIXES = (
    "VERIFIED FRESH WEB SNAPSHOTS",
    "WEB SEARCH DISCOVERY",
    "UNTRUSTED CLIENT-SUPPLIED",
    "OLYA trusted project context",
)
_SEARCH_BLOCK = re.compile(r"\[SEARCH\s+\d+\](.*?)(?=\n\n\[SEARCH\s+\d+\]|\Z)", re.I | re.S)
_VERIFIED_BLOCK = re.compile(r"\[VERIFIED SOURCE\s+\d+\](.*?)(?=\n\n\[VERIFIED SOURCE\s+\d+\]|\Z)", re.I | re.S)
_CYR = re.compile(r"[А-Яа-яЁё]")

_RELATIONS = {
    "author": re.compile(r"\b(?:кто\s+написал|кто\s+автор|автор\s+чего|who\s+wrote|author\s+of)\b", re.I),
    "founder": re.compile(r"\b(?:кто\s+основал|кто\s+основатель|who\s+founded|founder\s+of)\b", re.I),
    "inventor": re.compile(r"\b(?:кто\s+изобр[её]л|кто\s+изобретатель|who\s+invented|inventor\s+of)\b", re.I),
    "director": re.compile(r"\b(?:кто\s+режисс[её]р|кто\s+снял\s+фильм|who\s+directed|director\s+of)\b", re.I),
    "capital": re.compile(r"\b(?:какая\s+столица|столица\s+какой|what\s+is\s+the\s+capital|capital\s+of)\b", re.I),
}


def _latest_user(messages: list[ChatMessage]) -> str:
    for msg in reversed(messages):
        if msg.role != "user":
            continue
        text = str(msg.content or "").strip()
        if not text or any(text.startswith(prefix) for prefix in _INTERNAL_PREFIXES):
            continue
        return text
    return ""


def _evidence_blocks(messages: list[ChatMessage]) -> list[str]:
    blocks: list[str] = []
    for msg in messages:
        text = str(msg.content or "")
        if "WEB SEARCH DISCOVERY" in text:
            blocks.extend(m.group(1).strip() for m in _SEARCH_BLOCK.finditer(text))
        if "VERIFIED FRESH WEB SNAPSHOTS" in text:
            blocks.extend(m.group(1).strip() for m in _VERIFIED_BLOCK.finditer(text))
    return [b for b in blocks if b]


def _relation(question: str) -> str | None:
    for name, pattern in _RELATIONS.items():
        if pattern.search(question):
            return name
    return None


def _clean_name(value: str) -> str:
    value = re.sub(r"\s+", " ", value).strip(" \t\n\r.,;:—-()[]{}\"'«»")
    value = re.sub(r"^(?:писатель|writer|author|автор)\s+", "", value, flags=re.I)
    return value[:120]


def _candidates(block: str, relation: str) -> list[str]:
    patterns: tuple[re.Pattern[str], ...]
    if relation == "author":
        patterns = (
            re.compile(r"(?:автор(?:ом)?(?:\s+романа|\s+книги|\s+произведения)?\s*(?:является|—|:)?\s*)([А-ЯЁ][А-Яа-яЁё'’.-]+(?:\s+[А-ЯЁ][А-Яа-яЁё'’.-]+){1,3})", re.I),
            re.compile(r"([А-ЯЁ][А-Яа-яЁё'’.-]+(?:\s+[А-ЯЁ][А-Яа-яЁё'’.-]+){1,3})\s+(?:написал|написала)\b", re.I),
            re.compile(r"(?:written\s+by|author(?:ed)?\s+by|author\s*[:—-])\s*([A-Z][A-Za-z'’.-]+(?:\s+[A-Z][A-Za-z'’.-]+){1,3})", re.I),
        )
    elif relation == "founder":
        patterns = (
            re.compile(r"(?:основател(?:ь|ем)|основан[ао]?\s+)(?:является\s+)?([А-ЯЁ][А-Яа-яЁё'’.-]+(?:\s+[А-ЯЁ][А-Яа-яЁё'’.-]+){1,3})", re.I),
            re.compile(r"(?:founded\s+by|founder\s*[:—-])\s*([A-Z][A-Za-z'’.-]+(?:\s+[A-Z][A-Za-z'’.-]+){1,3})", re.I),
        )
    elif relation == "inventor":
        patterns = (
            re.compile(r"(?:изобр[её]л|изобретател(?:ь|ем)\s*(?:является|—|:)?\s*)([А-ЯЁ][А-Яа-яЁё'’.-]+(?:\s+[А-ЯЁ][А-Яа-яЁё'’.-]+){1,3})", re.I),
            re.compile(r"(?:invented\s+by|inventor\s*[:—-])\s*([A-Z][A-Za-z'’.-]+(?:\s+[A-Z][A-Za-z'’.-]+){1,3})", re.I),
        )
    elif relation == "director":
        patterns = (
            re.compile(r"(?:режисс[её]р(?:ом)?\s*(?:является|—|:)?\s*)([А-ЯЁ][А-Яа-яЁё'’.-]+(?:\s+[А-ЯЁ][А-Яа-яЁё'’.-]+){1,3})", re.I),
            re.compile(r"(?:directed\s+by|director\s*[:—-])\s*([A-Z][A-Za-z'’.-]+(?:\s+[A-Z][A-Za-z'’.-]+){1,3})", re.I),
        )
    else:
        patterns = (
            re.compile(r"(?:столица(?:\s+страны)?\s*(?:—|:|является)?\s*)([А-ЯЁ][А-Яа-яЁё'’.-]+(?:\s+[А-ЯЁ][А-Яа-яЁё'’.-]+){0,2})", re.I),
            re.compile(r"(?:capital\s+of\s+[^.]{1,80}?\s+is\s+)([A-Z][A-Za-z'’.-]+(?:\s+[A-Z][A-Za-z'’.-]+){0,2})", re.I),
        )

    out: list[str] = []
    for pattern in patterns:
        for m in pattern.finditer(block):
            candidate = _clean_name(m.group(1))
            if 2 <= len(candidate) <= 120:
                out.append(candidate)
    return out


def _normalize(value: str) -> str:
    return re.sub(r"[^a-zа-яё0-9]+", "", value.casefold())


def resolve_stable_fact(question: str, messages: list[ChatMessage]) -> str | None:
    if classify_freshness(question).required:
        return None
    relation = _relation(question)
    if relation is None:
        return None
    blocks = _evidence_blocks(messages)
    if len(blocks) < 2:
        return None

    per_source: list[str] = []
    for block in blocks:
        candidates = _candidates(block, relation)
        if candidates:
            per_source.append(candidates[0])
    if len(per_source) < 2:
        return None

    keys = [_normalize(v) for v in per_source]
    winner, count = Counter(keys).most_common(1)[0]
    if not winner or count < 2:
        return None
    answer = next(v for v, k in zip(per_source, keys) if k == winner)
    russian = len(_CYR.findall(question)) >= 2

    if russian:
        templates = {
            "author": f"Автор — {answer}.",
            "founder": f"Основатель — {answer}.",
            "inventor": f"Изобретатель — {answer}.",
            "director": f"Режиссёр — {answer}.",
            "capital": f"Столица — {answer}.",
        }
    else:
        templates = {
            "author": f"The author is {answer}.",
            "founder": f"The founder is {answer}.",
            "inventor": f"The inventor is {answer}.",
            "director": f"The director is {answer}.",
            "capital": f"The capital is {answer}.",
        }
    return templates[relation]


def install_stable_fact_evidence_guard() -> None:
    current = LlamaClient.generate
    if getattr(current, "_olya_stable_fact_evidence_guard", False):
        return

    async def guarded(self, messages, *, max_tokens: int, reasoning: bool, on_token=None):
        question = _latest_user(messages)
        resolved = resolve_stable_fact(question, messages) if question else None
        if resolved is None:
            return await current(self, messages, max_tokens=max_tokens, reasoning=reasoning, on_token=on_token)
        if on_token is not None:
            await on_token(resolved)
        return LlamaGeneration(
            text=resolved,
            ttft_ms=0,
            output_tokens=max(1, len(resolved) // 4),
            tokens_per_second=0.0,
            generation_ms=0,
        )

    guarded._olya_stable_fact_evidence_guard = True  # type: ignore[attr-defined]
    LlamaClient.generate = guarded
