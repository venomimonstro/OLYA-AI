from __future__ import annotations

import secrets

from fastapi import APIRouter
from fastapi.responses import HTMLResponse

from app.admin_users_ui import router as admin_users_ui_router
from app.api.routes.admin_user_ops import router as admin_user_ops_router
from app.api.routes.capabilities import router as capabilities_router
from app.api.routes.product_analytics import router as product_analytics_router

router = APIRouter(tags=["admin-ui"])
router.include_router(admin_users_ui_router)
router.include_router(admin_user_ops_router)
router.include_router(capabilities_router)
router.include_router(product_analytics_router)

PAGE = r'''<!doctype html><html lang="ru"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>X1 Admin Control Center</title><style nonce="__NONCE__">
:root{--bg:#090b10;--panel:#111620;--line:#29313d;--text:#f4f6fa;--muted:#94a0b1;--ok:#65d392;--warn:#efbd63;--bad:#ff8080;--accent:#d34747}*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--text);font:14px/1.5 Inter,system-ui,-apple-system,Segoe UI,Arial,sans-serif}button,input{font:inherit}.top{position:sticky;top:0;z-index:4;padding:12px max(14px,calc((100% - 1220px)/2));display:flex;gap:8px;align-items:center;flex-wrap:wrap;background:#090b10f5;border-bottom:1px solid var(--line)}.brand{font-weight:850}.grow{flex:1}.nav{display:flex;gap:6px;flex-wrap:wrap}.nav a,.btn{border:1px solid var(--line);border-radius:9px;padding:8px 11px;background:#171d27;color:#eef2f8;text-decoration:none;cursor:pointer}.nav a.owner{border-color:#834448}.primary{background:var(--accent);border-color:transparent}.wrap{width:min(1220px,100%);margin:auto;padding:18px 14px 60px}.card{background:var(--panel);border:1px solid var(--line);border-radius:13px;padding:14px}.auth{display:grid;grid-template-columns:1fr auto;gap:8px}.auth input{min-width:0;padding:10px;border:1px solid var(--line);border-radius:9px;background:#0c1118;color:var(--text)}.section{margin-top:18px}.head{display:flex;gap:8px;align-items:center;flex-wrap:wrap;margin-bottom:9px}.head h2{margin:0;font-size:17px}.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(170px,1fr));gap:9px}.metric .label{color:var(--muted);font-size:12px}.metric .value{font-weight:800;font-size:19px;overflow-wrap:anywhere}.muted{color:var(--muted)}.ok{color:var(--ok)}.warn{color:var(--warn)}.bad{color:var(--bad)}.wide{overflow:auto;border:1px solid var(--line);border-radius:10px;background:var(--panel)}table{width:100%;border-collapse:collapse;min-width:660px}th,td{padding:8px 9px;border-bottom:1px solid var(--line);text-align:left;font-size:12px;vertical-align:top}th{color:var(--muted)}.gap{margin-top:9px}.notice{padding:10px 11px;border:1px solid var(--line);border-radius:9px;background:#151b25;margin-bottom:9px}@media(max-width:680px){.top{padding:10px}.nav{width:100%;overflow:auto;flex-wrap:nowrap}.nav a{white-space:nowrap}.auth{grid-template-columns:1fr}.grid{grid-template-columns:repeat(2,minmax(0,1fr))}}@media(max-width:420px){.grid{grid-template-columns:1fr}}
</style></head><body><header class="top"><div class="brand">X1 Admin · Control Center</div><div class="grow"></div><nav class="nav"><a class="owner" href="/admin/owner">Owner</a><a href="/admin/integrations">Integrations</a><a href="/admin/support">Support</a><a href="/admin/users">Users</a><a href="/admin/capabilities">Capabilities</a><a href="/admin/analytics">Analytics</a><a href="/admin/beta">Beta</a><a href="/admin/launch">Launch</a><a href="/admin/media">Media</a></nav></header><main class="wrap">
<section class="card"><div class="auth"><input id="token" type="password" autocomplete="off" placeholder="Admin Bearer token"><button class="btn primary" id="connect">Открыть</button></div><div class="muted gap" id="auth-state">Используется текущая сессия администратора; ручной token нужен только для диагностики.</div></section>
<section class="section"><div class="head"><h2>Владелец · 30 дней</h2><a class="btn" href="/admin/owner">Полная owner dashboard</a></div><div class="grid" id="owner"></div></section>
<section class="section"><div class="head"><h2>Операционный статус</h2><button class="btn" id="refresh">Обновить</button><button class="btn" id="deep">Глубокая проверка</button><button class="btn" id="snapshot">Performance snapshot</button><span class="muted" id="updated"></span></div><div class="grid" id="headline"></div></section>
<section class="section"><div class="head"><h2>Сигналы и release blockers</h2></div><div class="grid" id="signals"></div><div id="release-note" class="gap"></div><div class="wide" id="blockers"></div></section>
<section class="section"><div class="head"><h2>Трафик и экономика</h2></div><div class="grid" id="traffic"></div><div class="grid gap" id="economics"></div></section>
<section class="section"><div class="head"><h2>Billing и API</h2></div><div class="grid" id="billing"></div><div class="grid gap" id="api"></div></section>
<section class="section"><div class="head"><h2>Нагрузка и ресурсы</h2></div><div class="grid" id="resources"></div><div class="wide gap" id="overload"></div></section>
<section class="section"><div class="head"><h2>Health checkpoints</h2></div><div class="grid" id="health-summary"></div><div class="wide gap" id="health-table"></div></section>
<section class="section"><div class="head"><h2>Performance</h2></div><div class="wide" id="performance"></div></section>
<section class="section"><div class="head"><h2>Admin audit trail</h2></div><div class="wide" id="audit"></div></section>
</main><script nonce="__NONCE__">
const $=id=>document.getElementById(id);
function currentToken(){return sessionStorage.getItem('x1AdminToken')||sessionStorage.getItem('x1_access_token')||''}
$('token').value=currentToken();
function fmt(v){if(v===null||v===undefined)return '—';if(typeof v==='boolean')return v?'Да':'Нет';return String(v)}
function pct(v){return v===null||v===undefined?'—':(Number(v)*100).toFixed(1)+'%'}
function rub(v){return v===null||v===undefined?'—':Number(v).toLocaleString('ru-RU',{maximumFractionDigits:2})+' ₽'}
async function api(path,opts={}){const t=currentToken();if(!t)throw new Error('Войдите как администратор или введите token');const headers={...(opts.headers||{}),Authorization:'Bearer '+t};if(opts.body)headers['Content-Type']='application/json';const r=await fetch(path,{...opts,headers,credentials:'omit'});let d=null;try{d=await r.json()}catch(_e){}if(r.status===401||r.status===403)throw new Error('Недостаточно прав или сессия недействительна');if(!r.ok)throw new Error(typeof d?.detail==='string'?d.detail:'HTTP '+r.status);return d}
function clear(e){e.replaceChildren()}
function metric(label,value,tone=''){const e=document.createElement('div');e.className='card metric';const l=document.createElement('div');l.className='label';l.textContent=label.replaceAll('_',' ');const v=document.createElement('div');v.className='value '+tone;v.textContent=fmt(value);e.append(l,v);return e}
function map(id,obj){const root=$(id);clear(root);for(const [k,v] of Object.entries(obj||{})){if(v&&typeof v==='object')continue;root.append(metric(k,v))}if(!root.childNodes.length)root.append(metric('status','Нет данных','warn'))}
function table(id,rows,cols){const root=$(id);clear(root);if(!rows?.length){const e=document.createElement('div');e.className='muted card';e.textContent='Нет данных';root.append(e);return}const t=document.createElement('table'),h=document.createElement('tr');for(const [name] of cols){const th=document.createElement('th');th.textContent=name;h.append(th)}t.append(h);for(const row of rows){const tr=document.createElement('tr');for(const [,key] of cols){const td=document.createElement('td');td.textContent=fmt(row?.[key]);tr.append(td)}t.append(tr)}root.append(t)}
function headline(cc,h,r){const root=$('headline');clear(root);const ready=Boolean(r.ready_for_public_release),hs=String(h.status||'unknown');root.append(metric('release',ready?'READY':'BLOCKED',ready?'ok':'bad'),metric('health',hs,hs==='stable'?'ok':'warn'),metric('users',cc.accounts?.users),metric('active users',cc.accounts?.active_users),metric('requests',cc.traffic?.requests),metric('queue p95 ms',cc.traffic?.p95_queue_ms),metric('complaints',cc.signals?.open_complaints),metric('release regressions',cc.signals?.release_blocking_regressions))}
function owner(o){const root=$('owner');clear(root);const f=o.finance||{},a=o.accounts||{},q=o.quality||{},s=o.support||{},i=o.integrations||{};root.append(metric('MRR',rub(f.mrr_rub),Number(f.mrr_after_server_rub||0)>=0?'ok':'warn'),metric('MRR − server',rub(f.mrr_after_server_rub),Number(f.mrr_after_server_rub||0)>=0?'ok':'bad'),metric('paid users',f.active_paid_users),metric('ARPPU',rub(f.arppu_rub)),metric('activation',pct(a.activation_rate)),metric('D7 retention',pct(a.d7_retention)),metric('request success',pct(q.request_success_rate)),metric('support ждёт',s.waiting_admin,Number(s.waiting_admin||0)>0?'warn':'ok'),metric('SMTP',i.smtp?.ready?'ready':'not ready',i.smtp?.ready?'ok':'bad'),metric('payment',i.payments?.yookassa?.ready||i.payments?.yoomoney?.ready?'ready':'not ready',i.payments?.yookassa?.ready||i.payments?.yoomoney?.ready?'ok':'bad'),metric('Yandex ID',i.auth?.yandex?.ready?'ready':'off/not ready',i.auth?.yandex?.ready?'ok':'warn'))}
function health(h){map('health-summary',{status:h.status,score:h.score,stable:h.stable,degraded:h.degraded,critical:h.critical??h.failed,unknown:h.unknown});table('health-table',(h.checkpoints||h.checks||[]).filter(x=>x.status!=='stable').slice(0,40),[['Checkpoint','key'],['Status','status'],['Message','message'],['Root cause','root_cause'],['Checked','last_checked_at']])}
async function load(deep=false){$('auth-state').textContent='Загрузка…';try{const h=await api('/v1/admin/operations/health?refresh=true&deep='+(deep?'true':'false'));const [cc,r,p,a,o]=await Promise.all([api('/v1/admin/operations/control-center?window_hours=24'),api('/v1/admin/reliability/release-readiness?refresh=false'),api('/v1/admin/performance/snapshots?limit=8'),api('/v1/admin/audit-log?limit=20'),api('/v1/admin/owner-dashboard?days=30')]);headline(cc,h,r);owner(o);map('signals',cc.signals);map('traffic',cc.traffic);map('economics',cc.economics);map('billing',cc.billing);map('api',cc.api);map('resources',cc.resources);table('overload',Object.entries(cc.overload||{}).map(([name,x])=>({name,...x})),[['Lane','name'],['Active','active'],['Waiting','waiting'],['Max active','max_concurrent'],['Max queue','max_queue'],['Breaker','breaker_state']]);health(h);const n=$('release-note');clear(n);const x=document.createElement('div');x.className='notice '+(r.ready_for_public_release?'ok':'bad');x.textContent=r.ready_for_public_release?'Release gate зелёный.':'Release заблокирован: '+(r.blockers||[]).length+' blocker(s).';n.append(x);table('blockers',r.blockers||[],[['Checkpoint','key'],['Status','status'],['Message','message'],['Root cause','root_cause'],['Action','recommended_action']]);table('performance',p,[['Window','window_minutes'],['Requests','request_count'],['Success','success_count'],['P95 ms','p95_duration_ms'],['P95 queue','p95_queue_ms'],['CPU/success','cpu_seconds_per_success'],['Date','created_at']]);table('audit',a,[['Action','action'],['Target','target_type'],['Target ID','target_id'],['Actor','actor_user_id'],['Date','created_at']]);$('updated').textContent='Обновлено '+new Date(cc.generated_at).toLocaleString('ru-RU');$('auth-state').textContent='Control Center подключён.'}catch(e){$('auth-state').textContent=e.message}}
$('connect').addEventListener('click',()=>{const t=$('token').value.trim();sessionStorage.setItem('x1AdminToken',t);load(false)});
$('refresh').addEventListener('click',()=>load(false));
$('deep').addEventListener('click',()=>load(true));
$('snapshot').addEventListener('click',async()=>{try{await api('/v1/admin/performance/snapshots?window_minutes=60',{method:'POST'});await load(false)}catch(e){$('auth-state').textContent=e.message}});
if(currentToken())load(false);
</script></body></html>'''


@router.get("/admin", response_class=HTMLResponse, include_in_schema=False)
def admin_console() -> HTMLResponse:
    nonce = secrets.token_urlsafe(18)
    response = HTMLResponse(PAGE.replace("__NONCE__", nonce))
    response.headers.update({
        "Content-Security-Policy": "default-src 'none'; base-uri 'none'; frame-ancestors 'none'; form-action 'none'; " + f"style-src 'nonce-{nonce}'; script-src 'nonce-{nonce}'; connect-src 'self'",
        "Cache-Control": "no-store",
        "Pragma": "no-cache",
        "Referrer-Policy": "no-referrer",
        "X-Content-Type-Options": "nosniff",
        "X-Frame-Options": "DENY",
        "Permissions-Policy": "camera=(), microphone=(), geolocation=(), payment=()",
    })
    return response
