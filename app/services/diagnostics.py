from __future__ import annotations

import hashlib
import re
from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import FrustrationEvent, Message, UsageEvent


def _fingerprint(kind: str, user_id: str, conversation_id: str | None, request_id: str | None) -> str:
    raw = f"{kind}|{user_id}|{conversation_id or ''}|{request_id or ''}"
    return hashlib.sha256(raw.encode()).hexdigest()


def add_event(db: Session, *, user_id: str, kind: str, severity: str = "warning", source: str = "server",
              project_id: str | None = None, conversation_id: str | None = None, request_id: str | None = None,
              metrics: dict | None = None) -> FrustrationEvent:
    fingerprint = _fingerprint(kind, user_id, conversation_id, request_id)
    if request_id:
        for pending in db.new:
            if isinstance(pending, FrustrationEvent) and pending.fingerprint == fingerprint:
                return pending
        existing = db.scalar(select(FrustrationEvent).where(FrustrationEvent.fingerprint == fingerprint))
        if existing is not None:
            return existing
    event = FrustrationEvent(
        user_id=user_id, project_id=project_id, conversation_id=conversation_id, request_id=request_id,
        kind=kind, severity=severity, source=source, metrics=metrics or {}, fingerprint=fingerprint,
    )
    db.add(event)
    return event


def normalize_query(text: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[^\w\s]", " ", text.lower())).strip()


def detect_repeat_query(db: Session, conversation_id: str | None, current_text: str) -> bool:
    if not conversation_id or not current_text.strip():
        return False
    recent = db.scalars(
        select(Message).where(Message.conversation_id == conversation_id, Message.role == "user")
        .order_by(Message.created_at.desc()).limit(4)
    ).all()
    current = normalize_query(current_text)
    return bool(current and any(normalize_query(m.content) == current for m in recent))


def observe_usage(db: Session, usage: UsageEvent, *, max_queue_ms: int = 5000, max_duration_ms: int = 120000,
                  repeat_query: bool = False) -> None:
    common = dict(user_id=usage.user_id, project_id=usage.project_id, conversation_id=usage.conversation_id, request_id=usage.request_id)
    if not usage.success:
        add_event(db, **common, kind="inference_failure", severity="critical", metrics={"inference_ms": usage.inference_ms, "queue_ms": usage.queue_ms})
    if usage.queue_ms >= max_queue_ms:
        add_event(db, **common, kind="slow_queue", severity="warning", metrics={"queue_ms": usage.queue_ms})
    if usage.duration_ms >= max_duration_ms:
        add_event(db, **common, kind="slow_response", severity="warning", metrics={"duration_ms": usage.duration_ms, "inference_ms": usage.inference_ms})
    if repeat_query:
        add_event(db, **common, kind="repeat_query", severity="warning", metrics={"signal": "exact_normalized_repeat"})
    if usage.raw_chars > 0 and usage.compiled_chars > usage.raw_chars * 1.25:
        add_event(db, **common, kind="context_inflation", severity="warning", metrics={"raw_chars": usage.raw_chars, "compiled_chars": usage.compiled_chars})


def summary(db: Session, hours: int = 24) -> dict:
    since = datetime.now(timezone.utc) - timedelta(hours=hours)
    rows = db.execute(
        select(FrustrationEvent.kind, FrustrationEvent.severity, func.count())
        .where(FrustrationEvent.created_at >= since)
        .group_by(FrustrationEvent.kind, FrustrationEvent.severity)
    ).all()
    total = sum(int(r[2]) for r in rows)
    return {"hours": hours, "total": total, "by_kind": [{"kind": r[0], "severity": r[1], "count": int(r[2])} for r in rows]}
