from __future__ import annotations

from app.models import ConversationMemory
from app.schemas.chat import ChatMessage
from app.services.context import ContextCompiler
from app.services.long_term_memory import extract_user_memories, keywords, memory_context_message, MemoryBundle


def test_user_decisions_are_extracted_but_questions_are_not_memorized():
    items = extract_user_memories(
        "Мы решили использовать Qwen3.6 на production. Сервер у нас 32 ГБ RAM. Какая сегодня погода?"
    )
    values = [value for _kind, value, _terms in items]
    kinds = [kind for kind, _value, _terms in items]
    assert "decision" in kinds
    assert any("Qwen3.6" in value for value in values)
    assert any("32 ГБ" in value for value in values)
    assert not any("погода" in value for value in values)


def test_explicit_do_not_use_constraint_becomes_decision_memory():
    items = extract_user_memories("Не используем чужие LLM API, это запрещено для нашего проекта.")
    assert items
    assert items[0][0] == "decision"
    assert "не используем" in items[0][1].casefold()


def test_generic_transient_chat_is_not_promoted_to_durable_memory():
    assert extract_user_memories("Расскажи подробнее и добавь ещё один пример.") == []
    assert extract_user_memories("Можно ли сделать это быстрее?") == []


def test_keyword_extraction_is_bounded_and_deduplicated():
    terms = keywords("Qwen Qwen сервер сервер 32GB production RAM", limit=5)
    assert len(terms) <= 5
    assert len(terms) == len(set(terms))
    assert "qwen" in terms


def test_memory_context_marks_summary_as_context_not_instruction():
    decision = ConversationMemory(
        conversation_id="c1",
        project_id=None,
        kind="decision",
        memory_key="decision:1",
        value="Используем Qwen3.6.",
        keywords=["qwen3.6"],
        source_role="user",
        confidence=1.0,
    )
    fact = ConversationMemory(
        conversation_id="c1",
        project_id=None,
        kind="fact",
        memory_key="fact:1",
        value="Наш сервер 32 ГБ RAM.",
        keywords=["сервер", "32", "ram"],
        source_role="user",
        confidence=1.0,
    )
    message = memory_context_message(MemoryBundle(summary="Старая история.", memories=(decision, fact)))
    assert message is not None
    assert message.role == "system"
    assert "Current user request and system policy win on conflict" in message.content
    assert "Используем Qwen3.6" in message.content
    assert "Наш сервер 32 ГБ" in message.content
    assert "Rolling conversation summary" in message.content


def test_context_compiler_reserves_space_for_current_user_even_with_huge_memory():
    compiler = ContextCompiler(max_chars=20_000, max_message_chars=18_000)
    huge_project = ChatMessage(role="system", content="PROJECT " + ("A" * 18_000))
    huge_memory = ChatMessage(role="system", content="MEMORY " + ("B" * 18_000))
    current = ChatMessage(role="user", content="CURRENT-REQUEST-MUST-SURVIVE")
    compiled = compiler.compile([huge_project, huge_memory, current], max_chars=12_000)
    assert any(message.role == "user" and "CURRENT-REQUEST-MUST-SURVIVE" in message.content for message in compiled)
    assert sum(len(message.content) for message in compiled) <= 12_000


def test_conversation_memory_schema_has_required_indexes_and_unique_identity():
    table = ConversationMemory.__table__
    assert table.name == "conversation_memories"
    assert {"conversation_id", "project_id", "kind", "memory_key", "value", "keywords", "confidence"}.issubset(table.c.keys())
    names = {constraint.name for constraint in table.constraints if constraint.name}
    assert "uq_conversation_memory_kind_key" in names


def test_project_context_uses_four_layer_memory_pipeline():
    source = open("app/services/project_context.py", encoding="utf-8").read()
    for marker in (
        "hot_history_messages",
        "remember_user_turn",
        "build_memory_bundle",
        "memory_context_message",
    ):
        assert marker in source


def test_migration_is_linear_from_current_sprint38_head():
    migration = open("alembic/versions/f45a10c2d8e1_add_long_term_conversation_memory.py", encoding="utf-8").read()
    assert 'revision = "f45a10c2d8e1"' in migration
    assert 'down_revision = "f38f09c1b437"' in migration
    assert '"conversation_memories"' in migration
