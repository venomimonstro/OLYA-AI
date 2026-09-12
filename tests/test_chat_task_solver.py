from __future__ import annotations

from app.inference.router import choose_route
from app.schemas.chat import ChatMessage, ChatRequest, ChatResponse, ChatUsage
from app.services.context import ContextCompiler
from app.services.discovery import SearchHit
from app.services.freshness import classify_freshness
from app.services.research import extract_document
from app.services.task_solver import (
    current_task_solver_context,
    diversify_hits,
    plan_task,
    reset_task_solver_context,
    set_task_solver_context,
)


def test_seo_audit_is_autonomous_current_deep_task():
    question = "Проведи SEO аудит этого сайта https://example.com/ и дай готовый план исправлений"
    plan = plan_task(question)
    route = choose_route(question, "auto", 4096, 4096)
    fresh = classify_freshness(question)
    assert plan.kind == "website_audit"
    assert plan.requires_web is True
    assert "https://example.com/robots.txt" in plan.direct_urls
    assert "https://example.com/sitemap.xml" in plan.direct_urls
    assert any(query.startswith("site:example.com") for query in plan.queries)
    assert route.mode == "deep"
    assert route.reasoning is True
    assert fresh.required is True
    assert fresh.min_independent_hosts == 1


def test_moscow_dentistry_uses_multiple_evidence_angles_not_search_rank_only():
    question = "Найди лучшую стоматологию в Москве и объясни почему"
    plan = plan_task(question)
    route = choose_route(question, "auto", 4096, 4096)
    fresh = classify_freshness(question)
    assert plan.kind == "local_recommendation"
    assert plan.location == "Москва"
    assert plan.category == "стоматология"
    assert len(plan.queries) == 4
    joined = " ".join(plan.queries)
    assert "Яндекс" in joined and "2ГИС" in joined and "ПроДокторов" in joined
    assert "цены" in joined and "специалисты" in joined
    assert "лицензия" in joined and "официальный сайт" in joined
    assert fresh.required is True and fresh.min_independent_hosts >= 2
    assert route.mode in {"work", "deep"}
    assert route.reasoning is True


def test_simple_transformation_does_not_waste_web_or_deep_compute():
    question = "Перепиши этот текст короче и дружелюбнее"
    plan = plan_task(question)
    route = choose_route(question, "auto", 4096, 4096)
    assert plan.kind == "direct"
    assert plan.requires_web is False
    assert route.mode == "fast"


def test_result_diversification_prefers_source_types_and_independent_hosts():
    hits = [
        SearchHit(query="q", title="Официальный сайт Альфа", url="https://alpha.ru/", snippet="официальный сайт", rank=5, provider="searxng"),
        SearchHit(query="q", title="Альфа на Яндекс", url="https://yandex.ru/maps/org/alpha", snippet="рейтинг 4.9 500 отзывов", rank=1, provider="searxng"),
        SearchHit(query="q", title="Отзывы Альфа", url="https://otzovik.com/reviews/alpha", snippet="Отзывы пациентов", rank=2, provider="searxng"),
        SearchHit(query="q", title="SEO статья", url="https://spam.example/top", snippet="топ клиник", rank=1, provider="searxng"),
        SearchHit(query="q", title="Ещё Яндекс", url="https://yandex.ru/maps/org/beta", snippet="рейтинг 4.8", rank=2, provider="searxng"),
    ]
    rows = diversify_hits(hits, kind="local_recommendation", limit=4)
    hosts = {row["host"] for row in rows}
    kinds = {row["source_kind"] for row in rows}
    assert len(hosts) >= 3
    assert "official_candidate" in kinds
    assert "maps_catalog" in kinds
    assert "reviews" in kinds
    # Search rank 1 from an undifferentiated SEO article cannot monopolize the set.
    assert len(rows) == 4


def test_lightweight_html_inspector_extracts_real_seo_signals():
    html = '''<html lang="ru"><head><title>Тестовая страница</title>
    <meta name="description" content="Описание страницы">
    <meta name="robots" content="index,follow">
    <link rel="canonical" href="https://example.com/canonical">
    <script type="application/ld+json">{"@type":"Organization"}</script></head>
    <body><h1>Главный заголовок</h1><h2>Раздел</h2><p>Контент</p></body></html>'''
    title, text, metadata = extract_document(html, "text/html")
    assert title == "Тестовая страница"
    assert "Главный заголовок" in text
    assert metadata["meta_description"] == "Описание страницы"
    assert metadata["canonical"] == "https://example.com/canonical"
    assert metadata["h1_count"] == 1
    assert metadata["h2_count"] == 1
    assert metadata["structured_data_blocks"] == 1
    assert metadata["noindex"] is False


def test_task_context_is_bounded_request_local_and_does_not_replace_user_goal():
    token = set_task_solver_context([
        ChatMessage(role="system", content="TASK CONTRACT: solve the task autonomously"),
        ChatMessage(role="user", content="UNTRUSTED SEARCH-DISCOVERY SIGNALS: candidate data"),
    ])
    try:
        compiled = ContextCompiler(max_chars=5000).compile([ChatMessage(role="user", content="Найди лучший вариант")])
        assert any("TASK CONTRACT" in message.content for message in compiled)
        assert compiled[-1].content == "Найди лучший вариант"
        assert current_task_solver_context()
    finally:
        reset_task_solver_context(token)
    assert current_task_solver_context() == []


def test_chat_response_exposes_only_high_level_task_execution_metadata():
    response = ChatResponse(
        text="Готовый ответ",
        model="Qwen3-4B-Q4_K_M",
        usage=ChatUsage(raw_message_chars=10, compiled_message_chars=20, mode="work"),
        task_execution={"kind": "local_recommendation", "fetched_sources": 5, "independent_hosts": 4},
    )
    assert response.task_execution["kind"] == "local_recommendation"
    assert "reasoning" not in response.task_execution
