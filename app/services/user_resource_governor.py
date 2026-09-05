import asyncio
from collections import defaultdict
from contextlib import asynccontextmanager


class UserConcurrencyBusyError(RuntimeError):
    pass


class UserResourceGovernor:
    """Per-user inference admission without creating unbounded semaphores forever."""

    def __init__(self) -> None:
        self._active: dict[str, int] = defaultdict(int)
        self._lock = asyncio.Lock()

    @asynccontextmanager
    async def slot(self, user_id: str, limit: int):
        async with self._lock:
            if self._active[user_id] >= max(1, limit):
                raise UserConcurrencyBusyError("User inference concurrency limit reached")
            self._active[user_id] += 1
        try:
            yield
        finally:
            async with self._lock:
                self._active[user_id] -= 1
                if self._active[user_id] <= 0:
                    self._active.pop(user_id, None)
