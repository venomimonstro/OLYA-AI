from __future__ import annotations

from importlib import import_module
import re

from fastapi.responses import HTMLResponse


_NAV_ITEMS = (
    ("Owner", "/admin/owner"),
    ("Control Center", "/admin"),
    ("Integrations", "/admin/integrations"),
    ("Support", "/admin/support"),
    ("Users", "/admin/users"),
    ("Capabilities", "/admin/capabilities"),
    ("Analytics", "/admin/analytics"),
    ("Beta", "/admin/beta"),
    ("Launch", "/admin/launch"),
    ("Media", "/admin/media"),
)

_SHELL_STYLE = r'''
#x1-admin-shell{position:sticky;top:0;z-index:2147483000;display:flex;align-items:center;gap:6px;min-height:54px;padding:8px 12px;background:#090b10f7;border-bottom:1px solid #29313d;box-shadow:0 8px 24px #0003;color:#f4f6fa;font:14px/1.3 Inter,system-ui,-apple-system,Segoe UI,Arial,sans-serif;overflow-x:auto;scrollbar-width:thin}
#x1-admin-shell *{box-sizing:border-box}#x1-admin-shell .x1-admin-brand{font-weight:900;white-space:nowrap;padding:0 8px 0 2px}#x1-admin-shell a{display:flex;align-items:center;min-height:36px;padding:7px 10px;border:1px solid transparent;border-radius:8px;color:#dce2eb;text-decoration:none;white-space:nowrap;background:transparent}#x1-admin-shell a:hover{background:#151b25;border-color:#29313d}#x1-admin-shell a[aria-current="page"]{background:#1b202b;border-color:#834448;color:#fff}#x1-admin-shell .x1-admin-spacer{flex:1;min-width:8px}#x1-admin-shell .x1-admin-app{border-color:#29313d;background:#151b25}
body>header.top,body>header:not(#x1-admin-shell),body>.top{display:none!important}body>.layout>.sidebar,body>.layout>.main>.topbar{display:none!important}body>.layout{display:block!important;min-height:0!important}body>.layout>.main{min-width:0!important}#token,#connect{display:none!important}#token+button{display:none!important}
@media(max-width:760px){#x1-admin-shell{padding:7px 8px;min-height:50px}#x1-admin-shell .x1-admin-brand{position:sticky;left:0;background:#090b10;padding-right:10px}#x1-admin-shell a{min-height:34px;padding:6px 9px}}
'''

_SHELL_SCRIPT = r'''
(()=>{const t=sessionStorage.getItem('x1AdminToken')||sessionStorage.getItem('x1_access_token')||'';if(t){sessionStorage.setItem('x1AdminToken',t)}else{location.replace('/login?next='+encodeURIComponent(location.pathname+location.search));return}const p=location.pathname.replace(/\/$/,'')||'/';for(const a of document.querySelectorAll('#x1-admin-shell a[data-admin-path]')){const x=(a.dataset.adminPath||'').replace(/\/$/,'')||'/';if(p===x)a.setAttribute('aria-current','page')}})();
'''


def _nav_html() -> str:
    links = "".join(f'<a data-admin-path="{path}" href="{path}">{label}</a>' for label, path in _NAV_ITEMS)
    return (
        '<nav id="x1-admin-shell" aria-label="Административное меню">'
        '<div class="x1-admin-brand">OLYA AI · Admin</div>'
        f'{links}<div class="x1-admin-spacer"></div>'
        '<a class="x1-admin-app" href="/app">← В приложение</a>'
        '</nav>'
    )


def decorate_admin_page(page: str, *, nonce: str | None = None) -> str:
    """Inject one stable admin navigation shell into legacy admin HTML.

    The patch is intentionally presentation-only: existing page APIs and actions
    remain unchanged. The current login token is promoted to x1AdminToken so old
    admin consoles no longer need a separate bearer-token field.
    """
    if not isinstance(page, str) or 'id="x1-admin-shell"' in page:
        return page
    if "<body" not in page or "</head>" not in page:
        return page

    token = nonce
    if token is None and "__NONCE__" in page:
        token = "__NONCE__"
    if token is None:
        match = re.search(r'nonce="([^"]+)"', page)
        if match:
            token = match.group(1)

    nonce_attr = f' nonce="{token}"' if token else ""
    style = f"<style{nonce_attr}>{_SHELL_STYLE}</style>"
    script = f"<script{nonce_attr}>{_SHELL_SCRIPT}</script>"
    page = page.replace("</head>", style + "</head>", 1)

    body_end = page.find(">", page.find("<body"))
    if body_end < 0:
        return page
    insertion = _nav_html() + script
    return page[: body_end + 1] + insertion + page[body_end + 1 :]


def _patch_page_constant(module_name: str) -> None:
    module = import_module(module_name)
    page = getattr(module, "PAGE", None)
    if isinstance(page, str):
        setattr(module, "PAGE", decorate_admin_page(page))


def _patch_support_response() -> None:
    module = import_module("app.support_ui")
    original = getattr(module, "_response", None)
    if original is None or getattr(original, "_x1_admin_shell", False):
        return

    def wrapped(title: str, body: str, script: str) -> HTMLResponse:
        response = original(title, body, script)
        if "Admin" not in title:
            return response
        rendered = response.body.decode("utf-8")
        rendered = decorate_admin_page(rendered)
        headers = {
            key: value
            for key, value in response.headers.items()
            if key.lower() not in {"content-length", "content-type"}
        }
        return HTMLResponse(rendered, status_code=response.status_code, headers=headers)

    wrapped._x1_admin_shell = True  # type: ignore[attr-defined]
    module._response = wrapped


def install_admin_shell_patch() -> None:
    """Patch every legacy /admin HTML console before routers are registered."""
    for module_name in (
        "app.admin_ui",
        "app.admin_users_ui",
        "app.beta_admin_ui",
        "app.launch_admin_ui",
        "app.media_admin_ui",
        "app.owner_dashboard_ui",
        "app.api.routes.owner_integrations",
        "app.api.routes.capabilities",
        "app.api.routes.product_analytics",
    ):
        _patch_page_constant(module_name)
    _patch_support_response()
