from __future__ import annotations

from app.schemas.chat import ChatMessage


_COMPACT_MARKER = "\n[…older context compacted…]\n"


class ContextCompiler:
    """Small deterministic compiler for the CPU chat path.

    Upstream code already decides policy, project context, memory and optional web
    evidence. This class only enforces a character budget. It does not inject
    extra policies, answer contracts, routing instructions or task context.
    """

    def __init__(self, max_chars: int = 48_000, max_message_chars: int | None = None) -> None:
        self.max_chars = max(512, int(max_chars))
        self.max_message_chars = max_message_chars or max(512, min(16_000, self.max_chars // 2))

    @staticmethod
    def _clip_to(text: str, limit: int) -> str:
        value = str(text or "")
        if limit <= 0:
            return ""
        if len(value) <= limit:
            return value
        if limit <= len(_COMPACT_MARKER) + 64:
            return value[-limit:]
        payload = limit - len(_COMPACT_MARKER)
        head = max(32, payload // 3)
        tail = max(0, payload - head)
        return value[:head] + _COMPACT_MARKER + value[-tail:]

    def compile(self, messages: list[ChatMessage], *, max_chars: int | None = None) -> list[ChatMessage]:
        budget = max(512, int(max_chars or self.max_chars))
        if not messages:
            return []

        systems = [message for message in messages if message.role == "system"]
        conversation = [message for message in messages if message.role != "system"]

        # Preserve the primary system prompt and the most recent bounded system
        # contexts (project/memory/web). Do not allow metadata to crowd out the
        # actual user conversation.
        system_budget = min(int(budget * 0.45), 5200)
        kept_systems: list[ChatMessage] = []
        used_system = 0
        if systems:
            first = systems[0]
            first_text = self._clip_to(first.content, min(len(first.content), 2200, system_budget))
            if first_text:
                kept_systems.append(ChatMessage(role="system", content=first_text))
                used_system += len(first_text)

            for message in reversed(systems[1:]):
                remaining = system_budget - used_system
                if remaining <= 0:
                    break
                text = self._clip_to(message.content, min(self.max_message_chars, remaining, 2400))
                if not text:
                    continue
                kept_systems.insert(1, ChatMessage(role="system", content=text))
                used_system += len(text)

        conversation_budget = max(256, budget - used_system)
        kept_conversation: list[ChatMessage] = []
        used = 0
        for message in reversed(conversation):
            remaining = conversation_budget - used
            if remaining <= 0:
                break
            text = self._clip_to(message.content, min(self.max_message_chars, remaining))
            if not text:
                continue
            kept_conversation.append(ChatMessage(role=message.role, content=text))
            used += len(text)
        kept_conversation.reverse()

        return kept_systems + kept_conversation
