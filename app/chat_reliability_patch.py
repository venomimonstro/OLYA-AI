from __future__ import annotations

import asyncio

from fastapi import HTTPException


# A live SSE heartbeat must never allow a request to consume the CPU forever.
# This is an end-to-end ceiling across research, inference and verification.
_CHAT_RUN_WALL_TIMEOUT_SECONDS = 210.0


def install_chat_reliability_patch() -> None:
    from app.services.chat_runtime import ChatExecutionManager

    current = ChatExecutionManager._execute
    if getattr(current, "_olya_wall_timeout", False):
        return

    async def bounded_execute(self, key, job, runner):
        async def bounded_runner(active_job):
            try:
                return await asyncio.wait_for(
                    runner(active_job),
                    timeout=_CHAT_RUN_WALL_TIMEOUT_SECONDS,
                )
            except TimeoutError as exc:
                raise HTTPException(
                    status_code=504,
                    detail=(
                        "Запрос превысил безопасное время обработки и был остановлен. "
                        "Текст запроса сохранён; можно повторить его без ожидания зависшей генерации."
                    ),
                ) from exc

        return await current(self, key, job, bounded_runner)

    bounded_execute._olya_wall_timeout = True  # type: ignore[attr-defined]
    ChatExecutionManager._execute = bounded_execute
