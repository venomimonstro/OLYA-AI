from __future__ import annotations

from types import SimpleNamespace

import pytest
from alembic.config import Config
from alembic.script import ScriptDirectory

from app.main import app
from app.services.adaptive_capacity import apply_live_safe, approve_plan, compare_plans, compare_wave_signals, evaluate_wave, signal_snapshot
from app.services.beta_trends import compare_snapshots


def settings(**overrides):
    data={"beta_wave_min_observation_requests":50,"beta_min_request_success_rate":0.97,"beta_max_frustration_per_request":0.05,"beta_max_p95_queue_ms":5000,"beta_wave_max_queue_regression_ratio":1.5,"beta_wave_max_duration_regression_ratio":1.5,"beta_wave_min_cpu_efficiency_ratio":0.70,"beta_trend_max_success_drop":0.02,"beta_trend_max_frustration_increase":0.02,"beta_trend_max_quality_drop":0.05}
    data.update(overrides); return SimpleNamespace(**data)


def snapshot(*,requests:int,successes:int,frustration:int,queue:int=1000,duration:int=20000,efficiency:float=1.0,compute_minutes:float=10.0):
    return {"request_count":requests,"success_count":successes,"frustration_count":frustration,"task_count":0,"completed_task_count":0,"compute_minutes_total":compute_minutes,"p95_queue_ms":queue,"p95_duration_ms":duration,"metrics":{"request_success_rate":successes/requests if requests else 0,"frustration_per_request":frustration/requests if requests else 0,"verified_request_success_per_cpu_minute":efficiency,"completed_task_success_per_cpu_minute":1.0,"quality_supported_request_rate":0.9,"d1_retention":0.5,"d7_retention":0.4,"d30_retention":0.3}}


def wave(baseline:dict,*,compute_budget_seconds:int=0):
    return SimpleNamespace(state="open",admission_paused=False,admitted_count=3,target_participants=10,compute_budget_seconds=compute_budget_seconds,baseline_metrics=signal_snapshot(baseline),latest_metrics=signal_snapshot(baseline),decision={},pause_reason="",observing_at=None)


def test_wave_guardrail_allows_stable_increment():
    baseline=snapshot(requests=100,successes=98,frustration=2); current=snapshot(requests=150,successes=147,frustration=3,queue=1200,duration=22000,efficiency=0.95)
    result=compare_wave_signals(current,signal_snapshot(baseline),settings()); assert result["enough_observation"] is True and result["regressions"]==[]
    decision=evaluate_wave(wave(baseline),current=current,capacity_report={"status":"passed"},settings=settings()); assert decision["status"]=="admit" and decision["admission_allowed"] is True


def test_compact_wave_baseline_preserves_cpu_efficiency_guardrail():
    baseline=snapshot(requests=100,successes=99,frustration=1,efficiency=1.0); current=snapshot(requests=150,successes=149,frustration=1,efficiency=0.5)
    assert "verified_cpu_efficiency_regressed" in compare_wave_signals(current,signal_snapshot(baseline),settings())["regressions"]


def test_wave_guardrail_pauses_on_new_request_failures():
    baseline=snapshot(requests=100,successes=99,frustration=1); current=snapshot(requests=150,successes=139,frustration=2)
    assert "wave_request_success_below_guardrail" in compare_wave_signals(current,baseline,settings())["regressions"]


def test_wave_guardrail_detects_queue_regression():
    baseline=snapshot(requests=100,successes=99,frustration=1,queue=1000); current=snapshot(requests=150,successes=149,frustration=1,queue=6000)
    result=compare_wave_signals(current,baseline,settings()); assert "p95_queue_above_guardrail" in result["regressions"] and "p95_queue_regressed" in result["regressions"]


def test_wave_compute_budget_stops_new_admissions():
    baseline=snapshot(requests=100,successes=99,frustration=1,compute_minutes=10.0); current=snapshot(requests=120,successes=119,frustration=1,compute_minutes=12.0); target=wave(baseline,compute_budget_seconds=60)
    decision=evaluate_wave(target,current=current,capacity_report={"status":"passed"},settings=settings(),persist=True)
    assert decision["status"]=="paused_for_budget" and "wave_compute_budget_exhausted" in decision["reasons"] and decision["comparison"]["compute_spent_seconds"]==120
    assert target.state=="paused" and target.admission_paused is True


def test_capacity_plan_approval_is_fail_closed():
    plan=SimpleNamespace(status="draft",guardrails={"calibration_status":"collecting_data","blockers":["beta_tasks_below_minimum"]},approved_by=None,approved_at=None)
    with pytest.raises(ValueError): approve_plan(plan,actor_id="admin")
    assert plan.status=="draft"


def test_live_safe_plan_can_reduce_limits_without_restart(tmp_path):
    runtime_settings=SimpleNamespace(max_context_tokens=8192,deep_context_tokens=16384,max_concurrent_generations=1,max_queue_size=64,inference_queue_timeout_seconds=120.0,default_monthly_compute_seconds=600,backup_storage_path=str(tmp_path))
    fake_app=SimpleNamespace(state=SimpleNamespace(settings=runtime_settings,capacity_boot_max_context_tokens=8192,capacity_boot_deep_context_tokens=16384,capacity_boot_max_concurrent_generations=1,context=SimpleNamespace(max_chars=16384*6),governor=SimpleNamespace(max_queue=64,wait_timeout_seconds=120.0)))
    plan=SimpleNamespace(version=2,plan={"max_context_tokens":8192,"deep_context_tokens":12288,"max_concurrent_generations":1,"max_queue_size":8,"inference_queue_timeout_seconds":90.0,"default_monthly_compute_seconds":900},requires_restart=True,runtime_applied=False)
    result=apply_live_safe(fake_app,plan); assert result["runtime_applied"] is True and runtime_settings.deep_context_tokens==12288 and fake_app.state.governor.max_queue==8 and plan.requires_restart is False


def test_plan_above_boot_ceiling_requires_restart(tmp_path):
    runtime_settings=SimpleNamespace(max_context_tokens=8192,deep_context_tokens=8192,max_concurrent_generations=1,max_queue_size=8,inference_queue_timeout_seconds=90.0,default_monthly_compute_seconds=600,backup_storage_path=str(tmp_path))
    fake_app=SimpleNamespace(state=SimpleNamespace(settings=runtime_settings,capacity_boot_max_context_tokens=8192,capacity_boot_deep_context_tokens=8192,capacity_boot_max_concurrent_generations=1,context=SimpleNamespace(max_chars=8192*6),governor=SimpleNamespace(max_queue=8,wait_timeout_seconds=90.0)))
    plan=SimpleNamespace(version=3,plan={"max_context_tokens":8192,"deep_context_tokens":16384,"max_concurrent_generations":1},requires_restart=False,runtime_applied=True)
    result=apply_live_safe(fake_app,plan); assert result["requires_restart"] is True and result["runtime_applied"] is False and runtime_settings.deep_context_tokens==8192


def test_plan_diff_is_explicit_and_version_friendly():
    result=compare_plans({"deep_context_tokens":12288,"max_queue_size":8},{"deep_context_tokens":8192,"max_queue_size":8})
    assert result["changed"] is True and result["changes"]["deep_context_tokens"]=={"from":8192,"to":12288}


def test_trend_anomaly_detects_quality_and_latency_regression():
    previous=snapshot(requests=100,successes=99,frustration=1,queue=1000,duration=20000,efficiency=1.0); current=snapshot(requests=120,successes=115,frustration=6,queue=3000,duration=40000,efficiency=0.5)
    current["id"]="new"; previous["id"]="old"; current["metrics"]["quality_supported_request_rate"]=0.7; previous["metrics"]["quality_supported_request_rate"]=0.9
    result=compare_snapshots(current,previous,settings()); assert result["status"]=="regressed" and "quality_supported_rate_regressed" in result["anomalies"] and "p95_queue_regressed" in result["anomalies"] and "verified_cpu_efficiency_regressed" in result["anomalies"]


def test_sprint37_routes_remain_registered_on_newer_versions():
    paths={getattr(route,"path","") for route in app.routes}
    assert tuple(map(int,app.version.split(".")[:2])) >= (0,37)
    for path in ("/admin/beta","/v1/admin/beta/control","/v1/admin/beta/waves","/v1/admin/beta/trends","/v1/admin/beta/feedback","/v1/admin/beta/capacity-plans/propose","/v1/admin/beta/capacity-plans/rollback"): assert path in paths


def test_sprint37_revision_remains_in_current_alembic_history():
    script=ScriptDirectory.from_config(Config("alembic.ini")); revision=script.get_revision("f37e6b04a325")
    assert revision is not None
    assert len(script.get_heads())==1
