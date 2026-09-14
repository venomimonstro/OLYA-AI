from __future__ import annotations

import os

from app.inference.client import LlamaClient


def _is_gigachat31() -> bool:
    name = str(os.getenv("X1_LLAMA_MODEL_NAME", "") or "").casefold()
    model_file = str(os.getenv("X1_LLAMA_MODEL_FILE", "") or "").casefold()
    return "gigachat3.1" in name or "gigachat3.1" in model_file


def install_gigachat31_runtime_patch() -> None:
    """Use the native GigaChat 3.1 OpenAI-compatible generation contract.

    GigaChat 3.1 ships its own Jinja chat template in GGUF. Qwen-specific
    thinking controls must not be sent to it. The official GigaChat 3.1 model
    card demonstrates chat inference with temperature=0; OLYA therefore uses
    deterministic decoding for answer stability. Product Simple/Medium/High
    still control context/output budgets upstream, not sampling randomness.
    """
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

    def payload(self, messages, *, max_tokens: int, reasoning: bool) -> dict:
        return {
            "model": "local",
            "messages": [message.model_dump() for message in messages],
            "max_tokens": max(32, int(max_tokens)),
            "stream": True,
            **sampling(bool(reasoning)),
        }

    sampling._olya_gigachat31_profile = True  # type: ignore[attr-defined]
    payload._olya_gigachat31_profile = True  # type: ignore[attr-defined]
    LlamaClient._sampling = staticmethod(sampling)
    LlamaClient._payload = payload
