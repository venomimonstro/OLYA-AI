import asyncio
from collections import defaultdict
from contextlib import asynccontextmanager


class UserConcurrencyBusyError(RuntimeError):
    pass


class UserResourceGovernor:
    """Per-user inference admission with a small safe waiting room.

    A user may work in several chats at once, but a low-memory installation still
    decodes only the plan-allowed number of model generations concurrently. Extra
    chat jobs wait instead of failing immediately. The global ResourceGovernor
    remains the final CPU/RAM safety boundary.
    """

    def __init__(self, max_waiting_per_user: int = 4) -> None:
        self._active: dict[str, int] = defaultdict(int)
        self._waiting: dict[str, int] = defaultdict(int)
        self._condition = asyncio.Condition()
        self.max_waiting_per_user = max(1, int(max_waiting_per_user))

    @staticmethod
    def _structured_answer_ready() -> bool:
        try:
            from app.services.interactive_evidence import current_interactive_evidence
            return bool(str(current_interactive_evidence().resolved_answer or "").strip())
        except Exception:
            return False

    @asynccontextmanager
    async def slot(self, user_id: str, limit: int):
        # Structured live facts never touch llama.cpp: their resolver has already
        # produced the final answer and LlamaClient.generate is short-circuited by
        # structured_fact_answer_guard. They must not wait behind a long Deep run.
        if self._structured_answer_ready():
            yield
            return

        cap = max(1, int(limit))
        admitted = False
        waiting_registered = False
        async with self._condition:
            if self._active[user_id] >= cap:
                if self._waiting[user_id] >= self.max_waiting_per_user:
                    raise UserConcurrencyBusyError("Too many chat jobs are already waiting for this account")
                self._waiting[user_id] += 1
                waiting_registered = True
            try:
                while self._active[user_id] >= cap:
                    await self._condition.wait()
                if waiting_registered:
                    self._waiting[user_id] -= 1
                    if self._waiting[user_id] <= 0:
                        self._waiting.pop(user_id, None)
                    waiting_registered = False
                self._active[user_id] += 1
                admitted = True
            except BaseException:
                if waiting_registered:
                    self._waiting[user_id] -= 1
                    if self._waiting[user_id] <= 0:
                        self._waiting.pop(user_id, None)
                self._condition.notify_all()
                raise

        try:
            yield
        finally:
            if admitted:
                async with self._condition:
                    self._active[user_id] -= 1
                    if self._active[user_id] <= 0:
                        self._active.pop(user_id, None)
                    self._condition.notify_all()
