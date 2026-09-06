from fastapi import APIRouter
from fastapi.responses import HTMLResponse

router = APIRouter(tags=["launch-admin-ui"])

PAGE = r'''<!doctype html><html lang="ru"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>X1 Public Launch</title><style>
body{font-family:system-ui,sans-serif;margin:0;background:#f6f7f9;color:#151515}header{padding:18px 24px;background:#111;color:#fff}main{max-width:1320px;margin:auto;padding:24px}.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(180px,1fr));gap:12px}.card{background:#fff;border:1px solid #ddd;border-radius:12px;padding:16px;margin-bottom:12px}input,button{padding:9px;border:1px solid #bbb;border-radius:8px}button{cursor:pointer;margin:3px}table{width:100%;border-collapse:collapse;background:#fff}td,th{padding:9px;border-bottom:1px solid #eee;text-align:left;font-size:13px;vertical-align:top}.ok{color:#08752b}.bad{color:#b42318}.warn{color:#9a6700}.muted{color:#667085}.wide{overflow:auto}.stage{font-weight:700}</style></head><body><header><b>X1 Progressive Public Launch</b> · Sprint 38</header><main>
<div class="card"><b>Админ-токен</b> <input id="token" type="password" style="width:min(650px,80%)"><button onclick="save()">Открыть</button> <span id="auth"></span></div>
<div class="card"><button onclick="load()">Обновить</button><button onclick="evaluateNow()">Проверить guardrails</button><button onclick="proposeCatalog()">Собрать measured catalog</button><button onclick="createRollout()">Создать rollout 0%</button></div>
<h2>Публичный запуск</h2><div id="summary" class="grid"></div><div id="blockers"></div><div id="stages" class="card"></div>
<h2>Ресурсная экономика</h2><div id="economics" class="grid"></div>
<h2>Rollout history</h2><div id="rollouts" class="wide"></div>
<h2>Measured plan catalogs</h2><div id="catalogs" class="wide"></div>
<h2>Circuit breakers</h2><div id="breakers" class="wide"></div>
</main><script>
const esc=s=>String(s??'').replace(/[&<>"']/g,m=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[m]));
function save(){sessionStorage.x1AdminToken=document.getElementById('token').value;load()}
async function api(path,opts={}){let t=sessionStorage.x1AdminToken||'';let r=await fetch(path,{...opts,headers:{...(opts.headers||{}),'Authorization':'Bearer '+t,'Content-Type':'application/json'}});let text=await r.text();if(!r.ok)throw new Error(r.status+' '+text);return text?JSON.parse(text):null}
async function act(path,body={}){try{await api(path,{method:'POST',body:JSON.stringify(body)});await load()}catch(e){alert(e.message)}}
function cards(o){return Object.entries(o||{}).map(([k,v])=>`<div class="card"><div class="muted">${esc(k)}</div><b>${esc(v)}</b></div>`).join('')}
async function evaluateNow(){await act('/v1/admin/launch/evaluate')}
async function proposeCatalog(){await act('/v1/admin/launch/catalogs/propose',{cohort:'closed-beta-1',window_days:30})}
async function createRollout(){await act('/v1/admin/launch/rollouts',{cohort:'closed-beta-1',window_days:30})}
async function load(){try{
 document.getElementById('token').value=sessionStorage.x1AdminToken||'';
 let [s,r,c,b]=await Promise.all([api('/v1/admin/launch/status'),api('/v1/admin/launch/rollouts'),api('/v1/admin/launch/catalogs'),api('/v1/admin/launch/breakers?limit=100')]);
 document.getElementById('auth').textContent=' ✓';let e=s.evaluation||{},ro=s.rollout||{},cat=s.measured_catalog||{},rm=e.resource_month||{},rq=e.request_24h||{};
 document.getElementById('summary').innerHTML=cards({guardrails:e.status,exposure_percent:ro.exposure_percent??0,rollout_version:ro.version??'none',rollout_state:ro.state??'none',catalog_version:cat.version??'none',failure_rate_24h:rq.failure_rate??0,open_breakers:(e.open_breakers||[]).length});
 document.getElementById('blockers').innerHTML='<div class="card '+(e.status==='stable'?'ok':'bad')+'"><b>Блокеры:</b> '+esc((e.blockers||[]).join(', ')||'нет')+'<br><b>Предупреждения:</b> '+esc((e.warnings||[]).join(', ')||'нет')+'</div>';
 document.getElementById('economics').innerHTML=cards({spent_microunits:rm.total_cost_microunits??0,budget_microunits:rm.budget_microunits??0,budget_source:rm.budget_source??'',cpu_ms:rm.usage_ms?.cpu??0,image_ms:rm.usage_ms?.image_worker??0,sandbox_ms:rm.usage_ms?.sandbox??0,gpu_ms:rm.usage_ms?.gpu??0});
 let current=ro.id||'';document.getElementById('stages').innerHTML='<b>Следующий этап:</b> '+(s.stages||[]).map(x=>`<button class="stage" ${!current?'disabled':''} onclick="act('/v1/admin/launch/rollouts/${esc(current)}/advance',{exposure_percent:${Number(x)}})">${esc(x)}%</button>`).join('')+(current?`<button onclick="act('/v1/admin/launch/rollouts/${esc(current)}/freeze')">Freeze</button><button onclick="act('/v1/admin/launch/rollouts/${esc(current)}/rollback')">Rollback</button>`:'');
 document.getElementById('rollouts').innerHTML='<table><tr><th>v</th><th>state</th><th>exposure</th><th>decision</th><th>opened</th></tr>'+r.map(x=>`<tr><td>${esc(x.version)}</td><td>${esc(x.state)}</td><td>${esc(x.exposure_percent)}%</td><td>${esc(x.decision?.status)}</td><td>${esc(x.opened_at)}</td></tr>`).join('')+'</table>';
 document.getElementById('catalogs').innerHTML='<table><tr><th>v</th><th>status</th><th>base sec</th><th>verified/CPU</th><th>actions</th></tr>'+c.map(x=>`<tr><td>${esc(x.version)}</td><td>${esc(x.status)}</td><td>${esc(x.economics?.base_monthly_compute_seconds_with_headroom)}</td><td>${esc(x.economics?.verified_request_success_per_cpu_minute)}</td><td><button onclick="act('/v1/admin/launch/catalogs/${esc(x.id)}/approve')">approve</button><button onclick="act('/v1/admin/launch/catalogs/${esc(x.id)}/activate')">activate</button>${x.status==='active'?`<button onclick="act('/v1/admin/launch/catalogs/${esc(x.id)}/rollback')">rollback</button>`:''}</td></tr>`).join('')+'</table>';
 document.getElementById('breakers').innerHTML='<table><tr><th>kind</th><th>status</th><th>reason</th><th>automatic</th><th>action</th></tr>'+b.map(x=>`<tr><td>${esc(x.kind)}</td><td>${esc(x.status)}</td><td>${esc(x.reason)}</td><td>${esc(x.automatic)}</td><td>${x.status==='open'?`<button onclick="act('/v1/admin/launch/breakers/${esc(x.id)}/resolve')">resolve</button>`:''}</td></tr>`).join('')+'</table>';
}catch(e){document.getElementById('auth').textContent=' '+e.message}}
if(sessionStorage.x1AdminToken)load();</script></body></html>'''


@router.get("/admin/launch", response_class=HTMLResponse, include_in_schema=False)
def launch_admin_console() -> HTMLResponse:
    return HTMLResponse(PAGE, headers={
        "Cache-Control": "no-store",
        "X-Frame-Options": "DENY",
        "Content-Security-Policy": "default-src 'self'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; connect-src 'self'; frame-ancestors 'none'",
    })
