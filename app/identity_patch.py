from __future__ import annotations

import re

from app.schemas.chat import ChatMessage

_CREATOR_REPLY_RU = "Меня создал Лысенко Артём."
_CREATOR_REPLY_EN = "I was created by Artyom Lysenko."

_CREATOR_RU = re.compile(
    r"(?:"
    r"кто\s+(?:тебя|вас)\s+(?:создал|разработал|сделал)|"
    r"кто\s+(?:твой|ваш)\s+(?:создатель|разработчик|автор)|"
    r"кто\s+(?:создал|разработал|сделал)\s+(?:тебя|x1|ол[ьяю]|оля\s*ai)|"
    r"чья\s+ты\s+(?:нейросеть|модель)|"
    r"кто\s+автор\s+(?:x1|ол[ьяю]|оля\s*ai)"
    r")",
    re.IGNORECASE,
)

_CREATOR_EN = re.compile(
    r"(?:who\s+(?:created|made|developed)\s+(?:you|x1)|"
    r"who\s+is\s+(?:your|x1['’]s)\s+(?:creator|developer|author))",
    re.IGNORECASE,
)


def creator_reply(user_text: str) -> str | None:
    text = " ".join((user_text or "").strip().split())
    if not text:
        return None
    if _CREATOR_RU.search(text):
        return _CREATOR_REPLY_RU
    if _CREATOR_EN.search(text):
        return _CREATOR_REPLY_EN
    return None


def _latest_user_text(messages: list[ChatMessage]) -> str:
    return next((message.content for message in reversed(messages) if message.role == "user"), "")


def install_identity_patch() -> None:
    """Return canonical X1 creator identity before local-model inference."""
    from app.inference.client import LlamaClient, LlamaGeneration

    current = LlamaClient.generate
    if getattr(current, "_x1_identity_guard", False):
        return

    async def guarded_generate(self, messages, *, max_tokens: int, reasoning: bool, on_token=None):
        reply = creator_reply(_latest_user_text(messages))
        if reply is not None:
            if on_token is not None:
                await on_token(reply)
            return LlamaGeneration(
                text=reply,
                ttft_ms=0,
                output_tokens=max(1, len(reply) // 4),
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

    guarded_generate._x1_identity_guard = True  # type: ignore[attr-defined]
    LlamaClient.generate = guarded_generate
