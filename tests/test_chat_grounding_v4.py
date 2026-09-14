from scripts.chat_grounding_audit import audit
from app.services.fast_web_grounding import should_auto_ground
from app.services.conditional_verification import plan_verification
from app.workspace_client_v4 import enhance_workspace_v4


def test_chat_grounding_v4_audit_passes():
    result = audit()
    assert result["status"] == "passed", result["errors"]


def test_auto_web_checks_facts_but_not_creative_rewrites():
    assert should_auto_ground("Кто сейчас руководит этой компанией?") is True
    assert should_auto_ground("Сравни актуальные характеристики этих моделей") is True
    assert should_auto_ground("Перепиши этот абзац короче") is False
    assert should_auto_ground("Придумай пять названий для магазина") is False


def test_ordinary_grounded_work_answer_does_not_need_second_llm_critic():
    plan = plan_verification(
        verification="auto",
        user_text="Какая сейчас цена подписки?",
        route_mode="work",
        requirements=(),
        freshness_required=True,
        verified_source_count=3,
    )
    assert plan.risk_score == 3
    assert plan.run_critic is False


def test_workspace_v4_is_idempotent_and_keeps_light_interactions():
    base = '<html><head><script nonce="abc"></script></head><body><section id="view-chat"><div id="messages"></div></section><button id="send"></button><div id="state"></div></body></html>'
    once = enhance_workspace_v4(base)
    twice = enhance_workspace_v4(once)
    assert once == twice
    assert "OLYA_WORKSPACE_CLIENT_V4" in once
    assert "background:#e9e9ec!important" in once
    assert "olya-waiting-mark" in once
    assert "olya-source-chip" in once
    assert 'nonce="abc"' in once
