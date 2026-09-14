from __future__ import annotations

import os

from app.inference.client import LlamaClient


_EVIDENCE_MARKERS = (
    "STRUCTURED OFFICIAL FACT",
    "VERIFIED FRESH WEB SNAPSHOTS",
    "WEB SEARCH DISCOVERY",
)
_POLICY_MARKERS = ("OLYA RESPONSE POLICY", "ANSWER SHAPE:")


def _is_gigachat31() -> bool:
    name = str(os.getenv("X1_LLAMA_MODEL_NAME", "") or "").casefold()
    model_file = str(os.getenv("X1_LLAMA_MODEL_FILE", "") or "").casefold()
    return "gigachat3.1" in name or "gigachat3.1" in model_file


def _clip(text: str, limit: int) -> str:
    value = str(text or "").strip()
    if len(value) <= limit:
        return value
    return value[: max(1, limit - 1)].rstrip() + "…"


def compact_prompt_rows(rows: list[dict], *, reasoning: bool) -> list[dict]:
    """Hard final prompt budget for the CPU-only GigaChat deployment.

    Full history/memory remains persisted by OLYA. This is only the last-mile
    inference view, protecting simple requests from multi-minute prompt eval if a
    legacy context layer accidentally expands the message list again.
    """
    if not rows:
        return rows

    char_budget = int(os.getenv(
        "X1_GIGACHAT_PROMPT_CHAR_BUDGET_DEEP" if reasoning else "X1_GIGACHAT_PROMPT_CHAR_BUDGET_SIMPLE",
        "6500" if reasoning else "3000",
    ))
    char_budget = max(1800, min(char_budget, 12000 if reasoning else 5000))

    latest_user = next((i for i in range(len(rows) - 1, -1, -1) if rows[i].get("role") == "user"), -1)
    essential: set[int] = set()
    if latest_user >= 0:
        essential.add(latest_user)
    for i, row in enumerate(rows):
        text = str(row.get("content") or "")
        if any(marker in text for marker in (*_EVIDENCE_MARKERS, *_POLICY_MARKERS)):
            essential.add(i)

    # Keep one prior conversational turn for continuity, but not an entire chat.
    conversational = [i for i, row in enumerate(rows[: latest_user if latest_user >= 0 else len(rows)]) if row.get("role") in {"user", "assistant"}]
    essential.update(conversational[-2:])

    # Preserve at most one additional system/project/memory context nearest the tail.
    contextual = [
        i for i, row in enumerate(rows)
        if i not in essential and row.get("role") == "system"
    ]
    if contextual:
        essential.add(contextual[-1])

    def item_limit(i: int, row: dict) -> int:
        text = str(row.get("content") or "")
        if i == latest_user:
            return 1400 if not reasoning else 3000
        if "ANSWER SHAPE:" in text:
            return 420
        if "OLYA RESPONSE POLICY" in text:
            return 850
        if any(marker in text for marker in _EVIDENCE_MARKERS):
            return 1200 if not reasoning else 2600
        if row.get("role") == "system":
            return 500 if not reasoning else 1200
        return 550 if not reasoning else 1400

    selected: list[tuple[int, dict]] = []
    used = 0
    # Essential rows first, then fill recent context backwards while budget remains.
    for i in sorted(essential):
        row = rows[i]
        content = _clip(str(row.get("content") or ""), item_limit(i, row))
        cost = len(content) + 24
        if used + cost > char_budget and i != latest_user:
            continue
        selected.append((i, {**row, "content": content}))
        used += cost

    if used < char_budget:
        for i in range(len(rows) - 1, -1, -1):
            if i in essential:
                continue
            row = rows[i]
            content = _clip(str(row.get("content") or ""), item_limit(i, row))
            cost = len(content) + 24
            if used + cost > char_budget:
                continue
            selected.append((i, {**row, "content": content}))
            used += cost
            if used >= char_budget * 0.92:
                break

    selected.sort(key=lambda pair: pair[0])
    return [row for _, row in selected]


def install_gigachat31_runtime_patch() -> None:
    """Use the native GigaChat 3.1 OpenAI-compatible generation contract."""
    if not _is_gigachat31():
        return

    current_payload = LlamaClient._payload
    if getattr(current_payload, "_olya_gigachat31_profile", False):
        return

    def sampling(reasoning: bool) -> dict:
        _ = reasoning
        return {
            "temperature": 0.0,
            "top_p": 1.0,
            "top_k": 0,
            "min_p": 0.0,
            "presence_penalty": 0.0,
            "repeat_penalty": 1.0,
        }

    def _messages(messages, reasoning: bool) -> list[dict]:
        rows = [message.model_dump() for message in messages]
        if reasoning:
            quality = (
                "Тщательно проверь факты, вычисления, ограничения и противоречия перед финальным ответом. "
                "Не показывай скрытые рассуждения; выдай только проверенный итог и необходимые объяснения."
            )
            # Keep this dynamic quality instruction at the tail to preserve llama.cpp prefix-cache reuse.
            insert_at = next((i for i in range(len(rows) - 1, -1, -1) if rows[i].get("role") == "user"), len(rows))
            rows.insert(insert_at, {"role": "system", "content": quality})
        return compact_prompt_rows(rows, reasoning=reasoning)

    def payload(self, messages, *, max_tokens: int, reasoning: bool) -> dict:
        return {
            "model": "local",
            "messages": _messages(messages, bool(reasoning)),
            "max_tokens": max(32, int(max_tokens)),
            "stream": True,
            **sampling(bool(reasoning)),
        }

    sampling._olya_gigachat31_profile = True  # type: ignore[attr-defined]
    payload._olya_gigachat31_profile = True  # type: ignore[attr-defined]
    LlamaClient._sampling = staticmethod(sampling)
    LlamaClient._payload = payload
