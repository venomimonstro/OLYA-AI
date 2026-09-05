import asyncio
from contextlib import asynccontextmanager


class ResourceBusyError(RuntimeError):
    pass


class ResourceGovernor:
    """Fair bounded admission control for expensive local inference."""

    def __init__(self, max_concurrent: int = 1, max_queue: int = 64) -> None:
        if max_concurrent < 1:
            raise ValueError("max_concurrent must be >= 1")
        if max_queue < 0:
            raise ValueError("max_queue must be >= 0")
        self._sem = asyncio.Semaphore(max_concurrent)
        self.max_queue = max_queue
        self._waiting = 0
        self._lock = asyncio.Lock()

    @property
    def waiting(self) -> int:
        return self._waiting

    @asynccontextmanager
    async def slot(self):
        async with self._lock:
            if self._waiting >= self.max_queue and self._sem.locked():
                raise ResourceBusyError("local inference queue is full")
            self._waiting += 1

        try:
            await self._sem.acquire()
        finally:
            async with self._lock:
                self._waiting -= 1

        try:
            yield
        finally:
            self._sem.release()
