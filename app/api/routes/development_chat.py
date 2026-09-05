from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy.orm import Session

from app.api.routes.engineering import execute_role as execute_engineering_role
from app.api.routes.execution import execute as execute_engineering_execution
from app.db import get_db
from app.models import Conversation, EngineeringExecution, EngineeringRun, Message, User
from app.schemas.development_chat import DevelopmentChatRequest, DevelopmentChatResponse
from app.schemas.engineering import ExecuteApprovedRequest, ExecuteRoleRequest
from app.services.access import require_project_role
from app.services.auth import get_current_user
from app.services.development_chat import (
    DevelopmentChatError,
    commit_verified_execution,
    detect_command,
    get_or_create_session,
    prepare_next_step,
    rollback_latest,
    serialize_state,
    set_paused,
)

router = APIRouter(prefix="/v1/development-chat", tags=["development-chat"])


def _conversation(db: Session, user: User, project_id: str, conversation_id: str | None, title: str) -> Conversation:
    require_project_role(db, user, project_id, "member")
    if conversation_id:
        row = db.get(Conversation, conversation_id)
        if row is None or row.project_id != project_id:
            raise HTTPException(status_code=404, detail="Conversation not found")
        if row.owner_id != user.id:
            # Project members may read shared project context, but chat-control ownership
            # stays with the user who owns this conversation.
            raise HTTPException(status_code=403, detail="Development conversation is owned by another user")
        return row
    row = Conversation(owner_id=user.id, project_id=project_id, title=title[:120] or "Development")
    db.add(row); db.flush()
    return row


def _reply(db: Session, conversation: Conversation, user_text: str, text: str) -> None:
    db.add(Message(conversation_id=conversation.id, role="user", content=user_text))
    db.add(Message(conversation_id=conversation.id, role="assistant", content=text))
    conversation.updated_at = __import__("app.models", fromlist=["utcnow"]).utcnow()


@router.post("", response_model=DevelopmentChatResponse)
async def development_chat(
    payload: DevelopmentChatRequest,
    request: Request,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    command = detect_command(payload.message, payload.command)
    if command is None:
        raise HTTPException(
            status_code=409,
            detail="Message is not an explicit development control command; use ordinary /v1/chat or set command explicitly",
        )
    conversation = _conversation(db, user, payload.project_id, payload.conversation_id, payload.message)
    try:
        session = get_or_create_session(db, project_id=payload.project_id, conversation=conversation, user_id=user.id)
        text = ""
        action = command
        if command == "status":
            session.last_action = "status"
            session.last_summary = "Development status refreshed"
            text = session.last_summary
        elif command == "pause":
            text = set_paused(session, True)
        elif command == "resume":
            text = set_paused(session, False)
        elif command == "rollback":
            text = rollback_latest(db, session)
        else:
            step, obj = prepare_next_step(db, session, user_id=user.id)
            action = step
            if step == "execute_role":
                run = obj
                assert isinstance(run, EngineeringRun)
                role = run.current_role or "engineering"
                await execute_engineering_role(
                    run.id,
                    ExecuteRoleRequest(expected_version=run.state_version),
                    request,
                    user,
                    db,
                )
                fresh = db.get(EngineeringRun, run.id)
                session.engineering_run_id = fresh.id if fresh else run.id
                session.last_action = f"role:{role}"
                session.last_summary = f"Engineering role completed: {role}"
                text = session.last_summary
            elif step == "execute_implementation":
                execution = obj
                assert isinstance(execution, EngineeringExecution)
                await execute_engineering_execution(
                    execution.id,
                    ExecuteApprovedRequest(expected_version=execution.state_version),
                    request,
                    user,
                    db,
                )
                fresh = db.get(EngineeringExecution, execution.id)
                session.execution_id = fresh.id if fresh else execution.id
                if fresh is not None and fresh.status == "verified":
                    commit = commit_verified_execution(db, session, user_id=user.id)
                    if commit:
                        text = f"Implementation verified and committed locally: {commit['head_after'][:8]}"
                    else:
                        session.last_action = "verified"
                        session.last_summary = "Implementation verification passed"
                        text = session.last_summary
                else:
                    session.last_action = "execution"
                    session.last_summary = f"Implementation execution status: {fresh.status if fresh else 'unknown'}"
                    text = session.last_summary
            elif step == "verified":
                commit = commit_verified_execution(db, session, user_id=user.id)
                if commit:
                    text = f"Verified implementation committed locally: {commit['head_after'][:8]}"
                    action = "local_commit"
                else:
                    text = "Implementation is verified; acceptance criteria still control task completion"
                    session.last_action = "verified"
                    session.last_summary = text
            else:
                text = session.last_summary or {
                    "activated_sprint": "Next sprint activated",
                    "completed_sprint": "Current sprint completed",
                    "engineering_created": "Engineering team initialized for current work item",
                    "execution_created": "Approved implementation execution prepared",
                    "project_completed": "Development plan is complete",
                    "blocked": "Development is blocked and needs review",
                    "paused": "Development is paused for this chat",
                }.get(step, step)
                if not session.last_summary:
                    session.last_action = step
                    session.last_summary = text
        _reply(db, conversation, payload.message, text)
        db.commit(); db.refresh(session)
        return DevelopmentChatResponse(text=text, action=action, state=serialize_state(db, session))
    except HTTPException:
        db.rollback(); raise
    except DevelopmentChatError as exc:
        db.rollback(); raise HTTPException(status_code=409, detail=str(exc)) from exc
