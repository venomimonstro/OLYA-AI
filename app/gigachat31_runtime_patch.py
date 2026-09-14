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
    thinking controls must not be sent to it. Official GigaChat 3.1 examples
    demonstrate deterministic chat inference with temperature=0, which OLYA
    uses for factual stability. Deeper product modes add a private quality
    instruction rather than incompatible Qwen reasoning flags.
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

    def _messages(messages, reasoning: bool) -> list[dict]:
        rows = [message.model_dump() for message in messages]
        if not reasoning:
            return rows
        quality = (
            "Для этой сложной задачи проведи тщательную внутреннюю проверку перед финальным ответом: "
            "проверь факты, вычисления, ограничения и логические противоречия. Не показывай скрытые "
            "рассуждения, chain-of-thought или черновик; пользователю выдай только проверенный итог, "
            "необходимые объяснения, расчёты и выводы."
        )
        if rows and rows[0].get("role") == "system":
            rows[0] = {**rows[0], "content": str(rows[0].get("content") or "") + "\n\n" + quality}
        else:
            rows.insert(0, {"role": "system", "content": quality})
        return rows

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
