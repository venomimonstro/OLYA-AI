from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_db
from app.models import AnswerAudit, User
from app.schemas.chat import QualityReport
from app.services.auth import get_current_user

router = APIRouter(prefix="/v1/quality", tags=["quality"])


def _response(item: AnswerAudit) -> QualityReport:
    return QualityReport(
        audit_id=item.id,
        status=item.status,
        checks=item.checks,
        warnings=item.warnings,
        critic=item.critic or None,
    )


@router.get("/audits", response_model=list[QualityReport])
def list_answer_audits(
    limit: int = 50,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> list[QualityReport]:
    safe_limit = min(max(limit, 1), 200)
    items = list(
        db.scalars(
            select(AnswerAudit)
            .where(AnswerAudit.user_id == user.id)
            .order_by(AnswerAudit.created_at.desc())
            .limit(safe_limit)
        ).all()
    )
    return [_response(item) for item in items]


@router.get("/audits/{audit_id}", response_model=QualityReport)
def get_answer_audit(
    audit_id: str,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> QualityReport:
    item = db.get(AnswerAudit, audit_id)
    if item is None or item.user_id != user.id:
        # Keep the same response for absent and unauthorized records.
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Quality audit not found")
    return _response(item)
