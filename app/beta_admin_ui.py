from fastapi import APIRouter
from fastapi.responses import HTMLResponse

router = APIRouter(tags=["beta-admin-ui"])

PAGE = r'''<!doctype html><html lang="ru"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>X1 Beta Operations</title><style>
body{font-family:system-ui,sans-serif;margin:0;background:#f6f7f9;color:#151515}header{padding:18px 24px;background:#111;color:#fff}main{max-width:1280px;margin:auto;padding:24px}.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(180px,1fr));gap:12px}.card{background:#fff;border:1px solid #ddd;border-radius:12px;padding:16px;margin-bottom:12px}input,button{padding:9px;border:1px solid #bbb;border-radius:8px}button{cursor:pointer;margin:3px}table{width:100%;border-collapse:collapse;background:#fff}td,th{padding:9px;border-bottom:1px solid #eee;text-align:left;font-size:13px;vertical-align:top}.ok{color:#08752b}.bad{color:#b42318}.warn{color:#9a6700}.muted{color:#667085}.wide{overflow:auto}</style></head><body><header><b>X1 Closed-Beta Operations</b> · Sprint 37</header><main>
<div class="card"><b>Админ-токен</b> <input id="token" type="password" style="width:min(650px,80%)"><button onclick="save()">Открыть</button> <span id="auth"></span></div>
<div class="card"><button onclick="load()">Обновить</button><button onclick="newWave()">Создать волну</button><input id="waveSize" type="number" min="1" max="100" value="10" style="width:80px"><button onclick="proposePlan()">Предложить capacity plan</button><button onclick="rollbackPlan()">Rollback capacity</button></div>
<h2>Admission control</h2><div id="decision" class="grid"></div><div id="reasons"></div>
<h2>Beta-метрики</h2><div id="metrics" class="grid"></div>
<h2>Тренды</h2><div id="trend"></div>
<h2>Волны</h2><div id="waves" class="wide"></div>
<h2>Capacity plans</h2><div id="plans" class="wide"></div>
<h2>Beta feedback</h2><div id="feedback" class="wide"></div>
</main><script>
const esc=s=>String(s??'').replace(/[&<>"']/g,m=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[m]));
function save(){sessionStorage.x1AdminToken=document.getElementById('token').value;load()}
async function api(path,opts={}){let t=sessionStorage.x1AdminToken||'';let r=await fetch(path,{...opts,headers:{...(opts.headers||{}),'Authorization':'Bearer '+t,'Content-Type':'application/json'}});let text=await r.text();if(!r.ok)throw new Error(r.status+' '+text);return text?JSON.parse(text):null}
function cards(o){return Object.entries(o||{}).map(([k,v])=>`<div class="card"><div class="muted">${esc(k)}</div><b>${esc(v)}</b></div>`).join('')}
async function act(path,body){try{await api(path,{method:'POST',body:JSON.stringify(body||{})});await load()}catch(e){alert(e.message)}}
async function newWave(){let n=Number(document.getElementById('waveSize').value||10);await act('/v1/admin/beta/waves',{cohort:'closed-beta-1',target_participants:n,window_days:30})}
async function proposePlan(){await act('/v1/admin/beta/capacity-plans/propose',{cohort:'closed-beta-1',window_days:30})}
async function rollbackPlan(){await act('/v1/admin/beta/capacity-plans/rollback',{target_plan_id:null})}
async function load(){try{document.getElementById('token').value=sessionStorage.x1AdminToken||'';let [c,t,w,p,f]=await Promise.all([api('/v1/admin/beta/control'),api('/v1/admin/beta/trends'),api('/v1/admin/beta/waves'),api('/v1/admin/beta/capacity-plans'),api('/v1/admin/beta/feedback?limit=50')]);document.getElementById('auth').textContent=' ✓';let d=c.decision||{};document.getElementById('decision').innerHTML=cards({status:d.status,admission_allowed:d.admission_allowed,capacity:c.capacity?.status,active_plan:c.active_capacity_plan?.version??'none'});document.getElementById('reasons').innerHTML='<div class="card '+(d.admission_allowed?'ok':'warn')+'"><b>Причины:</b> '+esc((d.reasons||[]).join(', ')||'нет')+'<br><b>Предупреждения:</b> '+esc((d.warnings||[]).join(', ')||'нет')+'</div>';let m=c.beta||{},mm=m.metrics||{};document.getElementById('metrics').innerHTML=cards({participants:m.enrolled_count,active:m.active_participant_count,requests:m.request_count,tasks:m.task_count,D1:mm.d1_retention,D7:mm.d7_retention,D30:mm.d30_retention,success:mm.request_success_rate,frustration:mm.frustration_per_request,p95_queue_ms:m.p95_queue_ms,p95_duration_ms:m.p95_duration_ms,verified_per_cpu_min:mm.verified_request_success_per_cpu_minute});let l=t.latest||{};document.getElementById('trend').innerHTML='<div class="card '+(t.status==='regressed'?'bad':'ok')+'"><b>'+esc(t.status)+'</b><br>Аномалии: '+esc((l.anomalies||[]).join(', ')||'нет')+'<br>Дельты: '+esc(JSON.stringify(l.deltas||{}))+'</div>';document.getElementById('waves').innerHTML='<table><tr><th>#</th><th>state</th><th>admitted/target</th><th>pause</th><th>actions</th></tr>'+w.map(x=>`<tr><td>${esc(x.wave_number)}</td><td>${esc(x.state)}</td><td>${esc(x.admitted_count)}/${esc(x.target_participants)}</td><td>${esc(x.pause_reason)}</td><td><button onclick="act('/v1/admin/beta/waves/${esc(x.id)}/open')">open</button><button onclick="act('/v1/admin/beta/waves/${esc(x.id)}/evaluate')">evaluate</button><button onclick="act('/v1/admin/beta/waves/${esc(x.id)}/resume')">resume</button><button onclick="act('/v1/admin/beta/waves/${esc(x.id)}/close')">close</button></td></tr>`).join('')+'</table>';document.getElementById('plans').innerHTML='<table><tr><th>v</th><th>status</th><th>context</th><th>queue</th><th>runtime</th><th>actions</th></tr>'+p.map(x=>`<tr><td>${esc(x.version)}</td><td>${esc(x.status)}</td><td>${esc(x.plan?.deep_context_tokens)}</td><td>${esc(x.plan?.max_queue_size)}</td><td>${esc(x.runtime_applied?'applied':x.requires_restart?'restart':'pending')}</td><td><button onclick="act('/v1/admin/beta/capacity-plans/${esc(x.id)}/approve')">approve</button><button onclick="act('/v1/admin/beta/capacity-plans/${esc(x.id)}/activate')">activate</button></td></tr>`).join('')+'</table>';document.getElementById('feedback').innerHTML='<table><tr><th>severity</th><th>status</th><th>title</th><th>wave</th><th>action</th></tr>'+f.map(x=>`<tr><td>${esc(x.complaint.severity)}</td><td>${esc(x.complaint.status)}</td><td>${esc(x.complaint.title)}</td><td>${esc(x.beta.wave_number)}</td><td><button onclick="act('/v1/admin/beta/feedback/${esc(x.complaint.id)}/confirm',{cohort:'closed-beta-1',reproduction:{}})">confirm→regression</button></td></tr>`).join('')+'</table>'}catch(e){document.getElementById('auth').textContent=' '+e.message}}
if(sessionStorage.x1AdminToken)load();</script></body></html>'''


@router.get("/admin/beta", response_class=HTMLResponse, include_in_schema=False)
def beta_admin_console() -> HTMLResponse:
    return HTMLResponse(
        PAGE,
        headers={
            "Cache-Control": "no-store",
            "X-Frame-Options": "DENY",
            "Content-Security-Policy": "default-src 'self'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; connect-src 'self'; frame-ancestors 'none'",
        },
    )
