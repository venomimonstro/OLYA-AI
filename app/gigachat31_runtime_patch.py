from __future__ import annotations

import os

from app.inference.client import LlamaClient


def _is_gigachat31() -> bool:
    name = str(os.getenv("X1_LLAMA_MODEL_NAME", "") or "").casefold()
    model_file = str(os.getenv("X1_LLAMA_MODEL_FILE", "") or "").casefold()
    return "gigachat3.1" in name or "gigachat3.1" in model_file


def install_gigachat31_runtime_patch() -> None:
    """Use the official minimal OpenAI-compatible llama.cpp contract for GigaChat 3.1.

    GigaChat owns its Jinja template inside GGUF. Do not send Qwen/DeepSeek
    reasoning flags or custom sampler fields. Official examples require
    tool_choice=none for ordinary chat to prevent an unwanted JSON/tool-call
    instruction from being injected into the prompt.
    """
    if not _is_gigachat31():
        return
    if getattr(LlamaClient._payload, "_olya_gigachat31_profile", False):
        return

    def sampling(reasoning: bool) -> dict:
        _ = reasoning
        return {"temperature": 0.0}

    def payload(self, messages, *, max_tokens: int, reasoning: bool) -> dict:
        _ = reasoning
        return {
            "model": "local",
            "messages": [message.model_dump() for message in messages],
            "max_tokens": max(32, int(max_tokens)),
            "temperature": 0.0,
            "tool_choice": "none",
            "stream": True,
        }

    sampling._olya_gigachat31_profile = True  # type: ignore[attr-defined]
    payload._olya_gigachat31_profile = True  # type: ignore[attr-defined]
    LlamaClient._sampling = staticmethod(sampling)
    LlamaClient._payload = payload
