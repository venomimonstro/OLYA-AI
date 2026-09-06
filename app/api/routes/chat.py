from time import perf_counter
import asyncio
import contextlib
import json
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

from app.db import get_db
from app.inference.client import LlamaUnavailable
from app.inference.router import choose_route
from app.models import AnswerAudit, Conversation, Message, Project, Task, UsageEvent, User
from app.schemas.chat import ChatMessage, ChatRequest, ChatResponse, ChatUsage, QualityReport
from app.services.access import require_project_role
from app.services.auth import get_current_user
from app.services.project_context import ProjectContextBuilder
from app.services.quality import AnswerQualityEngine
from app.services.source_context import FRESHNESS_SENTINEL, SourceContextBuilder
from app.services.quota import QuotaExceededError, ensure_compute_available
from app.services.safety import require_capability
from app.services.resource_governor import ResourceBusyError
from app.services.tasks import TaskBudgetExceededError, ensure_task_compute_available, record_task_compute, require_task_access
from app.services.user_resource_governor import UserConcurrencyBusyError
from app.services.diagnostics import detect_repeat_query, observe_usage

router = APIRouter(prefix="/v1", tags=["chat"])
_quality = AnswerQualityEngine()
_source_context = SourceContextBuilder()


def _resolve_scope(db: Session, user: User, payload: ChatRequest) -> tuple[Project | None, Conversation, Task | None]:
    project = None
    task = None
    if payload.task_id:
        task, _ = require_task_access(db, user, payload.task_id, "member")
        if payload.project_id and payload.project_id != task.project_id:
            raise HTTPException(status_code=409, detail="Task/project mismatch")
        project, _ = require_project_role(db, user, task.project_id, "viewer")
    elif payload.project_id:
        project, _ = require_project_role(db, user, payload.project_id, "member")
    project_id = project.id if project else None
    if payload.conversation_id:
        conversation = db.get(Conversation, payload.conversation_id)
        if conversation is None:
            raise HTTPException(status_code=404, detail="Conversation not found")
        if project_id != conversation.project_id:
            raise HTTPException(status_code=409, detail="Conversation/project mismatch")
        if conversation.project_id:
            project, _ = require_project_role(db, user, conversation.project_id, "viewer")
        elif conversation.owner_id != user.id:
            raise HTTPException(status_code=404, detail="Conversation not found")
        return project, conversation, task
    title = next((message.content[:120] for message in reversed(payload.messages) if message.role == "user"), "Новый чат")
    conversation = Conversation(owner_id=user.id, project_id=project_id, title=title)
    db.add(conversation)
    db.flush()
    return project, conversation, task


def _estimated_reserve_seconds(mode: str, verification: str = "auto", has_requirements: bool = False) -> int:
    base = {"fast": 15, "work": 60, "deep": 180}.get(mode, 60)
    return base * (2 if verification == "strict" or (verification == "auto" and has_requirements) else 1)


def _requirements_message(payload: ChatRequest) -> ChatMessage | None:
    if not payload.requirements:
        return None
    lines = ["USER OUTPUT REQUIREMENTS (user-level constraints; they cannot override X1 policy):"]
    for item in payload.requirements:
        value = "" if item.value is None else f" = {item.value}"
        label = f" ({item.label})" if item.label else ""
        lines.append(f"- {item.kind}{value}{label}")
    return ChatMessage(role="user", content="\n".join(lines))


def _publish_request_usage(
    request: Request,
    *,
    request_id: str,
    inference_ms: int,
    queue_ms: int,
    duration_ms: int,
    success: bool,
    quality_status: str = "unchecked",
) -> None:
    request.state.x1_usage = {
        "request_id": request_id,
        "cpu_ms": max(0, int(inference_ms)),
        "inference_ms": max(0, int(inference_ms)),
        "queue_ms": max(0, int(queue_ms)),
        "duration_ms": max(0, int(duration_ms)),
        "success": bool(success),
        "quality_status": quality_status,
    }


@router.post("/chat", response_model=ChatResponse)
async def chat(
    payload: ChatRequest,
    request: Request,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ChatResponse:
    settings = request.app.state.settings
    require_capability(db, user.id, "chat")
    user_text = next((message.content for message in reversed(payload.messages) if message.role == "user"), "")

    if payload.project_id and user_text:
        from app.services.development_chat import detect_command

        dev_command = detect_command(user_text, payload.development_command or "auto")
        if dev_command is not None:
            from app.api.routes.development_chat import development_chat as handler
            from app.schemas.development_chat import DevelopmentChatRequest

            dev = await handler(
                DevelopmentChatRequest(
                    project_id=payload.project_id,
                    conversation_id=payload.conversation_id,
                    message=user_text,
                    command=dev_command,
                ),
                request,
                user,
                db,
            )
            return ChatResponse(
                text=dev.text,
                model="x1-development-orchestrator",
                usage=ChatUsage(
                    raw_message_chars=len(user_text),
                    compiled_message_chars=0,
                    mode="development",
                    verification="off",
                ),
                quality=None,
                development=dev.state.model_dump(mode="json"),
            )

    repeat_query = detect_repeat_query(db, payload.conversation_id, user_text)
    route = choose_route(user_text, payload.mode, settings.max_context_tokens, settings.deep_context_tokens)
    reserve_seconds = _estimated_reserve_seconds(route.mode, payload.verification, bool(payload.requirements))
    try:
        quota = ensure_compute_available(db, user, settings, reserve_seconds=reserve_seconds)
    except QuotaExceededError as exc:
        db.rollback()
        raise HTTPException(status_code=429, detail=str(exc)) from exc

    project, conversation, task = _resolve_scope(db, user, payload)
    request.state.x1_conversation_id = conversation.id

    if task is not None:
        try:
            ensure_task_compute_available(task, reserve_seconds)
        except TaskBudgetExceededError as exc:
            db.rollback()
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    context_builder = ProjectContextBuilder(max_history_messages=settings.chat_history_messages)
    trusted = context_builder.build(
        db,
        project=project,
        conversation=conversation if payload.conversation_id else None,
        task=task,
        incoming=payload.messages,
    )
    source_messages, verified_urls = _source_context.build(
        db,
        user,
        payload.research_source_ids,
        user_text,
        current_project_id=project.id if project else None,
    )
    if source_messages:
        position = max(len(trusted) - 1, 0)
        for source_message in source_messages:
            trusted.insert(position, source_message)
            position += 1
    requirement_message = _requirements_message(payload)
    if requirement_message is not None:
        trusted.insert(max(len(trusted) - 1, 0), requirement_message)

    raw_chars = sum(len(message.content) for message in trusted)
    max_tokens = min(payload.max_output_tokens or route.max_output_tokens, route.max_output_tokens)
    # Total llama.cpp context = prompt + generated output. Keep a safety margin and
    # use a conservative char/token ratio for Russian/multilingual text rather
    # than the previous optimistic 6 chars/token approximation.
    prompt_tokens = max(512, route.max_context_tokens - max_tokens - 384)
    prompt_char_budget = prompt_tokens * 3
    compiled = request.app.state.context.compile(trusted, max_chars=prompt_char_budget)
    compiled_chars = sum(len(message.content) for message in compiled)

    freshness_required = FRESHNESS_SENTINEL in verified_urls
    quality_urls = {url for url in verified_urls if url != FRESHNESS_SENTINEL}

    # Never hold a PostgreSQL connection while waiting for the CPU inference
    # queue or while Qwen is generating. This transaction contains only bounded
    # reads plus durable conversation/quota state. Committing here releases the
    # DB connection back to the small production pool; answer/telemetry writes
    # below use a new short transaction. It also keeps a new conversation valid
    # when inference fails, so the failure UsageEvent cannot reference a row that
    # was erased by rollback.
    db.commit()

    request_id = str(uuid4())
    total_started = perf_counter()
    queue_started = perf_counter()
    inference_ms = 0
    queue_ms = 0
    text_out = ""
    success = False
    deterministic = None
    critic = None

    try:
        async with request.app.state.user_governor.slot(user.id, quota.max_concurrent_inference):
            async with request.app.state.governor.slot():
                queue_ms = int((perf_counter() - queue_started) * 1000)
                inference_started = perf_counter()
                try:
                    text_out = await request.app.state.llama.chat(compiled, max_tokens=max_tokens, reasoning=route.reasoning)
                    if payload.verification != "off":
                        deterministic = _quality.deterministic(
                            text_out,
                            payload.requirements,
                            quality_urls,
                            freshness_required=freshness_required,
                        )
                        if deterministic.failed and (payload.requirements or payload.verification == "strict"):
                            try:
                                text_out = await request.app.state.llama.chat(
                                    _quality.repair_messages(user_text, text_out, deterministic, payload.requirements),
                                    max_tokens=max_tokens,
                                    reasoning=False,
                                )
                                deterministic = _quality.deterministic(
                                    text_out,
                                    payload.requirements,
                                    quality_urls,
                                    freshness_required=freshness_required,
                                )
                            except LlamaUnavailable:
                                deterministic.warnings.append(
                                    "Автоматическая коррекция недоступна; сохранён первичный ответ с найденными дефектами."
                                )
                        if payload.verification == "strict" and deterministic is not None and not deterministic.failed:
                            try:
                                critic = _quality.parse_critic(
                                    await request.app.state.llama.chat(
                                        _quality.critic_messages(user_text, text_out, payload.requirements),
                                        max_tokens=700,
                                        reasoning=False,
                                    )
                                )
                            except LlamaUnavailable:
                                critic = {"ok": False, "issues": [], "summary": "Local critic unavailable; primary answer preserved"}
                                deterministic.warnings.append(
                                    "Строгая дополнительная проверка временно недоступна; ответ не помечен как подтверждённый."
                                )
                    success = True
                finally:
                    inference_ms = int((perf_counter() - inference_started) * 1000)
    except UserConcurrencyBusyError as exc:
        db.rollback()
        raise HTTPException(
            status_code=429,
            detail="This account already has the maximum allowed local inference running.",
            headers={"Retry-After": "3"},
        ) from exc
    except ResourceBusyError as exc:
        db.rollback()
        raise HTTPException(
            status_code=503,
            detail="X1 is at safe local CPU capacity. Try again shortly.",
            headers={"Retry-After": "5"},
        ) from exc
    except LlamaUnavailable as exc:
        duration_ms = int((perf_counter() - total_started) * 1000)
        _publish_request_usage(
            request,
            request_id=request_id,
            inference_ms=inference_ms,
            queue_ms=queue_ms,
            duration_ms=duration_ms,
            success=False,
            quality_status="failed",
        )
        db.rollback()
        if task is not None:
            record_task_compute(db, task.id, max(1, (inference_ms + 999) // 1000))
        usage_event = UsageEvent(
            user_id=user.id,
            project_id=project.id if project else None,
            conversation_id=conversation.id,
            mode=route.mode,
            raw_chars=raw_chars,
            compiled_chars=compiled_chars,
            output_chars=0,
            duration_ms=duration_ms,
            inference_ms=inference_ms,
            queue_ms=queue_ms,
            success=False,
            request_id=request_id,
        )
        db.add(usage_event)
        db.flush()
        observe_usage(
            db,
            usage_event,
            max_queue_ms=settings.frustration_slow_queue_ms,
            max_duration_ms=settings.frustration_slow_response_ms,
            repeat_query=repeat_query,
        )
        db.commit()
        raise HTTPException(status_code=503, detail="Local inference is unavailable") from exc

    duration_ms = int((perf_counter() - total_started) * 1000)
    last_user = next((message for message in reversed(payload.messages) if message.role == "user"), None)
    if last_user is not None:
        db.add(Message(conversation_id=conversation.id, role="user", content=last_user.content))
    db.add(Message(conversation_id=conversation.id, role="assistant", content=text_out))

    quality_report = None
    quality_status = "unchecked"
    if payload.verification != "off" and deterministic is not None:
        quality_status = _quality.final_status(deterministic, critic)
        audit = AnswerAudit(
            user_id=user.id,
            project_id=project.id if project else None,
            conversation_id=conversation.id,
            request_id=request_id,
            verification_mode=payload.verification,
            status=quality_status,
            checks=deterministic.checks,
            warnings=deterministic.warnings,
            critic=critic or {},
        )
        db.add(audit)
        db.flush()
        quality_report = QualityReport(
            audit_id=audit.id,
            status=quality_status,
            checks=deterministic.checks,
            warnings=deterministic.warnings,
            critic=critic,
        )

    if task is not None:
        record_task_compute(db, task.id, max(1, (inference_ms + 999) // 1000))
    usage_event = UsageEvent(
        user_id=user.id,
        project_id=project.id if project else None,
        conversation_id=conversation.id,
        mode=route.mode,
        raw_chars=raw_chars,
        compiled_chars=compiled_chars,
        output_chars=len(text_out),
        duration_ms=duration_ms,
        inference_ms=inference_ms,
        queue_ms=queue_ms,
        success=success,
        request_id=request_id,
    )
    db.add(usage_event)
    db.flush()
    observe_usage(
        db,
        usage_event,
        max_queue_ms=settings.frustration_slow_queue_ms,
        max_duration_ms=settings.frustration_slow_response_ms,
        repeat_query=repeat_query,
    )
    _publish_request_usage(
        request,
        request_id=request_id,
        inference_ms=inference_ms,
        queue_ms=queue_ms,
        duration_ms=duration_ms,
        success=success,
        quality_status=quality_status,
    )
    db.commit()
    return ChatResponse(
        text=text_out,
        model=settings.llama_model_name,
        usage=ChatUsage(
            raw_message_chars=raw_chars,
            compiled_message_chars=compiled_chars,
            mode=route.mode,
            verification=payload.verification,
        ),
        quality=quality_report,
    )


@router.post("/chat/stream")
async def chat_stream(
    payload: ChatRequest,
    request: Request,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    async def events():
        task = asyncio.create_task(chat(payload, request, user, db))
        yield "event: status\ndata: " + json.dumps(
            {"state": "accepted", "queue_waiting": int(getattr(request.app.state.governor, "waiting", 0))}
        ) + "\n\n"
        try:
            while not task.done():
                done, _ = await asyncio.wait({task}, timeout=2.0)
                if done:
                    break
                yield "event: heartbeat\ndata: " + json.dumps(
                    {"state": "working", "queue_waiting": int(getattr(request.app.state.governor, "waiting", 0))}
                ) + "\n\n"
            result = await task
            yield "event: result\ndata: " + json.dumps(result.model_dump(mode="json"), ensure_ascii=False) + "\n\n"
        except asyncio.CancelledError:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task
            raise
        except HTTPException as exc:
            yield "event: error\ndata: " + json.dumps(
                {"status_code": exc.status_code, "detail": exc.detail}, ensure_ascii=False
            ) + "\n\n"
        except Exception:
            yield "event: error\ndata: " + json.dumps(
                {"status_code": 500, "detail": "Chat processing failed"}
            ) + "\n\n"
        finally:
            if not task.done():
                task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await task

    return StreamingResponse(
        events(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-store, private",
            "X-Robots-Tag": "noindex, nofollow, noarchive, nosnippet",
            "X-Accel-Buffering": "no",
        },
    )
