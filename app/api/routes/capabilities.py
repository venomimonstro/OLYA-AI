from __future__ import annotations

import secrets

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import HTMLResponse
from sqlalchemy.orm import Session

from app.db import get_db
from app.models import User
from app.services.admin import require_admin
from app.services.auth import get_current_user
from app.services.capabilities import CAPABILITY_IDS, capability_decision, registry_payload

router = APIRouter(tags=["capabilities"])


@router.get("/v1/capabilities")
def my_capabilities(
    request: Request,
    capability: str | None = Query(default=None, max_length=80),
    live: bool = Query(default=False),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    if capability is not None:
        if capability not in CAPABILITY_IDS:
            raise HTTPException(status_code=404, detail="Capability not found")
        result = capability_decision(request.app, db, user, capability, live=live)
        db.commit()
        return {"registry_version": "x1-capabilities-v1", "capability": result}
    result = registry_payload(request.app, db, user, live=live)
    db.commit()
    return result


@router.get("/v1/admin/capabilities")
def admin_capabilities(
    request: Request,
    user_id: str | None = Query(default=None, max_length=36),
    capability: str | None = Query(default=None, max_length=80),
    live: bool = Query(default=False),
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
) -> dict:
    target = admin if not user_id else db.get(User, user_id)
    if target is None:
        raise HTTPException(status_code=404, detail="User not found")
    if capability is not None:
        if capability not in CAPABILITY_IDS:
            raise HTTPException(status_code=404, detail="Capability not found")
        result = capability_decision(request.app, db, target, capability, live=live)
        db.commit()
        return {
            "registry_version": "x1-capabilities-v1",
            "target_user_id": target.id,
            "capability": result,
        }
    result = registry_payload(request.app, db, target, live=live)
    db.commit()
    result["target_user_id"] = target.id
    return result


PAGE = r'''<!doctype html><html lang="ru"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>X1 Admin · Capabilities</title><style nonce="__NONCE__">
:root{--bg:#090b10;--panel:#111620;--line:#29313d;--text:#f4f6fa;--muted:#94a0b1;--ok:#65d392;--warn:#efbd63;--bad:#ff8080;--accent:#d34747}*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--text);font:14px/1.5 Inter,system-ui,-apple-system,Segoe UI,Arial,sans-serif}button,input{font:inherit}.top{position:sticky;top:0;z-index:4;padding:12px max(14px,calc((100% - 1120px)/2));display:flex;gap:8px;align-items:center;flex-wrap:wrap;background:#090b10f5;border-bottom:1px solid var(--line)}.top a,.btn{border:1px solid var(--line);border-radius:9px;padding:8px 11px;background:#171d27;color:#eef2f8;text-decoration:none;cursor:pointer}.brand{font-weight:850}.grow{flex:1}.wrap{width:min(1120px,100%);margin:auto;padding:18px 14px 60px}.card{background:var(--panel);border:1px solid var(--line);border-radius:13px;padding:14px}.row{display:flex;gap:8px;align-items:center;flex-wrap:wrap}.row input{min-width:0;flex:1;padding:9px;border:1px solid var(--line);border-radius:8px;background:#0c1118;color:var(--text)}.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(260px,1fr));gap:10px;margin-top:14px}.cap h3{margin:0 0 4px}.muted{color:var(--muted)}.ok{color:var(--ok)}.bad{color:var(--bad)}.warn{color:var(--warn)}.reqs{display:grid;gap:5px;margin-top:10px}.req{border-top:1px solid var(--line);padding-top:6px}.status{margin-top:8px;min-height:20px}.primary{background:var(--accent);border-color:transparent}.pill{display:inline-block;border:1px solid var(--line);border-radius:999px;padding:2px 7px;font-size:12px}@media(max-width:620px){.top{padding:10px}.wrap{padding:12px 10px 50px}.row .btn{width:100%}.grid{grid-template-columns:1fr}}
</style></head><body><header class="top"><div class="brand">X1 Admin · Capabilities</div><div class="grow"></div><a href="/admin">Control Center</a><a href="/admin/users">Users</a></header><main class="wrap">
<section class="card"><div class="row"><input id="token" type="password" autocomplete="off" placeholder="Admin Bearer token"><input id="user" autocomplete="off" placeholder="User ID (пусто = текущий admin)"><button class="btn primary" id="load">Проверить</button><button class="btn" id="live">Live probe</button></div><div class="status muted" id="status">Registry показывает availability, reason и requirements без секретов конфигурации.</div></section><section class="grid" id="caps"></section>
</main><script nonce="__NONCE__">
const $=id=>document.getElementById(id);$('token').value=sessionStorage.getItem('x1AdminToken')||'';function clear(e){e.replaceChildren()}async function api(path){const t=sessionStorage.getItem('x1AdminToken')||'';if(!t)throw new Error('Введите admin token');const r=await fetch(path,{headers:{Authorization:'Bearer '+t},credentials:'omit'});let d=null;try{d=await r.json()}catch(_e){}if(!r.ok)throw new Error(typeof d?.detail==='string'?d.detail:'HTTP '+r.status);return d}function render(data){const root=$('caps');clear(root);for(const cap of data.capabilities||[]){const card=document.createElement('article');card.className='card cap';const h=document.createElement('h3');h.textContent=cap.label+' · '+cap.id;const state=document.createElement('div');state.className='pill '+(cap.available?'ok':'bad');state.textContent=cap.available?'AVAILABLE':'UNAVAILABLE';const reason=document.createElement('div');reason.className='muted';reason.textContent=cap.reason?cap.reason+' · '+(cap.message||''):'Готово';const reqs=document.createElement('div');reqs.className='reqs';for(const req of cap.requirements||[]){const row=document.createElement('div');row.className='req';row.textContent=(req.satisfied===true?'✓ ':req.satisfied===false?'✕ ':'? ')+req.key+(req.mandatory===false?' · advisory':'');reqs.append(row)}card.append(h,state,reason,reqs);root.append(card)}$('status').textContent='Registry '+String(data.registry_version||'')+' · '+String((data.available||[]).length)+' available / '+String((data.unavailable||[]).length)+' unavailable'}async function load(live){sessionStorage.setItem('x1AdminToken',$('token').value.trim());const user=$('user').value.trim();let path='/v1/admin/capabilities?live='+(live?'true':'false');if(user)path+='&user_id='+encodeURIComponent(user);$('status').textContent='Проверяю…';try{render(await api(path))}catch(e){$('status').textContent=e.message}}$('load').addEventListener('click',()=>load(false));$('live').addEventListener('click',()=>load(true));if(sessionStorage.getItem('x1AdminToken'))load(false);
</script></body></html>'''


@router.get("/admin/capabilities", response_class=HTMLResponse, include_in_schema=False)
def capabilities_console() -> HTMLResponse:
    nonce = secrets.token_urlsafe(18)
    response = HTMLResponse(PAGE.replace("__NONCE__", nonce))
    response.headers.update(
        {
            "Content-Security-Policy": (
                "default-src 'none'; base-uri 'none'; frame-ancestors 'none'; form-action 'none'; "
                f"style-src 'nonce-{nonce}'; script-src 'nonce-{nonce}'; connect-src 'self'"
            ),
            "Cache-Control": "no-store",
            "Pragma": "no-cache",
            "Referrer-Policy": "no-referrer",
            "X-Content-Type-Options": "nosniff",
            "X-Frame-Options": "DENY",
            "Permissions-Policy": "camera=(), microphone=(), geolocation=(), payment=()",
        }
    )
    return response
