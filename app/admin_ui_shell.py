from __future__ import annotations

import functools
import inspect
import re
from collections.abc import Callable

from fastapi import Depends
from fastapi.routing import APIRouter
from fastapi.responses import HTMLResponse

from app.services.admin_browser_session import require_admin_browser_session

_INSTALLED = False
_ORIGINAL_ADD_API_ROUTE = APIRouter.add_api_route

_NAV = (
    ("/admin/owner", "Owner"),
    ("/admin", "Control Center"),
    ("/admin/integrations", "Integrations"),
    ("/admin/support", "Support"),
    ("/admin/users", "Users"),
    ("/admin/capabilities", "Capabilities"),
    ("/admin/analytics", "Analytics"),
    ("/admin/beta", "Beta"),
    ("/admin/launch", "Launch"),
    ("/admin/media", "Media"),
)

_CSS = r"""
.x1-admin-shell{position:sticky;top:0;z-index:2147483000;display:flex;align-items:center;gap:8px;min-height:56px;padding:8px 12px;background:#090b10f7;border-bottom:1px solid #29313d;color:#f4f6fa;font:14px/1.3 Inter,system-ui,-apple-system,Segoe UI,Arial,sans-serif;backdrop-filter:blur(12px)}
.x1-admin-shell *{box-sizing:border-box}.x1-admin-shell-brand{font-weight:900;white-space:nowrap;margin-right:4px}.x1-admin-shell-nav{display:flex;align-items:center;gap:5px;min-width:0;overflow-x:auto;scrollbar-width:thin;flex:1}.x1-admin-shell a{display:inline-flex;align-items:center;min-height:38px;padding:8px 10px;border:1px solid transparent;border-radius:9px;color:#dce2eb!important;background:transparent!important;text-decoration:none!important;white-space:nowrap}.x1-admin-shell a:hover{background:#141a24!important;border-color:#29313d}.x1-admin-shell a.active{background:#1b202b!important;border-color:#834448;color:#fff!important}.x1-admin-shell .x1-admin-shell-app{flex:0 0 auto;border-color:#29313d}
/* One shell owns navigation. Old page-specific menus are removed to stop layout jumps. */
body>.top,body>header.top{display:none!important}.layout>.sidebar{display:none!important}.layout{display:block!important}.main{min-width:0!important}.topbar{top:56px!important}
/* Legacy technical token forms remain in DOM for old JS compatibility but are not user-facing. */
.card:has(#token),.auth:has(#token){display:none!important}#token,#connect{display:none!important}
@media(max-width:760px){.x1-admin-shell{padding:7px 9px;min-height:52px}.x1-admin-shell-brand{font-size:12px}.x1-admin-shell a{min-height:36px;padding:7px 9px}.topbar{top:52px!important}}
"""


def _active_href(path: str, href: str) -> bool:
    if href == "/admin":
        return path.rstrip("/") == "/admin"
    return path == href or path.startswith(href + "/")


def _shell(path: str) -> str:
    links = "".join(
        f'<a class="{"active" if _active_href(path, href) else ""}" href="{href}">{label}</a>'
        for href, label in _NAV
    )
    return (
        '<div class="x1-admin-shell" role="navigation" aria-label="Административное меню">'
        '<div class="x1-admin-shell-brand">OLYA AI · Admin</div>'
        f'<nav class="x1-admin-shell-nav">{links}</nav>'
        '<a class="x1-admin-shell-app" href="/app">← В приложение</a>'
        '</div>'
    )


def _decorate_html_response(response: HTMLResponse, path: str) -> HTMLResponse:
    if not isinstance(response, HTMLResponse):
        return response
    try:
        text = bytes(response.body).decode(response.charset or "utf-8")
    except (UnicodeDecodeError, AttributeError):
        return response
    if "x1-admin-shell" in text:
        return response

    nonce_match = re.search(r'<style\s+nonce=["\']([^"\']+)["\']', text, flags=re.I)
    nonce_attr = f' nonce="{nonce_match.group(1)}"' if nonce_match else ""
    style = f"<style{nonce_attr}>{_CSS}</style>"
    if "</head>" in text:
        text = text.replace("</head>", style + "</head>", 1)
    if "<body" in text:
        body_end = text.find(">", text.find("<body"))
        if body_end >= 0:
            text = text[: body_end + 1] + _shell(path) + text[body_end + 1 :]

    response.body = text.encode(response.charset or "utf-8")
    response.headers["content-length"] = str(len(response.body))
    return response


def _wrap_endpoint(endpoint: Callable, path: str) -> Callable:
    signature = inspect.signature(endpoint)
    if inspect.iscoroutinefunction(endpoint):
        @functools.wraps(endpoint)
        async def async_wrapper(*args, **kwargs):
            result = await endpoint(*args, **kwargs)
            return _decorate_html_response(result, path)
        async_wrapper.__signature__ = signature  # type: ignore[attr-defined]
        return async_wrapper

    @functools.wraps(endpoint)
    def sync_wrapper(*args, **kwargs):
        result = endpoint(*args, **kwargs)
        return _decorate_html_response(result, path)
    sync_wrapper.__signature__ = signature  # type: ignore[attr-defined]
    return sync_wrapper


def install_admin_ui_shell() -> None:
    """Apply one server-side security and navigation contract to every /admin HTML route.

    This is installed before route modules are imported. API routes under /v1/admin
    are unaffected because their local paths do not start with /admin.
    """
    global _INSTALLED
    if _INSTALLED:
        return
    _INSTALLED = True

    def guarded_add_api_route(self, path: str, endpoint: Callable, *args, **kwargs):
        normalized = str(path or "").rstrip("/") or "/"
        if normalized == "/admin" or normalized.startswith("/admin/"):
            dependencies = list(kwargs.get("dependencies") or [])
            if not any(getattr(dep, "dependency", None) is require_admin_browser_session for dep in dependencies):
                dependencies.append(Depends(require_admin_browser_session))
            kwargs["dependencies"] = dependencies
            endpoint = _wrap_endpoint(endpoint, normalized)
        return _ORIGINAL_ADD_API_ROUTE(self, path, endpoint, *args, **kwargs)

    APIRouter.add_api_route = guarded_add_api_route
