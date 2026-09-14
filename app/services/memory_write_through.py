from __future__ import annotations

from sqlalchemy import event, select

from app.models import Conversation, ConversationMemory, Message
from app.services.long_term_memory import _memory_key, extract_user_memories


@event.listens_for(Message, "after_insert")
def persist_user_memory_after_message(_mapper, connection, target: Message) -> None:
    """Capture memory from every persisted user turn, including the first turn of a new chat."""
    if str(getattr(target, "role", "")) != "user":
        return
    text = str(getattr(target, "content", "") or "")
    candidates = extract_user_memories(text)
    if not candidates:
        return

    conversation_table = Conversation.__table__
    memory_table = ConversationMemory.__table__
    project_id = connection.execute(
        select(conversation_table.c.project_id).where(conversation_table.c.id == target.conversation_id)
    ).scalar_one_or_none()

    for kind, value, terms in candidates:
        key = _memory_key(kind, value)
        exists = connection.execute(
            select(memory_table.c.id).where(
                memory_table.c.conversation_id == target.conversation_id,
                memory_table.c.kind == kind,
                memory_table.c.memory_key == key,
            ).limit(1)
        ).scalar_one_or_none()
        if exists is not None:
            continue
        payload = {
            "conversation_id": target.conversation_id,
            "project_id": project_id,
            "kind": kind,
            "memory_key": key,
            "value": value,
            "keywords": terms,
            "source_role": "user",
            "confidence": 1.0,
        }
        connection.execute(memory_table.insert().values(**payload))
