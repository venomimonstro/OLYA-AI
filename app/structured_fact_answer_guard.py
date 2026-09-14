from __future__ import annotations

from app.inference.client import LlamaClient, LlamaGeneration
from app.services.interactive_evidence import current_interactive_evidence


_INTERNAL_QUALITY_MARKERS = (
    "внутренний evidence-aware критик",
    "финальный редактор",
    "evidence-aware critic",
    "repair",
)


def _quality_call(messages) -> bool:
    for message in messages:
        if getattr(message, "role", "") != "system":
            continue
        text = str(getattr(message, "content", "") or "").casefold()
        if any(marker in text for marker in _INTERNAL_QUALITY_MARKERS):
            return True
    return False


def install_structured_fact_answer_guard() -> None:
    current = LlamaClient.generate
    if getattr(current, "_olya_structured_fact_answer", False):
        return

    async def guarded(self, messages, *, max_tokens: int, reasoning: bool, on_token=None):
        evidence = current_interactive_evidence()
        answer = str(evidence.resolved_answer or "").strip()
        if answer and not _quality_call(messages):
            if on_token is not None:
                await on_token(answer)
            return LlamaGeneration(
                text=answer,
                ttft_ms=0,
                output_tokens=max(1, len(answer) // 4),
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

    guarded._olya_structured_fact_answer = True  # type: ignore[attr-defined]
    LlamaClient.generate = guarded
