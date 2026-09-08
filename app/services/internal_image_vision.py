from __future__ import annotations

from urllib.parse import urlsplit

from app.services.image_editing import ImageEditError, LocalImageEditVision


class InternalImageEditVision(LocalImageEditVision):
    """Container-network variant restricted to the pinned llama service.

    The generic LocalImageEditVision remains loopback-only. Production Docker
    workers cannot reach another container through loopback, so this adapter adds
    exactly one service DNS name: `llama`. Arbitrary hosts, IPs and URLs remain
    rejected and `httpx` still runs with trust_env=False in the parent class.
    """

    _ALLOWED_HOSTS = {"127.0.0.1", "localhost", "::1", "llama"}

    def __init__(self, base_url: str, timeout_seconds: int = 45):
        parsed = urlsplit(base_url)
        host = (parsed.hostname or "").lower()
        if parsed.scheme not in {"http", "https"} or host not in self._ALLOWED_HOSTS:
            raise ImageEditError("Image edit vision endpoint must be loopback or the internal llama service")
        if parsed.username or parsed.password or parsed.query or parsed.fragment:
            raise ImageEditError("Image edit vision endpoint contains unsupported URL components")
        try:
            port = parsed.port
        except ValueError as exc:
            raise ImageEditError("Image edit vision endpoint has an invalid port") from exc
        if port not in {None, 8080}:
            raise ImageEditError("Image edit vision endpoint must use the internal llama port")
        self.url = base_url.rstrip("/") + "/v1/chat/completions"
        self.timeout = max(5, int(timeout_seconds))
