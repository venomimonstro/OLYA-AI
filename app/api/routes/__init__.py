"""API route package bootstrap.

Runtime chat quality guards are installed before individual route modules import
and bind inference functions. Keeping this at the package boundary makes the
policy apply consistently to normal chat, streaming chat and verification calls.
"""

from app.runtime_quality_patch import install_runtime_quality_patch
from app.qwen4b_runtime_patch import install_qwen4b_runtime_patch
from app.work_quality_floor_patch import install_work_quality_floor_patch
from app.response_policy_patch import install_response_policy_patch
from app.identity_patch import install_identity_patch
from app.admin_surface_patch import install_admin_surface_patch
from app.tester_access_patch import install_tester_access_patch

install_runtime_quality_patch()
install_qwen4b_runtime_patch()
install_work_quality_floor_patch()
install_response_policy_patch()
install_identity_patch()
install_admin_surface_patch()
install_tester_access_patch()

# Public attribution and the dedicated administrator login are registered before
# main.py includes the public UI router. Normal /login remains a user-only entry
# point and never redirects into the administration surface.
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

    _public_page_with_creator._x1_creator_credit = True  # type: ignore[attr-defined]
    _public_ui._page = _public_page_with_creator
