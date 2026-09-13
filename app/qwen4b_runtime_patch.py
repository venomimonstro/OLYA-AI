from __future__ import annotations

from app.inference.client import LlamaClient


def install_qwen4b_runtime_patch() -> None:
    """Apply the pinned Qwen3-4B generation profile process-wide.

    Qwen3 uses materially different sampling in thinking and non-thinking mode.
    Keep this policy close to application bootstrap so every chat/tool path uses
    the same parameters and old deployments cannot silently retain the former
    over-hot thinking temperature.
    """

    current = LlamaClient._sampling
    if getattr(current, "_olya_qwen4b_profile", False):
        return

    def sampling(reasoning: bool) -> dict:
        if reasoning:
            return {
                "temperature": 0.6,
                "top_p": 0.95,
                "top_k": 20,
                "min_p": 0.0,
                "presence_penalty": 1.5,
                "repeat_penalty": 1.0,
            }
        return {
            "temperature": 0.7,
            "top_p": 0.8,
            "top_k": 20,
            "min_p": 0.0,
            "presence_penalty": 1.5,
            "repeat_penalty": 1.0,
        }

    sampling._olya_qwen4b_profile = True  # type: ignore[attr-defined]
    LlamaClient._sampling = staticmethod(sampling)
