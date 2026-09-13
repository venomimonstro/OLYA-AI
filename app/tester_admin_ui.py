from __future__ import annotations

import secrets

from fastapi import APIRouter, Depends
from fastapi.responses import HTMLResponse

from app.models import User
from app.services.admin_browser_session import require_admin_browser_session

router = APIRouter(tags=["tester-admin-ui"])

PAGE = r'''<!doctype html><html lang="ru"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>OLYA AI · Testers</title><style nonce="__NONCE__">
:root{--bg:#090b10;--panel:#111620;--line:#29313d;--text:#f4f6fa;--muted:#94a0b1;--accent:#d34747;--ok:#65d392;--warn:#efbd63}*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--text);font:14px/1.5 Inter,system-ui,-apple-system,Segoe UI,Arial,sans-serif}.top{position:sticky;top:0;z-index:5;display:flex;gap:8px;align-items:center;padding:12px 14px;background:#090b10f2;border-bottom:1px solid var(--line);overflow:auto}.top a{white-space:nowrap;color:var(--text);text-decoration:none;border:1px solid var(--line);border-radius:9px;padding:8px 10px;background:#151a23}.top a.active{border-color:#834448}.brand{font-weight:900;margin-right:8px}.wrap{width:min(1100px,100%);margin:auto;padding:20px 14px 60px}.card{background:var(--panel);border:1px solid var(--line);border-radius:13px;padding:14px}.row{display:flex;gap:8px;align-items:center;flex-wrap:wrap}.grow{flex:1}.muted{color:var(--muted)}input,button,select{font:inherit;border:1px solid var(--line);border-radius:9px;background:#0c1118;color:var(--text);padding:9px 10px}button{cursor:pointer}.primary{background:var(--accent);border-color:transparent;font-weight:750}.list{display:grid;gap:8px;margin-top:14px}.user{display:grid;grid-template-columns:minmax(0,1fr) 150px 120px;gap:8px;align-items:center;padding:11px;border:1px solid var(--line);border-radius:10px;background:#0e131b}.email{font-weight:750;overflow-wrap:anywhere}.ok{color:var(--ok)}.warn{color:var(--warn)}.status{min-height:24px;margin-top:10px}@media(max-width:700px){.user{grid-template-columns:1fr}.top{padding:9px}.wrap{padding-top:14px}}
</style></head><body><header class="top"><div class="brand">OLYA AI</div><a href="/admin/owner">Owner</a><a href="/admin">Control Center</a><a href="/admin/users">Users</a><a class="active" href="/admin/testers">Testers</a><a href="/admin/integrations">Integrations</a><a href="/admin/support">Support</a><a href="/admin/analytics">Analytics</a><a href="/admin/beta">Beta</a><a href="/admin/launch">Launch</a></header><main class="wrap"><section class="card"><h1>Тестировщики</h1><p class="muted">Назначайте отдельным пользователям роль tester. Администраторы остаются отдельной защищённой ролью.</p><div class="row"><input class="grow" id="q" placeholder="Email, имя или ID"><button class="primary" id="search">Найти</button></div><div class="status muted" id="status"></div><div class="list" id="list"></div></section></main><script nonce="__NONCE__">
const $=id=>document.getElementById(id);function token(){return sessionStorage.getItem('x1AdminToken')||sessionStorage.getItem('x1_access_token')||''}async function api(path,opts={}){const t=token();if(!t){location.replace('/login?next=/admin/testers');throw new Error('Нужен вход администратора')}const headers={...(opts.headers||{}),Authorization:'Bearer '+t};if(opts.body)headers['Content-Type']='application/json';const r=await fetch(path,{...opts,headers,credentials:'omit'});let d=null;try{d=await r.json()}catch{}if(r.status===401||r.status===403){location.replace('/login?next=/admin/testers');throw new Error('Сессия администратора завершена')}if(!r.ok)throw new Error(typeof d?.detail==='string'?d.detail:'HTTP '+r.status);return d}function clear(){ $('list').replaceChildren() }function row(u){const e=document.createElement('div');e.className='user';const a=document.createElement('div');const b=document.createElement('div');b.className='email';b.textContent=u.email;const m=document.createElement('div');m.className='muted';m.textContent=(u.display_name||'')+' · '+u.id+(u.is_admin?' · ADMIN':'');a.append(b,m);const s=document.createElement('select');for(const v of ['user','tester']){const o=document.createElement('option');o.value=v;o.textContent=v==='tester'?'Тестировщик':'Пользователь';s.append(o)}s.value=u.role==='tester'?'tester':'user';s.disabled=Boolean(u.is_admin);const btn=document.createElement('button');btn.textContent='Сохранить';btn.disabled=Boolean(u.is_admin);btn.onclick=async()=>{try{btn.disabled=true;await api('/v1/admin/testers/'+encodeURIComponent(u.id)+'/role',{method:'PUT',body:JSON.stringify({role:s.value})});$('status').className='status ok';$('status').textContent='Роль сохранена для '+u.email}catch(err){$('status').className='status warn';$('status').textContent=err.message}finally{btn.disabled=Boolean(u.is_admin)}};e.append(a,s,btn);return e}async function load(){try{$('status').className='status muted';$('status').textContent='Загрузка…';const q=encodeURIComponent($('q').value.trim());const data=await api('/v1/admin/testers?q='+q+'&limit=100');clear();for(const u of data)$('list').append(row(u));$('status').textContent='Найдено: '+data.length}catch(e){$('status').className='status warn';$('status').textContent=e.message}}$('search').onclick=load;$('q').onkeydown=e=>{if(e.key==='Enter')load()};if(token())load();else location.replace('/login?next=/admin/testers');
</script></body></html>'''


@router.get('/admin/testers', response_class=HTMLResponse, include_in_schema=False)
def testers_page(_admin: User = Depends(require_admin_browser_session)) -> HTMLResponse:
    nonce = secrets.token_urlsafe(18)
    response = HTMLResponse(PAGE.replace('__NONCE__', nonce))
    response.headers.update({
        'Content-Security-Policy': f"default-src 'none'; base-uri 'none'; frame-ancestors 'none'; form-action 'none'; style-src 'nonce-{nonce}'; script-src 'nonce-{nonce}'; connect-src 'self'",
        'Cache-Control': 'no-store',
        'Pragma': 'no-cache',
        'Referrer-Policy': 'no-referrer',
        'X-Robots-Tag': 'noindex, nofollow, noarchive, nosnippet',
        'X-Content-Type-Options': 'nosniff',
        'X-Frame-Options': 'DENY',
    })
    return response
