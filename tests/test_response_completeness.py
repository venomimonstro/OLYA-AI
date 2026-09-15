from __future__ import annotations

from app.services.response_completeness import needs_expansion


def test_complex_one_line_requires_expansion() -> None:
    assert needs_expansion(
        'проанализируй проект и предложи как улучшить архитектуру и снизить расходы',
        'Нужно использовать кэширование, оптимизировать запросы и следить за нагрузкой на сервер.',
    )


def test_list_request_requires_multiple_items() -> None:
    assert needs_expansion(
        'назови лучшие варианты продвижения и дай несколько примеров',
        'Лучше всего использовать SEO и контекстную рекламу, потому что они дают стабильный трафик.',
    )


def test_explicit_short_request_is_respected() -> None:
    assert not needs_expansion('ответь коротко: что такое DNS?', 'DNS преобразует доменные имена в IP-адреса.')


def test_atomic_fact_can_stay_short() -> None:
    assert not needs_expansion('сколько будет 2 + 2?', '4')
