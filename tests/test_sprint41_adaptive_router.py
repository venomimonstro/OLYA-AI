from app.inference.router import choose_route


def test_simple_transformation_uses_work_quality_floor_without_reasoning():
    route = choose_route("Перепиши это предложение короче", "auto", 8192, 8192)
    assert route.mode == "work"
    assert route.reasoning is False
    assert route.max_context_tokens == 8192
    assert route.max_output_tokens >= 1000


def test_basic_question_does_not_waste_thinking_budget():
    route = choose_route("Что такое сухой джин?", "auto", 8192, 8192)
    assert route.mode == "work"
    assert route.reasoning is False
    assert route.complexity_score < 3
    assert route.max_output_tokens >= 1000


def test_medium_analytical_work_gets_thinking_without_full_deep_budget():
    route = choose_route(
        "Проанализируй API и оптимизируй архитектуру сервиса, объясни узкие места.",
        "auto",
        8192,
        12288,
    )
    assert route.mode == "work"
    assert route.reasoning is True
    assert route.max_context_tokens == 8192
    assert route.max_output_tokens >= 1600


def test_security_audit_is_always_deep_even_when_prompt_is_short():
    route = choose_route("Проведи аудит безопасности проекта", "auto", 8192, 12288)
    assert route.mode == "deep"
    assert route.reasoning is True
    assert route.complexity_score >= 7


def test_large_rewrite_does_not_become_deep_only_because_input_is_long():
    route = choose_route("Перепиши текст без изменения смысла: " + ("слово " * 900), "auto", 8192, 12288)
    assert route.mode == "work"
    assert route.reasoning is False


def test_legacy_explicit_fast_is_treated_as_auto_and_can_escalate_to_deep():
    legacy_fast = choose_route("Проведи аудит безопасности", "fast", 16384, 8192)
    assert legacy_fast.mode == "deep"
    assert legacy_fast.reasoning is True
    assert legacy_fast.max_context_tokens == 8192
    assert "fast_upgraded_to_auto" in legacy_fast.reason

    simple_legacy_fast = choose_route("Кратко объясни HTTP", "fast", 16384, 8192)
    assert simple_legacy_fast.mode == "work"
    assert simple_legacy_fast.reasoning is False

    deep = choose_route("кратко", "deep", 16384, 8192)
    assert deep.mode == "deep"
    assert deep.reasoning is True
    assert deep.max_context_tokens == 8192


def test_work_context_is_clamped_to_llama_boot_context():
    route = choose_route("Обычный вопрос", "work", 32768, 8192)
    assert route.max_context_tokens == 8192
