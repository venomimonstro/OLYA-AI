from __future__ import annotations

import asyncio
import contextlib
import json
from collections.abc import Awaitable, Callable
from datetime import datetime, timezone
from time import perf_counter
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import SessionLocal, get_db
from app.inference.client import LlamaGeneration, LlamaUnavailable
from app.inference.router import choose_route
from app.models import AnswerAudit, ChatRun, Conversation, Message, Project, Task, UsageEvent, User
from app.schemas.chat import ChatMessage, ChatRequest, ChatResponse, ChatRunStatus, ChatUsage, QualityReport
from app.services.access import require_project_role
from app.services.auth import get_current_user
from app.services.chat_runtime import (
    ActiveChatJob,
    ChatRunConflict,
    ChatRunSnapshot,
    chat_execution_manager,
)
from app.services.conditional_verification import (
    audit_with_critic_issues,
    critic_has_repairable_issue,
    plan_verification,
)
from app.services.diagnostics import detect_repeat_query, observe_usage
from app.services.freshness import classify_freshness
from app.services.project_context import ProjectContextBuilder
from app.services.quality import AnswerQualityEngine
from app.services.quota import QuotaExceededError, ensure_compute_available
from app.services.resource_governor import ResourceBusyError
from app.services.safety import require_capability
from app.services.source_context import FRESHNESS_SENTINEL, SourceContextBuilder
from app.services.tasks import TaskBudgetExceededError, ensure_task_compute_available, record_task_compute, require_task_access
from app.services.user_resource_governor import UserConcurrencyBusyError

router = APIRouter(prefix="/v1", tags=["chat"])
_quality = AnswerQualityEngine()
_source_context = SourceContextBuilder()
TokenSink = Callable[[str], Awaitable[None]]
_MAX_VERIFICATION_EXTRA_INFERENCES = 2


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


def _persist_accepted_user_turn(db: Session, conversation: Conversation, message: ChatMessage | None) -> Message | None:
    if message is None:
        return None
    latest = db.scalar(
        select(Message)
        .where(Message.conversation_id == conversation.id)
        .order_by(Message.created_at.desc())
        .limit(1)
    )
    # A failed/cancelled attempt already owns this canonical user turn. Reusing
    # it on retry preserves the transcript without manufacturing duplicate user
    # messages. After a successful assistant turn, identical user text is a new
    # intentional turn and is therefore persisted normally.
    if latest is not None and latest.role == "user" and latest.content == message.content:
        return latest
    row = Message(conversation_id=conversation.id, role="user", content=message.content)
    db.add(row)
    db.flush()
    return row


def _estimated_reserve_seconds(mode: str, extra_inferences: int = 0) -> int:
    base = {"fast": 15, "work": 60, "deep": 180}.get(mode, 60)
    return base * (1 + max(0, min(_MAX_VERIFICATION_EXTRA_INFERENCES, int(extra_inferences))))


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
    ttft_ms: int | None = None,
    output_tokens: int = 0,
    tokens_per_second: float | None = None,
    cancelled: bool = False,
) -> None:
    request.state.x1_usage = {
        "request_id": request_id,
        "cpu_ms": max(0, int(inference_ms)),
        "inference_ms": max(0, int(inference_ms)),
        "queue_ms": max(0, int(queue_ms)),
        "duration_ms": max(0, int(duration_ms)),
        "success": bool(success),
        "quality_status": quality_status,
        "ttft_ms": None if ttft_ms is None else max(0, int(ttft_ms)),
        "output_tokens": max(0, int(output_tokens)),
        "tokens_per_second": None if tokens_per_second is None else max(0.0, float(tokens_per_second)),
        "cancelled": bool(cancelled),
    }


def _usage_event(
    *,
    user: User,
    project: Project | None,
    conversation: Conversation,
    mode: str,
    raw_chars: int,
    compiled_chars: int,
    output_chars: int,
    duration_ms: int,
    inference_ms: int,
    queue_ms: int,
    success: bool,
    request_id: str,
) -> UsageEvent:
    return UsageEvent(
        user_id=user.id,
        project_id=project.id if project else None,
        conversation_id=conversation.id,
        mode=mode,
        raw_chars=raw_chars,
        compiled_chars=compiled_chars,
        output_chars=max(0, int(output_chars)),
        duration_ms=max(0, int(duration_ms)),
        inference_ms=max(0, int(inference_ms)),
        queue_ms=max(0, int(queue_ms)),
        success=bool(success),
        request_id=request_id,
    )


async def _primary_generation(
    request: Request,
    messages: list[ChatMessage],
    *,
    max_tokens: int,
    reasoning: bool,
    on_token: TokenSink | None,
) -> LlamaGeneration:
    """Use streaming inference and retry once only before visible output.

    A short llama.cpp restart/connect race before TTFT is safe to replay. Once a
    token has reached the caller, replaying a generation could concatenate two
    different continuations, so post-TTFT failures remain explicit/retryable.
    """
    llama = request.app.state.llama
    generate = getattr(llama, "generate", None)
    if callable(generate):
        emitted = False

        async def guarded_sink(text: str) -> None:
            nonlocal emitted
            emitted = True
            if on_token is not None:
                await on_token(text)

        try:
            return await generate(
                messages,
                max_tokens=max_tokens,
                reasoning=reasoning,
                on_token=guarded_sink if on_token is not None else None,
            )
        except LlamaUnavailable:
            if emitted:
                raise
            await asyncio.sleep(0.75)
            return await generate(
                messages,
                max_tokens=max_tokens,
                reasoning=reasoning,
                on_token=guarded_sink if on_token is not None else None,
            )
    chat = getattr(llama, "chat", None)
    if not callable(chat):
        raise LlamaUnavailable("local inference backend does not expose generate or chat")
    started = perf_counter()
    text = await chat(messages, max_tokens=max_tokens, reasoning=reasoning)
    finished = perf_counter()
    if not isinstance(text, str) or not text.strip():
        raise LlamaUnavailable("legacy local inference backend returned invalid content")
    cleaned = text.strip()
    if on_token is not None:
        await on_token(cleaned)
    return LlamaGeneration(
        text=cleaned,
        ttft_ms=max(0, int((finished - started) * 1000)),
        output_tokens=0,
        tokens_per_second=0.0,
        generation_ms=max(0, int((finished - started) * 1000)),
    )


async def _chat_impl(
    payload: ChatRequest,
    request: Request,
    user: User,
    db: Session,
    *,
    on_token: TokenSink | None = None,
    on_replace: TokenSink | None = None,
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
                conversation_id=getattr(dev.state, "conversation_id", None) or payload.conversation_id,
                run_id=getattr(request.state, "x1_chat_run_id", None),
                client_request_id=payload.client_request_id,
            )

    repeat_query = detect_repeat_query(db, payload.conversation_id, user_text)
    route = choose_route(user_text, payload.mode, settings.max_context_tokens, settings.deep_context_tokens)
    preliminary_freshness = classify_freshness(user_text).required
    preliminary_plan = plan_verification(
        verification=payload.verification,
        user_text=user_text,
        route_mode=route.mode,
        requirements=payload.requirements,
        freshness_required=preliminary_freshness,
        verified_source_count=len(payload.research_source_ids),
    )
    reserve_seconds = _estimated_reserve_seconds(route.mode, preliminary_plan.extra_inference_budget)
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
    prompt_tokens = max(512, route.max_context_tokens - max_tokens - 384)
    prompt_char_budget = prompt_tokens * 3
    compiled = request.app.state.context.compile(trusted, max_chars=prompt_char_budget)
    compiled_chars = sum(len(message.content) for message in compiled)

    freshness_required = FRESHNESS_SENTINEL in verified_urls
    quality_urls = {url for url in verified_urls if url != FRESHNESS_SENTINEL}

    last_user = next((message for message in reversed(payload.messages) if message.role == "user"), None)
    _persist_accepted_user_turn(db, conversation, last_user)
    conversation.updated_at = datetime.now(timezone.utc)
    run_id = str(getattr(request.state, "x1_chat_run_id", "") or "")
    if run_id:
        run_row = db.get(ChatRun, run_id)
        if run_row is not None and run_row.user_id == user.id:
            run_row.conversation_id = conversation.id
            run_row.project_id = project.id if project else None
            run_row.updated_at = datetime.now(timezone.utc)
    # The accepted user turn and resolved run scope are durable before entering
    # the scarce inference queue. A transport failure can no longer erase what
    # the user actually submitted.
    db.commit()

    request_id = run_id or str(uuid4())
    total_started = perf_counter()
    queue_started = perf_counter()
    inference_started = 0.0
    inference_ms = 0
    queue_ms = 0
    text_out = ""
    success = False
    deterministic = None
    critic = None
    primary: LlamaGeneration | None = None
    streamed_output_chars = 0
    streamed_chunks = 0
    streamed_ttft_ms: int | None = None
    verification_extra_inferences = 0
    verification_risk_score = preliminary_plan.risk_score
    critic_used = False
    repair_applied = False

    async def relay_token(text: str) -> None:
        nonlocal streamed_output_chars, streamed_chunks, streamed_ttft_ms
        streamed_output_chars += len(text)
        streamed_chunks += 1
        if streamed_ttft_ms is None and inference_started > 0:
            streamed_ttft_ms = max(0, int((perf_counter() - inference_started) * 1000))
        if on_token is not None:
            await on_token(text)

    async def repair_once(audit) -> bool:
        nonlocal text_out, deterministic, verification_extra_inferences, repair_applied
        if verification_extra_inferences >= _MAX_VERIFICATION_EXTRA_INFERENCES:
            return False
        repaired = await request.app.state.llama.chat(
            _quality.repair_messages(user_text, text_out, audit, payload.requirements),
            max_tokens=max_tokens,
            reasoning=False,
        )
        verification_extra_inferences += 1
        repair_applied = True
        if repaired != text_out and on_replace is not None:
            await on_replace(repaired)
        text_out = repaired
        deterministic = _quality.deterministic(
            text_out,
            payload.requirements,
            quality_urls,
            freshness_required=freshness_required,
        )
        return True

    try:
        async with request.app.state.user_governor.slot(user.id, quota.max_concurrent_inference):
            async with request.app.state.governor.slot():
                queue_ms = int((perf_counter() - queue_started) * 1000)
                inference_started = perf_counter()
                try:
                    primary = await _primary_generation(
                        request,
                        compiled,
                        max_tokens=max_tokens,
                        reasoning=route.reasoning,
                        on_token=relay_token if on_token is not None else None,
                    )
                    text_out = primary.text
                    if payload.verification != "off":
                        deterministic = _quality.deterministic(
                            text_out,
                            payload.requirements,
                            quality_urls,
                            freshness_required=freshness_required,
                        )
                        plan = plan_verification(
                            verification=payload.verification,
                            user_text=user_text,
                            route_mode=route.mode,
                            requirements=payload.requirements,
                            freshness_required=freshness_required,
                            verified_source_count=len(quality_urls),
                            answer=text_out,
                            deterministic=deterministic,
                        )
                        verification_risk_score = plan.risk_score

                        if plan.repair_deterministic and deterministic.failed:
                            try:
                                await repair_once(deterministic)
                            except LlamaUnavailable:
                                deterministic.warnings.append(
                                    "Автоматическая коррекция недоступна; сохранён первичный ответ с найденными дефектами."
                                )

                        plan = plan_verification(
                            verification=payload.verification,
                            user_text=user_text,
                            route_mode=route.mode,
                            requirements=payload.requirements,
                            freshness_required=freshness_required,
                            verified_source_count=len(quality_urls),
                            answer=text_out,
                            deterministic=deterministic,
                        )
                        verification_risk_score = max(verification_risk_score, plan.risk_score)

                        if (
                            plan.run_critic
                            and deterministic is not None
                            and not deterministic.failed
                            and verification_extra_inferences < _MAX_VERIFICATION_EXTRA_INFERENCES
                        ):
                            try:
                                critic_raw = await request.app.state.llama.chat(
                                    _quality.critic_messages(user_text, text_out, payload.requirements),
                                    max_tokens=plan.critic_max_tokens,
                                    reasoning=False,
                                )
                                verification_extra_inferences += 1
                                critic_used = True
                                critic = _quality.parse_critic(critic_raw)
                            except LlamaUnavailable:
                                critic = {
                                    "ok": False,
                                    "issues": [],
                                    "summary": "Local critic unavailable; primary answer preserved",
                                }
                                deterministic.warnings.append(
                                    "Дополнительная семантическая проверка временно недоступна; ответ не помечен как подтверждённый."
                                )

                        if (
                            plan.repair_critic
                            and critic_has_repairable_issue(critic)
                            and deterministic is not None
                            and verification_extra_inferences < _MAX_VERIFICATION_EXTRA_INFERENCES
                        ):
                            original_issues = list(critic.get("issues", [])) if critic else []
                            try:
                                repair_audit = audit_with_critic_issues(deterministic, critic)
                                if await repair_once(repair_audit):
                                    critic = {
                                        "ok": True,
                                        "issues": [],
                                        "summary": "Major/critical critic findings were repaired in one bounded pass.",
                                        "repair_applied": True,
                                        "original_issues": original_issues[:10],
                                    }
                            except LlamaUnavailable:
                                deterministic.warnings.append(
                                    "Critic нашёл существенный дефект, но дополнительный repair-pass недоступен."
                                )
                    success = True
                finally:
                    inference_ms = int((perf_counter() - inference_started) * 1000)
    except asyncio.CancelledError:
        duration_ms = int((perf_counter() - total_started) * 1000)
        effective_ttft = primary.ttft_ms if primary is not None else streamed_ttft_ms
        effective_tokens = primary.output_tokens if primary is not None else streamed_chunks
        effective_tps = primary.tokens_per_second if primary is not None else (
            round(streamed_chunks / max(0.001, (inference_ms - (effective_ttft or 0)) / 1000.0), 3)
            if streamed_chunks and inference_ms > (effective_ttft or 0)
            else None
        )
        _publish_request_usage(
            request,
            request_id=request_id,
            inference_ms=inference_ms,
            queue_ms=queue_ms,
            duration_ms=duration_ms,
            success=False,
            quality_status="cancelled",
            ttft_ms=effective_ttft,
            output_tokens=effective_tokens,
            tokens_per_second=effective_tps,
            cancelled=True,
        )
        db.rollback()
        with contextlib.suppress(Exception):
            if task is not None and inference_ms > 0:
                record_task_compute(db, task.id, max(1, (inference_ms + 999) // 1000))
            usage_event = _usage_event(
                user=user,
                project=project,
                conversation=conversation,
                mode=route.mode,
                raw_chars=raw_chars,
                compiled_chars=compiled_chars,
                output_chars=streamed_output_chars,
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
        raise
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
            ttft_ms=streamed_ttft_ms,
            output_tokens=streamed_chunks,
        )
        db.rollback()
        if task is not None:
            record_task_compute(db, task.id, max(1, (inference_ms + 999) // 1000))
        usage_event = _usage_event(
            user=user,
            project=project,
            conversation=conversation,
            mode=route.mode,
            raw_chars=raw_chars,
            compiled_chars=compiled_chars,
            output_chars=streamed_output_chars,
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
    db.add(Message(conversation_id=conversation.id, role="assistant", content=text_out))
    conversation.updated_at = datetime.now(timezone.utc)

    quality_report = None
    quality_status = "unchecked"
    if payload.verification != "off" and deterministic is not None:
        quality_status = _quality.final_status(deterministic, critic)
        critic_payload = dict(critic or {})
        critic_payload["conditional_verification"] = {
            "risk_score": verification_risk_score,
            "extra_inferences": verification_extra_inferences,
            "critic_used": critic_used,
            "repair_applied": repair_applied,
        }
        audit = AnswerAudit(
            user_id=user.id,
            project_id=project.id if project else None,
            conversation_id=conversation.id,
            request_id=request_id,
            verification_mode=payload.verification,
            status=quality_status,
            checks=deterministic.checks,
            warnings=deterministic.warnings,
            critic=critic_payload,
        )
        db.add(audit)
        db.flush()
        quality_report = QualityReport(
            audit_id=audit.id,
            status=quality_status,
            checks=deterministic.checks,
            warnings=deterministic.warnings,
            critic=critic_payload,
        )

    if task is not None:
        record_task_compute(db, task.id, max(1, (inference_ms + 999) // 1000))
    usage_event = _usage_event(
        user=user,
        project=project,
        conversation=conversation,
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
        ttft_ms=primary.ttft_ms if primary is not None else None,
        output_tokens=primary.output_tokens if primary is not None else 0,
        tokens_per_second=primary.tokens_per_second if primary is not None else None,
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
            queue_ms=queue_ms,
            ttft_ms=primary.ttft_ms if primary is not None else None,
            output_tokens=primary.output_tokens if primary is not None else 0,
            tokens_per_second=primary.tokens_per_second if primary is not None else None,
            verification_risk_score=verification_risk_score,
            verification_extra_inferences=verification_extra_inferences,
            critic_used=critic_used,
            repair_applied=repair_applied,
        ),
        quality=quality_report,
        conversation_id=conversation.id,
        run_id=getattr(request.state, "x1_chat_run_id", None),
        client_request_id=payload.client_request_id,
    )


async def _managed_runner(payload: ChatRequest, request: Request, user_id: str, job: ActiveChatJob) -> ChatResponse:
    with SessionLocal() as job_db:
        job_user = job_db.get(User, user_id)
        if job_user is None:
            raise HTTPException(status_code=401, detail="Account is no longer available")
        request.state.x1_chat_run_id = job.run_id
        managed_payload = payload.model_copy(update={"client_request_id": job.client_request_id})
        result = await _chat_impl(
            managed_payload,
            request,
            job_user,
            job_db,
            on_token=job.token,
            on_replace=job.replace,
        )
        result.run_id = job.run_id
        result.client_request_id = job.client_request_id
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
        return await _managed_runner(payload, request, user.id, job)

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
async def chat_run_status(
    client_request_id: str,
    user: User = Depends(get_current_user),
) -> ChatRunStatus:
    try:
        snapshot = await chat_execution_manager.status(user_id=user.id, client_request_id=client_request_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Chat run not found") from exc
    return ChatRunStatus.model_validate(snapshot.as_dict())


@router.post("/chat/runs/{client_request_id}/cancel", response_model=ChatRunStatus)
async def cancel_chat_run(
    client_request_id: str,
    user: User = Depends(get_current_user),
) -> ChatRunStatus:
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
        return await _managed_runner(payload, request, user.id, job)

    async def events():
        queue: asyncio.Queue | None = None
        job: ActiveChatJob | None = None
        legacy_disconnect_cancels = payload.client_request_id is None
        try:
            try:
                snapshot, queue, job = await chat_execution_manager.attach_stream(
                    user_id=user.id,
                    payload=payload,
                    runner=runner,
                )
            except ChatRunConflict as exc:
                yield _sse("error", {"status_code": 409, "detail": str(exc), "retryable": False})
                return

            yield _sse(
                "status",
                {
                    "state": snapshot.status,
                    "run_id": snapshot.run_id,
                    "client_request_id": snapshot.client_request_id,
                    "conversation_id": snapshot.conversation_id,
                    "resumed": bool(snapshot.partial_text),
                    "queue_waiting": int(getattr(request.app.state.governor, "waiting", 0)),
                },
            )
            if snapshot.partial_text:
                yield _sse(
                    "replace",
                    {
                        "text": snapshot.partial_text,
                        "run_id": snapshot.run_id,
                        "client_request_id": snapshot.client_request_id,
                        "reason": "reconnect_snapshot",
                    },
                )
            if snapshot.status == "succeeded" and snapshot.result:
                yield _sse("result", snapshot.result)
                return
            if snapshot.status in {"failed", "interrupted"}:
                yield _sse(
                    "error",
                    {
                        "status_code": 503,
                        "detail": snapshot.error_detail or "Chat run failed",
                        "retryable": snapshot.retryable,
                    },
                )
                return
            if snapshot.status == "cancelled":
                yield _sse("cancelled", {"detail": snapshot.error_detail or "Chat run was cancelled"})
                return

            heartbeat_at = perf_counter()
            while True:
                if await request.is_disconnected():
                    if legacy_disconnect_cancels:
                        await chat_execution_manager.cancel(
                            user_id=user.id,
                            client_request_id=snapshot.client_request_id,
                        )
                    return
                try:
                    event, data = await asyncio.wait_for(queue.get(), timeout=0.25)
                except TimeoutError:
                    if job is not None and job.task is not None and job.task.done():
                        terminal = await chat_execution_manager.status(
                            user_id=user.id,
                            client_request_id=snapshot.client_request_id,
                        )
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
                        yield _sse(
                            "heartbeat",
                            {
                                "state": "working",
                                "run_id": snapshot.run_id,
                                "client_request_id": snapshot.client_request_id,
                                "queue_waiting": int(getattr(request.app.state.governor, "waiting", 0)),
                            },
                        )
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
        headers={
            "Cache-Control": "no-store, private",
            "X-Robots-Tag": "noindex, nofollow, noarchive, nosnippet",
            "X-Accel-Buffering": "no",
        },
    )
