from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_db
from app.models import ComplaintCase, Conversation, RegressionCase, RegressionRun, ReleaseGateDecision, User
from app.services.access import require_project_role
from app.services.admin import audit, require_admin
from app.services.auth import get_current_user
from app.services.complaint_regression import (
    complaint_dict, confirm_complaint, create_complaint, evaluate_release_gate,
    record_regression_run, regression_dict, reject_complaint, resolve_complaint,
)

router = APIRouter(tags=["complaint-regression"])


class ComplaintCreate(BaseModel):
    component: str = Field(default="auto", min_length=1, max_length=80)
    category: str = Field(default="auto", min_length=1, max_length=64)
    severity: str = Field(default="medium")
    title: str = Field(min_length=1, max_length=240)
    actual_behavior: str = Field(min_length=1, max_length=12000)
    expected_behavior: str = Field(default="", max_length=12000)
    project_id: str | None = None
    conversation_id: str | None = None
    request_id: str | None = Field(default=None, max_length=64)
    reproduction: dict = Field(default_factory=dict)
    evidence: dict = Field(default_factory=dict)


class ConfirmComplaint(BaseModel):
    reproduction: dict = Field(default_factory=dict)


class RegressionRunCreate(BaseModel):
    release_version: str = Field(min_length=1, max_length=80)
    result: str
    details: dict = Field(default_factory=dict)


def _severity(value: str) -> str:
    value = value.lower().strip()
    if value not in {"low", "medium", "high", "critical"}:
        raise HTTPException(status_code=422, detail="Unsupported complaint severity")
    return value


@router.post("/v1/feedback/complaints", status_code=status.HTTP_201_CREATED)
def submit_complaint(payload: ComplaintCreate, user: User = Depends(get_current_user), db: Session = Depends(get_db)) -> dict:
    if payload.project_id:
        require_project_role(db, user, payload.project_id, "viewer")
    if payload.conversation_id:
        conversation = db.get(Conversation, payload.conversation_id)
        if conversation is None:
            raise HTTPException(status_code=404, detail="Conversation not found")
        if conversation.project_id:
            require_project_role(db, user, conversation.project_id, "viewer")
            if payload.project_id and payload.project_id != conversation.project_id:
                raise HTTPException(status_code=409, detail="Conversation/project mismatch")
        elif conversation.owner_id != user.id:
            raise HTTPException(status_code=404, detail="Conversation not found")
    row = create_complaint(
        db, reporter_user_id=user.id, component=payload.component, category=payload.category,
        severity=_severity(payload.severity), title=payload.title,
        expected_behavior=payload.expected_behavior, actual_behavior=payload.actual_behavior,
        project_id=payload.project_id, conversation_id=payload.conversation_id,
        request_id=payload.request_id, reproduction=payload.reproduction, evidence=payload.evidence,
    )
    db.commit(); db.refresh(row)
    return complaint_dict(row)


@router.get("/v1/feedback/complaints")
def my_complaints(limit: int = Query(default=50, ge=1, le=200), user: User = Depends(get_current_user), db: Session = Depends(get_db)) -> list[dict]:
    rows = db.scalars(select(ComplaintCase).where(ComplaintCase.reporter_user_id == user.id)
                      .order_by(ComplaintCase.created_at.desc()).limit(limit)).all()
    return [complaint_dict(x) for x in rows]


@router.get("/v1/admin/complaints")
def admin_complaints(status_filter: str | None = Query(default=None, alias="status"), fingerprint: str | None = None,
                     limit: int = Query(default=100, ge=1, le=500), admin: User = Depends(require_admin),
                     db: Session = Depends(get_db)) -> list[dict]:
    stmt = select(ComplaintCase).order_by(ComplaintCase.created_at.desc()).limit(limit)
    if status_filter: stmt = stmt.where(ComplaintCase.status == status_filter)
    if fingerprint: stmt = stmt.where(ComplaintCase.fingerprint == fingerprint)
    return [complaint_dict(x) for x in db.scalars(stmt).all()]


@router.post("/v1/admin/complaints/{complaint_id}/confirm")
def admin_confirm_complaint(complaint_id: str, payload: ConfirmComplaint, admin: User = Depends(require_admin),
                            db: Session = Depends(get_db)) -> dict:
    complaint = db.get(ComplaintCase, complaint_id)
    if complaint is None: raise HTTPException(status_code=404, detail="Complaint not found")
    try:
        regression = confirm_complaint(db, complaint, admin_user_id=admin.id, reproduction=payload.reproduction or None)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    audit(db, admin, "complaint.confirm", "complaint_case", complaint.id, {"regression_case_id": regression.id})
    db.commit(); db.refresh(complaint); db.refresh(regression)
    return {"complaint": complaint_dict(complaint), "regression_case": regression_dict(regression)}


@router.post("/v1/admin/complaints/{complaint_id}/reject", status_code=204)
def admin_reject_complaint(complaint_id: str, admin: User = Depends(require_admin), db: Session = Depends(get_db)) -> None:
    complaint = db.get(ComplaintCase, complaint_id)
    if complaint is None: raise HTTPException(status_code=404, detail="Complaint not found")
    try: reject_complaint(complaint)
    except ValueError as exc: raise HTTPException(status_code=409, detail=str(exc)) from exc
    audit(db, admin, "complaint.reject", "complaint_case", complaint.id); db.commit()


@router.post("/v1/admin/complaints/{complaint_id}/resolve", status_code=204)
def admin_resolve_complaint(complaint_id: str, admin: User = Depends(require_admin), db: Session = Depends(get_db)) -> None:
    complaint = db.get(ComplaintCase, complaint_id)
    if complaint is None: raise HTTPException(status_code=404, detail="Complaint not found")
    resolve_complaint(complaint); audit(db, admin, "complaint.resolve", "complaint_case", complaint.id); db.commit()


@router.get("/v1/admin/regression-cases")
def list_regression_cases(blocking: bool | None = None, limit: int = Query(default=100, ge=1, le=500),
                          admin: User = Depends(require_admin), db: Session = Depends(get_db)) -> list[dict]:
    stmt = select(RegressionCase).order_by(RegressionCase.updated_at.desc()).limit(limit)
    if blocking is not None: stmt = stmt.where(RegressionCase.release_blocking.is_(blocking))
    return [regression_dict(x) for x in db.scalars(stmt).all()]


@router.post("/v1/admin/regression-cases/{case_id}/runs")
def add_regression_run(case_id: str, payload: RegressionRunCreate, admin: User = Depends(require_admin),
                       db: Session = Depends(get_db)) -> dict:
    case = db.get(RegressionCase, case_id)
    if case is None: raise HTTPException(status_code=404, detail="Regression case not found")
    try:
        run = record_regression_run(db, case, release_version=payload.release_version, result=payload.result,
                                    details=payload.details, executed_by=admin.id)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    audit(db, admin, "regression.run", "regression_case", case.id, {"release": payload.release_version, "result": payload.result})
    db.commit(); db.refresh(case); db.refresh(run)
    return {"run_id": run.id, "result": run.result, "regression_case": regression_dict(case)}


@router.get("/v1/admin/regression-cases/{case_id}/runs")
def list_regression_runs(case_id: str, limit: int = Query(default=50, ge=1, le=200), admin: User = Depends(require_admin),
                         db: Session = Depends(get_db)) -> list[dict]:
    if db.get(RegressionCase, case_id) is None: raise HTTPException(status_code=404, detail="Regression case not found")
    rows = db.scalars(select(RegressionRun).where(RegressionRun.regression_case_id == case_id)
                      .order_by(RegressionRun.created_at.desc()).limit(limit)).all()
    return [{"id":x.id,"release_version":x.release_version,"result":x.result,"details":x.details,"created_at":x.created_at} for x in rows]


@router.post("/v1/admin/release-gate/{release_version}")
def release_gate(release_version: str, channel: str = Query(default="stable", pattern="^(stable|beta|canary)$"),
                 admin: User = Depends(require_admin), db: Session = Depends(get_db)) -> dict:
    row = evaluate_release_gate(db, release_version=release_version, channel=channel, evaluated_by=admin.id)
    audit(db, admin, "release_gate.evaluate", "release", release_version,
          {"channel": channel, "decision": row.decision, "blocker_ids": row.blocker_ids})
    db.commit(); db.refresh(row)
    return {"id":row.id,"release_version":row.release_version,"channel":row.channel,"decision":row.decision,
            "blocker_ids":row.blocker_ids,"reasons":row.reasons,"created_at":row.created_at}


@router.get("/v1/admin/release-gate")
def release_gate_history(limit: int = Query(default=50, ge=1, le=200), admin: User = Depends(require_admin),
                         db: Session = Depends(get_db)) -> list[dict]:
    rows = db.scalars(select(ReleaseGateDecision).order_by(ReleaseGateDecision.created_at.desc()).limit(limit)).all()
    return [{"id":x.id,"release_version":x.release_version,"channel":x.channel,"decision":x.decision,
             "blocker_ids":x.blocker_ids,"reasons":x.reasons,"created_at":x.created_at} for x in rows]
