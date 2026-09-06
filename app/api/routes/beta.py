from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_db
from app.models import BetaParticipant, BetaSnapshot, BetaWave, CapacityPlan, User
from app.services.adaptive_capacity import (
    active_plan,
    active_wave,
    activate_plan,
    approve_plan,
    compare_wave_signals,
    create_wave,
    evaluate_wave,
    plan_dict,
    propose_plan,
    record_wave_admission,
    rollback_plan,
    wave_dict,
)
from app.services.admin import audit, require_admin
from app.services.beta import build_beta_snapshot, calculate_beta_metrics, snapshot_dict
from app.services.capacity import build_launch_calibration, read_capacity_report

router = APIRouter(prefix="/v1/admin/beta", tags=["admin-beta"])


def _now() -> datetime:
    return datetime.now(timezone.utc)


class Enroll(BaseModel):
    cohort: str = Field(default="closed-beta-1", min_length=1, max_length=64)
    source: str = Field(default="manual", max_length=64)
    metadata: dict = Field(default_factory=dict)


class State(BaseModel):
    state: str


class WaveCreate(BaseModel):
    cohort: str = Field(default="closed-beta-1", min_length=1, max_length=64)
    target_participants: int = Field(default=10, ge=1, le=100)
    window_days: int = Field(default=30, ge=1, le=180)


class WavePause(BaseModel):
    reason: str = Field(default="manual_pause", min_length=1, max_length=500)


class PlanProposal(BaseModel):
    cohort: str = Field(default="closed-beta-1", min_length=1, max_length=64)
    window_days: int = Field(default=30, ge=1, le=180)


class RollbackRequest(BaseModel):
    target_plan_id: str | None = None


@router.post("/participants/{user_id}")
def enroll(
    user_id: str,
    payload: Enroll,
    request: Request,
    force: bool = Query(default=False),
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    if db.get(User, user_id) is None:
        raise HTTPException(404, "User not found")

    row = db.scalar(select(BetaParticipant).where(BetaParticipant.user_id == user_id, BetaParticipant.cohort == payload.cohort))
    already_active = bool(row is not None and row.state == "active")
    wave = None
    decision = None

    if not already_active:
        wave = active_wave(db, payload.cohort)
        metrics = calculate_beta_metrics(db, cohort=payload.cohort, window_days=30)
        capacity = read_capacity_report(request.app.state.settings)
        decision = evaluate_wave(wave, current=metrics, capacity_report=capacity, settings=request.app.state.settings, persist=True)
        if not decision["admission_allowed"] and not force:
            audit(
                db,
                admin,
                "beta.admission_blocked",
                "beta_wave",
                wave.id if wave else payload.cohort,
                {"user_id": user_id, "decision": decision},
            )
            db.commit()
            raise HTTPException(status_code=409, detail={"message": "Beta admission is paused", "decision": decision})

    metadata = dict(payload.metadata)
    if wave is not None:
        metadata["wave_id"] = wave.id
        metadata["wave_number"] = wave.wave_number
    if force and not already_active:
        metadata["forced_admission"] = True

    if row is None:
        row = BetaParticipant(
            user_id=user_id,
            cohort=payload.cohort,
            source=payload.source,
            metadata_json=metadata,
        )
        db.add(row)
        db.flush()
    else:
        row.state = "active"
        row.source = payload.source
        row.metadata_json = metadata

    if not already_active and wave is not None:
        record_wave_admission(wave)

    audit(
        db,
        admin,
        "beta.enroll.forced" if force and not already_active else "beta.enroll",
        "beta_participant",
        row.id,
        {"cohort": payload.cohort, "wave_id": wave.id if wave else None, "force": force},
    )
    db.commit()
    return {
        "id": row.id,
        "user_id": row.user_id,
        "cohort": row.cohort,
        "state": row.state,
        "source": row.source,
        "metadata": row.metadata_json,
        "enrolled_at": row.enrolled_at,
        "wave": wave_dict(wave) if wave else None,
        "admission_decision": decision,
    }


@router.patch("/participants/{participant_id}")
def state(participant_id: str, payload: State, admin: User = Depends(require_admin), db: Session = Depends(get_db)):
    if payload.state not in {"active", "paused", "removed"}:
        raise HTTPException(422, "Unsupported beta participant state")
    row = db.get(BetaParticipant, participant_id)
    if row is None:
        raise HTTPException(404, "Beta participant not found")
    row.state = payload.state
    audit(db, admin, "beta.state", "beta_participant", row.id, {"state": row.state})
    db.commit()
    return {"id": row.id, "state": row.state}


@router.get("/participants")
def participants(
    cohort: str = "closed-beta-1",
    limit: int = Query(default=100, ge=1, le=500),
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    _ = admin
    rows = db.scalars(
        select(BetaParticipant).where(BetaParticipant.cohort == cohort).order_by(BetaParticipant.enrolled_at.desc()).limit(limit)
    ).all()
    return [
        {
            "id": row.id,
            "user_id": row.user_id,
            "state": row.state,
            "source": row.source,
            "metadata": row.metadata_json,
            "enrolled_at": row.enrolled_at,
        }
        for row in rows
    ]


@router.get("/current")
def current_metrics(
    cohort: str = "closed-beta-1",
    window_days: int = Query(default=30, ge=1, le=180),
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    _ = admin
    return calculate_beta_metrics(db, cohort=cohort, window_days=window_days)


@router.post("/snapshots")
def snapshot(
    cohort: str = "closed-beta-1",
    window_days: int = Query(default=30, ge=1, le=180),
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    row = build_beta_snapshot(db, cohort=cohort, window_days=window_days)
    audit(db, admin, "beta.snapshot", "beta_snapshot", row.id, {"cohort": cohort})
    db.commit()
    db.refresh(row)
    return snapshot_dict(row)


@router.get("/snapshots")
def snapshots(
    cohort: str = "closed-beta-1",
    limit: int = Query(default=50, ge=1, le=200),
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    _ = admin
    return [
        snapshot_dict(row)
        for row in db.scalars(
            select(BetaSnapshot).where(BetaSnapshot.cohort == cohort).order_by(BetaSnapshot.created_at.desc()).limit(limit)
        ).all()
    ]


@router.get("/calibration")
def launch_calibration(
    request: Request,
    cohort: str = "closed-beta-1",
    window_days: int = Query(default=30, ge=1, le=180),
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    _ = admin
    metrics = calculate_beta_metrics(db, cohort=cohort, window_days=window_days)
    capacity = read_capacity_report(request.app.state.settings)
    calibration = build_launch_calibration(metrics, capacity, request.app.state.settings)
    return {"beta": metrics, "capacity": capacity, "calibration": calibration}


@router.post("/waves")
def create_beta_wave(
    payload: WaveCreate,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    existing = db.scalar(
        select(BetaWave)
        .where(BetaWave.cohort == payload.cohort, BetaWave.state.in_(["planned", "open", "observing", "paused"]))
        .order_by(BetaWave.wave_number.desc())
        .limit(1)
    )
    if existing is not None:
        raise HTTPException(409, f"Wave {existing.wave_number} is still {existing.state}")
    baseline = calculate_beta_metrics(db, cohort=payload.cohort, window_days=payload.window_days)
    try:
        wave = create_wave(
            db,
            cohort=payload.cohort,
            target_participants=payload.target_participants,
            baseline=baseline,
            actor_id=admin.id,
        )
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc
    audit(db, admin, "beta.wave.create", "beta_wave", wave.id, {"cohort": wave.cohort, "target": wave.target_participants})
    db.commit()
    db.refresh(wave)
    return wave_dict(wave)


@router.get("/waves")
def list_beta_waves(
    cohort: str = "closed-beta-1",
    limit: int = Query(default=50, ge=1, le=200),
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    _ = admin
    rows = db.scalars(
        select(BetaWave).where(BetaWave.cohort == cohort).order_by(BetaWave.wave_number.desc()).limit(limit)
    ).all()
    return [wave_dict(row) for row in rows]


@router.post("/waves/{wave_id}/open")
def open_beta_wave(
    wave_id: str,
    request: Request,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    wave = db.get(BetaWave, wave_id)
    if wave is None:
        raise HTTPException(404, "Beta wave not found")
    if wave.state != "planned":
        raise HTTPException(409, "Only a planned beta wave can be opened")
    capacity = read_capacity_report(request.app.state.settings)
    if capacity.get("status") != "passed":
        raise HTTPException(409, {"message": "Target-node capacity report is not current", "capacity": capacity})
    current = calculate_beta_metrics(db, cohort=wave.cohort, window_days=30)
    comparison = compare_wave_signals(current, wave.baseline_metrics, request.app.state.settings)
    if comparison["regressions"]:
        raise HTTPException(409, {"message": "Beta wave cannot be opened while guardrails are failing", "comparison": comparison})
    wave.state = "open"
    wave.admission_paused = False
    wave.pause_reason = ""
    wave.opened_at = _now()
    wave.latest_metrics = comparison["current"]
    wave.decision = {
        "status": "admit",
        "admission_allowed": True,
        "reasons": [],
        "warnings": comparison["warnings"],
        "comparison": comparison,
        "capacity_status": capacity.get("status"),
        "evaluated_at": _now().isoformat(),
    }
    audit(db, admin, "beta.wave.open", "beta_wave", wave.id, {"wave_number": wave.wave_number})
    db.commit()
    return wave_dict(wave)


@router.post("/waves/{wave_id}/evaluate")
def evaluate_beta_wave(
    wave_id: str,
    request: Request,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    wave = db.get(BetaWave, wave_id)
    if wave is None:
        raise HTTPException(404, "Beta wave not found")
    current = calculate_beta_metrics(db, cohort=wave.cohort, window_days=30)
    capacity = read_capacity_report(request.app.state.settings)
    decision = evaluate_wave(wave, current=current, capacity_report=capacity, settings=request.app.state.settings, persist=True)
    audit(db, admin, "beta.wave.evaluate", "beta_wave", wave.id, {"decision": decision["status"]})
    db.commit()
    return {"wave": wave_dict(wave), "decision": decision}


@router.post("/waves/{wave_id}/pause")
def pause_beta_wave(
    wave_id: str,
    payload: WavePause,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    wave = db.get(BetaWave, wave_id)
    if wave is None:
        raise HTTPException(404, "Beta wave not found")
    if wave.state not in {"open", "observing"}:
        raise HTTPException(409, "Only an open or observing wave can be paused")
    wave.state = "paused"
    wave.admission_paused = True
    wave.pause_reason = payload.reason
    audit(db, admin, "beta.wave.pause", "beta_wave", wave.id, {"reason": payload.reason})
    db.commit()
    return wave_dict(wave)


@router.post("/waves/{wave_id}/resume")
def resume_beta_wave(
    wave_id: str,
    request: Request,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    wave = db.get(BetaWave, wave_id)
    if wave is None:
        raise HTTPException(404, "Beta wave not found")
    if wave.state != "paused":
        raise HTTPException(409, "Only a paused wave can be resumed")
    if wave.admitted_count >= wave.target_participants:
        raise HTTPException(409, "Wave target has already been reached; close the observation window instead")
    current = calculate_beta_metrics(db, cohort=wave.cohort, window_days=30)
    capacity = read_capacity_report(request.app.state.settings)
    comparison = compare_wave_signals(current, wave.baseline_metrics, request.app.state.settings)
    if capacity.get("status") != "passed" or comparison["regressions"]:
        raise HTTPException(409, {"message": "Wave guardrails have not recovered", "capacity": capacity, "comparison": comparison})
    wave.state = "open"
    wave.admission_paused = False
    wave.pause_reason = ""
    wave.latest_metrics = comparison["current"]
    audit(db, admin, "beta.wave.resume", "beta_wave", wave.id, {})
    db.commit()
    return wave_dict(wave)


@router.post("/waves/{wave_id}/close")
def close_beta_wave(
    wave_id: str,
    request: Request,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    wave = db.get(BetaWave, wave_id)
    if wave is None:
        raise HTTPException(404, "Beta wave not found")
    if wave.state == "closed":
        return wave_dict(wave)
    current = calculate_beta_metrics(db, cohort=wave.cohort, window_days=30)
    capacity = read_capacity_report(request.app.state.settings)
    decision = evaluate_wave(wave, current=current, capacity_report=capacity, settings=request.app.state.settings, persist=True)
    wave.state = "closed"
    wave.admission_paused = True
    wave.closed_at = _now()
    wave.latest_metrics = decision["comparison"]["current"]
    wave.decision = {**decision, "closed_by_admin": True}
    audit(db, admin, "beta.wave.close", "beta_wave", wave.id, {"decision": decision["status"]})
    db.commit()
    return wave_dict(wave)


@router.get("/control")
def beta_control(
    request: Request,
    cohort: str = "closed-beta-1",
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    _ = admin
    wave = db.scalar(
        select(BetaWave)
        .where(BetaWave.cohort == cohort, BetaWave.state.in_(["planned", "open", "observing", "paused"]))
        .order_by(BetaWave.wave_number.desc())
        .limit(1)
    )
    current = calculate_beta_metrics(db, cohort=cohort, window_days=30)
    capacity = read_capacity_report(request.app.state.settings)
    decision = evaluate_wave(wave, current=current, capacity_report=capacity, settings=request.app.state.settings, persist=False)
    current_plan = active_plan(db)
    return {
        "wave": wave_dict(wave) if wave else None,
        "decision": decision,
        "beta": current,
        "capacity": capacity,
        "active_capacity_plan": plan_dict(current_plan) if current_plan else None,
    }


@router.post("/capacity-plans/propose")
def propose_capacity_plan(
    payload: PlanProposal,
    request: Request,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    metrics = calculate_beta_metrics(db, cohort=payload.cohort, window_days=payload.window_days)
    capacity = read_capacity_report(request.app.state.settings)
    calibration = build_launch_calibration(metrics, capacity, request.app.state.settings)
    try:
        plan = propose_plan(db, calibration=calibration, actor_id=admin.id)
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc
    audit(db, admin, "capacity.plan.propose", "capacity_plan", plan.id, {"version": plan.version, "status": calibration["status"]})
    db.commit()
    db.refresh(plan)
    return {"capacity_plan": plan_dict(plan), "calibration": calibration}


@router.get("/capacity-plans")
def capacity_plans(
    limit: int = Query(default=50, ge=1, le=200),
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    _ = admin
    rows = db.scalars(select(CapacityPlan).order_by(CapacityPlan.version.desc()).limit(limit)).all()
    return [plan_dict(row) for row in rows]


@router.get("/capacity-plans/active")
def active_capacity_plan(admin: User = Depends(require_admin), db: Session = Depends(get_db)):
    _ = admin
    plan = active_plan(db)
    return plan_dict(plan) if plan else None


@router.post("/capacity-plans/{plan_id}/approve")
def approve_capacity_plan(
    plan_id: str,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    plan = db.get(CapacityPlan, plan_id)
    if plan is None:
        raise HTTPException(404, "Capacity plan not found")
    try:
        approve_plan(plan, actor_id=admin.id)
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc
    audit(db, admin, "capacity.plan.approve", "capacity_plan", plan.id, {"version": plan.version})
    db.commit()
    return plan_dict(plan)


@router.post("/capacity-plans/{plan_id}/activate")
def activate_capacity_plan(
    plan_id: str,
    request: Request,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    plan = db.get(CapacityPlan, plan_id)
    if plan is None:
        raise HTTPException(404, "Capacity plan not found")
    try:
        runtime = activate_plan(db, request.app, plan)
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc
    audit(db, admin, "capacity.plan.activate", "capacity_plan", plan.id, {"version": plan.version, "runtime": runtime})
    db.commit()
    return {"capacity_plan": plan_dict(plan), "runtime": runtime}


@router.post("/capacity-plans/rollback")
def rollback_capacity_plan(
    payload: RollbackRequest,
    request: Request,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    try:
        plan, runtime = rollback_plan(db, request.app, actor_id=admin.id, target_plan_id=payload.target_plan_id)
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc
    audit(
        db,
        admin,
        "capacity.plan.rollback",
        "capacity_plan",
        plan.id,
        {"version": plan.version, "rollback_of_id": plan.rollback_of_id, "runtime": runtime},
    )
    db.commit()
    return {"capacity_plan": plan_dict(plan), "runtime": runtime}
