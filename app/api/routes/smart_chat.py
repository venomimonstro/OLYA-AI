from __future__ import annotations

import asyncio
import contextlib
import json
from time import perf_counter

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

from app.api.routes import chat as legacy_chat
from app.db import SessionLocal, get_db
from app.models import User
from app.schemas.chat import ChatRequest, ChatResponse, ChatRunStatus
from app.services.auth import get_current_user
from app.services.chat_runtime import ActiveChatJob, ChatRunConflict, ChatRunSnapshot, chat_execution_manager
from app.services.task_solver import execute_task_solver, reset_task_solver_context, set_task_solver_context

router = APIRouter(prefix="/v1", tags=["chat"])


def _latest_user_text(payload: ChatRequest) -> str:
    return next((message.content for message in reversed(payload.messages) if message.role == "user"), "")


async def _smart_managed_runner(payload: ChatRequest, request: Request, user_id: str, job: ActiveChatJob) -> ChatResponse:
    with SessionLocal() as job_db:
        job_user = job_db.get(User, user_id)
        if job_user is None:
            raise HTTPException(status_code=401, detail="Account is no longer available")
        request.state.x1_chat_run_id = job.run_id
        managed_payload = payload.model_copy(update={"client_request_id": job.client_request_id})
        execution = None
        context_token = None
        if not managed_payload.research_source_ids:
            question = _latest_user_text(managed_payload)
            if question:
                execution = await execute_task_solver(
                    db=job_db,
                    user=job_user,
                    settings=request.app.state.settings,
                    discovery=request.app.state.discovery,
                    fetcher=request.app.state.research,
                    question=question,
                    # Automatic sources are private to the account rather than
                    # project-owned. The normal SourceContextBuilder still
                    # enforces access before they enter a prompt.
                    project_id=None,
                )
                if execution.source_ids:
                    managed_payload = managed_payload.model_copy(update={"research_source_ids": execution.source_ids})
                context_token = set_task_solver_context(execution.context_messages)
        try:
            result = await legacy_chat._chat_impl(
                managed_payload,
                request,
                job_user,
                job_db,
                on_token=job.token,
                on_replace=job.replace,
            )
        finally:
            if context_token is not None:
                reset_task_solver_context(context_token)
        result.run_id = job.run_id
        result.client_request_id = job.client_request_id
        if execution is not None:
            result.task_execution = execution.public_metadata()
        return result


def _snapshot_response(snapshot: ChatRunSnapshot) -> ChatResponse:
    if snapshot.status == "succeeded" and snapshot.result:
        return ChatResponse.model_validate(snapshot.result)
    if snapshot.status == "cancelled":
        raise HTTPException(status_code=409, detail="Chat run was cancelled")
    if snapshot.status == "interrupted":
        raise HTTPException(status_code=503, detail=snapshot.error_detail or "Chat run was interrupted", headers={"Retry-After": "1"})
    status_code = 503
    if snapshot.error_code.startswith("http_"):
        try:
            status_code = int(snapshot.error_code.split("_", 1)[1])
        except ValueError:
            status_code = 503
    raise HTTPException(status_code=status_code, detail=snapshot.error_detail or "Chat run failed")


@router.post("/chat", response_model=ChatResponse)
async def chat(
    payload: ChatRequest,
    request: Request,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ChatResponse:
    _ = db

    async def runner(job: ActiveChatJob) -> ChatResponse:
        return await _smart_managed_runner(payload, request, user.id, job)

    try:
        execution = await chat_execution_manager.start_or_attach(user_id=user.id, payload=payload, runner=runner)
    except ChatRunConflict as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    if isinstance(execution, ChatRunSnapshot):
        return _snapshot_response(execution)
    try:
        await asyncio.shield(execution.task)
    except asyncio.CancelledError:
        raise
    snapshot = await chat_execution_manager.status(user_id=user.id, client_request_id=execution.client_request_id)
    return _snapshot_response(snapshot)


def _sse(event: str, payload: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(payload, ensure_ascii=False, separators=(',', ':'))}\n\n"


@router.get("/chat/runs/{client_request_id}", response_model=ChatRunStatus)
async def chat_run_status(client_request_id: str, user: User = Depends(get_current_user)) -> ChatRunStatus:
    try:
        snapshot = await chat_execution_manager.status(user_id=user.id, client_request_id=client_request_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Chat run not found") from exc
    return ChatRunStatus.model_validate(snapshot.as_dict())


@router.post("/chat/runs/{client_request_id}/cancel", response_model=ChatRunStatus)
async def cancel_chat_run(client_request_id: str, user: User = Depends(get_current_user)) -> ChatRunStatus:
    try:
        snapshot = await chat_execution_manager.cancel(user_id=user.id, client_request_id=client_request_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Chat run not found") from exc
    return ChatRunStatus.model_validate(snapshot.as_dict())


@router.post("/chat/stream")
async def chat_stream(
    payload: ChatRequest,
    request: Request,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    _ = db

    async def runner(job: ActiveChatJob) -> ChatResponse:
        return await _smart_managed_runner(payload, request, user.id, job)

    async def events():
        queue: asyncio.Queue | None = None
        job: ActiveChatJob | None = None
        legacy_disconnect_cancels = payload.client_request_id is None
        try:
            try:
                snapshot, queue, job = await chat_execution_manager.attach_stream(user_id=user.id, payload=payload, runner=runner)
            except ChatRunConflict as exc:
                yield _sse("error", {"status_code": 409, "detail": str(exc), "retryable": False})
                return
            yield _sse("status", {
                "state": snapshot.status,
                "run_id": snapshot.run_id,
                "client_request_id": snapshot.client_request_id,
                "conversation_id": snapshot.conversation_id,
                "resumed": bool(snapshot.partial_text),
                "queue_waiting": int(getattr(request.app.state.governor, "waiting", 0)),
            })
            if snapshot.partial_text:
                yield _sse("replace", {"text": snapshot.partial_text, "run_id": snapshot.run_id, "client_request_id": snapshot.client_request_id, "reason": "reconnect_snapshot"})
            if snapshot.status == "succeeded" and snapshot.result:
                yield _sse("result", snapshot.result); return
            if snapshot.status in {"failed", "interrupted"}:
                yield _sse("error", {"status_code": 503, "detail": snapshot.error_detail or "Chat run failed", "retryable": snapshot.retryable}); return
            if snapshot.status == "cancelled":
                yield _sse("cancelled", {"detail": snapshot.error_detail or "Chat run was cancelled"}); return

            heartbeat_at = perf_counter()
            while True:
                if await request.is_disconnected():
                    if legacy_disconnect_cancels:
                        await chat_execution_manager.cancel(user_id=user.id, client_request_id=snapshot.client_request_id)
                    return
                try:
                    event, data = await asyncio.wait_for(queue.get(), timeout=0.25)
                except TimeoutError:
                    if job is not None and job.task is not None and job.task.done():
                        terminal = await chat_execution_manager.status(user_id=user.id, client_request_id=snapshot.client_request_id)
                        if terminal.status == "succeeded" and terminal.result:
                            yield _sse("result", terminal.result)
                        elif terminal.status == "cancelled":
                            yield _sse("cancelled", {"detail": terminal.error_detail})
                        else:
                            yield _sse("error", {"status_code": 503, "detail": terminal.error_detail or "Chat run failed", "retryable": terminal.retryable})
                        return
                    now = perf_counter()
                    if now - heartbeat_at >= 2.0:
                        heartbeat_at = now
                        yield _sse("heartbeat", {"state": "working", "run_id": snapshot.run_id, "client_request_id": snapshot.client_request_id, "queue_waiting": int(getattr(request.app.state.governor, "waiting", 0))})
                    continue
                yield _sse(event, data)
                if event in {"result", "error", "cancelled"}:
                    return
        except asyncio.CancelledError:
            if legacy_disconnect_cancels and job is not None:
                with contextlib.suppress(Exception):
                    await chat_execution_manager.cancel(user_id=user.id, client_request_id=job.client_request_id)
            raise
        finally:
            chat_execution_manager.detach(job, queue)

    return StreamingResponse(
        events(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-store, private", "X-Robots-Tag": "noindex, nofollow, noarchive, nosnippet", "X-Accel-Buffering": "no"},
    )


# API clients import chat_handler from app.api.routes.chat. Point that module's
# public handler at the same smart entry point after this module is imported,
# while leaving its proven _chat_impl as the single execution core.
legacy_chat.chat = chat
