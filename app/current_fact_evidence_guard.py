from __future__ import annotations

import re
from collections import Counter

from app.inference.client import LlamaClient, LlamaGeneration
from app.schemas.chat import ChatMessage
from app.services.freshness import classify_freshness

_US_PRESIDENT = re.compile(
    r"(?:президент\w*\s+(?:сша|соедин[её]нн\w+\s+штат\w*|америк\w*)|"
    r"(?:сша|соедин[её]нн\w+\s+штат\w*|америк\w*)\s+президент\w*|"
    r"(?:current\s+)?president\s+(?:of\s+)?(?:the\s+)?(?:united\s+states|usa|us))",
    re.IGNORECASE,
)
_CYRILLIC = re.compile(r"[А-Яа-яЁё]")
_INTERNAL_USER_PREFIXES = (
    "VERIFIED FRESH WEB SNAPSHOTS",
    "WEB SEARCH DISCOVERY",
    "UNTRUSTED CLIENT-SUPPLIED",
    "X1 trusted project context",
    "OLYA trusted project context",
)
_VERIFIED_BLOCK = re.compile(
    r"\[VERIFIED SOURCE\s+\d+\](.*?)(?=\n\n\[VERIFIED SOURCE\s+\d+\]|\Z)",
    re.IGNORECASE | re.DOTALL,
)

_ROLE_PATTERNS: dict[str, tuple[re.Pattern[str], ...]] = {
    "president": (
        re.compile(r"\bPresident[ \t]+([A-Z][A-Za-z'’.-]+(?:[ \t]+(?:[A-Z][A-Za-z'’.-]+|[A-Z]\.)){1,4})\b"),
        re.compile(r"\bПрезидент(?:ом)?[ \t]+(?:является[ \t]+)?([А-ЯЁ][А-Яа-яЁё'’.-]+(?:[ \t]+[А-ЯЁ][А-Яа-яЁё'’.-]+){1,3})\b"),
    ),
    "ceo": (
        re.compile(r"\bCEO[ \t:,-]+([A-Z][A-Za-z'’.-]+(?:[ \t]+(?:[A-Z][A-Za-z'’.-]+|[A-Z]\.)){1,4})\b"),
        re.compile(r"\bChief Executive Officer[ \t:,-]+([A-Z][A-Za-z'’.-]+(?:[ \t]+(?:[A-Z][A-Za-z'’.-]+|[A-Z]\.)){1,4})\b", re.IGNORECASE),
        re.compile(r"\b(?:генеральный директор|гендиректор)[ \t:,-]+([А-ЯЁ][А-Яа-яЁё'’.-]+(?:[ \t]+[А-ЯЁ][А-Яа-яЁё'’.-]+){1,3})\b", re.IGNORECASE),
    ),
    "prime_minister": (
        re.compile(r"\bPrime Minister[ \t]+([A-Z][A-Za-z'’.-]+(?:[ \t]+(?:[A-Z][A-Za-z'’.-]+|[A-Z]\.)){1,4})\b"),
        re.compile(r"\bпремьер-министр[ \t:,-]+([А-ЯЁ][А-Яа-яЁё'’.-]+(?:[ \t]+[А-ЯЁ][А-Яа-яЁё'’.-]+){1,3})\b", re.IGNORECASE),
    ),
}


def _latest_real_user_text(messages: list[ChatMessage]) -> str:
    for message in reversed(messages):
        if message.role != "user":
            continue
        text = str(message.content or "").strip()
        if not text:
            continue
        if any(text.startswith(prefix) for prefix in _INTERNAL_USER_PREFIXES):
            continue
        return text
    return ""


def _verified_blocks(messages: list[ChatMessage]) -> list[str]:
    blocks: list[str] = []
    for message in messages:
        text = str(message.content or "")
        if "VERIFIED FRESH WEB SNAPSHOTS" not in text:
            continue
        blocks.extend(match.group(1).strip() for match in _VERIFIED_BLOCK.finditer(text))
    return [block for block in blocks if block]


def _role_kind(question: str) -> str | None:
    q = question.casefold()
    if "президент" in q or "president" in q:
        return "president"
    if "ceo" in q or "гендиректор" in q or "генеральный директор" in q:
        return "ceo"
    if "премьер" in q or "prime minister" in q:
        return "prime_minister"
    return None


def _clean_candidate(value: str) -> str:
    value = re.sub(r"\s+", " ", value).strip(" \t\n\r.,;:—-()[]")
    # Prevent common page-heading words from being swallowed by a permissive
    # proper-name expression.
    parts = value.split()
    stop = {"Official", "Administration", "Biography", "News", "White", "House"}
    while parts and parts[-1] in stop:
        parts.pop()
    return " ".join(parts)


def _candidate_from_block(block: str, role: str) -> list[str]:
    values: list[str] = []
    for pattern in _ROLE_PATTERNS.get(role, ()):
        for match in pattern.finditer(block):
            candidate = _clean_candidate(match.group(1))
            if 3 <= len(candidate) <= 100 and len(candidate.split()) >= 2:
                values.append(candidate)
    return values


def _whitehouse_us_president(blocks: list[str]) -> str | None:
    for block in blocks:
        lowered = block.casefold()
        if "whitehouse.gov/administration" not in lowered:
            continue
        candidates = _candidate_from_block(block, "president")
        if candidates:
            return candidates[0]
        # Current administration pages commonly put the name on its own line
        # immediately before an ordinal President-of-the-United-States heading.
        match = re.search(
            r"\n([A-Z][A-Za-z'’.-]+(?:[ \t]+(?:[A-Z][A-Za-z'’.-]+|[A-Z]\.)){1,4})\s*\n"
            r"\s*\d{1,2}(?:st|nd|rd|th).*?President of the United States",
            block,
            re.IGNORECASE,
        )
        if match:
            return _clean_candidate(match.group(1))
    return None


def _consensus_holder(blocks: list[str], role: str) -> str | None:
    per_source: list[str] = []
    for block in blocks:
        candidates = _candidate_from_block(block, role)
        if candidates:
            per_source.append(candidates[0])
    if len(per_source) < 2:
        return None
    normalized = [re.sub(r"[^a-zа-яё]+", "", item.casefold()) for item in per_source]
    counts = Counter(normalized)
    winner, count = counts.most_common(1)[0]
    if count < 2:
        return None
    return next(value for value, key in zip(per_source, normalized) if key == winner)


def resolve_current_office_holder(question: str, messages: list[ChatMessage]) -> str | None:
    freshness = classify_freshness(question)
    if not freshness.required or freshness.category != "official_role":
        return None
    blocks = _verified_blocks(messages)
    if not blocks:
        return None

    role = _role_kind(question)
    if role is None:
        return None

    holder: str | None = None
    if role == "president" and _US_PRESIDENT.search(question):
        holder = _whitehouse_us_president(blocks)
    if holder is None:
        holder = _consensus_holder(blocks, role)
    if holder is None:
        return None

    russian = len(_CYRILLIC.findall(question)) >= 2
    if role == "president" and _US_PRESIDENT.search(question):
        if russian:
            display = "Дональд Трамп" if re.sub(r"[^a-z]", "", holder.casefold()) in {"donaldjtrump", "donaldtrump"} else holder
            original = f" ({holder})" if display != holder else ""
            return f"Сейчас президент США — {display}{original}. Данные подтверждены актуальной страницей администрации Белого дома."
        return f"The current President of the United States is {holder}. This is confirmed by the current White House administration page."

    if russian:
        return f"По свежим проверенным источникам, сейчас эту должность занимает {holder}."
    return f"Fresh verified sources identify the current office holder as {holder}."


def install_current_fact_evidence_guard() -> None:
    current = LlamaClient.generate
    if getattr(current, "_olya_current_fact_evidence_guard", False):
        return

    async def guarded(self, messages, *, max_tokens: int, reasoning: bool, on_token=None):
        question = _latest_real_user_text(messages)
        resolved = resolve_current_office_holder(question, messages) if question else None
        if resolved:
            if on_token is not None:
                await on_token(resolved)
            return LlamaGeneration(
                text=resolved,
                ttft_ms=0,
                output_tokens=max(1, len(resolved) // 4),
                tokens_per_second=0.0,
                generation_ms=0,
            )
        return await current(
            self,
            messages,
            max_tokens=max_tokens,
            reasoning=reasoning,
            on_token=on_token,
        )

    guarded._olya_current_fact_evidence_guard = True  # type: ignore[attr-defined]
    LlamaClient.generate = guarded
