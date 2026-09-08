from pathlib import Path

from app.services.budget_transparency import estimated_reserve_seconds, warning_for


def test_warning_thresholds_are_preemptive():
    assert warning_for(69.9) is None
    assert warning_for(70)["level"] == "notice"
    assert warning_for(85)["level"] == "high"
    assert warning_for(95)["level"] == "critical"


def test_mode_reserve_is_bounded_and_verification_visible():
    assert estimated_reserve_seconds("fast", 0) == 15
    assert estimated_reserve_seconds("work", 1) == 120
    assert estimated_reserve_seconds("deep", 2) == 540
    assert estimated_reserve_seconds("deep", 99) == 540


def test_usage_api_has_current_budget_and_preflight():
    source = Path("app/api/routes/usage.py").read_text(encoding="utf-8")
    assert '@router.get("/budget")' in source
    assert '@router.post("/budget-preview")' in source
    assert "choose_route" in source
    assert "plan_verification" in source
    assert "fits_remaining_compute" in Path("app/services/budget_transparency.py").read_text(encoding="utf-8")


def test_user_ui_preflights_before_chat_and_shows_budget():
    source = Path("app/user_ui.py").read_text(encoding="utf-8")
    assert "id=\"budget\"" in source
    assert "/v1/usage/budget-preview" in source
    assert "await previewBudget(text)" in source
    assert source.index("await previewBudget(text)") < source.index("const data=await streamChat", source.index("async function submit"))
    assert "refreshBudget()" in source
    assert "fits_remaining_compute===false" in source


def test_compute_breakdown_is_persisted_from_canonical_usage_event():
    source = Path("app/services/diagnostics.py").read_text(encoding="utf-8")
    model = Path("app/models_sprint53.py").read_text(encoding="utf-8")
    assert "_record_breakdown_from_usage" in source
    assert "total_ms = max(0, int(usage.inference_ms or 0))" in source
    assert "wasted_ms" in model
    assert "UniqueConstraint(\"request_id\"" in model
    assert "call_equivalent_estimate" in source


def test_no_mid_response_budget_cutoff_was_added_to_chat_loop():
    source = Path("app/api/routes/chat.py").read_text(encoding="utf-8")
    ensure_at = source.index("ensure_compute_available")
    inference_at = source.index("async with request.app.state.user_governor.slot")
    assert ensure_at < inference_at
    loop = source[inference_at:]
    assert "ensure_compute_available(" not in loop


def test_admin_operations_exposes_success_cost_waste_and_modes():
    source = Path("app/services/operations_analytics.py").read_text(encoding="utf-8")
    for marker in (
        '"compute_economics"',
        '"compute_ms_per_successful_answer"',
        '"wasted_ms"',
        '"verification_share"',
        '"by_mode"',
        '"top_compute_users"',
    ):
        assert marker in source


def test_migration_is_linear_after_sprint51():
    source = Path("alembic/versions/f53b21e7c4a0_add_compute_breakdown_events.py").read_text(encoding="utf-8")
    assert 'revision = "f53b21e7c4a0"' in source
    assert 'down_revision = "f51c0a11d9e2"' in source
