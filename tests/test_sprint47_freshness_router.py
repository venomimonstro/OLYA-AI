from pathlib import Path

from app.services.freshness import classify_freshness
from app.services.research_planner import plan_research


def test_current_weather_requires_fresh_web():
    decision = classify_freshness("Какая погода в Москве сегодня?")
    assert decision.required is True
    assert decision.category == "weather"
    assert decision.max_age_seconds <= 15 * 60


def test_market_rate_requires_multiple_independent_hosts():
    decision = classify_freshness("Какой сейчас курс доллара?")
    assert decision.required is True
    assert decision.category == "market"
    assert decision.min_independent_hosts >= 2


def test_current_price_is_not_answered_from_model_memory():
    decision = classify_freshness("Сколько сейчас стоит подписка сервиса?")
    assert decision.required is True
    assert decision.category == "price"


def test_current_official_role_requires_freshness():
    decision = classify_freshness("Кто сейчас генеральный директор компании?")
    assert decision.required is True
    assert decision.category == "official_role"


def test_software_release_requires_web():
    decision = classify_freshness("Какая последняя версия PostgreSQL?")
    assert decision.required is True
    assert decision.category == "software_version"


def test_current_law_requires_evidence():
    decision = classify_freshness("Какие сейчас действуют требования закона к этой услуге?")
    assert decision.required is True
    assert decision.category == "law"


def test_stable_definition_does_not_waste_research():
    decision = classify_freshness("Что такое инфляция и как она работает?")
    assert decision.required is False
    assert decision.category == "stable"


def test_historical_question_does_not_force_current_search():
    decision = classify_freshness("Кто был президентом США в 1995 году?")
    assert decision.required is False


def test_price_definition_is_stable_without_current_value():
    decision = classify_freshness("Что такое цена и как она формируется?")
    assert decision.required is False


def test_general_recency_marker_routes_to_research():
    decision = classify_freshness("Какие последние данные по этому проекту опубликованы?")
    assert decision.required is True
    assert decision.category == "recent_general"


def test_research_planner_skips_discovery_for_stable_question():
    plan = plan_research("Объясни принцип работы REST API")
    assert plan.freshness == "stable"
    assert len(plan.queries) == 1


def test_research_planner_builds_current_queries_for_latest_version():
    plan = plan_research("Какая последняя версия Python?")
    assert plan.freshness == "current"
    assert plan.freshness_category == "software_version"
    assert any("release" in item.lower() for item in plan.queries)


def test_source_context_uses_canonical_freshness_router():
    source = Path("app/services/source_context.py").read_text(encoding="utf-8")
    assert "from app.services.freshness import classify_freshness" in source
    assert "verdict = classify_freshness(query)" in source
    assert "max_snapshot_age_seconds" in source
    assert "minimum_independent_hosts" in source


def test_browser_no_longer_owns_freshness_regex():
    ui = Path("app/user_ui.py").read_text(encoding="utf-8")
    assert "function needsFresh" not in ui
    assert "run.plan&&run.plan.freshness" in ui
    assert "freshness!=='current'" in ui


def test_auto_mode_asks_backend_plan_while_forced_mode_still_works():
    ui = Path("app/user_ui.py").read_text(encoding="utf-8")
    assert "research(text,false)" in ui
    assert "research(text,true)" in ui
    assert "webMode==='off'" not in ui or "else if(webMode==='auto')" in ui
