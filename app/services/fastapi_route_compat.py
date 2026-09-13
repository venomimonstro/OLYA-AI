from __future__ import annotations

from typing import Any


REQUIRED_ROUTE_PREFIXES: dict[str, tuple[str, ...]] = {
    "auth": ("/v1/auth",),
    "chat": ("/v1/chat",),
    "projects": ("/v1/projects",),
    "documents": ("/v1/documents",),
    "images": ("/v1/images",),
    "research": ("/v1/research",),
    "development": ("/v1/development-plans", "/v1/development"),
    "engineering": ("/v1/engineering-runs", "/v1/engineering"),
    "sandbox": ("/v1/project-sandboxes", "/v1/sandbox"),
    "git": ("/v1/git",),
    "commerce": ("/v1/commerce",),
    "external_api": ("/v1/api",),
    "complaints": ("/v1/feedback/complaints", "/v1/complaints"),
    "beta": ("/v1/admin/beta",),
    "reliability": ("/v1/admin/reliability",),
    "operations": ("/v1/admin/operations",),
}


def effective_route_paths(app: Any) -> set[str]:
    """Return concrete effective paths for FastAPI 0.137+ lazy included routers.

    FastAPI 0.137+ stores ``include_router()`` calls as private ``_IncludedRouter``
    wrappers in ``app.routes``. Those wrappers intentionally have no ``path``;
    their resolved, prefix-aware children are exposed by ``effective_candidates``.
    We use capability detection instead of importing FastAPI's private class so
    this keeps working on older FastAPI releases too.
    """
    paths: set[str] = set()
    stack = list(getattr(app, "routes", ()) or ())
    seen: set[int] = set()

    while stack:
        route = stack.pop()
        marker = id(route)
        if marker in seen:
            continue
        seen.add(marker)

        path = getattr(route, "path", None)
        if isinstance(path, str) and path:
            paths.add(path)

        candidates = getattr(route, "effective_candidates", None)
        if callable(candidates):
            try:
                stack.extend(list(candidates()))
            except Exception:
                # A readiness check must fail closed through missing prefixes,
                # not crash the application because FastAPI internals changed.
                continue

        nested = getattr(route, "routes", None)
        if nested:
            try:
                stack.extend(list(nested))
            except TypeError:
                pass

        original_router = getattr(route, "original_router", None)
        original_routes = getattr(original_router, "routes", None)
        if original_routes:
            try:
                stack.extend(list(original_routes))
            except TypeError:
                pass

    return paths


def route_contract(app: Any) -> dict[str, Any]:
    paths = effective_route_paths(app)
    missing = sorted(
        name
        for name, accepted_prefixes in REQUIRED_ROUTE_PREFIXES.items()
        if not any(path.startswith(prefix) for path in paths for prefix in accepted_prefixes)
    )
    if missing:
        return {
            "key": "product.route_contract",
            "subsystem": "api",
            "status": "failed",
            "message": "Required product API surfaces are not registered",
            "critical": True,
            "dependency": "",
            "latency_ms": 0,
            "severity": "critical",
            "details": {
                "missing_features": missing,
                "route_count": len(paths),
                "recommended_action": "Restore/register missing routers before Stable release.",
            },
        }
    return {
        "key": "product.route_contract",
        "subsystem": "api",
        "status": "stable",
        "message": "Required product API surfaces are registered",
        "critical": True,
        "dependency": "",
        "latency_ms": 0,
        "severity": "info",
        "details": {
            "features": sorted(REQUIRED_ROUTE_PREFIXES),
            "route_count": len(paths),
            "recommended_action": "",
        },
    }


def install_system_observability_compat() -> None:
    """Patch only the route-contract probe used by system observability."""
    from app.services import system_observability

    system_observability._route_contract = route_contract
