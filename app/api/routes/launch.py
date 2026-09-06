from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_db
from app.models import BetaParticipant, CircuitBreakerEvent, MeasuredPlanCatalog, PublicRollout, User
from app.services.admin import audit, require_admin
from app.services.auth import get_current_user
from app.services.beta import calculate_beta_metrics
from app.services.capacity import read_capacity_report
from app.services.progressive_launch import (
    ROLLOUT_STAGES,
    active_measured_catalog,
    active_rollout,
    activate_catalog,
    advance_rollout,
    approve_catalog,
    build_measured_plan_catalog,
    catalog_dict,
    create_measured_catalog,
    create_rollout,
    evaluate_public_launch,
    freeze_rollout,
    resolve_breaker,
    rollback_rollout,
    rollout_allows_user,
    rollout_dict,
)

router = APIRouter(tags=["public-launch"])


class CatalogProposal(BaseModel):
    cohort: str = Field(default="closed-beta-1", min_length=1, max_length=64)
    window_days: int = Field(default=30, ge=1, le=180)


class RolloutCreate(BaseModel):
    cohort: str = Field(default="closed-beta-1", min_length=1, max_length=64)
    window_days: int = Field(default=30, ge=1, le=180)


class RolloutAdvance(BaseModel):
    exposure_percent: int


@router.get("/v1/launch/eligibility")
def eligibility(request: Request, user: User = Depends(get_current_user), db: Session = Depends(get_db)) -> dict:
    beta = db.scalar(select(BetaParticipant).where(BetaParticipant.user_id == user.id, BetaParticipant.state != "removed").limit(1))
    rollout = active_rollout(db)
    exposed = bool(beta) or rollout_allows_user(rollout, user.id) or bool(user.is_admin)
    return {
        "eligible": exposed,
        "reason": "admin" if user.is_admin else "closed_beta" if beta else "public_rollout" if exposed else "not_exposed_yet",
        "rollout_version": rollout.version if rollout else None,
        "exposure_percent": rollout.exposure_percent if rollout else 0,
        "enforcement_enabled": bool(getattr(request.app.state.settings, "public_launch_enforce_exposure", False)),
    }


@router.get("/v1/admin/launch/status")
def launch_status(request: Request, admin: User = Depends(require_admin), db: Session = Depends(get_db)) -> dict:
    _ = admin
    evaluation = evaluate_public_launch(db, request.app.state.settings, persist=True)
    rollout = active_rollout(db)
    catalog = active_measured_catalog(db)
    db.commit()
    return {
        "evaluation": evaluation,
        "rollout": rollout_dict(rollout) if rollout else None,
        "measured_catalog": catalog_dict(catalog) if catalog else None,
        "stages": list(ROLLOUT_STAGES),
    }


@router.post("/v1/admin/launch/evaluate")
def evaluate(request: Request, admin: User = Depends(require_admin), db: Session = Depends(get_db)) -> dict:
    evaluation = evaluate_public_launch(db, request.app.state.settings, persist=True)
    rollout = active_rollout(db)
    if rollout is not None and evaluation["status"] != "stable":
        freeze_rollout(rollout, evaluation=evaluation, actor_id=admin.id)
    audit(db, admin, "launch.evaluate", "public_rollout", rollout.id if rollout else "", {"status": evaluation["status"], "blockers": evaluation["blockers"]})
    db.commit()
    return {"evaluation": evaluation, "rollout": rollout_dict(rollout) if rollout else None}


@router.post("/v1/admin/launch/catalogs/propose")
def propose_catalog(payload: CatalogProposal, request: Request, admin: User = Depends(require_admin), db: Session = Depends(get_db)) -> dict:
    beta = calculate_beta_metrics(db, cohort=payload.cohort, window_days=payload.window_days)
    capacity = read_capacity_report(request.app.state.settings)
    measured = build_measured_plan_catalog(beta, capacity, request.app.state.settings)
    try:
        row = create_measured_catalog(db, measured=measured, actor_id=admin.id)
    except ValueError as exc:
        raise HTTPException(409, {"message": str(exc), "measured": measured}) from exc
    audit(db, admin, "launch.catalog.propose", "measured_plan_catalog", row.id, {"version": row.version})
    db.commit(); db.refresh(row)
    return catalog_dict(row)


@router.get("/v1/admin/launch/catalogs")
def catalogs(limit: int = Query(default=20, ge=1, le=100), admin: User = Depends(require_admin), db: Session = Depends(get_db)) -> list[dict]:
    _ = admin
    rows = db.scalars(select(MeasuredPlanCatalog).order_by(MeasuredPlanCatalog.version.desc()).limit(limit)).all()
    return [catalog_dict(row) for row in rows]


@router.post("/v1/admin/launch/catalogs/{catalog_id}/approve")
def approve_catalog_route(catalog_id: str, admin: User = Depends(require_admin), db: Session = Depends(get_db)) -> dict:
    row = db.get(MeasuredPlanCatalog, catalog_id)
    if row is None:
        raise HTTPException(404, "Measured plan catalog not found")
    try:
        approve_catalog(row, admin.id)
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc
    audit(db, admin, "launch.catalog.approve", "measured_plan_catalog", row.id, {"version": row.version})
    db.commit(); return catalog_dict(row)


@router.post("/v1/admin/launch/catalogs/{catalog_id}/activate")
def activate_catalog_route(catalog_id: str, admin: User = Depends(require_admin), db: Session = Depends(get_db)) -> dict:
    row = db.get(MeasuredPlanCatalog, catalog_id)
    if row is None:
        raise HTTPException(404, "Measured plan catalog not found")
    try:
        activate_catalog(db, row)
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc
    audit(db, admin, "launch.catalog.activate", "measured_plan_catalog", row.id, {"version": row.version})
    db.commit(); return catalog_dict(row)


@router.post("/v1/admin/launch/rollouts")
def new_rollout(payload: RolloutCreate, request: Request, admin: User = Depends(require_admin), db: Session = Depends(get_db)) -> dict:
    baseline = calculate_beta_metrics(db, cohort=payload.cohort, window_days=payload.window_days)
    evaluation = evaluate_public_launch(db, request.app.state.settings, persist=True)
    if evaluation["status"] != "stable":
        raise HTTPException(409, {"message": "Public launch guardrails are not green", "evaluation": evaluation})
    try:
        row = create_rollout(db, baseline=baseline, actor_id=admin.id)
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc
    audit(db, admin, "launch.rollout.create", "public_rollout", row.id, {"version": row.version})
    db.commit(); return rollout_dict(row)


@router.get("/v1/admin/launch/rollouts")
def rollouts(limit: int = Query(default=50, ge=1, le=200), admin: User = Depends(require_admin), db: Session = Depends(get_db)) -> list[dict]:
    _ = admin
    rows = db.scalars(select(PublicRollout).order_by(PublicRollout.version.desc()).limit(limit)).all()
    return [rollout_dict(row) for row in rows]


@router.post("/v1/admin/launch/rollouts/{rollout_id}/advance")
def advance(rollout_id: str, payload: RolloutAdvance, request: Request, admin: User = Depends(require_admin), db: Session = Depends(get_db)) -> dict:
    row = db.get(PublicRollout, rollout_id)
    if row is None:
        raise HTTPException(404, "Public rollout not found")
    evaluation = evaluate_public_launch(db, request.app.state.settings, persist=True)
    try:
        next_row = advance_rollout(db, row, exposure_percent=payload.exposure_percent, evaluation=evaluation, actor_id=admin.id)
    except ValueError as exc:
        db.commit()
        raise HTTPException(409, {"message": str(exc), "evaluation": evaluation}) from exc
    audit(db, admin, "launch.rollout.advance", "public_rollout", next_row.id, {"from": row.exposure_percent, "to": next_row.exposure_percent})
    db.commit(); return rollout_dict(next_row)


@router.post("/v1/admin/launch/rollouts/{rollout_id}/freeze")
def freeze(rollout_id: str, request: Request, admin: User = Depends(require_admin), db: Session = Depends(get_db)) -> dict:
    row = db.get(PublicRollout, rollout_id)
    if row is None:
        raise HTTPException(404, "Public rollout not found")
    evaluation = evaluate_public_launch(db, request.app.state.settings, persist=True)
    freeze_rollout(row, evaluation=evaluation, actor_id=admin.id)
    audit(db, admin, "launch.rollout.freeze", "public_rollout", row.id, {"evaluation": evaluation})
    db.commit(); return rollout_dict(row)


@router.post("/v1/admin/launch/rollouts/{rollout_id}/rollback")
def rollback(rollout_id: str, admin: User = Depends(require_admin), db: Session = Depends(get_db)) -> dict:
    row = db.get(PublicRollout, rollout_id)
    if row is None:
        raise HTTPException(404, "Public rollout not found")
    next_row = rollback_rollout(db, row, actor_id=admin.id)
    audit(db, admin, "launch.rollout.rollback", "public_rollout", next_row.id, {"from": row.exposure_percent, "to": next_row.exposure_percent})
    db.commit(); return rollout_dict(next_row)


@router.get("/v1/admin/launch/breakers")
def breakers(status_filter: str | None = Query(default=None, alias="status"), limit: int = Query(default=100, ge=1, le=500), admin: User = Depends(require_admin), db: Session = Depends(get_db)) -> list[dict]:
    _ = admin
    stmt = select(CircuitBreakerEvent).order_by(CircuitBreakerEvent.opened_at.desc()).limit(limit)
    if status_filter:
        stmt = stmt.where(CircuitBreakerEvent.status == status_filter)
    rows = db.scalars(stmt).all()
    return [{"id":x.id,"scope":x.scope,"kind":x.kind,"status":x.status,"automatic":x.automatic,"reason":x.reason,"details":x.details,"opened_at":x.opened_at,"resolved_at":x.resolved_at} for x in rows]


@router.post("/v1/admin/launch/breakers/{breaker_id}/resolve")
def resolve_breaker_route(breaker_id: str, admin: User = Depends(require_admin), db: Session = Depends(get_db)) -> dict:
    row = db.get(CircuitBreakerEvent, breaker_id)
    if row is None:
        raise HTTPException(404, "Circuit breaker not found")
    resolve_breaker(row, admin.id)
    audit(db, admin, "launch.breaker.resolve", "circuit_breaker", row.id, {"kind": row.kind})
    db.commit(); return {"id": row.id, "status": row.status, "resolved_at": row.resolved_at}
