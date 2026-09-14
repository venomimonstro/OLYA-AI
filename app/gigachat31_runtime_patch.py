from __future__ import annotations

import os

from app.inference.client import LlamaClient


def _is_gigachat31() -> bool:
    name = str(os.getenv("X1_LLAMA_MODEL_NAME", "") or "").casefold()
    model_file = str(os.getenv("X1_LLAMA_MODEL_FILE", "") or "").casefold()
    return "gigachat3.1" in name or "gigachat3.1" in model_file


def install_gigachat31_runtime_patch() -> None:
    """Use a native GigaChat 3.1 payload instead of Qwen thinking controls.

    GigaChat 3.1 Lightning ships its own Jinja chat template in GGUF and is
    served through llama.cpp's OpenAI-compatible endpoint. Qwen-specific
    ``enable_thinking``/``reasoning_format`` fields are intentionally omitted.
    The product's Simple/Medium/High modes still control output budget and the
    reasoning flag, but private chain-of-thought is never requested or exposed.
    """
    if not _is_gigachat31():
        return

    current_payload = LlamaClient._payload
    if getattr(current_payload, "_olya_gigachat31_profile", False):
        return

    def sampling(reasoning: bool) -> dict:
        # Conservative sampling prioritises factual stability. High/Medium still
        # receive a larger response budget upstream; a small temperature lift on
        # reasoning tasks avoids making longer synthesis unnaturally rigid.
        return {
            "temperature": 0.45 if reasoning else 0.30,
            "top_p": 0.90,
            "top_k": 40,
            "min_p": 0.02,
            "presence_penalty": 0.0,
            "repeat_penalty": 1.05,
        }

    def payload(self, messages, *, max_tokens: int, reasoning: bool) -> dict:
        return {
            "model": "local",
            "messages": [message.model_dump() for message in messages],
            "max_tokens": max(64, int(max_tokens)),
            "stream": True,
            **sampling(bool(reasoning)),
        }

    sampling._olya_gigachat31_profile = True  # type: ignore[attr-defined]
    payload._olya_gigachat31_profile = True  # type: ignore[attr-defined]
    LlamaClient._sampling = staticmethod(sampling)
    LlamaClient._payload = payload
