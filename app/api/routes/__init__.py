"""API route package bootstrap.

Interactive chat intentionally has one inference/runtime profile. Legacy quality,
search, freshness, critic and repair monkey-patches are no longer installed here;
the clean chat path owns routing, web grounding and prompt construction directly.
"""

from app.gigachat31_runtime_patch import install_gigachat31_runtime_patch
from app.testing_unlimited_usage_patch import install_testing_unlimited_usage_patch
from app.admin_surface_patch import install_admin_surface_patch
from app.tester_access_patch import install_tester_access_patch

install_gigachat31_runtime_patch()
install_testing_unlimited_usage_patch()
install_admin_surface_patch()
install_tester_access_patch()

from app import public_ui as _public_ui
from app.admin_login_ui import router as _admin_login_router

_public_ui.router.include_router(_admin_login_router)

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

    _public_page_with_creator._x1_creator_credit = True
    _public_ui._page = _public_page_with_creator
