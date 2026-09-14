"""API route package bootstrap.

Runtime chat quality guards are installed before individual route modules import
and bind inference functions. Keeping this at the package boundary makes the
policy apply consistently to normal chat, streaming chat and verification calls.
"""

from app.runtime_quality_patch import install_runtime_quality_patch
from app.qwen4b_runtime_patch import install_qwen4b_runtime_patch
from app.reasoning_budget_patch import install_reasoning_budget_patch
from app.gigachat31_runtime_patch import install_gigachat31_runtime_patch
from app.work_quality_floor_patch import install_work_quality_floor_patch
from app.current_fact_latency_patch import install_current_fact_latency_patch
from app.chat_reliability_patch import install_chat_reliability_patch
from app.response_policy_patch import install_response_policy_patch
from app.search_quality_patch import install_search_quality_patch
from app.fresh_search_policy_patch import install_fresh_search_policy_patch
from app.ultrafast_fresh_web_patch import install_ultrafast_fresh_web_patch
from app.atomic_fact_latency_patch import install_atomic_fact_latency_patch
from app.live_structured_fact_patch import install_live_structured_fact_patch
from app.live_location_parser_patch import install_live_location_parser_patch
from app.current_fact_evidence_guard import install_current_fact_evidence_guard
from app.stable_fact_evidence_guard import install_stable_fact_evidence_guard
from app.structured_fact_answer_guard import install_structured_fact_answer_guard
from app.structured_governor_bypass_patch import install_structured_governor_bypass_patch
from app.reasoning_privacy_patch import install_reasoning_privacy_patch
from app.quality_evidence_policy_patch import install_quality_evidence_policy_patch
from app.interactive_verification_policy_patch import install_interactive_verification_policy_patch
from app.high_risk_verification_patch import install_high_risk_verification_patch
from app.testing_unlimited_usage_patch import install_testing_unlimited_usage_patch
from app.identity_patch import install_identity_patch
from app.admin_surface_patch import install_admin_surface_patch
from app.tester_access_patch import install_tester_access_patch

install_runtime_quality_patch()
install_qwen4b_runtime_patch()
install_reasoning_budget_patch()
install_gigachat31_runtime_patch()
install_work_quality_floor_patch()
install_current_fact_latency_patch()
install_chat_reliability_patch()
install_response_policy_patch()
install_search_quality_patch()
install_fresh_search_policy_patch()
install_ultrafast_fresh_web_patch()
install_atomic_fact_latency_patch()
install_live_structured_fact_patch()
install_live_location_parser_patch()
install_current_fact_evidence_guard()
install_stable_fact_evidence_guard()
install_structured_fact_answer_guard()
install_structured_governor_bypass_patch()
install_reasoning_privacy_patch()
install_quality_evidence_policy_patch()
install_interactive_verification_policy_patch()
install_high_risk_verification_patch()
install_testing_unlimited_usage_patch()
install_identity_patch()
install_admin_surface_patch()
install_tester_access_patch()

from app import public_ui as _public_ui
from app.admin_login_ui import router as _admin_login_router

_public_ui.router.include_router(_admin_login_router)

_original_public_page = _public_ui._page
if not getattr(_original_public_page, "_x1_creator_credit", False):
    def _public_page_with_creator(*args, **kwargs):
        if len(args) >= 3 and isinstance(args[2], str):
            body = args[2]
            if "</footer>" in body and "Лысенко Артём" not in body:
                body = body.replace(
                    "</footer>",
                    '<div class="wrap" style="padding-top:8px">Создатель проекта — Лысенко Артём</div></footer>',
                    1,
                )
                args = (*args[:2], body, *args[3:])
        return _original_public_page(*args, **kwargs)

    _public_page_with_creator._x1_creator_credit = True
    _public_ui._page = _public_page_with_creator
