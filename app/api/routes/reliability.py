from __future__ import annotations

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_db
from app.models import PublicRollout, SystemHealthSnapshot, User
from app.services.adaptive_capacity import active_plan, plan_dict
from app.services.admin import audit, require_admin
from app.services.business_contract import evaluate_business_contract
from app.services.capacity import read_capacity_report
from app.services.progressive_launch import active_measured_catalog, catalog_dict, evaluate_public_launch, rollout_dict
from app.services.system_observability import STABLE, collect_system_health, latest_checkpoints

router = APIRouter(prefix="/v1/admin/reliability", tags=["admin-reliability"])


@router.get("/status")
async def reliability_status(request: Request, refresh: bool = Query(default=True), deep: bool = Query(default=False), admin: User = Depends(require_admin), db: Session = Depends(get_db)) -> dict:
    stale = int(getattr(request.app.state.settings, "health_checkpoint_stale_seconds", 300))
    if refresh:
        result = await collect_system_health(request.app, db, persist=True, deep=deep)
        audit(db, admin, "reliability.refresh", "system", "x1", {"deep": deep, "status": result["status"]})
        db.commit()
        result["checkpoints"] = latest_checkpoints(db, stale_after_seconds=stale)
        return result
    checkpoints = latest_checkpoints(db, stale_after_seconds=stale)
    latest = db.scalar(select(SystemHealthSnapshot).order_by(SystemHealthSnapshot.created_at.desc()).limit(1))
    return {"status": latest.overall_status if latest else "unknown", "score": latest.score if latest else 0, "stable": latest.stable_count if latest else 0, "degraded": latest.degraded_count if latest else 0, "failed": latest.failed_count if latest else 0, "critical": latest.failed_count if latest else 0, "unknown": sum(x.get("status") == "unknown" for x in checkpoints), "critical_failed": latest.critical_failed_count if latest else 0, "checked_at": latest.created_at if latest else None, "snapshot_id": latest.id if latest else None, "checkpoints": checkpoints}


@router.post("/run")
async def run_deep_reliability_check(request: Request, admin: User = Depends(require_admin), db: Session = Depends(get_db)) -> dict:
    result = await collect_system_health(request.app, db, persist=True, deep=True)
    audit(db, admin, "reliability.deep_run", "system", "x1", {"status": result["status"], "score": result["score"]})
    db.commit()
    result["checkpoints"] = latest_checkpoints(db, stale_after_seconds=int(getattr(request.app.state.settings, "health_checkpoint_stale_seconds", 300)))
    return result


@router.get("/business-contract")
def business_contract(request: Request, admin: User = Depends(require_admin), db: Session = Depends(get_db)) -> dict:
    _ = admin
    return evaluate_business_contract(db, request.app.state.settings)


def _runtime_capacity_match(request: Request, plan) -> tuple[bool, dict]:
    settings = request.app.state.settings
    desired = dict(plan.plan or {})
    actual = {
        "max_context_tokens": int(settings.max_context_tokens),
        "deep_context_tokens": int(settings.deep_context_tokens),
        "max_concurrent_generations": int(settings.max_concurrent_generations),
        "max_queue_size": int(settings.max_queue_size),
        "inference_queue_timeout_seconds": float(settings.inference_queue_timeout_seconds),
        "default_monthly_compute_seconds": int(settings.default_monthly_compute_seconds),
    }
    mismatches = {}
    for key, expected in desired.items():
        if key not in actual:
            continue
        observed = actual[key]
        equal = abs(float(expected) - observed) < 0.001 if isinstance(observed, float) else int(expected) == observed
        if not equal:
            mismatches[key] = {"expected": expected, "actual": observed}
    return not mismatches, {"actual": actual, "mismatches": mismatches}


@router.get("/release-readiness")
async def release_readiness(request: Request, refresh: bool = Query(default=True), admin: User = Depends(require_admin), db: Session = Depends(get_db)) -> dict:
    """Strict decision for a new public production release."""
    if refresh:
        result = await collect_system_health(request.app, db, persist=True, deep=True)
        audit(db, admin, "reliability.release_readiness", "system", "x1", {"status": result["status"]})
        db.commit()
        checks = result["checks"]
    else:
        result = {}
        checks = latest_checkpoints(db, stale_after_seconds=int(getattr(request.app.state.settings, "health_checkpoint_stale_seconds", 300)))

    by_key = {item.get("key"): item for item in checks}
    required = ["core.database", "core.schema", "core.configuration", "core.storage", "core.inference", "core.migrations", "runtime.queue", "product.route_contract", "quality.release_gate", "ops.backup", "ops.restore_drill", "ops.release_gate"]
    blockers: list[dict] = []
    for key in required:
        item = by_key.get(key)
        if item is None:
            blockers.append({"key": key, "status": "missing", "message": "Required release checkpoint is missing"})
        elif item.get("status") != STABLE:
            blockers.append({"key": key, "status": item.get("status"), "message": item.get("message", ""), "root_cause": item.get("root_cause", ""), "recommended_action": (item.get("details") or {}).get("recommended_action", item.get("recommended_action", ""))})

    business = evaluate_business_contract(db, request.app.state.settings)
    if business.get("status") != "passed":
        blockers.append({
            "key": "business.logic_contract",
            "status": "failed",
            "message": "Business logic contract has cross-domain invariant violations",
            "recommended_action": "Resolve billing/quota/org/API/project/task/agent invariant violations before release.",
            "reasons": (business.get("blockers") or [])[:50],
        })

    capacity = read_capacity_report(request.app.state.settings)
    if capacity.get("status") != "passed":
        blockers.append({"key": "ops.capacity_calibration", "status": capacity.get("status", "missing"), "message": "Target-node capacity calibration is not current", "recommended_action": "Run python3 scripts/capacity_calibrate.py on the deployed CPU/RAM node.", "reasons": capacity.get("reasons") or []})

    capacity_plan = active_plan(db)
    runtime_match = None
    if capacity_plan is None:
        blockers.append({"key": "ops.capacity_plan", "status": "missing", "message": "No approved active capacity plan exists", "recommended_action": "Build, approve and activate a capacity plan from measured beta + target-node calibration data."})
    else:
        matches, runtime_match = _runtime_capacity_match(request, capacity_plan)
        if not matches:
            blockers.append({"key": "ops.capacity_plan", "status": "degraded", "message": "Active capacity plan does not match the running configuration", "recommended_action": "Apply the generated capacity-plan env artifact and restart the affected runtime before release.", "mismatches": runtime_match["mismatches"]})

    measured_catalog = active_measured_catalog(db)
    if measured_catalog is None:
        blockers.append({"key": "ops.measured_plan_catalog", "status": "missing", "message": "Measured Free/X1/Pro/Max/Business catalog is not active", "recommended_action": "Propose, review, approve and activate the catalog from closed-beta measurements before public launch."})

    launch_evaluation = evaluate_public_launch(db, request.app.state.settings, persist=False)
    exposure_enforced = bool(getattr(request.app.state.settings, "public_launch_enforce_exposure", False))
    latest_rollout = db.scalar(select(PublicRollout).order_by(PublicRollout.version.desc()).limit(1))
    if exposure_enforced:
        if latest_rollout is None or latest_rollout.state not in {"active", "complete"}:
            blockers.append({"key": "ops.public_rollout", "status": "missing", "message": "Public exposure enforcement is enabled without an active/completed rollout", "recommended_action": "Create the rollout and advance it only while public-launch guardrails are green."})
        if launch_evaluation.get("status") != "stable":
            blockers.append({"key": "ops.public_launch_guardrails", "status": "degraded", "message": "Public launch watchdog has blockers", "recommended_action": "Resolve circuit breakers/system regressions before increasing public exposure.", "reasons": launch_evaluation.get("blockers") or []})

    required_extra = ["business.logic_contract", "ops.capacity_calibration", "ops.capacity_plan", "ops.measured_plan_catalog"]
    if exposure_enforced:
        required_extra += ["ops.public_rollout", "ops.public_launch_guardrails"]
    return {
        "ready_for_public_release": not blockers,
        "app_version": str(getattr(request.app, "version", "unknown")),
        "required_checkpoints": [*required, *required_extra],
        "business_contract": business,
        "capacity": capacity,
        "capacity_plan": plan_dict(capacity_plan) if capacity_plan else None,
        "capacity_runtime_match": runtime_match,
        "measured_plan_catalog": catalog_dict(measured_catalog) if measured_catalog else None,
        "public_rollout": rollout_dict(latest_rollout) if latest_rollout else None,
        "public_launch_evaluation": launch_evaluation,
        "blockers": blockers,
        "checked_at": result.get("checked_at") if refresh else None,
    }


@router.get("/history")
def reliability_history(limit: int = Query(default=50, ge=1, le=200), admin: User = Depends(require_admin), db: Session = Depends(get_db)) -> list[dict]:
    rows = list(db.scalars(select(SystemHealthSnapshot).order_by(SystemHealthSnapshot.created_at.desc()).limit(limit)).all())
    return [{"id": x.id, "status": x.overall_status, "score": x.score, "stable": x.stable_count, "degraded": x.degraded_count, "failed": x.failed_count, "critical_failed": x.critical_failed_count, "created_at": x.created_at} for x in rows]
