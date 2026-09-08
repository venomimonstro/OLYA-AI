from __future__ import annotations

from starlette.types import ASGIApp, Message, Receive, Scope, Send


class RequestBodyTooLarge(RuntimeError):
    pass


class RequestBodyLimitMiddleware:
    """Streaming ASGI body limit applied before FastAPI/Pydantic allocation.

    Content-Length is rejected immediately when present. Chunked/HTTP2 bodies
    are counted while ASGI receive frames arrive, so omitting Content-Length
    cannot bypass the memory-safety boundary. Duplicate Content-Length headers
    must agree exactly; conflicting or negative framing is rejected before the
    request reaches the application to avoid proxy/application ambiguity.
    """

    def __init__(self, app: ASGIApp, max_bytes: int) -> None:
        self.app = app
        self.max_bytes = max(1024, int(max_bytes))

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope.get("type") != "http":
            await self.app(scope, receive, send)
            return

        raw_lengths = [value.strip() for key, value in scope.get("headers") or [] if key.lower() == b"content-length"]
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
