from __future__ import annotations

import ipaddress
from dataclasses import dataclass
from urllib.parse import urlsplit


class VisionEndpointError(ValueError):
    pass


_LOOPBACK_HOSTS = {"127.0.0.1", "localhost", "::1"}
_INTERNAL_SERVICE_HOSTS = {"llama"}


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
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise VisionEndpointError("Vision endpoint URL contains unsupported credentials/query/fragment")
    return scheme, host, port


def _private_ip(host: str) -> bool:
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        return False
    return bool(address.is_loopback or address.is_private or address.is_link_local)


def is_self_hosted_vision_origin(value: str) -> bool:
    """Return True only for endpoints that cannot name a public Internet host.

    OLYA AI may run vision on loopback, the Docker `llama` service, or an
    explicitly addressed private/LAN host owned by the operator. Public DNS
    names and public IP addresses are intentionally rejected even if another
    configuration value points at the same origin.
    """

    try:
        _scheme, host, _port = _origin(value)
    except VisionEndpointError:
        return False
    return host in _LOOPBACK_HOSTS or host in _INTERNAL_SERVICE_HOSTS or _private_ip(host)


def validate_vision_endpoint(base_url: str, *, trusted_internal_base_url: str = "") -> VisionEndpoint:
    """Fail closed unless image bytes stay on self-hosted compute.

    Image inputs can contain private user photographs. Trust is therefore based
    on the destination class, not merely on equality with another environment
    variable: configuring LLAMA_BASE_URL to a public API must never make that
    public origin acceptable for image QA.
    """

    value = str(base_url or "").strip().rstrip("/")
    scheme, host, port = _origin(value)
    if host in _LOOPBACK_HOSTS:
        return VisionEndpoint(base_url=value, source="loopback", host=host)
    if host in _INTERNAL_SERVICE_HOSTS:
        return VisionEndpoint(base_url=value, source="internal_service", host=host)
    if _private_ip(host):
        return VisionEndpoint(base_url=value, source="private_network", host=host)

    trusted = str(trusted_internal_base_url or "").strip().rstrip("/")
    if trusted:
        trusted_origin = _origin(trusted)
        # Equality is insufficient on its own. The configured trusted origin
        # must itself be demonstrably self-hosted/private.
        if (scheme, host, port) == trusted_origin and is_self_hosted_vision_origin(trusted):
            return VisionEndpoint(base_url=value, source="trusted_self_hosted", host=host)

    raise VisionEndpointError(
        "Vision endpoint must be loopback, the internal llama service, or a private self-hosted IP"
    )
