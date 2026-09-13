"""API route package bootstrap.

Runtime chat quality guards are installed before individual route modules import
and bind inference functions. Keeping this at the package boundary makes the
policy apply consistently to normal chat, streaming chat and verification calls.
"""

from app.runtime_quality_patch import install_runtime_quality_patch
from app.identity_patch import install_identity_patch

install_runtime_quality_patch()
install_identity_patch()

# Public attribution is presentation-only. It is installed here before main.py
# imports the public UI router, so the landing page footer is decorated once
# without changing authentication, chat, billing or API behavior.
from app import public_ui as _public_ui

_original_public_page = _public_ui._page
if not getattr(_original_public_page, "_x1_creator_credit", False):
    def _public_page_with_creator(*args, **kwargs):
        if len(args) >= 3 and isinstance(args[2], str):
            body = args[2]
            if "</footer>" in body and "Лысенко Артём" not in body:
                body = body.replace(
                    "</footer>",
                    '<div class="wrap" style="padding-top:8px">Создатель проекта — Лысенко Артём</div></footer>',
                    1,
                )
                args = (*args[:2], body, *args[3:])
        return _original_public_page(*args, **kwargs)

    _public_page_with_creator._x1_creator_credit = True  # type: ignore[attr-defined]
    _public_ui._page = _public_page_with_creator

# Legacy admin consoles were built in different sprints and each carried its own
# navigation/token UI. Patch them once, before main.py registers their routers,
# so /admin pages share one stable shell and reuse the authenticated session.
from app.admin_shell_patch import install_admin_shell_patch

install_admin_shell_patch()
