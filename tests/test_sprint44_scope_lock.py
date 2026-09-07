from __future__ import annotations

import asyncio
from pathlib import Path

from app.schemas.chat import ChatMessage
from app.services.context import ContextCompiler
from app.services.quality import AnswerQualityEngine
from app.services.scope_lock import audit_scope, compile_scope_contract, get_scope_contract

ROOT = Path(__file__).resolve().parents[1]


def _statuses(checks):
    return {item["key"]: item["status"] for item in checks}


def test_scope_compiler_detects_edit_only_russian_constraints():
    contract = compile_scope_contract(
        "Исправь только орфографические ошибки. Ничего не добавляй, не сокращай, не меняй структуру и смысл.\n\nПервый абзац с ашипкой.\n\nВторой абзац."
    )
    assert contract.active is True
    assert contract.no_expand is True
    assert contract.no_shorten is True
    assert contract.preserve_structure is True
    assert contract.preserve_meaning is True
    assert contract.no_rewrite is True
    assert "Первый абзац" in contract.source_text


def test_scope_compiler_does_not_activate_on_normal_open_ended_request():
    contract = compile_scope_contract("Объясни, как работает индекс PostgreSQL и приведи пример")
    assert contract.active is False
    assert contract.must == ()
    assert contract.must_not == ()


def test_context_compiler_injects_scope_guard_before_generation():
    compiled = ContextCompiler(max_chars=12000).compile([
        ChatMessage(role="user", content="Верни только готовый текст без пояснений:\n\nТестовый исходный текст для проверки."),
    ])
    systems = [item.content for item in compiled if item.role == "system"]
    assert any("X1 USER-SCOPE CONTRACT" in item for item in systems)
    assert any("no preamble" in item for item in systems)
    assert get_scope_contract().active is True


def test_final_only_scope_rejects_unsolicited_preamble():
    contract = compile_scope_contract("Верни только готовый текст без пояснений:\n\nНормальный исходный текст для редактирования.")
    checks, _ = audit_scope("Конечно! Вот готовый текст: Нормальный исходный текст для редактирования.", contract)
    assert _statuses(checks)["scope_final_only"] == "failed"


def test_no_expand_and_no_shorten_are_measured_conservatively():
    source = "Это достаточно длинный исходный абзац. " * 12
    contract = compile_scope_contract(f"Исправь только ошибки. Ничего не добавляй и не сокращай:\n\n{source}")
    expanded = source + (" Новая непрошенная информация." * 15)
    checks, _ = audit_scope(expanded, contract)
    assert _statuses(checks)["scope_no_expand"] == "failed"
    shortened = source[:120]
    checks, _ = audit_scope(shortened, contract)
    assert _statuses(checks)["scope_no_shorten"] == "failed"


def test_preserve_structure_detects_large_paragraph_drift():
    source = "Абзац один с достаточным текстом.\n\nАбзац два с достаточным текстом.\n\nАбзац три с достаточным текстом.\n\nАбзац четыре с достаточным текстом."
    contract = compile_scope_contract(f"Не меняй структуру:\n\n{source}")
    checks, _ = audit_scope(source.replace("\n\n", " "), contract)
    assert _statuses(checks)["scope_preserve_structure"] == "failed"


def test_json_only_is_deterministically_enforced():
    contract = compile_scope_contract("Ответь только JSON, без пояснений")
    checks, _ = audit_scope('{"ok":true}', contract)
    assert _statuses(checks)["scope_format_json"] == "passed"
    checks, _ = audit_scope("Вот JSON: {\"ok\":true}", contract)
    assert _statuses(checks)["scope_format_json"] == "failed"


def test_quality_engine_makes_scope_lock_truthy_for_existing_auto_repair_path():
    compile_scope_contract("Верни только готовый текст без пояснений")
    requirements = []
    audit = AnswerQualityEngine().deterministic("Конечно! Вот готовый текст: пример", requirements)
    assert audit.failed is True
    # chat.py historically enters Auto repair when payload.requirements is truthy.
    # The internal harmless sentinel connects Scope Lock to that existing path.
    assert requirements
    assert requirements[0].label == "Внутренний Scope Lock активен"


def test_scope_context_is_isolated_between_async_tasks():
    async def worker(text: str, expected: bool):
        compile_scope_contract(text)
        await asyncio.sleep(0)
        assert get_scope_contract().active is expected

    async def scenario():
        await asyncio.gather(
            worker("Только ответ без пояснений", True),
            worker("Объясни подробно архитектуру", False),
        )

    asyncio.run(scenario())


def test_sprint44_is_low_compute_and_does_not_add_an_unconditional_llm_call():
    scope = (ROOT / "app/services/scope_lock.py").read_text("utf-8")
    context = (ROOT / "app/services/context.py").read_text("utf-8")
    quality = (ROOT / "app/services/quality.py").read_text("utf-8")
    assert "ContextVar" in scope
    assert "compile_scope_contract" in context
    assert "audit_scope(text)" in quality
    assert "llama" not in scope.casefold()
