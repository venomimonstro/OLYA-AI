from __future__ import annotations

import re

from app.inference.client import LlamaClient, LlamaGeneration
from app.services.freshness import classify_freshness
from app.services.interactive_evidence import current_interactive_evidence


_INTERNAL_QUALITY_MARKERS = (
    "внутренний evidence-aware критик",
    "финальный редактор",
    "evidence-aware critic",
    "repair",
)
_INTERNAL_USER_PREFIXES = (
    "VERIFIED FRESH WEB SNAPSHOTS",
    "WEB SEARCH DISCOVERY",
    "STRUCTURED OFFICIAL FACT",
    "UNTRUSTED CLIENT-SUPPLIED",
    "OLYA trusted project context",
)
_CYR = re.compile(r"[А-Яа-яЁё]")


def _quality_call(messages) -> bool:
    for message in messages:
        if getattr(message, "role", "") != "system":
            continue
        text = str(getattr(message, "content", "") or "").casefold()
        if any(marker in text for marker in _INTERNAL_QUALITY_MARKERS):
            return True
    return False


def _latest_user(messages) -> str:
    for message in reversed(messages):
        if getattr(message, "role", "") != "user":
            continue
        text = str(getattr(message, "content", "") or "").strip()
        if not text or any(text.startswith(prefix) for prefix in _INTERNAL_USER_PREFIXES):
            continue
        return text
    return ""


def _fresh_unavailable(question: str) -> str:
    if len(_CYR.findall(question)) >= 2:
        return "Не удалось быстро получить достаточно надёжных актуальных данных. Устаревшие сведения из памяти модели не используются."
    return "I could not obtain enough reliable current evidence quickly, so stale model-memory data was not used."


def _generation(text: str) -> LlamaGeneration:
    return LlamaGeneration(
        text=text,
        ttft_ms=0,
        output_tokens=max(1, len(text) // 4),
        tokens_per_second=0.0,
        generation_ms=0,
    )


def install_structured_fact_answer_guard() -> None:
    current = LlamaClient.generate
    if getattr(current, "_olya_structured_fact_answer", False):
        return

    async def guarded(self, messages, *, max_tokens: int, reasoning: bool, on_token=None):
        evidence = current_interactive_evidence()
        if not _quality_call(messages):
            answer = str(evidence.resolved_answer or "").strip()
            if answer:
                if on_token is not None:
                    await on_token(answer)
                return _generation(answer)

            question = _latest_user(messages)
            if (
                question
                and evidence.category != "stable"
                and evidence.evidence_count <= 0
                and classify_freshness(question).required
            ):
                # The web stage already exhausted its bounded interactive budget.
                # Running local inference cannot create current evidence, so fail
                # immediately instead of wasting CPU for a result we must reject.
                text = _fresh_unavailable(question)
                if on_token is not None:
                    await on_token(text)
                return _generation(text)

        return await current(
            self,
            messages,
            max_tokens=max_tokens,
            reasoning=reasoning,
            on_token=on_token,
        )

    guarded._olya_structured_fact_answer = True  # type: ignore[attr-defined]
    LlamaClient.generate = guarded
