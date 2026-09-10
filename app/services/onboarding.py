from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Conversation, ProductEvent, Project, ProjectFile, UsageEvent, User, UserOnboarding


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _aware(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


def _first_by_created(db: Session, model, *criteria):
    return db.scalar(select(model).where(*criteria).order_by(model.created_at.asc()).limit(1))


def _lock_user(db: Session, user_id: str) -> None:
    # Serializes onboarding creation/reconciliation with itself on PostgreSQL.
    db.execute(select(User.id).where(User.id == user_id).with_for_update())


def _state(db: Session, user: User) -> UserOnboarding:
    row = db.get(UserOnboarding, user.id)
    if row is None:
        row = UserOnboarding(user_id=user.id, started_at=user.created_at or _now(), updated_at=_now())
        db.add(row)
        db.flush()
    return row


def _event_once(
    db: Session,
    user_id: str,
    name: str,
    *,
    occurred_at: datetime | None = None,
    project_id: str | None = None,
    metadata: dict | None = None,
) -> None:
    key = f"onboarding:{user_id}:{name}"
    exists = db.scalar(select(ProductEvent.id).where(ProductEvent.dedupe_key == key).limit(1))
    if exists is not None:
        return
    db.add(
        ProductEvent(
            user_id=user_id,
            project_id=project_id,
            event_name=name,
            dedupe_key=key,
            metadata_json=metadata or {},
            created_at=occurred_at or _now(),
        )
    )


def reconcile_onboarding(db: Session, user: User) -> UserOnboarding:
    """Rebuild first-value milestones from authoritative persisted product facts.

    This function is intentionally idempotent. If a process crashes after the
    product action but before onboarding is observed, the next status request
    reconstructs the missing milestone from durable rows.
    """
    _lock_user(db, user.id)
    row = _state(db, user)
    changed = False

    _event_once(db, user.id, "registered", occurred_at=user.created_at or row.started_at)

    if row.first_chat_at is None:
        conversation = _first_by_created(db, Conversation, Conversation.owner_id == user.id)
        if conversation is not None:
            row.first_chat_at = conversation.created_at
            _event_once(
                db,
                user.id,
                "first_chat_started",
                occurred_at=conversation.created_at,
                project_id=conversation.project_id,
                metadata={"conversation_id": conversation.id},
            )
            changed = True

    if row.first_successful_answer_at is None:
        usage = _first_by_created(db, UsageEvent, UsageEvent.user_id == user.id, UsageEvent.success.is_(True))
        if usage is not None:
            row.first_successful_answer_at = usage.created_at
            _event_once(
                db,
                user.id,
                "first_successful_answer",
                occurred_at=usage.created_at,
                project_id=usage.project_id,
                metadata={"conversation_id": usage.conversation_id, "request_id": usage.request_id},
            )
            changed = True

    if row.first_project_at is None:
        project = _first_by_created(db, Project, Project.owner_id == user.id)
        if project is not None:
            row.first_project_at = project.created_at
            _event_once(
                db,
                user.id,
                "first_project_created",
                occurred_at=project.created_at,
                project_id=project.id,
            )
            changed = True

    if row.first_file_at is None:
        file = _first_by_created(
            db,
            ProjectFile,
            ProjectFile.uploaded_by == user.id,
            ProjectFile.status == "ready",
        )
        if file is not None:
            row.first_file_at = file.created_at
            _event_once(
                db,
                user.id,
                "first_file_uploaded",
                occurred_at=file.created_at,
                project_id=file.project_id,
                metadata={"file_id": file.id},
            )
            changed = True

    if row.completed_at is None:
        candidates = [row.first_successful_answer_at, row.first_project_at, row.first_file_at]
        candidates = [value for value in candidates if value is not None]
        if candidates:
            first_value = min(candidates, key=lambda value: _aware(value).timestamp())
            row.completed_at = first_value
            row.show_again = False
            _event_once(db, user.id, "onboarding_completed", occurred_at=first_value)
            changed = True

    if changed:
        row.updated_at = _now()
    db.flush()
    return row


def dismiss_onboarding(db: Session, user: User) -> UserOnboarding:
    _lock_user(db, user.id)
    row = _state(db, user)
    now = _now()
    row.dismissed_at = now
    row.show_again = False
    row.updated_at = now
    _event_once(db, user.id, "onboarding_dismissed", occurred_at=now)
    db.flush()
    return row


def reopen_onboarding(db: Session, user: User) -> UserOnboarding:
    _lock_user(db, user.id)
    row = _state(db, user)
    now = _now()
    row.reopened_at = now
    row.show_again = True
    row.updated_at = now
    _event_once(db, user.id, "onboarding_reopened", occurred_at=now)
    db.flush()
    return row


def onboarding_payload(row: UserOnboarding) -> dict:
    visible = bool(row.show_again or (row.completed_at is None and row.dismissed_at is None))
    milestones = {
        "chat_started": row.first_chat_at,
        "successful_answer": row.first_successful_answer_at,
        "project_created": row.first_project_at,
        "file_uploaded": row.first_file_at,
    }
    if row.first_successful_answer_at is None:
        recommended = "chat"
    elif row.first_project_at is None:
        recommended = "project"
    elif row.first_file_at is None:
        recommended = "file"
    else:
        recommended = None
    return {
        "visible": visible,
        "completed": row.completed_at is not None,
        "dismissed": row.dismissed_at is not None and not row.show_again,
        "show_again": bool(row.show_again),
        "started_at": row.started_at,
        "completed_at": row.completed_at,
        "dismissed_at": row.dismissed_at,
        "reopened_at": row.reopened_at,
        "recommended_action": recommended,
        "milestones": milestones,
    }
