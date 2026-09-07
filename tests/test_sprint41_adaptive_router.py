from app.inference.router import choose_route


def test_simple_transformation_uses_fast_without_reasoning():
    route = choose_route("Перепиши это предложение короче", "auto", 8192, 8192)
    assert route.mode == "fast"
    assert route.reasoning is False
    assert route.max_context_tokens <= 4096


def test_basic_question_does_not_waste_thinking_budget():
    route = choose_route("Что такое сухой джин?", "auto", 8192, 8192)
    assert route.mode == "work"
    assert route.reasoning is False
    assert route.complexity_score < 3


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


def test_security_audit_is_always_deep_even_when_prompt_is_short():
    route = choose_route("Проведи аудит безопасности проекта", "auto", 8192, 12288)
    assert route.mode == "deep"
    assert route.reasoning is True
    assert route.complexity_score >= 7


def test_large_rewrite_does_not_become_deep_only_because_input_is_long():
    route = choose_route("Перепиши текст без изменения смысла: " + ("слово " * 900), "auto", 8192, 12288)
    assert route.mode == "work"
    assert route.reasoning is False


def test_explicit_mode_is_honored_but_never_breaks_physical_context_ceiling():
    fast = choose_route("Проведи аудит безопасности", "fast", 16384, 8192)
    assert fast.mode == "fast"
    assert fast.reasoning is False
    assert fast.max_context_tokens <= 4096

    deep = choose_route("кратко", "deep", 16384, 8192)
    assert deep.mode == "deep"
    assert deep.reasoning is True
    assert deep.max_context_tokens == 8192


def test_work_context_is_clamped_to_llama_boot_context():
    route = choose_route("Обычный вопрос", "work", 32768, 8192)
    assert route.max_context_tokens == 8192
