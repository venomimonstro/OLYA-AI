from __future__ import annotations

from app.schemas.chat import ChatMessage


_COMPACT_MARKER = "\n[…older content compacted by X1…]\n"


class ContextCompiler:
    """Deterministic low-RAM compiler for long chats.

    The compiler intentionally does not globally deduplicate conversation turns:
    repeated questions, confirmations and constraints are valid dialogue state.
    De-duplication of client/server overlap belongs in the context builder where
    the provenance of messages is known.
    """

    def __init__(self, max_chars: int = 48_000, max_message_chars: int | None = None) -> None:
        self.max_chars = max(128, int(max_chars))
        self.max_message_chars = max_message_chars or max(128, min(24_000, self.max_chars // 2))

    @staticmethod
    def _clip_to(text: str, limit: int) -> str:
        if limit <= 0:
            return ""
        if len(text) <= limit:
            return text
        if limit <= len(_COMPACT_MARKER) + 32:
            # For extremely small residual budgets preserving the beginning is
            # preferable to keeping only a tail that may lose the user goal.
            return text[:limit]
        payload = limit - len(_COMPACT_MARKER)
        head = max(16, payload // 2)
        tail = max(0, payload - head)
        return text[:head] + _COMPACT_MARKER + (text[-tail:] if tail else "")

    def _clip(self, text: str) -> str:
        return self._clip_to(text, self.max_message_chars)

    def compile(self, messages: list[ChatMessage], *, max_chars: int | None = None) -> list[ChatMessage]:
        budget_total = max(128, int(max_chars or self.max_chars))
        systems = [ChatMessage(role="system", content=self._clip(message.content)) for message in messages if message.role == "system"][-2:]
        system_chars = sum(len(message.content) for message in systems)
        budget = max(0, budget_total - system_chars)

        conversational = [message for message in messages if message.role != "system"]
        kept: list[ChatMessage] = []
        used = 0
        for message in reversed(conversational):
            content = self._clip(message.content)
            remaining = budget - used
            if remaining <= 0:
                break
            if len(content) > remaining:
                # Always preserve both the goal-bearing beginning and recent tail
                # when enough room exists. The former implementation kept only
                # the tail and could silently discard the actual user request.
                content = self._clip_to(content, remaining)
            if not content:
                break
            kept.append(ChatMessage(role=message.role, content=content))
            used += len(content)
            if used >= budget:
                break
        return systems + list(reversed(kept))
