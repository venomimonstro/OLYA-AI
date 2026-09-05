from app.schemas.chat import ChatMessage


class ContextCompiler:
    """Cheap deterministic context compaction for Sprint 1.

    This deliberately avoids an extra LLM call. It preserves system messages,
    the newest user/assistant turns, removes exact duplicates and caps raw chars.
    A later sprint will add project state + retrieval.
    """

    def __init__(self, max_chars: int = 48_000) -> None:
        self.max_chars = max_chars

    def compile(self, messages: list[ChatMessage]) -> list[ChatMessage]:
        systems = [m for m in messages if m.role == "system"]
        conversational = [m for m in messages if m.role != "system"]

        seen: set[tuple[str, str]] = set()
        deduped_reversed: list[ChatMessage] = []
        chars = 0

        for msg in reversed(conversational):
            key = (msg.role, msg.content)
            if key in seen:
                continue
            if chars + len(msg.content) > self.max_chars and deduped_reversed:
                break
            seen.add(key)
            deduped_reversed.append(msg)
            chars += len(msg.content)

        result = systems[-2:] + list(reversed(deduped_reversed))
        return result
