from __future__ import annotations

from datetime import timezone

from app.models import ChatRun
from app.services.chat_runtime import ChatExecutionManager, utcnow


def install_dead_job_recovery() -> None:
    """Make conversation locking ignore/recover finished in-memory jobs.

    Under CPU pressure an asyncio task can reach done() before the manager's
    finally block removes its ActiveChatJob from `_jobs`. The old conversation
    guard treated that short-lived/dead entry as a real concurrent request and
    returned a user-visible 409. Recover it transactionally instead.
    """
    if getattr(ChatExecutionManager, "_olya_dead_job_recovery", False):
        return

    original = ChatExecutionManager._interrupt_or_reject_other_conversation_run

    def recovered(
        self,
        db,
        *,
        user_id: str,
        conversation_id: str | None,
        client_request_id: str,
    ) -> None:
        if conversation_id:
            dead_keys: list[tuple[str, str]] = []
            now = utcnow()
            for key, other in list(self._jobs.items()):
                if (
                    other.user_id != user_id
                    or other.conversation_id != conversation_id
                    or other.client_request_id == client_request_id
                    or other.status != "running"
                ):
                    continue
                task = other.task
                if task is not None and task.done():
                    dead_keys.append(key)
                    row = db.get(ChatRun, other.run_id)
                    if row is not None and row.status == "running":
                        row.status = "interrupted"
                        row.error_code = "dead_runtime_job_recovered"
                        row.error_detail = "A completed in-memory chat job was recovered before accepting the next request."
                        row.updated_at = now
                        row.completed_at = now
            for key in dead_keys:
                self._jobs.pop(key, None)

        return original(
            self,
            db,
            user_id=user_id,
            conversation_id=conversation_id,
            client_request_id=client_request_id,
        )

    ChatExecutionManager._interrupt_or_reject_other_conversation_run = recovered
    ChatExecutionManager._olya_dead_job_recovery = True
