from __future__ import annotations

from fastapi.responses import HTMLResponse


def install_login_role_patch() -> None:
    from app import public_ui

    original = public_ui._auth_form
    if getattr(original, "_x1_role_redirect_patch", False):
        return

    def patched_auth_form(kind, db):
        response = original(kind, db)
        body = response.body.decode("utf-8")

        body = body.replace(
            "location.assign(await destination(data.access_token))",
            "location.assign(data.is_admin?'/admin/owner':await destination(data.access_token))",
        )

        old_existing = "if(existing){if(oauth==='yandex'){const uid=sessionStorage.getItem('x1_user_id')||'';window.x1MetrikaUser&&window.x1MetrikaUser(uid);window.x1MetrikaGoal&&window.x1MetrikaGoal('yandex_login_success')}location.replace('/app')}"
        new_existing = "if(existing){if(oauth==='yandex'){const uid=sessionStorage.getItem('x1_user_id')||'';window.x1MetrikaUser&&window.x1MetrikaUser(uid);window.x1MetrikaGoal&&window.x1MetrikaGoal('yandex_login_success')}const requested=new URLSearchParams(location.search).get('next')||'';fetch('/v1/auth/me',{headers:{Authorization:'Bearer '+existing},credentials:'omit'}).then(r=>r.ok?r.json():null).then(me=>{if(me?.is_admin){location.replace('/admin/owner');return}if(requested==='/admin'||requested.startsWith('/admin/')){sessionStorage.removeItem('x1_access_token');sessionStorage.removeItem('x1AdminToken');sessionStorage.removeItem('x1_user_id');return}location.replace('/app')}).catch(()=>{if(!(requested==='/admin'||requested.startsWith('/admin/')))location.replace('/app')})}"
        body = body.replace(old_existing, new_existing)

        headers = {k: v for k, v in response.headers.items() if k.lower() != "content-length"}
        return HTMLResponse(content=body, status_code=response.status_code, headers=headers)

    patched_auth_form._x1_role_redirect_patch = True  # type: ignore[attr-defined]
    public_ui._auth_form = patched_auth_form
