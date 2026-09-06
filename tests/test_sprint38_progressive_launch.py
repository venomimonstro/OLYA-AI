from __future__ import annotations

from types import SimpleNamespace

import pytest
from alembic.config import Config
from alembic.script import ScriptDirectory

from app.main import app
from app.services.progressive_launch import (
    ROLLOUT_STAGES,
    advance_rollout,
    approve_catalog,
    assignment_bucket,
    build_measured_plan_catalog,
    create_rollout,
    rollout_allows_user,
)


def settings(**overrides):
    data = {
        "capacity_compute_headroom_ratio": 1.25,
        "capacity_min_monthly_compute_seconds": 300,
        "capacity_max_monthly_compute_seconds": 14400,
        "default_monthly_compute_seconds": 600,
        "default_max_concurrent_jobs": 1,
        "monthly_server_cost_rub": 4000.0,
        "commerce_cpu_microunits_per_second": 1000,
        "plan_ratio_free": 0.25,
        "plan_ratio_x1": 1.0,
        "plan_ratio_pro": 2.0,
        "plan_ratio_max": 4.0,
        "plan_ratio_business": 8.0,
        "plan_share_fast": 0.20,
        "plan_share_work": 0.35,
        "plan_share_deep": 0.25,
        "plan_share_api": 0.10,
        "plan_share_image": 0.05,
        "plan_share_sandbox": 0.05,
        "public_launch_enforce_exposure": False,
        "public_launch_max_requests_per_user_hour": 120,
    }
    data.update(overrides)
    return SimpleNamespace(**data)


def beta(*, ready=True, minutes=20.0):
    return {
        "readiness": {"capacity_recalibration_ready": ready},
        "compute_minutes_per_active_user": minutes,
        "metrics": {
            "verified_request_success_per_cpu_minute": 1.3,
            "completed_task_success_per_cpu_minute": 0.7,
        },
    }


def capacity(status="passed"):
    return {"status": status, "recommendation": {"max_concurrent_generations": 1}}


def test_assignment_is_deterministic_and_100_percent_allows_every_user():
    assert assignment_bucket("user-1", "salt") == assignment_bucket("user-1", "salt")
    rollout = SimpleNamespace(state="active", exposure_percent=100, assignment_salt="salt")
    assert all(rollout_allows_user(rollout, f"user-{i}") for i in range(100))


def test_measured_catalog_is_fail_closed_before_real_beta_gate():
    measured = build_measured_plan_catalog(beta(ready=False), capacity(), settings())
    assert measured["status"] == "blocked"
    assert "beta_sample_not_sufficient" in measured["blockers"]


def test_measured_catalog_uses_beta_baseline_and_normalized_fairness():
    measured = build_measured_plan_catalog(beta(minutes=20), capacity(), settings())
    assert measured["status"] == "ready"
    catalog = measured["catalog"]
    assert catalog["free"]["monthly_cpu_seconds"] < catalog["x1"]["monthly_cpu_seconds"]
    assert catalog["x1"]["monthly_cpu_seconds"] <= catalog["pro"]["monthly_cpu_seconds"]
    shares = catalog["x1"]["channel_shares"]
    assert abs(sum(shares.values()) - 1.0) < 0.00001
    assert set(shares) == {"fast", "work", "deep", "api", "image_worker", "sandbox"}


def test_catalog_approval_requires_draft_and_no_blockers():
    blocked = SimpleNamespace(status="draft", catalog={"free":{}}, guardrails={"blockers":["beta"]}, approved_by=None, approved_at=None)
    with pytest.raises(ValueError):
        approve_catalog(blocked, "admin")
    ready = SimpleNamespace(status="draft", catalog={"free":{"monthly_cpu_seconds":300}}, guardrails={"blockers":[]}, approved_by=None, approved_at=None)
    approve_catalog(ready, "admin")
    assert ready.status == "approved"
    assert ready.approved_by == "admin"


class FakeDB:
    def __init__(self, scalar_value=None, get_value=None):
        self.scalar_value = scalar_value
        self.get_value = get_value
        self.added = []
    def scalar(self, _statement):
        return self.scalar_value
    def add(self, row):
        self.added.append(row)
    def flush(self):
        return None
    def get(self, _model, _id):
        return self.get_value


def test_duplicate_planned_rollout_is_rejected():
    existing = SimpleNamespace(version=4, state="planned")
    with pytest.raises(ValueError, match="still planned"):
        create_rollout(FakeDB(scalar_value=existing), baseline={}, actor_id="admin")


def test_rollout_only_accepts_declared_stages():
    current = SimpleNamespace(state="planned", exposure_percent=0, assignment_salt="salt", baseline_metrics={}, guardrails={}, id="r0")
    with pytest.raises(ValueError, match="Exposure must be one of"):
        advance_rollout(FakeDB(scalar_value=1), current, exposure_percent=7, evaluation={"status":"stable"}, actor_id="admin")
    assert ROLLOUT_STAGES == (1, 5, 10, 25, 50, 100)


def test_100_percent_rollout_remains_active_for_watchdog():
    current = SimpleNamespace(state="active", exposure_percent=50, assignment_salt="salt", baseline_metrics={}, guardrails={}, id="r50")
    db = FakeDB(scalar_value=7)
    row = advance_rollout(db, current, exposure_percent=100, evaluation={"status":"stable"}, actor_id="admin")
    assert row.state == "active"
    assert row.exposure_percent == 100
    assert row.completed_at is not None


def test_sprint38_routes_ui_and_version_are_registered():
    paths = {getattr(route, "path", "") for route in app.routes}
    assert app.version == "0.38.0"
    for path in (
        "/v1/launch/eligibility",
        "/v1/admin/launch/status",
        "/v1/admin/launch/catalogs/propose",
        "/v1/admin/launch/rollouts/{rollout_id}/advance",
        "/v1/admin/launch/breakers",
        "/admin/launch",
    ):
        assert path in paths


def test_public_exposure_is_fail_safe_off_by_default():
    assert settings().public_launch_enforce_exposure is False
    assert settings().public_launch_max_requests_per_user_hour == 120


def test_sprint38_has_single_alembic_head():
    config = Config("alembic.ini")
    assert ScriptDirectory.from_config(config).get_heads() == ["f38f09c1b437"]
