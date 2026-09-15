from __future__ import annotations

import asyncio
import contextlib
import json
from collections import OrderedDict
from datetime import datetime, timezone
from time import monotonic, perf_counter

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from app import user_ui as _base_user_ui
from app.api.routes import chat as legacy_chat
from app.db import SessionLocal, get_db
from app.models import ChatRun, Message, UsageEvent, User
from app.schemas.chat import ChatRequest, ChatResponse, ChatRunStatus, ChatUsage
from app.services.auth import get_current_user
from app.services.chat_runtime import ActiveChatJob, ChatRunConflict, ChatRunSnapshot, chat_execution_manager
from app.services.clean_web import build_clean_web_context
from app.services.live_structured_facts import is_live_structured_question, resolve_live_structured_fact
from app.services.local_business_search import is_local_business_question, resolve_local_business
from app.services.long_term_memory import MemoryBundle, memory_context_message, retrieve_memories
from app.services.response_completeness import expansion_messages, needs_expansion
from app.services.response_strategy import is_atomic_knowledge_question, normalized_question, requires_memory_context
from app.services.task_solver import reset_task_solver_context, set_task_solver_context
from app.utility_chat import utility_reply
from app.task_solver_user_ui import router as _task_solver_user_ui_router

router = APIRouter(prefix="/v1", tags=["chat"])
_ATOMIC_CACHE_TTL_SECONDS = 7 * 24 * 60 * 60
_ATOMIC_CACHE_MAX = 512
_ATOMIC_CACHE: OrderedDict[str, tuple[float, str]] = OrderedDict()


def _install_workspace_route() -> None:
    retained = [route for route in _base_user_ui.router.routes if str(getattr(route, "path", "")) != "/app"]
    enhanced = [route for route in _task_solver_user_ui_router.routes if str(getattr(route, "path", "")) == "/app"]
    if len(enhanced) != 1:
        raise RuntimeError("Task-solver workspace must own exactly one /app route")
    _base_user_ui.router.routes[:] = [*retained, enhanced[0]]


_install_workspace_route()


def _latest_user_text(payload: ChatRequest) -> str:
    return next((message.content for message in reversed(payload.messages) if message.role == "user"), "")


def _cache_key(payload: ChatRequest, question: str) -> str | None:
    if payload.mode not in {"auto", "fast"} or payload.web_mode == "always": return None
    if payload.project_id or payload.task_id or payload.requirements: return None
    if not is_atomic_knowledge_question(question): return None
    value = normalized_question(question)
    return value[:500] if value else None


def _atomic_cache_get(key: str | None) -> str | None:
    if not key: return None
    row = _ATOMIC_CACHE.get(key)
    if row is None: return None
    expires_at, text = row
    if expires_at <= monotonic():
        _ATOMIC_CACHE.pop(key, None); return None
    _ATOMIC_CACHE.move_to_end(key)
    return text


def _atomic_cache_put(key: str | None, text: str) -> None:
    if not key or not text.strip(): return
    _ATOMIC_CACHE[key] = (monotonic() + _ATOMIC_CACHE_TTL_SECONDS, text.strip())
    _ATOMIC_CACHE.move_to_end(key)
    while len(_ATOMIC_CACHE) > _ATOMIC_CACHE_MAX: _ATOMIC_CACHE.popitem(last=False)


def _structured_metadata(execution) -> dict:
    sources = list(getattr(execution, "public_sources", []) or [])[:5]
    plan = getattr(execution, "plan", None)
    return {"kind": "structured_fact", "web_used": True, "searched_results": 0, "fetched_sources": int(getattr(execution, "fetched_sources", len(sources)) or len(sources)), "failed_fetches": 0, "sources": sources, "warnings": [], "steps": list(getattr(plan, "public_steps", ()) or ("Получаю точные данные", "Возвращаю ответ"))}


def _local_business_metadata(execution) -> dict:
    sources = list(getattr(execution, "sources", []) or [])[:10]
    return {"kind": "local_business", "web_used": True, "searched_results": int(getattr(execution, "searched", len(sources)) or len(sources)), "fetched_sources": len(sources), "failed_fetches": 0, "sources": sources, "warnings": [] if sources else ["Карточки компаний не удалось подтвердить"], "steps": ["Проверяю локальный индекс OLYA", "Обновляю через OpenStreetMap, Яндекс, 2ГИС, Zoon и Yell", "Возвращаю подтверждённые организации"]}


def _instant_metadata(kind: str, *, cached: bool = False) -> dict:
    return {"kind": kind, "web_used": False, "searched_results": 0, "fetched_sources": 0, "failed_fetches": 0, "sources": [], "warnings": [], "steps": ["Быстрый точный ответ" if not cached else "Ответ из проверенного кеша GigaChat"]}


def _persist_fast_answer(*, db: Session, request: Request, user: User, payload: ChatRequest, job: ActiveChatJob, text: str, mode: str, started: float, task_execution: dict) -> ChatResponse:
    legacy_chat.require_capability(db, user.id, "chat")
    project, conversation, _task = legacy_chat._resolve_scope(db, user, payload)
    request.state.x1_conversation_id = conversation.id
    last_user = next((message for message in reversed(payload.messages) if message.role == "user"), None)
    legacy_chat._persist_accepted_user_turn(db, conversation, last_user)
    db.add(Message(conversation_id=conversation.id, role="assistant", content=text))
    conversation.updated_at = datetime.now(timezone.utc)
    run_row = db.get(ChatRun, job.run_id)
    if run_row is not None and run_row.user_id == user.id:
        run_row.conversation_id = conversation.id
        run_row.project_id = project.id if project else None
        run_row.updated_at = datetime.now(timezone.utc)
    duration_ms = max(0, int((perf_counter() - started) * 1000))
    db.add(UsageEvent(user_id=user.id, project_id=project.id if project else None, conversation_id=conversation.id, mode=mode, raw_chars=len(_latest_user_text(payload)), compiled_chars=0, output_chars=len(text), duration_ms=duration_ms, inference_ms=0, queue_ms=0, success=True, request_id=job.run_id))
    db.commit(); job.conversation_id = conversation.id
    return ChatResponse(text=text, model="OLYA AI", usage=ChatUsage(raw_message_chars=len(_latest_user_text(payload)), compiled_message_chars=0, mode=mode, verification="off", queue_ms=0, ttft_ms=duration_ms, output_tokens=0, tokens_per_second=None), quality=None, task_execution=task_execution, conversation_id=conversation.id, run_id=job.run_id, client_request_id=job.client_request_id)


def _replace_persisted_assistant(db: Session, conversation_id: str | None, text: str) -> None:
    if not conversation_id: return
    row = db.scalar(select(Message).where(Message.conversation_id == conversation_id, Message.role == "assistant").order_by(Message.created_at.desc()).limit(1))
    if row is not None:
        row.content = text
        db.commit()


async def _smart_managed_runner(payload: ChatRequest, request: Request, user_id: str, job: ActiveChatJob) -> ChatResponse:
    with SessionLocal() as job_db:
        job_user = job_db.get(User, user_id)
        if job_user is None: raise HTTPException(status_code=401, detail="Account is no longer available")
        request.state.x1_chat_run_id = job.run_id
        question = _latest_user_text(payload); started = perf_counter()

        instant = utility_reply(question)
        if instant is not None:
            job._publish_nowait("status", {"state": "instant", "message": "Отвечаю сразу…", "task_kind": instant.kind})
            result = _persist_fast_answer(db=job_db, request=request, user=job_user, payload=payload, job=job, text=instant.text, mode=instant.kind, started=started, task_execution=_instant_metadata(instant.kind))
            await job.token(instant.text); return result

        if payload.web_mode != "off" and is_live_structured_question(question):
            job._publish_nowait("status", {"state": "lookup", "message": "Получаю точные актуальные данные…", "task_kind": "structured_fact"})
            execution = await resolve_live_structured_fact(question)
            answer = str(getattr(execution, "resolved_answer", "") or "").strip() if execution is not None else ""
            if answer:
                result = _persist_fast_answer(db=job_db, request=request, user=job_user, payload=payload, job=job, text=answer, mode="structured_fact", started=started, task_execution=_structured_metadata(execution))
                await job.token(answer); return result

        if payload.web_mode != "off" and is_local_business_question(question):
            job._publish_nowait("status", {"state": "lookup", "message": "Проверяю локальный индекс и источники компаний…", "task_kind": "local_business"})
            execution = await resolve_local_business(question, request.app.state.discovery)
            if execution is not None:
                result = _persist_fast_answer(db=job_db, request=request, user=job_user, payload=payload, job=job, text=execution.text, mode="local_business", started=started, task_execution=_local_business_metadata(execution))
                await job.token(execution.text); return result

        atomic_key = _cache_key(payload, question); cached = _atomic_cache_get(atomic_key)
        if cached is not None:
            job._publish_nowait("status", {"state": "instant", "message": "Готово…", "task_kind": "atomic_cache"})
            result = _persist_fast_answer(db=job_db, request=request, user=job_user, payload=payload, job=job, text=cached, mode="atomic_cache", started=started, task_execution=_instant_metadata("stable_fact", cached=True))
            await job.token(cached); return result

        deep = payload.mode == "deep"; atomic = atomic_key is not None
        job._publish_nowait("status", {"state": "thinking" if atomic else ("researching" if payload.web_mode == "always" else "working"), "message": "Проверяю факт…" if atomic else ("Ищу актуальные данные…" if payload.web_mode == "always" else "Формирую ответ…"), "task_kind": "atomic_knowledge" if atomic else "clean_chat"})
        web = await build_clean_web_context(discovery=request.app.state.discovery, fetcher=request.app.state.research, question=question, web_mode=payload.web_mode, deep=deep)
        if web.used:
            job._publish_nowait("status", {"state": "synthesizing", "message": "Источники собраны. Формирую ответ…", "task_kind": "web_research", "fetched_sources": web.fetched, "search_results": web.searched})

        context_messages = list(web.context_messages)
        if not payload.conversation_id and requires_memory_context(question):
            memories = retrieve_memories(job_db, conversation_id="", query=question, limit=5, user_id=job_user.id)
            memory_message = memory_context_message(MemoryBundle(summary="", memories=memories))
            if memory_message is not None: context_messages.append(memory_message)

        context_token = set_task_solver_context(context_messages) if context_messages else None
        managed_payload = payload.model_copy(update={"client_request_id": job.client_request_id, "verification": "off", "research_source_ids": []})
        try:
            result = await legacy_chat._chat_impl(managed_payload, request, job_user, job_db, on_token=job.token, on_replace=job.replace)
            if not atomic and needs_expansion(question, result.text):
                job._publish_nowait("status", {"state": "verifying", "message": "Ответ слишком короткий. Дорабатываю полноту…", "task_kind": "completeness_repair"})
                repair = expansion_messages(question, result.text)
                repair_messages = [repair[0], *context_messages, repair[1]] if context_messages else repair
                try:
                    expanded = (await request.app.state.llama.chat(repair_messages, max_tokens=1100 if payload.mode != "deep" else 1800, reasoning=False)).strip()
                except Exception:
                    expanded = ""
                if expanded and len(expanded) > len(result.text):
                    result.text = expanded
                    _replace_persisted_assistant(job_db, result.conversation_id, expanded)
                    await job.replace(expanded)
        finally:
            if context_token is not None: reset_task_solver_context(context_token)

        if atomic_key and not web.used: _atomic_cache_put(atomic_key, result.text)
        result.run_id = job.run_id; result.client_request_id = job.client_request_id; result.task_execution = web.public_metadata()
        return result


def _snapshot_response(snapshot: ChatRunSnapshot) -> ChatResponse:
    if snapshot.status == "succeeded" and snapshot.result: return ChatResponse.model_validate(snapshot.result)
    if snapshot.status == "cancelled": raise HTTPException(status_code=409, detail="Chat run was cancelled")
    if snapshot.status == "interrupted": raise HTTPException(status_code=503, detail=snapshot.error_detail or "Chat run was interrupted", headers={"Retry-After": "1"})
    status_code = 503
    if snapshot.error_code.startswith("http_"):
        try: status_code = int(snapshot.error_code.split("_", 1)[1])
        except ValueError: status_code = 503
    raise HTTPException(status_code=status_code, detail=snapshot.error_detail or "Chat run failed")


@router.post("/chat", response_model=ChatResponse)
async def chat(payload: ChatRequest, request: Request, user: User = Depends(get_current_user), db: Session = Depends(get_db)) -> ChatResponse:
    _ = db
    async def runner(job: ActiveChatJob) -> ChatResponse: return await _smart_managed_runner(payload, request, user.id, job)
    try: execution = await chat_execution_manager.start_or_attach(user_id=user.id, payload=payload, runner=runner)
    except ChatRunConflict as exc: raise HTTPException(status_code=409, detail=str(exc)) from exc
    if isinstance(execution, ChatRunSnapshot): return _snapshot_response(execution)
    try: await asyncio.shield(execution.task)
    except asyncio.CancelledError: raise
    snapshot = await chat_execution_manager.status(user_id=user.id, client_request_id=execution.client_request_id)
    return _snapshot_response(snapshot)


def _sse(event: str, payload: dict) -> str: return f"event: {event}\ndata: {json.dumps(payload, ensure_ascii=False, separators=(',', ':'))}\n\n"


@router.get("/chat/runs/{client_request_id}", response_model=ChatRunStatus)
async def chat_run_status(client_request_id: str, user: User = Depends(get_current_user)) -> ChatRunStatus:
    try: snapshot = await chat_execution_manager.status(user_id=user.id, client_request_id=client_request_id)
    except KeyError as exc: raise HTTPException(status_code=404, detail="Chat run not found") from exc
    return ChatRunStatus.model_validate(snapshot.as_dict())


@router.post("/chat/runs/{client_request_id}/cancel", response_model=ChatRunStatus)
async def cancel_chat_run(client_request_id: str, user: User = Depends(get_current_user)) -> ChatRunStatus:
    try: snapshot = await chat_execution_manager.cancel(user_id=user.id, client_request_id=client_request_id)
    except KeyError as exc: raise HTTPException(status_code=404, detail="Chat run not found") from exc
    return ChatRunStatus.model_validate(snapshot.as_dict())


@router.post("/chat/stream")
async def chat_stream(payload: ChatRequest, request: Request, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    _ = db
    async def runner(job: ActiveChatJob) -> ChatResponse: return await _smart_managed_runner(payload, request, user.id, job)
    async def events():
        queue: asyncio.Queue | None = None; job: ActiveChatJob | None = None; legacy_disconnect_cancels = payload.client_request_id is None
        try:
            try: snapshot, queue, job = await chat_execution_manager.attach_stream(user_id=user.id, payload=payload, runner=runner)
            except ChatRunConflict as exc:
                yield _sse("error", {"status_code": 409, "detail": str(exc), "retryable": False}); return
            yield _sse("status", {"state": snapshot.status, "run_id": snapshot.run_id, "client_request_id": snapshot.client_request_id, "conversation_id": snapshot.conversation_id, "resumed": bool(snapshot.partial_text), "queue_waiting": int(getattr(request.app.state.governor, "waiting", 0))})
            if snapshot.partial_text: yield _sse("replace", {"text": snapshot.partial_text, "run_id": snapshot.run_id, "client_request_id": snapshot.client_request_id, "reason": "reconnect_snapshot"})
            if snapshot.status == "succeeded" and snapshot.result: yield _sse("result", snapshot.result); return
            if snapshot.status in {"failed", "interrupted"}: yield _sse("error", {"status_code": 503, "detail": snapshot.error_detail or "Chat run failed", "retryable": snapshot.retryable}); return
            if snapshot.status == "cancelled": yield _sse("cancelled", {"detail": snapshot.error_detail or "Chat run was cancelled"}); return
            heartbeat_at = perf_counter()
            while True:
                if await request.is_disconnected():
                    if legacy_disconnect_cancels: await chat_execution_manager.cancel(user_id=user.id, client_request_id=snapshot.client_request_id)
                    return
                try: event, data = await asyncio.wait_for(queue.get(), timeout=0.25)
                except TimeoutError:
                    if job is not None and job.task is not None and job.task.done():
                        terminal = await chat_execution_manager.status(user_id=user.id, client_request_id=snapshot.client_request_id)
                        if terminal.status == "succeeded" and terminal.result: yield _sse("result", terminal.result)
                        elif terminal.status == "cancelled": yield _sse("cancelled", {"detail": terminal.error_detail})
                        else: yield _sse("error", {"status_code": 503, "detail": terminal.error_detail or "Chat run failed", "retryable": terminal.retryable})
                        return
                    now = perf_counter()
                    if now - heartbeat_at >= 2.0:
                        heartbeat_at = now; yield _sse("heartbeat", {"state": "working", "run_id": snapshot.run_id, "client_request_id": snapshot.client_request_id, "queue_waiting": int(getattr(request.app.state.governor, "waiting", 0))})
                    continue
                yield _sse(event, data)
                if event in {"result", "error", "cancelled"}: return
        except asyncio.CancelledError:
            if legacy_disconnect_cancels and job is not None:
                with contextlib.suppress(Exception): await chat_execution_manager.cancel(user_id=user.id, client_request_id=job.client_request_id)
            raise
        finally: chat_execution_manager.detach(job, queue)
    return StreamingResponse(events(), media_type="text/event-stream", headers={"Cache-Control": "no-store, private", "X-Robots-Tag": "noindex, nofollow, noarchive, nosnippet", "X-Accel-Buffering": "no"})

legacy_chat.chat = chat
legacy_chat.chat_stream = chat_stream
legacy_chat.chat_run_status = chat_run_status
legacy_chat.cancel_chat_run = cancel_chat_run