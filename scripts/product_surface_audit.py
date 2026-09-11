#!/usr/bin/env python3
from __future__ import annotations

import importlib
import json
import re
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

# User/admin surfaces that must never silently disappear from the application.
ESSENTIAL_PATHS = {
    "/",
    "/register",
    "/login",
    "/welcome",
    "/app",
    "/studio",
    "/admin",
    "/admin/media",
    "/admin/beta",
    "/admin/launch",
    "/health",
    "/ready",
    "/v1/auth/register",
    "/v1/auth/login",
    "/v1/auth/me",
    "/v1/account/onboarding",
    "/v1/chat",
    "/v1/chat/stream",
    "/v1/chat/runs/{client_request_id}",
    "/v1/chat/runs/{client_request_id}/cancel",
    "/v1/projects",
    "/v1/projects/{project_id}/workspace",
    "/v1/projects/{project_id}/files",
    "/v1/projects/{project_id}/files/{file_id}/retry",
    "/v1/projects/{project_id}/files/{file_id}/make-current",
    "/v1/documents",
    "/v1/images/status",
    "/v1/images/generations",
    "/v1/images/references",
    "/v1/images/edits",
    "/v1/commerce/api-keys/{api_key_id}/rotate",
    "/v1/api/contexts",
    "/v1/api/contexts/{context_id}",
    "/v1/api/chat",
}


def _route_endpoint_key(route) -> tuple[str, str] | None:
    endpoint = getattr(route, "endpoint", None)
    if endpoint is None:
        return None
    return (str(getattr(endpoint, "__module__", "")), str(getattr(endpoint, "__name__", "")))


def _app_routes():
    from app.main import app

    return list(app.routes)


def _discover_router_modules() -> list[str]:
    modules: list[str] = []
    for path in sorted((ROOT / "app" / "api" / "routes").glob("*.py")):
        if path.name != "__init__.py":
            modules.append("app.api.routes." + path.stem)
    for path in sorted((ROOT / "app").glob("*_ui.py")):
        modules.append("app." + path.stem)
    return sorted(set(modules))


def _path_shape(value: str) -> tuple[str, ...]:
    raw = str(value or "").split("?", 1)[0].split("#", 1)[0]
    raw = re.sub(r"\$\{[^}]+\}", "{param}", raw)
    return tuple(part for part in raw.split("/") if part)


def _path_matches(reference: str, registered: str) -> bool:
    ref = _path_shape(reference)
    actual = _path_shape(registered)
    if not ref or len(ref) != len(actual):
        return False
    for left, right in zip(ref, actual):
        if left.startswith("{") or right.startswith("{"):
            continue
        if left != right:
            return False
    return True


def _ui_contract_errors(registered_paths: set[str]) -> list[dict]:
    errors: list[dict] = []
    for path in sorted((ROOT / "app").glob("*_ui.py")):
        source = path.read_text("utf-8")
        rel = path.relative_to(ROOT).as_posix()

        # Capture quoted/template-literal internal URLs as a whole. This keeps
        # `${esc(id)}` or `${generationId}` intact so _path_shape can normalize
        # the complete dynamic segment to a path parameter instead of treating
        # a partial JavaScript expression as a literal route.
        refs = sorted(set(re.findall(r"[\'\"`](/v1/[^\'\"`\s<>]+)", source)))
        for ref in refs:
            base = ref.split("?", 1)[0]
            # A trailing slash commonly means string concatenation continues
            # after the literal; the concrete dynamic URL is checked elsewhere.
            if base.endswith("/"):
                continue
            if not any(_path_matches(base, route_path) for route_path in registered_paths):
                errors.append({"code": "ui_api_route_missing", "file": rel, "reference": ref})

        hrefs = sorted(set(re.findall(r"href=[\\\'\"](/[^\\\'\"#?]+)", source)))
        for href in hrefs:
            if href.startswith("/v1/"):
                continue
            if href not in registered_paths:
                errors.append({"code": "ui_navigation_route_missing", "file": rel, "reference": href})

        # Catch visible buttons that have neither inline behavior nor an ID that
        # is referenced again by page JavaScript. Submit buttons are bound at the
        # form level and are therefore valid without their own handler.
        for match in re.finditer(r"<button\b([^>]*)>", source, flags=re.IGNORECASE):
            attrs = match.group(1)
            if re.search(r"\bonclick\s*=", attrs, flags=re.IGNORECASE):
                continue
            if re.search(r"\btype\s*=\s*\\?[\\\'\"]submit\\?[\\\'\"]", attrs, flags=re.IGNORECASE):
                continue
            id_match = re.search(r"\bid\s*=\s*[\\\'\"]([^\\\'\"]+)", attrs, flags=re.IGNORECASE)
            if not id_match:
                errors.append({"code": "ui_button_unwired", "file": rel, "button": attrs[:160]})
                continue
            button_id = id_match.group(1)
            if source.count(button_id) < 2:
                errors.append({"code": "ui_button_id_not_referenced", "file": rel, "button_id": button_id})
    return errors


def audit() -> dict:
    app_routes = _app_routes()
    app_endpoint_keys = {_route_endpoint_key(route) for route in app_routes}
    registered_paths = {str(getattr(route, "path", "")) for route in app_routes if getattr(route, "path", None)}
    errors: list[dict] = []

    # Every router module in the product must contribute all of its endpoint
    # functions to the final app, directly or through a nested APIRouter.
    module_stats: dict[str, int] = {}
    for module_name in _discover_router_modules():
        module = importlib.import_module(module_name)
        router = getattr(module, "router", None)
        if router is None:
            errors.append({"code": "router_export_missing", "module": module_name})
            continue
        routes = list(getattr(router, "routes", []))
        module_stats[module_name] = len(routes)
        for route in routes:
            key = _route_endpoint_key(route)
            if key is not None and key not in app_endpoint_keys:
                errors.append({
                    "code": "router_endpoint_not_registered",
                    "module": module_name,
                    "endpoint": f"{key[0]}.{key[1]}",
                    "local_path": str(getattr(route, "path", "")),
                })

    for path in sorted(ESSENTIAL_PATHS - registered_paths):
        errors.append({"code": "essential_product_path_missing", "path": path})

    # A same-method/same-path collision means one implementation may shadow
    # another depending on registration order. Ignore framework docs endpoints.
    keys: list[tuple[str, str]] = []
    for route in app_routes:
        path = str(getattr(route, "path", ""))
        if not path or path in {"/openapi.json", "/docs", "/docs/oauth2-redirect", "/redoc"}:
            continue
        for method in sorted(set(getattr(route, "methods", set()) or set())):
            if method not in {"HEAD", "OPTIONS"}:
                keys.append((method, path))
    for (method, path), count in sorted(Counter(keys).items()):
        if count > 1:
            errors.append({"code": "duplicate_route", "method": method, "path": path, "count": count})

    errors.extend(_ui_contract_errors(registered_paths))
    return {
        "format": "x1-product-surface-audit-v1",
        "status": "passed" if not errors else "failed",
        "registered_route_count": len(app_routes),
        "router_modules": module_stats,
        "essential_paths": sorted(ESSENTIAL_PATHS),
        "errors": errors,
    }


def main() -> int:
    result = audit()
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["status"] == "passed" else 2


if __name__ == "__main__":
    raise SystemExit(main())
