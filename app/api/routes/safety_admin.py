from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Header, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_db
from app.models import (
    AdminAuditLog,
    Conversation,
    LegalReview,
    Message,
    RiskEvent,
    SafetyCase,
    User,
    UserRestriction,
)
from app.services.admin import audit, require_admin
from app.services.safety import create_risk_event

router = APIRouter(prefix="/v1/admin/safety", tags=["admin-safety"])


class RiskEventCreate(BaseModel):
    user_id: str | None = None
    category: str = Field(min_length=1, max_length=64)
    severity: int = Field(ge=1, le=5)
    rule_id: str = Field(default="manual.admin", max_length=120)
    summary: str = Field(min_length=1, max_length=2000)
    evidence: dict = Field(default_factory=dict)
    conversation_id: str | None = None
    message_id: str | None = None


class RiskReviewPatch(BaseModel):
    state: str
    note: str = Field(default="", max_length=4000)


class CaseCreate(BaseModel):
    user_id: str
    title: str = Field(min_length=1, max_length=240)
    reason: str = Field(default="", max_length=8000)
    priority: int = Field(default=1, ge=1, le=5)
    risk_event_ids: list[str] = Field(default_factory=list, max_length=100)


class CasePatch(BaseModel):
    status: str | None = None
    decision: str | None = Field(default=None, max_length=8000)
    assigned_admin_id: str | None = None


class RestrictionCreate(BaseModel):
    capability: str = Field(default="all", pattern="^(all|chat|research|tools|images)$")
    reason: str = Field(min_length=1, max_length=4000)
    expires_at: datetime | None = None


class LegalReviewCreate(BaseModel):
    legal_basis: str = Field(min_length=1, max_length=8000)
    justification: str = Field(min_length=1, max_length=8000)
    requested_scope: dict = Field(default_factory=dict)


class LegalDecision(BaseModel):
    decision: str = Field(pattern="^(approve|reject)$")
    note: str = Field(default="", max_length=8000)


def _event_dict(e: RiskEvent) -> dict:
    return {
        "id": e.id,
        "user_id": e.user_id,
        "project_id": e.project_id,
        "conversation_id": e.conversation_id,
        "message_id": e.message_id,
        "category": e.category,
        "severity": e.severity,
        "rule_id": e.rule_id,
        "summary": e.summary,
        "evidence": e.evidence,
        "detected_by": e.detected_by,
        "state": e.state,
        "reviewed_by": e.reviewed_by,
        "reviewed_at": e.reviewed_at,
        "created_at": e.created_at,
    }


@router.get("/events")
def list_events(
    state: str | None = Query(default=None),
    min_severity: int = Query(default=1, ge=1, le=5),
    user_id: str | None = Query(default=None),
    limit: int = Query(default=100, ge=1, le=500),
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
) -> list[dict]:
    stmt = select(RiskEvent).where(RiskEvent.severity >= min_severity).order_by(RiskEvent.severity.desc(), RiskEvent.created_at.desc()).limit(limit)
    if state:
        stmt = stmt.where(RiskEvent.state == state)
    if user_id:
        stmt = stmt.where(RiskEvent.user_id == user_id)
    return [_event_dict(x) for x in db.scalars(stmt).all()]


@router.post("/events", status_code=201)
def add_event(payload: RiskEventCreate, admin: User = Depends(require_admin), db: Session = Depends(get_db)) -> dict:
    if payload.user_id is not None and db.get(User, payload.user_id) is None:
        raise HTTPException(status_code=404, detail="User not found")
    event = create_risk_event(
        db,
        user_id=payload.user_id,
        category=payload.category,
        severity=payload.severity,
        rule_id=payload.rule_id,
        summary=payload.summary,
        evidence=payload.evidence,
        conversation_id=payload.conversation_id,
        message_id=payload.message_id,
        detected_by="admin",
    )
    audit(db, admin, "safety.event.create", "risk_event", event.id, {"category": event.category, "severity": event.severity})
    db.commit()
    return _event_dict(event)


@router.patch("/events/{event_id}")
def review_event(event_id: str, payload: RiskReviewPatch, admin: User = Depends(require_admin), db: Session = Depends(get_db)) -> dict:
    if payload.state not in {"new", "reviewing", "confirmed", "dismissed"}:
        raise HTTPException(status_code=422, detail="Invalid risk event state")
    event = db.get(RiskEvent, event_id)
    if event is None:
        raise HTTPException(status_code=404, detail="Risk event not found")
    event.state = payload.state
    event.reviewed_by = admin.id
    event.reviewed_at = datetime.now(timezone.utc)
    audit(db, admin, "safety.event.review", "risk_event", event.id, {"state": payload.state, "note": payload.note})
    db.commit()
    return _event_dict(event)


@router.post("/cases", status_code=201)
def create_case(payload: CaseCreate, admin: User = Depends(require_admin), db: Session = Depends(get_db)) -> dict:
    if db.get(User, payload.user_id) is None:
        raise HTTPException(status_code=404, detail="User not found")
    if payload.risk_event_ids:
        events = db.scalars(select(RiskEvent).where(RiskEvent.id.in_(payload.risk_event_ids))).all()
        if len({x.id for x in events}) != len(set(payload.risk_event_ids)) or any(x.user_id not in {None, payload.user_id} for x in events):
            raise HTTPException(status_code=409, detail="Risk events do not match case user")
    row = SafetyCase(
        user_id=payload.user_id,
        title=payload.title,
        reason=payload.reason,
        priority=payload.priority,
        risk_event_ids=list(dict.fromkeys(payload.risk_event_ids)),
        created_by=admin.id,
        assigned_admin_id=admin.id,
    )
    db.add(row); db.flush()
    audit(db, admin, "safety.case.create", "safety_case", row.id, {"user_id": row.user_id, "risk_event_ids": row.risk_event_ids})
    db.commit()
    return {"id": row.id, "status": row.status, "user_id": row.user_id, "priority": row.priority, "risk_event_ids": row.risk_event_ids}


@router.get("/cases")
def list_cases(status: str | None = None, limit: int = Query(default=100, ge=1, le=500), admin: User = Depends(require_admin), db: Session = Depends(get_db)) -> list[dict]:
    stmt = select(SafetyCase).order_by(SafetyCase.priority.desc(), SafetyCase.created_at.desc()).limit(limit)
    if status:
        stmt = stmt.where(SafetyCase.status == status)
    return [{"id":x.id,"user_id":x.user_id,"status":x.status,"priority":x.priority,"title":x.title,"reason":x.reason,"risk_event_ids":x.risk_event_ids,"assigned_admin_id":x.assigned_admin_id,"decision":x.decision,"created_at":x.created_at,"updated_at":x.updated_at} for x in db.scalars(stmt).all()]


@router.patch("/cases/{case_id}")
def patch_case(case_id: str, payload: CasePatch, admin: User = Depends(require_admin), db: Session = Depends(get_db)) -> dict:
    row = db.get(SafetyCase, case_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Safety case not found")
    if payload.status is not None:
        if payload.status not in {"open", "reviewing", "restricted", "closed", "dismissed"}:
            raise HTTPException(status_code=422, detail="Invalid case status")
        row.status = payload.status
        if payload.status in {"closed", "dismissed"}:
            row.closed_at = datetime.now(timezone.utc)
    if payload.decision is not None:
        row.decision = payload.decision
    if payload.assigned_admin_id is not None:
        target = db.get(User, payload.assigned_admin_id)
        if target is None or not target.is_admin:
            raise HTTPException(status_code=409, detail="Assignee must be an administrator")
        row.assigned_admin_id = target.id
    audit(db, admin, "safety.case.update", "safety_case", row.id, payload.model_dump(exclude_none=True))
    db.commit()
    return {"id":row.id,"status":row.status,"decision":row.decision,"assigned_admin_id":row.assigned_admin_id}


@router.post("/cases/{case_id}/restrictions", status_code=201)
def restrict_user(case_id: str, payload: RestrictionCreate, admin: User = Depends(require_admin), db: Session = Depends(get_db)) -> dict:
    case = db.get(SafetyCase, case_id)
    if case is None:
        raise HTTPException(status_code=404, detail="Safety case not found")
    row = UserRestriction(user_id=case.user_id, case_id=case.id, capability=payload.capability, reason=payload.reason, expires_at=payload.expires_at, created_by=admin.id)
    db.add(row); case.status = "restricted"; db.flush()
    audit(db, admin, "safety.restriction.create", "user_restriction", row.id, {"case_id":case.id,"user_id":case.user_id,"capability":row.capability,"expires_at":str(row.expires_at) if row.expires_at else None})
    db.commit()
    return {"id":row.id,"user_id":row.user_id,"capability":row.capability,"active":row.active,"expires_at":row.expires_at}


@router.delete("/restrictions/{restriction_id}", status_code=204)
def revoke_restriction(restriction_id: str, admin: User = Depends(require_admin), db: Session = Depends(get_db)) -> None:
    row = db.get(UserRestriction, restriction_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Restriction not found")
    row.active=False; row.revoked_by=admin.id; row.revoked_at=datetime.now(timezone.utc)
    audit(db, admin, "safety.restriction.revoke", "user_restriction", row.id, {"user_id":row.user_id,"capability":row.capability})
    db.commit()


@router.get("/cases/{case_id}/conversation/{conversation_id}")
def review_conversation(
    case_id: str,
    conversation_id: str,
    x_admin_access_reason: str = Header(default=""),
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
) -> dict:
    if len(x_admin_access_reason.strip()) < 10:
        raise HTTPException(status_code=400, detail="A specific review reason is required")
    case = db.get(SafetyCase, case_id)
    conv = db.get(Conversation, conversation_id)
    if case is None or conv is None or conv.owner_id != case.user_id:
        raise HTTPException(status_code=404, detail="Conversation not available for this case")
    messages = db.scalars(select(Message).where(Message.conversation_id == conversation_id).order_by(Message.created_at.asc())).all()
    audit(db, admin, "safety.break_glass_conversation_read", "conversation", conversation_id, {"case_id":case_id,"reason":x_admin_access_reason.strip(),"message_count":len(messages)})
    db.commit()
    return {"conversation_id":conversation_id,"case_id":case_id,"messages":[{"id":m.id,"role":m.role,"content":m.content,"created_at":m.created_at} for m in messages]}


@router.post("/cases/{case_id}/legal-reviews", status_code=201)
def create_legal_review(case_id: str, payload: LegalReviewCreate, admin: User = Depends(require_admin), db: Session = Depends(get_db)) -> dict:
    case = db.get(SafetyCase, case_id)
    if case is None:
        raise HTTPException(status_code=404, detail="Safety case not found")
    row = LegalReview(case_id=case.id, legal_basis=payload.legal_basis, justification=payload.justification, requested_scope=payload.requested_scope, requested_by=admin.id, status="pending_approval")
    db.add(row); db.flush()
    audit(db, admin, "legal.review.request", "legal_review", row.id, {"case_id":case.id,"scope":row.requested_scope})
    db.commit()
    return {"id":row.id,"status":row.status,"case_id":row.case_id}


@router.post("/legal-reviews/{review_id}/decision")
def decide_legal_review(review_id: str, payload: LegalDecision, admin: User = Depends(require_admin), db: Session = Depends(get_db)) -> dict:
    row = db.get(LegalReview, review_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Legal review not found")
    if row.requested_by == admin.id and payload.decision == "approve":
        raise HTTPException(status_code=409, detail="Legal approval requires a second administrator")
    if row.status != "pending_approval":
        raise HTTPException(status_code=409, detail="Legal review is not pending")
    row.status = "approved" if payload.decision == "approve" else "rejected"
    if row.status == "approved":
        row.approved_by = admin.id; row.approved_at = datetime.now(timezone.utc)
    audit(db, admin, f"legal.review.{row.status}", "legal_review", row.id, {"note":payload.note})
    db.commit()
    return {"id":row.id,"status":row.status,"approved_by":row.approved_by}


@router.post("/legal-reviews/{review_id}/prepare-export")
def prepare_legal_export(review_id: str, admin: User = Depends(require_admin), db: Session = Depends(get_db)) -> dict:
    row = db.get(LegalReview, review_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Legal review not found")
    if row.status != "approved":
        raise HTTPException(status_code=409, detail="Legal review must be approved before export preparation")
    case = db.get(SafetyCase, row.case_id)
    user = db.get(User, case.user_id) if case else None
    if case is None or user is None:
        raise HTTPException(status_code=409, detail="Case data unavailable")
    # Data minimization: only explicitly requested, server-supported fields are included.
    allowed = set(row.requested_scope.get("fields", [])) & {"user_id", "email", "risk_events", "case_summary"}
    manifest: dict = {"legal_review_id":row.id,"case_id":case.id,"generated_at":datetime.now(timezone.utc).isoformat(),"fields":sorted(allowed)}
    data: dict = {}
    if "user_id" in allowed: data["user_id"] = user.id
    if "email" in allowed: data["email"] = user.email
    if "case_summary" in allowed: data["case_summary"] = {"title":case.title,"reason":case.reason,"decision":case.decision}
    if "risk_events" in allowed:
        events = db.scalars(select(RiskEvent).where(RiskEvent.id.in_(case.risk_event_ids))).all() if case.risk_event_ids else []
        data["risk_events"] = [{"id":e.id,"category":e.category,"severity":e.severity,"summary":e.summary,"state":e.state,"created_at":e.created_at.isoformat()} for e in events]
    payload = {"manifest":manifest,"data":data}
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",",":"), default=str).encode("utf-8")
    row.export_manifest = manifest; row.export_sha256 = hashlib.sha256(raw).hexdigest()
    audit(db, admin, "legal.export.prepare", "legal_review", row.id, {"sha256":row.export_sha256,"fields":sorted(allowed)})
    db.commit()
    return {"sha256":row.export_sha256, **payload}
