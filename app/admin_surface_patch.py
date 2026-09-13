from __future__ import annotations

import re
from collections.abc import Sequence

from fastapi import Depends
from fastapi.routing import APIRouter
from starlette.responses import HTMLResponse

from app.services.admin_browser_session import require_admin_browser_session

_NAV = (
    ("Owner", "/admin/owner", "owner"),
    ("Control Center", "/admin", "control"),
    ("Integrations", "/admin/integrations", "integrations"),
    ("Support", "/admin/support", "support"),
    ("Users", "/admin/users", "users"),
    ("Capabilities", "/admin/capabilities", "capabilities"),
    ("Analytics", "/admin/analytics", "analytics"),
    ("Beta", "/admin/beta", "beta"),
    ("Launch", "/admin/launch", "launch"),
    ("Media", "/admin/media", "media"),
)

_ADMIN_MARKERS = (
    "X1 Admin", "OLYA AI · Owner", "X1 Closed-Beta", "X1 Beta Operations",
    "X1 Public Launch", "X1 Progressive Public Launch", "X1 Media Center",
    "/v1/admin/", 'href="/admin',
)


def _active_key(document: str) -> str:
    match = re.search(r"<title[^>]*>(.*?)</title>", document, flags=re.I | re.S)
    title = re.sub(r"\s+", " ", match.group(1)).casefold() if match else ""
    for marker, key in (
        ("owner", "owner"), ("integrat", "integrations"), ("support", "support"),
        ("users", "users"), ("capabil", "capabilities"), ("analytic", "analytics"),
        ("beta", "beta"), ("launch", "launch"), ("media", "media"),
    ):
        if marker in title:
            return key
    return "control"


def _looks_like_admin(document: str) -> bool:
    lowered = document.casefold()
    return "<html" in lowered and any(marker.casefold() in lowered for marker in _ADMIN_MARKERS)


def _nonce_attr(document: str) -> str:
    match = re.search(r"nonce=['\"]([^'\"]+)['\"]", document, flags=re.I)
    return f' nonce="{match.group(1)}"' if match else ""


def _shell(document: str) -> str:
    active = _active_key(document)
    links = "".join(
        f'<a class="x1-admin-nav-link{" active" if key == active else ""}" href="{href}">{label}</a>'
        for label, href, key in _NAV
    )
    return (
        '<aside class="x1-admin-global" aria-label="Административное меню">'
        '<div class="x1-admin-global-brand">OLYA AI<small>Администрирование</small></div>'
        f'<nav class="x1-admin-global-links">{links}</nav>'
        '<div class="x1-admin-global-foot"><a href="/app">← В приложение</a></div>'
        '</aside>'
    )


def _style(document: str) -> str:
    return r'''
<style__NONCE__>
:root{--x1-admin-panel:#0b0e14;--x1-admin-line:#29313d;--x1-admin-text:#f4f6fa;--x1-admin-muted:#95a0b0}
html{scrollbar-gutter:stable}body{padding-left:238px!important;min-width:0!important}.x1-admin-global{position:fixed;z-index:2147483000;left:0;top:0;bottom:0;width:238px;padding:16px 12px;background:var(--x1-admin-panel);border-right:1px solid var(--x1-admin-line);color:var(--x1-admin-text);font:14px/1.4 Inter,system-ui,-apple-system,Segoe UI,Arial,sans-serif;display:flex;flex-direction:column;box-sizing:border-box}.x1-admin-global *{box-sizing:border-box}.x1-admin-global-brand{padding:5px 10px 17px;font-size:16px;font-weight:900}.x1-admin-global-brand small{display:block;margin-top:2px;color:var(--x1-admin-muted);font-size:11px;font-weight:600}.x1-admin-global-links{display:grid;gap:4px;overflow:auto}.x1-admin-nav-link,.x1-admin-global-foot a{display:flex;align-items:center;min-height:40px;padding:9px 11px;border:1px solid transparent;border-radius:9px;color:#dce2eb!important;text-decoration:none!important;background:transparent}.x1-admin-nav-link:hover,.x1-admin-global-foot a:hover{background:#141a24;border-color:#252e3a}.x1-admin-nav-link.active{background:#1b202b;border-color:#834448;color:#fff!important}.x1-admin-global-foot{margin-top:auto;padding-top:10px;border-top:1px solid var(--x1-admin-line)}body>.layout{display:block!important;min-height:100vh!important}body>.layout>.sidebar{display:none!important}body>header.top{display:none!important}section:has(#token),div.card:has(#token),div.auth:has(#token){display:none!important}#token,#connect{display:none!important}.topbar{top:0!important}
@media(max-width:760px){body{padding-left:0!important;padding-top:61px!important}.x1-admin-global{right:0;bottom:auto;width:auto;height:61px;padding:7px 10px;border-right:0;border-bottom:1px solid var(--x1-admin-line);display:block;overflow:hidden}.x1-admin-global-brand,.x1-admin-global-foot{display:none}.x1-admin-global-links{display:flex;gap:5px;overflow-x:auto;padding-bottom:2px}.x1-admin-nav-link{white-space:nowrap;flex:0 0 auto;min-height:44px}.topbar{top:61px!important}}
</style>
'''.replace("__NONCE__", _nonce_attr(document))


def _session_script(document: str) -> str:
    return (
        f'<script{_nonce_attr(document)}>(function(){{'
        "var a=sessionStorage.getItem('x1AdminToken')||sessionStorage.getItem('x1_access_token')||'';"
        "if(!a){location.replace('/login?next='+encodeURIComponent(location.pathname+location.search));return;}"
        "if(!sessionStorage.getItem('x1AdminToken'))sessionStorage.setItem('x1AdminToken',a);"
        "})();</script>"
    )


def _decorate(document: str) -> str:
    if not _looks_like_admin(document) or "x1-admin-global" in document:
        return document
    if "</head>" in document:
        document = document.replace("</head>", _style(document) + "</head>", 1)
    body = re.search(r"<body(?:\s[^>]*)?>", document, flags=re.I)
    if body:
        document = document[:body.end()] + _shell(document) + _session_script(document) + document[body.end():]
    return document


def _is_admin_html_route(path: str, methods: Sequence[str] | set[str] | None, include_in_schema: bool) -> bool:
    normalized = str(path or "").rstrip("/") or "/"
    method_set = {str(item).upper() for item in (methods or ())}
    return (
        not include_in_schema
        and (not method_set or "GET" in method_set)
        and (normalized == "/admin" or normalized.startswith("/admin/") or normalized == "/media-admin" or normalized.startswith("/media-admin/"))
    )


def install_admin_surface_patch() -> None:
    """Apply one server-gated, persistent navigation shell to every admin HTML page."""
    current_add = APIRouter.add_api_route
    if not getattr(current_add, "_x1_admin_surface_guard", False):
        def guarded_add(self, path, endpoint, *args, **kwargs):
            if _is_admin_html_route(path, kwargs.get("methods"), bool(kwargs.get("include_in_schema", True))):
                dependencies = list(kwargs.get("dependencies") or [])
                if not any(getattr(dep, "dependency", None) is require_admin_browser_session for dep in dependencies):
                    dependencies.append(Depends(require_admin_browser_session))
                kwargs["dependencies"] = dependencies
            return current_add(self, path, endpoint, *args, **kwargs)

        guarded_add._x1_admin_surface_guard = True  # type: ignore[attr-defined]
        APIRouter.add_api_route = guarded_add

    current_html_init = HTMLResponse.__init__
    if not getattr(current_html_init, "_x1_admin_surface_shell", False):
        def guarded_html_init(self, content, *args, **kwargs):
            if isinstance(content, str):
                content = _decorate(content)
            return current_html_init(self, content, *args, **kwargs)

        guarded_html_init._x1_admin_surface_shell = True  # type: ignore[attr-defined]
        HTMLResponse.__init__ = guarded_html_init
