from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import urlsplit


class VisionEndpointError(ValueError):
    pass


_LOOPBACK_HOSTS = {"127.0.0.1", "localhost", "::1"}


@dataclass(frozen=True)
class VisionEndpoint:
    base_url: str
    source: str
    host: str


def _origin(value: str) -> tuple[str, str, int | None]:
    parsed = urlsplit(str(value or "").strip())
    scheme = (parsed.scheme or "").lower()
    host = (parsed.hostname or "").lower()
    try:
        port = parsed.port
    except ValueError as exc:
        raise VisionEndpointError("Vision endpoint port is invalid") from exc
    if scheme not in {"http", "https"} or not host:
        raise VisionEndpointError("Vision endpoint must be an absolute http(s) URL")
    if parsed.username or parsed.password:
        raise VisionEndpointError("Vision endpoint credentials in URL are not allowed")
    return scheme, host, port


def validate_vision_endpoint(base_url: str, *, trusted_internal_base_url: str = "") -> VisionEndpoint:
    """Allow only loopback or the exact configured llama.cpp origin.

    Image inputs can contain private user photographs. They must never be sent to
    an arbitrary remote host simply because an environment variable was typoed or
    compromised. Docker production uses the private `llama` service; development
    may use loopback.
    """

    value = str(base_url or "").strip().rstrip("/")
    scheme, host, port = _origin(value)
    if host in _LOOPBACK_HOSTS:
        return VisionEndpoint(base_url=value, source="loopback", host=host)

    trusted = str(trusted_internal_base_url or "").strip().rstrip("/")
    if trusted:
        trusted_origin = _origin(trusted)
        if (scheme, host, port) == trusted_origin:
            return VisionEndpoint(base_url=value, source="trusted_llama", host=host)
    raise VisionEndpointError("Vision endpoint must be loopback or the configured internal llama.cpp origin")
