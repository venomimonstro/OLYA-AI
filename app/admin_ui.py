from __future__ import annotations

import secrets

from fastapi import APIRouter
from fastapi.responses import HTMLResponse

from app.admin_users_ui import router as admin_users_ui_router
from app.admin_chats_ui import router as admin_chats_ui_router
from app.api.routes.admin_user_ops import router as admin_user_ops_router
from app.api.routes.admin_chat_observer import router as admin_chat_observer_router
from app.api.routes.capabilities import router as capabilities_router
from app.api.routes.product_analytics import router as product_analytics_router

router = APIRouter(tags=["admin-ui"])
router.include_router(admin_users_ui_router)
router.include_router(admin_chats_ui_router)
router.include_router(admin_user_ops_router)
router.include_router(admin_chat_observer_router)
router.include_router(capabilities_router)
router.include_router(product_analytics_router)

PAGE = r'''<!doctype html><html lang="ru"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>OLYA AI · Панель владельца</title><style nonce="__NONCE__">
:root{--bg:#0a0b0d;--panel:#121419;--line:#292d34;--text:#f5f6f8;--muted:#9298a3;--ok:#67d39a;--warn:#efbd63;--bad:#ff8585;--accent:#c53d3e}*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--text);font:14px/1.5 Inter,system-ui,-apple-system,Segoe UI,Arial,sans-serif}button,input{font:inherit}.top{position:sticky;top:0;z-index:5;display:flex;align-items:center;gap:7px;padding:11px max(14px,calc((100% - 1180px)/2));background:#0a0b0df3;border-bottom:1px solid var(--line)}.brand{font-weight:850}.grow{flex:1}.nav{display:flex;gap:6px;flex-wrap:wrap}.nav a,.btn{border:1px solid var(--line);background:#171a20;color:#fff;border-radius:9px;padding:8px 10px;text-decoration:none;cursor:pointer}.nav a:hover,.btn:hover{background:#20242b}.wrap{width:min(1180px,100%);margin:auto;padding:18px 14px 60px}.auth{display:grid;grid-template-columns:1fr auto;gap:8px}.auth input{min-width:0;border:1px solid var(--line);background:#0f1115;color:#fff;border-radius:9px;padding:10px}.status{color:var(--muted);margin-top:7px}.section{margin-top:18px}.section h2{font-size:17px;margin:0 0 9px}.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(170px,1fr));gap:9px}.card{background:var(--panel);border:1px solid var(--line);border-radius:13px;padding:13px}.label{font-size:11px;color:var(--muted)}.value{font-size:20px;font-weight:800;margin-top:2px;overflow-wrap:anywhere}.ok{color:var(--ok)}.warn{color:var(--warn)}.bad{color:var(--bad)}.quick{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:10px}.quick a{display:block;background:var(--panel);border:1px solid var(--line);border-radius:13px;padding:15px;color:#fff;text-decoration:none}.quick b{display:block;font-size:16px;margin-bottom:3px}.quick span{color:var(--muted);font-size:12px}.wide{overflow:auto;border:1px solid var(--line);border-radius:11px;background:var(--panel)}table{width:100%;border-collapse:collapse;min-width:620px}th,td{padding:8px 9px;border-bottom:1px solid var(--line);text-align:left;font-size:12px}th{color:var(--muted)}@media(max-width:720px){.top{padding:10px}.nav{width:100%;overflow:auto;flex-wrap:nowrap}.nav a{white-space:nowrap}.auth{grid-template-columns:1fr}.quick{grid-template-columns:1fr}.grid{grid-template-columns:repeat(2,minmax(0,1fr))}}@media(max-width:430px){.grid{grid-template-columns:1fr}}
</style></head><body><header class="top"><div class="brand">OLYA AI · Владелец</div><div class="grow"></div><nav class="nav"><a href="/admin">Главная</a><a href="/admin/users">Пользователи</a><a href="/admin/chats">Чаты</a><a href="/admin/analytics">Аналитика</a><a href="/admin/support">Поддержка</a><a href="/admin/integrations">Интеграции</a></nav></header><main class="wrap">
<section class="card"><div class="auth"><input id="token" type="password" autocomplete="off" placeholder="Admin token"><button class="btn" id="connect">Подключить</button></div><div class="status" id="state">Если админская сессия уже открыта, token можно не вводить повторно.</div></section>
<section class="section"><h2>Быстрый доступ</h2><div class="quick"><a href="/admin/users"><b>Пользователи</b><span>Аккаунты, тарифы, ограничения и сессии.</span></a><a href="/admin/chats"><b>Чаты пользователей</b><span>Кто что пишет OLYA AI и какие ответы получает.</span></a><a href="/admin/analytics"><b>Аналитика</b><span>Трафик, использование и продуктовые показатели.</span></a></div></section>
<section class="section"><h2>Система сейчас</h2><div class="grid" id="system"></div></section>
<section class="section"><h2>Пользователи и качество</h2><div class="grid" id="users"></div></section>
<section class="section"><h2>Экономика</h2><div class="grid" id="finance"></div></section>
<section class="section"><h2>Проблемы, требующие внимания</h2><div class="wide" id="issues"></div></section>
</main><script nonce="__NONCE__">
const $=id=>document.getElementById(id);$('token').value=sessionStorage.getItem('x1AdminToken')||sessionStorage.getItem('x1_access_token')||'';
function token(){return $('token').value.trim()||sessionStorage.getItem('x1AdminToken')||sessionStorage.getItem('x1_access_token')||''}
async function api(path){const t=token();if(!t)throw new Error('Нет admin token');const r=await fetch(path,{headers:{Authorization:'Bearer '+t}});let d=null;try{d=await r.json()}catch(_e){}if(!r.ok)throw new Error(typeof d?.detail==='string'?d.detail:'HTTP '+r.status);return d}
function metric(root,label,value,tone=''){const c=document.createElement('div');c.className='card';const l=document.createElement('div');l.className='label';l.textContent=label;const v=document.createElement('div');v.className='value '+tone;v.textContent=value===null||value===undefined?'—':String(value);c.append(l,v);root.append(c)}
function pct(v){return v===null||v===undefined?'—':(Number(v)*100).toFixed(1)+'%'}function rub(v){return v===null||v===undefined?'—':Number(v).toLocaleString('ru-RU',{maximumFractionDigits:0})+' ₽'}
function issues(rows){const root=$('issues');root.replaceChildren();if(!rows.length){const x=document.createElement('div');x.className='card ok';x.textContent='Критичных проблем не обнаружено.';root.append(x);return}const t=document.createElement('table'),h=document.createElement('tr');['Проверка','Статус','Описание','Что делать'].forEach(x=>{const th=document.createElement('th');th.textContent=x;h.append(th)});t.append(h);for(const row of rows.slice(0,40)){const tr=document.createElement('tr');for(const x of [row.key||'',row.status||'',row.message||row.root_cause||'',row.recommended_action||'']){const td=document.createElement('td');td.textContent=x;tr.append(td)}t.append(tr)}root.append(t)}
async function load(){try{$('state').textContent='Загрузка…';const [health,cc,owner]=await Promise.all([api('/v1/admin/operations/health?refresh=true&deep=false'),api('/v1/admin/operations/control-center?window_hours=24'),api('/v1/admin/owner-dashboard?days=30')]);sessionStorage.setItem('x1AdminToken',token());const sys=$('system');sys.replaceChildren();metric(sys,'Состояние',health.status||'—',health.status==='stable'?'ok':'warn');metric(sys,'Оценка health',health.score??'—');metric(sys,'Запросов за сутки',cc.traffic?.requests??0);metric(sys,'Очередь p95',String(cc.traffic?.p95_queue_ms??0)+' мс');metric(sys,'Активных задач',cc.resources?.active_jobs??cc.resources?.active??'—');const usr=$('users');usr.replaceChildren();metric(usr,'Всего пользователей',cc.accounts?.users??0);metric(usr,'Активных пользователей',cc.accounts?.active_users??0);metric(usr,'Успешность ответов',pct(owner.quality?.request_success_rate));metric(usr,'Открытых обращений',owner.support?.waiting_admin??0,Number(owner.support?.waiting_admin||0)>0?'warn':'ok');const f=$('finance');f.replaceChildren();metric(f,'MRR',rub(owner.finance?.mrr_rub));metric(f,'MRR после сервера',rub(owner.finance?.mrr_after_server_rub),Number(owner.finance?.mrr_after_server_rub||0)>=0?'ok':'warn');metric(f,'Платных пользователей',owner.finance?.active_paid_users??0);metric(f,'ARPPU',rub(owner.finance?.arppu_rub));const bad=(health.checkpoints||health.checks||[]).filter(x=>x.status&&x.status!=='stable'&&x.status!=='ok');issues(bad);$('state').textContent='Данные обновлены '+new Date().toLocaleTimeString('ru-RU')}catch(e){$('state').textContent=e.message}}
$('connect').onclick=()=>{sessionStorage.setItem('x1AdminToken',$('token').value.trim());load()};if(token())load();
</script></body></html>'''


@router.get("/admin", response_class=HTMLResponse, include_in_schema=False)
def admin_console() -> HTMLResponse:
    nonce = secrets.token_urlsafe(18)
    response = HTMLResponse(PAGE.replace("__NONCE__", nonce))
    response.headers.update({
        "Content-Security-Policy": "default-src 'none'; base-uri 'none'; frame-ancestors 'none'; form-action 'none'; " + f"style-src 'nonce-{nonce}'; script-src 'nonce-{nonce}'; connect-src 'self'",
        "Cache-Control": "no-store", "Pragma": "no-cache", "Referrer-Policy": "no-referrer",
        "X-Content-Type-Options": "nosniff", "X-Frame-Options": "DENY",
        "Permissions-Policy": "camera=(), microphone=(), geolocation=(), payment=()",
    })
    return response
