from __future__ import annotations

"""Transactional safety net for recoverable chat execution.

The transport manager owns request idempotency, while the canonical chat route
owns the assistant Message and UsageEvent transaction. A process can die after
that transaction commits but before the manager gets a chance to persist its
terminal ChatRun snapshot. This listener closes that crash window: a successful
UsageEvent whose request id is a ChatRun id makes the ChatRun terminal in the
same database transaction as the canonical assistant message.

The manager may subsequently replace the compact recovery result with the full
ChatResponse payload. If the process dies first, the compact payload is still a
valid ChatResponse and, critically, the same logical request cannot be inferred
or billed a second time after restart.
"""

from datetime import datetime, timezone

from sqlalchemy import event, inspect, select
from sqlalchemy.orm import Session


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _restore_sticky_success(row) -> bool:
    """Prevent a late cancel/error callback from downgrading committed success."""
    state = inspect(row)
    status_history = state.attrs.status.history
    previous = status_history.deleted[0] if status_history.deleted else None
    if previous != "succeeded" or row.status == "succeeded":
        return False

    row.status = "succeeded"
    for name in ("result_json", "error_code", "error_detail", "completed_at"):
        history = state.attrs[name].history
        if history.deleted:
            setattr(row, name, history.deleted[0])
    return True


def _compact_result(*, run, usage, assistant) -> dict:
    """Return a schema-valid minimum result for crash recovery.

    Fine-grained verification telemetry is deliberately not guessed here. In a
    healthy process ChatExecutionManager immediately replaces this compact
    snapshot with the complete ChatResponse. After a crash, canonical answer
    text and measured UsageEvent data are enough to restore the user's result
    without re-running inference.
    """
    return {
        "text": assistant.content,
        "model": "local",
        "usage": {
            "raw_message_chars": max(0, int(usage.raw_chars or 0)),
            "compiled_message_chars": max(0, int(usage.compiled_chars or 0)),
            "mode": str(usage.mode or "work"),
            "verification": "auto",
            "queue_ms": max(0, int(usage.queue_ms or 0)),
            "ttft_ms": None,
            "output_tokens": 0,
            "tokens_per_second": None,
            "verification_risk_score": 0,
            "verification_extra_inferences": 0,
            "critic_used": False,
            "repair_applied": False,
        },
        "quality": None,
        "development": None,
        "conversation_id": usage.conversation_id,
        "run_id": run.id,
        "client_request_id": run.client_request_id,
        "recovered_from_canonical_commit": True,
    }


def _install_listener() -> None:
    # Import only after app.models has completed its base/extension registry.
    from app.models import ChatRun, Message, UsageEvent

    @event.listens_for(Session, "before_flush")
    def _chat_run_commit_guard(session: Session, flush_context, instances) -> None:  # noqa: ARG001
        # Once canonical success is committed it is immutable. A user pressing
        # Stop during the tiny post-commit manager window must not turn an
        # already persisted successful answer into cancelled/failed.
        for item in tuple(session.dirty):
            if isinstance(item, ChatRun):
                _restore_sticky_success(item)

        successful_usage = [
            item
            for item in tuple(session.new)
            if isinstance(item, UsageEvent) and bool(item.success) and bool(item.request_id)
        ]
        if not successful_usage:
            return

        new_assistants = [
            item for item in tuple(session.new) if isinstance(item, Message) and item.role == "assistant"
        ]

        for usage in successful_usage:
            run = session.get(ChatRun, str(usage.request_id))
            if run is None or run.user_id != usage.user_id:
                continue
            if run.status == "succeeded":
                continue
            if run.conversation_id not in {None, usage.conversation_id}:
                continue

            assistant = next(
                (
                    item
                    for item in reversed(new_assistants)
                    if item.conversation_id == usage.conversation_id
                    and (item.created_at is None or item.created_at >= run.created_at)
                ),
                None,
            )
            if assistant is None:
                # Verification can flush AnswerAudit before UsageEvent is added.
                # In that case the assistant Message is already INSERTed but is
                # still part of this uncommitted transaction and therefore safe
                # to read here. Restrict the fallback to this run's lifetime so
                # an older answer can never make a malformed future UsageEvent
                # look like a successful current run.
                assistant = session.scalar(
                    select(Message)
                    .where(
                        Message.conversation_id == usage.conversation_id,
                        Message.role == "assistant",
                        Message.created_at >= run.created_at,
                    )
                    .order_by(Message.created_at.desc())
                    .limit(1)
                )
            if assistant is None:
                continue

            now = _utcnow()
            run.status = "succeeded"
            run.conversation_id = usage.conversation_id
            if usage.project_id is not None:
                run.project_id = usage.project_id
            run.result_json = _compact_result(run=run, usage=usage, assistant=assistant)
            run.error_code = ""
            run.error_detail = ""
            run.updated_at = now
            run.completed_at = now


_install_listener()
