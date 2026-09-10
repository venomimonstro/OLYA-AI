from __future__ import annotations

import asyncio
import hashlib
import json
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Awaitable, Callable
from uuid import uuid4

from fastapi import HTTPException
from sqlalchemy import delete, select

from app.db import SessionLocal
from app.models import ChatRun
from app.schemas.chat import ChatRequest, ChatResponse


TERMINAL_CHAT_RUN_STATES = frozenset({"succeeded", "failed", "cancelled", "interrupted"})
_CHAT_RUN_RETENTION = timedelta(hours=24)
_CHAT_RUN_STALE_AFTER = timedelta(minutes=15)
_CHAT_RUN_PRUNE_INTERVAL_SECONDS = 3600.0
_MAX_TRANSIENT_RUNS_PER_USER = 256
_MAX_RUN_ATTEMPTS = 3


class ChatRunConflict(RuntimeError):
    pass


def _retryable(status: str, error_code: str) -> bool:
    if status == "interrupted":
        return True
    if status == "cancelled":
        return False
    if status != "failed":
        return False
    if error_code.startswith("http_"):
        try:
            code = int(error_code.split("_", 1)[1])
        except ValueError:
            return True
        return code == 429 or code >= 500
    return error_code not in {"resume_attempts_exhausted"}


@dataclass(frozen=True)
class ChatRunSnapshot:
    run_id: str
    client_request_id: str
    status: str
    conversation_id: str | None
    partial_text: str = ""
    result: dict | None = None
    error_code: str = ""
    error_detail: str = ""
    retryable: bool = False

    def as_dict(self) -> dict:
        return {
            "run_id": self.run_id,
            "client_request_id": self.client_request_id,
            "status": self.status,
            "conversation_id": self.conversation_id,
            "partial_text": self.partial_text,
            "result": self.result,
            "error_code": self.error_code,
            "error_detail": self.error_detail,
            "retryable": self.retryable,
        }


@dataclass
class ActiveChatJob:
    run_id: str
    user_id: str
    client_request_id: str
    input_hash: str
    conversation_id: str | None = None
    partial_text: str = ""
    status: str = "running"
    result: dict | None = None
    error_code: str = ""
    error_detail: str = ""
    sequence: int = 0
    subscribers: set[asyncio.Queue] = field(default_factory=set)
    task: asyncio.Task | None = None
    cancel_reason: str = ""

    def snapshot(self) -> ChatRunSnapshot:
        return ChatRunSnapshot(
            run_id=self.run_id,
            client_request_id=self.client_request_id,
            status=self.status,
            conversation_id=self.conversation_id,
            partial_text=self.partial_text,
            result=self.result,
            error_code=self.error_code,
            error_detail=self.error_detail,
            retryable=_retryable(self.status, self.error_code),
        )

    def _publish_nowait(self, event: str, data: dict) -> None:
        self.sequence += 1
        payload = {
            **data,
            "run_id": self.run_id,
            "client_request_id": self.client_request_id,
            "sequence": self.sequence,
        }
        for queue in tuple(self.subscribers):
            try:
                queue.put_nowait((event, payload))
            except asyncio.QueueFull:
                # A slow/reconnecting browser must never backpressure local
                # inference. Replace its stale event backlog with one complete
                # text snapshot, from which rendering can continue losslessly.
                try:
                    while True:
                        queue.get_nowait()
                except asyncio.QueueEmpty:
                    pass
                try:
                    queue.put_nowait(
                        (
                            "replace",
                            {
                                "text": self.partial_text,
                                "run_id": self.run_id,
                                "client_request_id": self.client_request_id,
                                "sequence": self.sequence,
                                "reason": "subscriber_resync",
                            },
                        )
                    )
                except asyncio.QueueFull:
                    pass

    async def token(self, text: str) -> None:
        self.partial_text += text
        self._publish_nowait("token", {"text": text})

    async def replace(self, text: str) -> None:
        self.partial_text = text
        self._publish_nowait("replace", {"text": text})


Runner = Callable[[ActiveChatJob], Awaitable[ChatResponse]]


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def request_fingerprint(user_id: str, payload: ChatRequest) -> str:
    material = payload.model_dump(mode="json", exclude={"client_request_id"})
    canonical = json.dumps(material, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256((user_id + "\0" + canonical).encode("utf-8")).hexdigest()


class ChatExecutionManager:
    """One-worker execution manager for chat reconnect/idempotency.

    X1 deliberately runs a single Uvicorn worker on its low-cost node. Active
    generation state therefore remains process-local while PostgreSQL provides a
    durable run ledger across browser reconnects and application restarts.
    """

    def __init__(self) -> None:
        self.runtime_id = str(uuid4())
        self._jobs: dict[tuple[str, str], ActiveChatJob] = {}
        self._lock = asyncio.Lock()
        self._next_prune_at = 0.0
        self._closing = False

    def startup(self) -> None:
        """Begin a new application lifespan with a fresh runtime identity."""
        if self._jobs:
            raise RuntimeError("Cannot start chat runtime while old jobs are still active")
        self.runtime_id = str(uuid4())
        self._closing = False
        self._next_prune_at = 0.0

    @staticmethod
    def _client_id(payload: ChatRequest) -> str:
        return payload.client_request_id or ("srv_" + uuid4().hex)

    @staticmethod
    def _snapshot_from_row(row: ChatRun) -> ChatRunSnapshot:
        result = dict(row.result_json or {}) if row.status == "succeeded" else None
        return ChatRunSnapshot(
            run_id=row.id,
            client_request_id=row.client_request_id,
            status=row.status,
            conversation_id=row.conversation_id,
            result=result,
            error_code=row.error_code or "",
            error_detail=row.error_detail or "",
            retryable=_retryable(row.status, row.error_code or ""),
        )

    @staticmethod
    def _adopt_snapshot(job: ActiveChatJob, snapshot: ChatRunSnapshot) -> None:
        job.status = snapshot.status
        job.conversation_id = snapshot.conversation_id or job.conversation_id
        job.result = snapshot.result
        job.error_code = snapshot.error_code
        job.error_detail = snapshot.error_detail
        if snapshot.result and isinstance(snapshot.result.get("text"), str):
            job.partial_text = snapshot.result["text"]

    @staticmethod
    def _event_for_snapshot(snapshot: ChatRunSnapshot) -> tuple[str, dict]:
        if snapshot.status == "succeeded" and snapshot.result:
            return "result", snapshot.result
        if snapshot.status == "cancelled":
            return "cancelled", {"detail": snapshot.error_detail or "Generation was cancelled by the user."}
        status_code = 503
        if snapshot.error_code.startswith("http_"):
            try:
                status_code = int(snapshot.error_code.split("_", 1)[1])
            except ValueError:
                status_code = 503
        return "error", {
            "status_code": status_code,
            "detail": snapshot.error_detail or "Chat processing failed",
            "retryable": snapshot.retryable,
        }

    def _load_row(self, user_id: str, client_request_id: str) -> ChatRun | None:
        with SessionLocal() as db:
            return db.scalar(
                select(ChatRun).where(
                    ChatRun.user_id == user_id,
                    ChatRun.client_request_id == client_request_id,
                )
            )

    def _prune_if_due(self) -> None:
        now_mono = time.monotonic()
        if now_mono < self._next_prune_at:
            return
        self._next_prune_at = now_mono + _CHAT_RUN_PRUNE_INTERVAL_SECONDS
        now = utcnow()
        stale_cutoff = now - _CHAT_RUN_STALE_AFTER
        retention_cutoff = now - _CHAT_RUN_RETENTION
        with SessionLocal() as db:
            stale = list(
                db.scalars(
                    select(ChatRun)
                    .where(ChatRun.status == "running", ChatRun.updated_at < stale_cutoff)
                    .limit(200)
                ).all()
            )
            for row in stale:
                # Never rewrite a process-local job that is actually still alive.
                if (row.user_id, row.client_request_id) in self._jobs:
                    continue
                row.status = "interrupted"
                row.error_code = "stale_execution"
                row.error_detail = "Chat execution did not reach a terminal state before its recovery deadline."
                row.updated_at = now
                row.completed_at = now
            db.flush()
            db.execute(
                delete(ChatRun).where(
                    ChatRun.status.in_(tuple(TERMINAL_CHAT_RUN_STATES)),
                    ChatRun.completed_at.is_not(None),
                    ChatRun.completed_at < retention_cutoff,
                )
            )
            db.commit()

    def _trim_user_runs(self, db, user_id: str) -> None:
        """Keep the transport ledger bounded without touching active executions."""
        ids = list(
            db.scalars(
                select(ChatRun.id)
                .where(ChatRun.user_id == user_id, ChatRun.status.in_(tuple(TERMINAL_CHAT_RUN_STATES)))
                .order_by(ChatRun.updated_at.desc())
                .offset(_MAX_TRANSIENT_RUNS_PER_USER)
                .limit(256)
            ).all()
        )
        if ids:
            db.execute(delete(ChatRun).where(ChatRun.id.in_(ids)))

    def _persist_terminal(
        self,
        job: ActiveChatJob,
        *,
        status: str,
        result: dict | None = None,
        error_code: str = "",
        error_detail: str = "",
    ) -> ChatRunSnapshot:
        with SessionLocal() as db:
            row = db.get(ChatRun, job.run_id)
            if row is None:
                return job.snapshot()
            row.status = status
            # A run without an initial conversation id can bind one inside the
            # worker before inference. Never erase that durable binding merely
            # because cancellation happened before the worker returned a final
            # ChatResponse and copied it back into ActiveChatJob.
            if job.conversation_id is not None:
                row.conversation_id = job.conversation_id
            row.result_json = result or {}
            row.error_code = error_code[:64]
            row.error_detail = error_detail[:2000]
            row.updated_at = utcnow()
            row.completed_at = utcnow()
            db.commit()
            db.refresh(row)
            # chat_run_atomicity may have restored a previously committed
            # success. Always return the state that actually reached PostgreSQL,
            # not the state the transport callback merely attempted to write.
            return self._snapshot_from_row(row)

    @staticmethod
    def _exhaust_resume(row: ChatRun) -> ChatRunSnapshot:
        now = utcnow()
        row.status = "failed"
        row.error_code = "resume_attempts_exhausted"
        row.error_detail = "Chat recovery stopped after repeated application interruptions. Submit the prompt again as a new request."
        row.updated_at = now
        row.completed_at = now
        return ChatExecutionManager._snapshot_from_row(row)

    def _activate_row(
        self,
        row: ChatRun,
        *,
        user_id: str,
        client_request_id: str,
        fingerprint: str,
        payload: ChatRequest,
        runner: Runner,
        key: tuple[str, str],
    ) -> ActiveChatJob:
        # The first attempt may have created/bound a canonical conversation before
        # the old process died. Mutate the current request object to that durable
        # scope before spawning the runner; its closure references this same
        # Pydantic instance. Without this, an API client that originally omitted
        # conversation_id could create a second conversation on restart.
        if row.conversation_id is not None and payload.conversation_id is None:
            payload.conversation_id = row.conversation_id
        row.status = "running"
        row.runtime_id = self.runtime_id
        row.attempt = max(1, int(row.attempt or 0) + 1)
        row.result_json = {}
        row.error_code = ""
        row.error_detail = ""
        row.completed_at = None
        row.updated_at = utcnow()
        job = ActiveChatJob(
            run_id=row.id,
            user_id=user_id,
            client_request_id=client_request_id,
            input_hash=fingerprint,
            conversation_id=payload.conversation_id or row.conversation_id,
        )
        self._jobs[key] = job
        job.task = asyncio.create_task(self._execute(key, job, runner), name=f"x1-chat-run-{row.id}")
        return job

    def _interrupt_or_reject_other_conversation_run(
        self,
        db,
        *,
        user_id: str,
        conversation_id: str | None,
        client_request_id: str,
    ) -> None:
        if not conversation_id:
            return
        for other in self._jobs.values():
            if (
                other.user_id == user_id
                and other.conversation_id == conversation_id
                and other.client_request_id != client_request_id
                and other.status == "running"
            ):
                raise ChatRunConflict("Conversation already has an active chat request")
        rows = list(
            db.scalars(
                select(ChatRun).where(
                    ChatRun.user_id == user_id,
                    ChatRun.conversation_id == conversation_id,
                    ChatRun.status == "running",
                    ChatRun.client_request_id != client_request_id,
                ).limit(8)
            ).all()
        )
        for row in rows:
            if (row.user_id, row.client_request_id) in self._jobs:
                raise ChatRunConflict("Conversation already has an active chat request")
            # In the supported single-worker topology an unowned running row is
            # left by an older/lost runtime. Mark it recoverable before admitting
            # a new turn so transcript order cannot become user,user,assistant.
            row.status = "interrupted"
            row.error_code = "superseded_after_runtime_loss"
            row.error_detail = "A newer request was accepted after the previous runtime was no longer active."
            row.updated_at = utcnow()
            row.completed_at = utcnow()

    async def start_or_attach(
        self,
        *,
        user_id: str,
        payload: ChatRequest,
        runner: Runner,
    ) -> ActiveChatJob | ChatRunSnapshot:
        if self._closing:
            raise HTTPException(status_code=503, detail="Chat runtime is restarting", headers={"Retry-After": "2"})
        client_request_id = self._client_id(payload)
        fingerprint = request_fingerprint(user_id, payload)
        key = (user_id, client_request_id)
        async with self._lock:
            self._prune_if_due()
            existing = self._jobs.get(key)
            if existing is not None:
                if existing.input_hash != fingerprint:
                    raise ChatRunConflict("client_request_id was already used for a different chat request")
                return existing

            with SessionLocal() as db:
                row = db.scalar(
                    select(ChatRun).where(
                        ChatRun.user_id == user_id,
                        ChatRun.client_request_id == client_request_id,
                    )
                )
                if row is not None:
                    if row.input_hash != fingerprint:
                        raise ChatRunConflict("client_request_id was already used for a different chat request")
                    if row.status == "running" and row.runtime_id == self.runtime_id:
                        # A same-runtime durable row without its process-local job
                        # is an impossible normal state. Fail closed instead of
                        # risking duplicate inference/side effects.
                        row.status = "interrupted"
                        row.error_code = "runtime_state_lost"
                        row.error_detail = "The in-memory execution state was lost before completion."
                        row.updated_at = utcnow()
                        row.completed_at = utcnow()
                        db.commit()
                        return self._snapshot_from_row(row)
                    if row.status in {"running", "interrupted"}:
                        if int(row.attempt or 0) >= _MAX_RUN_ATTEMPTS:
                            snapshot = self._exhaust_resume(row)
                            db.commit()
                            return snapshot
                        self._interrupt_or_reject_other_conversation_run(
                            db,
                            user_id=user_id,
                            conversation_id=row.conversation_id or payload.conversation_id,
                            client_request_id=client_request_id,
                        )
                        job = self._activate_row(
                            row,
                            user_id=user_id,
                            client_request_id=client_request_id,
                            fingerprint=fingerprint,
                            payload=payload,
                            runner=runner,
                            key=key,
                        )
                        db.commit()
                        return job
                    return self._snapshot_from_row(row)

                self._interrupt_or_reject_other_conversation_run(
                    db,
                    user_id=user_id,
                    conversation_id=payload.conversation_id,
                    client_request_id=client_request_id,
                )
                self._trim_user_runs(db, user_id)
                row = ChatRun(
                    user_id=user_id,
                    project_id=payload.project_id,
                    conversation_id=payload.conversation_id,
                    client_request_id=client_request_id,
                    input_hash=fingerprint,
                    status="running",
                    runtime_id=self.runtime_id,
                    attempt=1,
                    result_json={},
                    error_code="",
                    error_detail="",
                    created_at=utcnow(),
                    updated_at=utcnow(),
                )
                db.add(row)
                db.commit()
                db.refresh(row)
                job = ActiveChatJob(
                    run_id=row.id,
                    user_id=user_id,
                    client_request_id=client_request_id,
                    input_hash=fingerprint,
                    conversation_id=payload.conversation_id,
                )
                self._jobs[key] = job
                job.task = asyncio.create_task(self._execute(key, job, runner), name=f"x1-chat-run-{row.id}")
                return job

    async def _execute(self, key: tuple[str, str], job: ActiveChatJob, runner: Runner) -> None:
        try:
            response = await runner(job)
            result = response.model_dump(mode="json")
            actual = self._persist_terminal(job, status="succeeded", result=result)
            self._adopt_snapshot(job, actual)
            event, data = self._event_for_snapshot(actual)
            job._publish_nowait(event, data)
        except asyncio.CancelledError:
            if job.cancel_reason == "user":
                desired_status = "cancelled"
                code = "cancelled_by_user"
                detail = "Generation was cancelled by the user."
            else:
                desired_status = "interrupted"
                code = "runtime_shutdown"
                detail = "Chat execution was interrupted by an application restart."
            actual = self._persist_terminal(
                job,
                status=desired_status,
                error_code=code,
                error_detail=detail,
            )
            self._adopt_snapshot(job, actual)
            event, data = self._event_for_snapshot(actual)
            job._publish_nowait(event, data)
        except HTTPException as exc:
            detail = exc.detail if isinstance(exc.detail, str) else "Chat processing failed"
            actual = self._persist_terminal(
                job,
                status="failed",
                error_code=f"http_{exc.status_code}",
                error_detail=detail[:2000],
            )
            self._adopt_snapshot(job, actual)
            event, data = self._event_for_snapshot(actual)
            job._publish_nowait(event, data)
        except Exception:
            actual = self._persist_terminal(
                job,
                status="failed",
                error_code="chat_processing_failed",
                error_detail="Chat processing failed",
            )
            self._adopt_snapshot(job, actual)
            event, data = self._event_for_snapshot(actual)
            job._publish_nowait(event, data)
        finally:
            await asyncio.sleep(0)
            async with self._lock:
                self._jobs.pop(key, None)

    async def attach_stream(
        self,
        *,
        user_id: str,
        payload: ChatRequest,
        runner: Runner,
    ) -> tuple[ChatRunSnapshot, asyncio.Queue | None, ActiveChatJob | None]:
        execution = await self.start_or_attach(user_id=user_id, payload=payload, runner=runner)
        if isinstance(execution, ChatRunSnapshot):
            return execution, None, None
        queue: asyncio.Queue = asyncio.Queue(maxsize=128)
        execution.subscribers.add(queue)
        return execution.snapshot(), queue, execution

    @staticmethod
    def detach(job: ActiveChatJob | None, queue: asyncio.Queue | None) -> None:
        if job is not None and queue is not None:
            job.subscribers.discard(queue)

    async def status(self, *, user_id: str, client_request_id: str) -> ChatRunSnapshot:
        key = (user_id, client_request_id)
        async with self._lock:
            job = self._jobs.get(key)
            if job is not None:
                return job.snapshot()
        row = self._load_row(user_id, client_request_id)
        if row is None:
            raise KeyError(client_request_id)
        if row.status == "running" and row.runtime_id != self.runtime_id:
            return ChatRunSnapshot(
                run_id=row.id,
                client_request_id=row.client_request_id,
                status="interrupted",
                conversation_id=row.conversation_id,
                error_code="runtime_restarted",
                error_detail="The previous application runtime ended before this run completed. Reconnect the same request to resume safely.",
                retryable=True,
            )
        return self._snapshot_from_row(row)

    async def cancel(self, *, user_id: str, client_request_id: str) -> ChatRunSnapshot:
        key = (user_id, client_request_id)
        async with self._lock:
            job = self._jobs.get(key)
            if job is not None and job.task is not None and not job.task.done():
                job.cancel_reason = "user"
                task = job.task
                task.cancel()
            else:
                task = None
        if task is not None:
            try:
                await task
            except asyncio.CancelledError:
                pass
        return await self.status(user_id=user_id, client_request_id=client_request_id)

    async def shutdown(self) -> None:
        """Interrupt in-flight work without misreporting process shutdown as Stop."""
        self._closing = True
        async with self._lock:
            tasks: list[asyncio.Task] = []
            for job in self._jobs.values():
                if job.task is not None and not job.task.done():
                    job.cancel_reason = "shutdown"
                    job.task.cancel()
                    tasks.append(job.task)
        if tasks:
            try:
                await asyncio.wait_for(asyncio.gather(*tasks, return_exceptions=True), timeout=10.0)
            except TimeoutError:
                # Event-loop teardown will finish cancelling any non-responsive
                # task. Their durable rows remain recoverable/interrupted rather
                # than being falsely recorded as a user-requested cancellation.
                pass


chat_execution_manager = ChatExecutionManager()
