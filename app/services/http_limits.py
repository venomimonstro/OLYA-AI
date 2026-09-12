from __future__ import annotations

from app.core.config import get_settings
from app.services.deadline import begin_deadline_from_headers
from starlette.types import ASGIApp, Message, Receive, Scope, Send


class RequestBodyTooLarge(RuntimeError):
    pass


class RequestBodyLimitMiddleware:
    """Streaming body limit plus request-rooted deadline initialization.

    The deadline is established at the outer ASGI boundary before auth, body
    parsing, queueing, research, inference or tools. Inner layers only reuse the
    same ContextVar budget and therefore cannot accidentally extend it.
    """

    def __init__(self, app: ASGIApp, max_bytes: int) -> None:
        self.app = app
        self.max_bytes = max(1024, int(max_bytes))

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope.get("type") != "http":
            await self.app(scope, receive, send)
            return

        raw_headers = scope.get("headers") or []
        deadline_values = [value.strip() for key, value in raw_headers if key.lower() == b"x-x1-deadline-ms"]
        if len(set(deadline_values)) > 1:
            await self._reject(send, b"Conflicting X-X1-Deadline-Ms", status_code=400)
            return
        decoded_headers = {}
        for key, value in raw_headers:
            try:
                decoded_headers[key.decode("latin-1").lower()] = value.decode("latin-1")
            except UnicodeDecodeError:
                continue
        try:
            budget = begin_deadline_from_headers(
                decoded_headers,
                default_seconds=float(get_settings().request_timeout_seconds),
                source="http",
                replace=True,
            )
        except ValueError as exc:
            await self._reject(send, str(exc).encode("utf-8"), status_code=400)
            return
        state = scope.setdefault("state", {})
        state["x1_deadline_seconds"] = budget.budget_seconds

        raw_lengths = [value.strip() for key, value in raw_headers if key.lower() == b"content-length"]
        if raw_lengths:
            parsed_lengths: list[int] = []
            try:
                for raw in raw_lengths:
                    if not raw or b"," in raw:
                        raise ValueError
                    value = int(raw)
                    if value < 0:
                        raise ValueError
                    parsed_lengths.append(value)
            except ValueError:
                await self._reject(send, b"Invalid Content-Length")
                return
            if len(set(parsed_lengths)) != 1:
                await self._reject(send, b"Conflicting Content-Length")
                return
            if parsed_lengths[0] > self.max_bytes:
                await self._reject(send, b"Request body too large")
                return

        consumed = 0
        response_started = False

        async def limited_receive() -> Message:
            nonlocal consumed
            message = await receive()
            if message.get("type") == "http.request":
                consumed += len(message.get("body") or b"")
                if consumed > self.max_bytes:
                    raise RequestBodyTooLarge
            return message

        async def tracked_send(message: Message) -> None:
            nonlocal response_started
            if message.get("type") == "http.response.start":
                response_started = True
            await send(message)

        try:
            await self.app(scope, limited_receive, tracked_send)
        except RequestBodyTooLarge:
            if not response_started:
                await self._reject(send, b"Request body too large")

    @staticmethod
    async def _reject(send: Send, body: bytes, *, status_code: int = 413) -> None:
        await send({
            "type": "http.response.start",
            "status": status_code,
            "headers": [
                (b"content-type", b"text/plain; charset=utf-8"),
                (b"content-length", str(len(body)).encode("ascii")),
                (b"cache-control", b"no-store"),
                (b"connection", b"close"),
            ],
        })
        await send({"type": "http.response.body", "body": body})
