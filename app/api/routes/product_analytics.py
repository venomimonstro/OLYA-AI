from __future__ import annotations

import secrets

from fastapi import APIRouter, Depends, Query
from fastapi.responses import HTMLResponse
from sqlalchemy.orm import Session

from app.db import get_db
from app.models import User
from app.services.admin import require_admin
from app.services.product_analytics import product_analytics

router = APIRouter(tags=["product-analytics"])


@router.get("/v1/admin/product-analytics")
def analytics_api(
    days: int = Query(default=30, ge=1, le=365),
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
) -> dict:
    _ = admin
    return product_analytics(db, days=days)


PAGE = r'''<!doctype html><html lang="ru"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>X1 Admin · Product Analytics</title><style nonce="__NONCE__">
:root{--bg:#090b10;--panel:#111620;--line:#29313d;--text:#f4f6fa;--muted:#94a0b1;--ok:#65d392;--bad:#ff8080;--accent:#d34747}*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--text);font:14px/1.5 Inter,system-ui,Arial,sans-serif}.top{padding:12px max(14px,calc((100% - 1120px)/2));display:flex;gap:8px;align-items:center;flex-wrap:wrap;border-bottom:1px solid var(--line);background:#090b10f5;position:sticky;top:0}.top a,button{border:1px solid var(--line);border-radius:9px;padding:8px 11px;background:#171d27;color:#eef2f8;text-decoration:none;cursor:pointer}.brand{font-weight:850}.grow{flex:1}.wrap{width:min(1120px,100%);margin:auto;padding:18px 14px 60px}.card{background:var(--panel);border:1px solid var(--line);border-radius:13px;padding:14px}.row{display:flex;gap:8px;align-items:center;flex-wrap:wrap}.row input,.row select{min-width:0;padding:9px;border:1px solid var(--line);border-radius:8px;background:#0c1118;color:var(--text)}.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(190px,1fr));gap:10px;margin-top:14px}.value{font-size:22px;font-weight:800}.muted{color:var(--muted)}.ok{color:var(--ok)}.bad{color:var(--bad)}.section{margin-top:18px}.section h2{font-size:17px;margin:0 0 8px}.wide{overflow:auto;border:1px solid var(--line);border-radius:10px}table{width:100%;border-collapse:collapse;min-width:560px}th,td{padding:8px 9px;border-bottom:1px solid var(--line);text-align:left;font-size:12px}th{color:var(--muted)}@media(max-width:600px){.top{padding:10px}.wrap{padding:12px 10px 50px}.grid{grid-template-columns:1fr 1fr}.row input{width:100%}}@media(max-width:400px){.grid{grid-template-columns:1fr}}
</style></head><body><header class="top"><div class="brand">X1 Admin · Product Analytics</div><div class="grow"></div><a href="/admin">Control Center</a><a href="/admin/users">Users</a></header><main class="wrap"><section class="card"><div class="row"><input id="token" type="password" autocomplete="off" placeholder="Admin Bearer token"><select id="days"><option value="7">7 дней</option><option value="30" selected>30 дней</option><option value="90">90 дней</option></select><button id="load">Обновить</button></div><div class="muted" id="status">Агрегаты без prompt/message content.</div></section><section class="grid" id="headline"></section><section class="section"><h2>Retention</h2><div class="grid" id="retention"></div></section><section class="section"><h2>Task success / Frustration / Conversion</h2><div class="grid" id="product"></div></section><section class="section"><h2>Economics и resource efficiency</h2><div class="grid" id="economics"></div></section><section class="section"><h2>Product events</h2><div class="wide" id="events"></div></section></main><script nonce="__NONCE__">
const $=id=>document.getElementById(id);$('token').value=sessionStorage.getItem('x1AdminToken')||'';function clear(e){e.replaceChildren()}function card(label,value){const e=document.createElement('div');e.className='card';const l=document.createElement('div');l.className='muted';l.textContent=label;const v=document.createElement('div');v.className='value';v.textContent=value===null||value===undefined?'—':String(value);e.append(l,v);return e}function cards(id,obj){const root=$(id);clear(root);for(const [k,v] of Object.entries(obj||{})){if(v&&typeof v==='object')continue;root.append(card(k,v))}}async function api(path){const t=sessionStorage.getItem('x1AdminToken')||'';if(!t)throw new Error('Введите admin token');const r=await fetch(path,{headers:{Authorization:'Bearer '+t},credentials:'omit'});let d=null;try{d=await r.json()}catch{}if(!r.ok)throw new Error(typeof d?.detail==='string'?d.detail:'HTTP '+r.status);return d}function eventTable(events){const root=$('events');clear(root);const rows=Object.entries(events||{}).sort((a,b)=>b[1]-a[1]);if(!rows.length){root.textContent='Нет событий';return}const t=document.createElement('table'),h=document.createElement('tr');for(const x of ['Event','Count']){const th=document.createElement('th');th.textContent=x;h.append(th)}t.append(h);for(const [name,count] of rows){const tr=document.createElement('tr');for(const v of [name,count]){const td=document.createElement('td');td.textContent=String(v);tr.append(td)}t.append(tr)}root.append(t)}async function load(){sessionStorage.setItem('x1AdminToken',$('token').value.trim());$('status').textContent='Загрузка…';try{const d=await api('/v1/admin/product-analytics?days='+encodeURIComponent($('days').value));cards('headline',{registered:d.activation.registered,activated:d.activation.activated,activation_rate:d.activation.activation_rate,median_seconds_to_first_value:d.activation.median_seconds_to_first_value});cards('retention',{d1_eligible:d.retention.d1.eligible,d1_retained:d.retention.d1.retained,d1_rate:d.retention.d1.rate,d7_eligible:d.retention.d7.eligible,d7_retained:d.retention.d7.retained,d7_rate:d.retention.d7.rate});cards('product',{tasks_created:d.task_success.created,task_terminal_success_rate:d.task_success.terminal_success_rate,frustration_events:d.frustration.events,frustration_per_success:d.frustration.per_successful_request,active_paid_users:d.conversion.active_paid_users,active_paid_rate:d.conversion.active_paid_rate});cards('economics',{...d.economics,...d.resource_efficiency});eventTable(d.event_counts);$('status').textContent='Обновлено '+new Date(d.generated_at).toLocaleString('ru-RU')+' · prompt/message content не хранится.'}catch(e){$('status').textContent=e.message}}$('load').addEventListener('click',load);if(sessionStorage.getItem('x1AdminToken'))load();
</script></body></html>'''


@router.get("/admin/analytics", response_class=HTMLResponse, include_in_schema=False)
def analytics_page() -> HTMLResponse:
    nonce = secrets.token_urlsafe(18)
    response = HTMLResponse(PAGE.replace("__NONCE__", nonce))
    response.headers.update({
        "Content-Security-Policy": "default-src 'none'; base-uri 'none'; frame-ancestors 'none'; form-action 'none'; " + f"style-src 'nonce-{nonce}'; script-src 'nonce-{nonce}'; connect-src 'self'",
        "Cache-Control": "no-store",
        "Referrer-Policy": "no-referrer",
        "X-Content-Type-Options": "nosniff",
        "X-Frame-Options": "DENY",
    })
    return response
