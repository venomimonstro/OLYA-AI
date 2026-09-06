from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

from app.main import app
from app.services.capacity import build_launch_calibration, read_capacity_report
from scripts.capacity_calibrate import recommendation, summarize_tier


def settings(tmp_path: Path):
    return SimpleNamespace(capacity_report_path=str(tmp_path/"capacity-latest.json"),capacity_report_max_age_hours=168.0,beta_min_participants=50,beta_max_participants=100,beta_min_tasks=500,beta_min_request_success_rate=0.97,beta_max_frustration_per_request=0.05,beta_max_p95_queue_ms=5000,capacity_compute_headroom_ratio=1.25,capacity_min_monthly_compute_seconds=300,capacity_max_monthly_compute_seconds=14400,default_monthly_compute_seconds=600,max_queue_size=64,inference_queue_timeout_seconds=120.0,deep_context_tokens=16384)


def test_capacity_report_rejects_stale_and_accepts_current(tmp_path: Path):
    cfg=settings(tmp_path); path=Path(cfg.capacity_report_path)
    payload={"status":"passed","finished_at":datetime.now(timezone.utc).isoformat(),"recommendation":{"deep_context_tokens":12288,"max_concurrent_generations":1,"max_queue_size":4,"inference_queue_timeout_seconds":90}}
    path.write_text(json.dumps(payload),"utf-8"); assert read_capacity_report(cfg)["status"]=="passed"
    payload["finished_at"]=(datetime.now(timezone.utc)-timedelta(days=8)).isoformat(); path.write_text(json.dumps(payload),"utf-8")
    stale=read_capacity_report(cfg); assert stale["status"]=="degraded" and "stale" in stale["reasons"]


def test_capacity_recommendation_selects_largest_passing_tier():
    candidates=[{"tier":8192,"status":"passed","p95_latency_ms":30000},{"tier":12288,"status":"passed","p95_latency_ms":60000},{"tier":16384,"status":"failed","p95_latency_ms":90000}]
    plan=recommendation(candidates,180); assert plan["deep_context_tokens"]==12288 and plan["max_concurrent_generations"]==1


def test_capacity_candidate_fails_on_swap_or_memory_pressure():
    run={"status":"passed","model_latency_ms":40000,"peak_llama_rss_bytes":20_000_000_000,"swap_growth_bytes":400*1024*1024,"min_host_mem_available_bytes":2*1024*1024*1024}
    result=summarize_tier(16384,[run],min_headroom_bytes=3*1024*1024*1024,max_swap_growth_bytes=256*1024*1024,max_latency_ms=300000)
    assert result["status"]=="failed" and "memory_headroom_low" in result["reasons"] and "swap_growth_high" in result["reasons"]


def test_beta_capacity_plan_uses_measured_compute_only_after_data_gate(tmp_path: Path):
    cfg=settings(tmp_path); capacity={"status":"passed","recommendation":{"deep_context_tokens":12288,"max_concurrent_generations":1,"max_queue_size":6,"inference_queue_timeout_seconds":100}}
    beta={"enrolled_count":60,"task_count":550,"request_count":1000,"compute_minutes_per_active_user":20,"p95_queue_ms":3000,"p95_duration_ms":60000,"metrics":{"request_success_rate":0.99,"frustration_per_request":0.02,"d1_retention":0.5,"d7_retention":0.35,"d30_retention":0.2},"readiness":{"capacity_recalibration_ready":True}}
    result=build_launch_calibration(beta,capacity,cfg)
    assert result["status"]=="ready" and result["plan"]["deep_context_tokens"]==12288 and result["plan"]["default_monthly_compute_seconds"]==1500


def test_insufficient_beta_never_freezes_measured_limits(tmp_path: Path):
    cfg=settings(tmp_path); beta={"enrolled_count":12,"task_count":40,"request_count":80,"compute_minutes_per_active_user":50,"p95_queue_ms":0,"metrics":{"request_success_rate":1.0,"frustration_per_request":0.0},"readiness":{"capacity_recalibration_ready":False}}
    capacity={"status":"passed","recommendation":{"deep_context_tokens":8192,"max_concurrent_generations":1,"max_queue_size":4}}
    result=build_launch_calibration(beta,capacity,cfg); assert result["status"]=="collecting_data" and result["plan"]["default_monthly_compute_seconds"]==cfg.default_monthly_compute_seconds


def test_sprint36_admin_routes_remain_registered_on_newer_versions():
    paths={getattr(route,"path","") for route in app.routes}
    assert "/v1/admin/beta/current" in paths and "/v1/admin/beta/calibration" in paths
    assert tuple(map(int,app.version.split(".")[:2])) >= (0,36)
