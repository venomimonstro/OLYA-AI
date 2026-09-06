from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_db
from app.models import BetaParticipant, BetaSnapshot, User
from app.models_sprint30 import ComplaintCase
from app.services.admin import audit, require_admin
from app.services.beta_trends import build_trend
from app.services.complaint_regression import complaint_dict, confirm_complaint, regression_dict

router = APIRouter(prefix="/v1/admin/beta", tags=["admin-beta-ops"])


class BetaFeedbackConfirm(BaseModel):
    cohort: str = Field(default="closed-beta-1", min_length=1, max_length=64)
    reproduction: dict = Field(default_factory=dict)


@router.get("/trends")
def beta_trends(
    cohort: str = "closed-beta-1",
    limit: int = Query(default=14, ge=2, le=90),
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    _ = admin
    rows = list(
        db.scalars(
            select(BetaSnapshot)
            .where(BetaSnapshot.cohort == cohort)
            .order_by(BetaSnapshot.created_at.desc())
            .limit(limit)
        ).all()
    )
    from app.core.config import get_settings

    return build_trend(rows, get_settings())


@router.get("/feedback")
def beta_feedback(
    cohort: str = "closed-beta-1",
    status_filter: str | None = Query(default=None, alias="status"),
    include_removed: bool = False,
    limit: int = Query(default=100, ge=1, le=500),
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    _ = admin
    participant_stmt = select(BetaParticipant).where(BetaParticipant.cohort == cohort)
    if not include_removed:
        participant_stmt = participant_stmt.where(BetaParticipant.state != "removed")
    participants = list(db.scalars(participant_stmt).all())
    by_user = {row.user_id: row for row in participants}
    if not by_user:
        return []

    stmt = (
        select(ComplaintCase)
        .where(ComplaintCase.reporter_user_id.in_(list(by_user)))
        .order_by(ComplaintCase.created_at.desc())
        .limit(limit)
    )
    if status_filter:
        stmt = stmt.where(ComplaintCase.status == status_filter)
    rows = list(db.scalars(stmt).all())
    result = []
    for complaint in rows:
        participant = by_user.get(complaint.reporter_user_id)
        metadata = dict(participant.metadata_json or {}) if participant else {}
        result.append(
            {
                "complaint": complaint_dict(complaint),
                "beta": {
                    "participant_id": participant.id if participant else None,
                    "cohort": participant.cohort if participant else cohort,
                    "participant_state": participant.state if participant else "unknown",
                    "wave_id": metadata.get("wave_id"),
                    "wave_number": metadata.get("wave_number"),
                    "forced_admission": bool(metadata.get("forced_admission")),
                },
            }
        )
    return result


@router.post("/feedback/{complaint_id}/confirm")
def confirm_beta_feedback(
    complaint_id: str,
    payload: BetaFeedbackConfirm,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    complaint = db.get(ComplaintCase, complaint_id)
    if complaint is None:
        raise HTTPException(404, "Complaint not found")
    if not complaint.reporter_user_id:
        raise HTTPException(409, "Complaint is not associated with a beta user")
    participant = db.scalar(
        select(BetaParticipant).where(
            BetaParticipant.user_id == complaint.reporter_user_id,
            BetaParticipant.cohort == payload.cohort,
        )
    )
    if participant is None:
        raise HTTPException(409, "Complaint reporter is not in the selected beta cohort")
    try:
        regression = confirm_complaint(
            db,
            complaint,
            admin_user_id=admin.id,
            reproduction=payload.reproduction or None,
        )
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc
    audit(
        db,
        admin,
        "beta.feedback.confirm",
        "complaint_case",
        complaint.id,
        {
            "cohort": participant.cohort,
            "participant_id": participant.id,
            "regression_case_id": regression.id,
        },
    )
    db.commit()
    db.refresh(complaint)
    db.refresh(regression)
    return {
        "complaint": complaint_dict(complaint),
        "regression_case": regression_dict(regression),
        "beta": {
            "participant_id": participant.id,
            "cohort": participant.cohort,
            "state": participant.state,
            "metadata": participant.metadata_json,
        },
    }
