from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Response, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_db
from app.models import (
    AuthSession,
    Conversation,
    FileChunk,
    Message,
    Project,
    ProjectFile,
    ProjectMember,
    ProjectMemory,
    Task,
    TaskEvidence,
    UsageEvent,
    User,
)
from app.services.auth import get_current_user

router = APIRouter(prefix="/v1/account", tags=["account"])


def _iso(value):
    return value.isoformat() if value is not None else None


@router.get("/export")
def export_account_data(user: User = Depends(get_current_user), db: Session = Depends(get_db)) -> dict:
    """Portable export of data owned/created by this account.

    Shared project data created by other members is intentionally not copied into
    another user's export. Project owners get project metadata, while user-created
    records are included regardless of project ownership.
    """
    projects = list(db.scalars(select(Project).where(Project.owner_id == user.id)).all())
    memberships = list(db.scalars(select(ProjectMember).where(ProjectMember.user_id == user.id)).all())
    conversations = list(db.scalars(select(Conversation).where(Conversation.owner_id == user.id)).all())
    conversation_ids = [item.id for item in conversations]
    messages = list(db.scalars(select(Message).where(Message.conversation_id.in_(conversation_ids))).all()) if conversation_ids else []
    memories = list(db.scalars(select(ProjectMemory).where(ProjectMemory.created_by == user.id)).all())
    files = list(db.scalars(select(ProjectFile).where(ProjectFile.uploaded_by == user.id)).all())
    tasks = list(db.scalars(select(Task).where(Task.created_by == user.id)).all())
    evidence = list(db.scalars(select(TaskEvidence).where(TaskEvidence.created_by == user.id)).all())
    usage = list(db.scalars(select(UsageEvent).where(UsageEvent.user_id == user.id).order_by(UsageEvent.created_at)).all())

    return {
        "exported_at": datetime.now(timezone.utc).isoformat(),
        "account": {"id": user.id, "email": user.email, "display_name": user.display_name, "created_at": _iso(user.created_at)},
        "owned_projects": [
            {"id": x.id, "name": x.name, "description": x.description, "instructions": x.instructions, "created_at": _iso(x.created_at)}
            for x in projects
        ],
        "memberships": [{"project_id": x.project_id, "role": x.role, "created_at": _iso(x.created_at)} for x in memberships],
        "conversations": [{"id": x.id, "project_id": x.project_id, "title": x.title, "created_at": _iso(x.created_at)} for x in conversations],
        "messages": [{"id": x.id, "conversation_id": x.conversation_id, "role": x.role, "content": x.content, "created_at": _iso(x.created_at)} for x in messages],
        "memories_created": [{"id": x.id, "project_id": x.project_id, "key": x.key, "value": x.value, "source": x.source} for x in memories],
        "files_uploaded": [
            {"id": x.id, "project_id": x.project_id, "logical_name": x.logical_name, "original_name": x.original_name, "version": x.version, "sha256": x.content_sha256, "status": x.status, "created_at": _iso(x.created_at)}
            for x in files
        ],
        "tasks_created": [
            {"id": x.id, "project_id": x.project_id, "title": x.title, "goal": x.goal, "constraints": x.constraints, "status": x.status, "created_at": _iso(x.created_at)}
            for x in tasks
        ],
        "evidence_created": [
            {"id": x.id, "task_id": x.task_id, "criterion_id": x.criterion_id, "kind": x.kind, "source_ref": x.source_ref, "summary": x.summary, "state": x.state, "created_at": _iso(x.created_at)}
            for x in evidence
        ],
        "usage": [
            {"id": x.id, "project_id": x.project_id, "conversation_id": x.conversation_id, "mode": x.mode, "inference_ms": x.inference_ms, "queue_ms": x.queue_ms, "success": x.success, "created_at": _iso(x.created_at)}
            for x in usage
        ],
    }


@router.post("/deactivate", status_code=status.HTTP_204_NO_CONTENT)
def deactivate_account(user: User = Depends(get_current_user), db: Session = Depends(get_db)) -> Response:
    now = datetime.now(timezone.utc)
    user.is_active = False
    sessions = list(db.scalars(select(AuthSession).where(AuthSession.user_id == user.id, AuthSession.revoked_at.is_(None))).all())
    for session in sessions:
        session.revoked_at = now
    db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)
