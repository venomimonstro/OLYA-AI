from __future__ import annotations


def install_tester_access_patch() -> None:
    from app.services import auth as auth_service
    from app.services.user_roles import effective_role

    original = auth_service._enforce_public_exposure
    if getattr(original, "_x1_tester_access_patch", False):
        return

    def patched(request, db, user):
        if not bool(getattr(user, "is_admin", False)) and effective_role(db, user) == "tester":
            return
        return original(request, db, user)

    patched._x1_tester_access_patch = True  # type: ignore[attr-defined]
    auth_service._enforce_public_exposure = patched
