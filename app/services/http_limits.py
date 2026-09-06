from __future__ import annotations

from starlette.types import ASGIApp, Message, Receive, Scope, Send


class RequestBodyTooLarge(RuntimeError):
    pass


class RequestBodyLimitMiddleware:
    """Streaming ASGI body limit applied before FastAPI/Pydantic allocation.

    Content-Length is rejected immediately when present. Chunked/HTTP2 bodies
    are counted while ASGI receive frames arrive, so omitting Content-Length
    cannot bypass the memory-safety boundary.
    """

    def __init__(self, app: ASGIApp, max_bytes: int) -> None:
        self.app = app
        self.max_bytes = max(1024, int(max_bytes))

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope.get("type") != "http":
            await self.app(scope, receive, send)
            return

        headers = {key.lower(): value for key, value in scope.get("headers") or []}
        raw_length = headers.get(b"content-length", b"")
        if raw_length:
            try:
                length = int(raw_length)
            except ValueError:
                await self._reject(send, b"Invalid Content-Length")
                return
            if length > self.max_bytes:
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
    async def _reject(send: Send, body: bytes) -> None:
        await send({
            "type": "http.response.start",
            "status": 413,
            "headers": [
                (b"content-type", b"text/plain; charset=utf-8"),
                (b"content-length", str(len(body)).encode("ascii")),
                (b"cache-control", b"no-store"),
                (b"connection", b"close"),
            ],
        })
        await send({"type": "http.response.body", "body": body})
