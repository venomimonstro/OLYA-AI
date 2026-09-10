from __future__ import annotations

import secrets

from fastapi import APIRouter
from fastapi.responses import HTMLResponse

router = APIRouter(tags=["onboarding-ui"])


@router.get("/welcome", response_class=HTMLResponse, include_in_schema=False)
def welcome() -> HTMLResponse:
    nonce = secrets.token_urlsafe(18)
    document = r'''<!doctype html>
<html lang="ru"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Начало работы — X1 AI</title>
<style nonce="__NONCE__">
:root{--bg:#090b10;--panel:#111620;--panel2:#151b25;--line:#282f3b;--text:#f4f6fa;--muted:#9aa5b4;--accent:#d34747;--ok:#68d596;--bad:#ff8585}*{box-sizing:border-box}body{margin:0;min-height:100vh;background:radial-gradient(circle at 20% 0,#171b24 0,#090b10 45%);color:var(--text);font:15px/1.55 Inter,system-ui,-apple-system,Segoe UI,Arial,sans-serif}.wrap{width:min(980px,100%);margin:auto;padding:42px 22px 70px}.top{display:flex;align-items:center;justify-content:space-between;gap:16px;margin-bottom:54px}.brand{font-weight:850;letter-spacing:.08em}.skip,.ghost,.primary{border-radius:10px;padding:9px 13px;font:inherit;cursor:pointer}.skip,.ghost{border:1px solid var(--line);background:#151a23;color:#dfe4ec}.primary{border:0;background:var(--accent);color:#fff;font-weight:750}.primary:disabled{opacity:.45;cursor:not-allowed}h1{font-size:clamp(34px,6vw,58px);line-height:1.03;letter-spacing:-.045em;margin:0 0 13px;max-width:760px}.lead{font-size:18px;color:var(--muted);max-width:720px;margin:0 0 28px}.progress{display:flex;gap:8px;flex-wrap:wrap;margin:0 0 28px}.pill{font-size:12px;border:1px solid var(--line);border-radius:999px;padding:4px 9px;color:var(--muted)}.pill.done{color:var(--ok)}.grid{display:grid;grid-template-columns:repeat(3,1fr);gap:13px}.card{border:1px solid var(--line);background:rgba(17,22,32,.92);border-radius:16px;padding:18px;min-width:0}.card.recommended{border-color:#70464a;box-shadow:0 0 0 1px rgba(211,71,71,.15) inset}.num{width:28px;height:28px;border-radius:9px;background:#1d2430;display:grid;place-items:center;color:#dbe1ea;font-weight:800;margin-bottom:16px}.card h2{font-size:18px;margin:0 0 7px}.card p{color:var(--muted);min-height:68px}.form{display:grid;gap:8px;margin-top:14px}input,select{width:100%;background:#0c1016;color:#fff;border:1px solid var(--line);border-radius:9px;padding:10px;font:inherit}.state{min-height:22px;color:var(--muted);margin-top:16px}.state.ok{color:var(--ok)}.state.bad{color:var(--bad)}.footer{margin-top:26px;padding-top:18px;border-top:1px solid var(--line);display:flex;gap:10px;align-items:center;justify-content:space-between;color:var(--muted);font-size:13px}@media(max-width:760px){.wrap{padding:24px 14px 48px}.top{margin-bottom:38px}.grid{grid-template-columns:1fr}.card p{min-height:0}.footer{align-items:flex-start;flex-direction:column}.skip{white-space:nowrap}}
</style></head><body><main class="wrap">
<div class="top"><div class="brand">X1 AI</div><button class="skip" id="skip">Пропустить</button></div>
<h1>Получите первый результат в X1</h1><p class="lead">Можно сразу спросить нейросеть, создать рабочий проект или загрузить документ. Всё остальное настроится по ходу работы.</p>
<div class="progress"><span class="pill" id="m-chat">Чат</span><span class="pill" id="m-answer">Полезный ответ</span><span class="pill" id="m-project">Проект</span><span class="pill" id="m-file">Файл</span></div>
<section class="grid">
<div class="card" id="card-chat"><div class="num">1</div><h2>Начать с чата</h2><p>Самый быстрый путь: задайте обычный рабочий вопрос и получите первый проверяемый ответ локальной Qwen.</p><button class="primary" id="start-chat">Открыть чат</button></div>
<div class="card" id="card-project"><div class="num">2</div><h2>Создать проект</h2><p>Проект объединит инструкции, будущие чаты и файлы в одном контексте.</p><div class="form"><input id="project-name" maxlength="160" placeholder="Например, Новый сайт"><button class="primary" id="create-project">Создать проект</button></div></div>
<div class="card" id="card-file"><div class="num">3</div><h2>Загрузить файл</h2><p>Добавьте PDF, DOCX, XLSX или TXT в проект, чтобы X1 могла использовать его в ответах.</p><div class="form"><select id="project"><option value="">Выберите проект</option></select><input id="file" type="file"><button class="primary" id="upload" disabled>Загрузить и проиндексировать</button></div></div>
</section>
<div class="state" id="state" aria-live="polite"></div>
<div class="footer"><span id="footer-text">Прогресс хранится на сервере и восстановится после повторного входа.</span><button class="ghost" id="show-again" hidden>Показывать этот экран при входе</button></div>
</main><script nonce="__NONCE__">
const token=sessionStorage.getItem('x1_access_token');if(!token)location.replace('/login');const $=id=>document.getElementById(id);const state=$('state');let projects=[];
function errorText(data,status){const d=data&&data.detail;if(typeof d==='string')return d;if(d&&typeof d.message==='string')return d.message;if(status===429)return 'Достигнут лимит. Повторите позже.';if(status===503)return 'Сервис временно занят. Повторите позже.';return 'Операция не выполнена.'}
async function api(path,options={},timeout=120000){const c=new AbortController(),timer=setTimeout(()=>c.abort(),timeout);try{const headers={...(options.headers||{}),Authorization:'Bearer '+token};if(options.body&&typeof options.body==='string')headers['Content-Type']='application/json';const r=await fetch(path,{...options,headers,credentials:'omit',signal:c.signal});let data=null;try{data=await r.json()}catch(_e){}if(r.status===401){sessionStorage.clear();location.replace('/login');throw new Error('Сессия завершена')}if(!r.ok)throw new Error(errorText(data,r.status));return data}catch(e){if(e.name==='AbortError')throw new Error('Операция заняла слишком много времени.');throw e}finally{clearTimeout(timer)}}
function setState(text,kind=''){state.textContent=text;state.className='state '+kind}
function mark(id,value){$(id).classList.toggle('done',Boolean(value))}
function renderStatus(s){const m=s.milestones||{};mark('m-chat',m.chat_started);mark('m-answer',m.successful_answer);mark('m-project',m.project_created);mark('m-file',m.file_uploaded);for(const name of ['chat','project','file'])$('card-'+name).classList.toggle('recommended',s.recommended_action===name);$('show-again').hidden=Boolean(s.visible);if(s.completed)$('footer-text').textContent='Первый результат уже получен. Этот экран можно использовать как быстрый старт.';else if(s.dismissed)$('footer-text').textContent='Подсказки скрыты. Их можно включить снова в любой момент.'}
async function refreshStatus(){const s=await api('/v1/account/onboarding',{},20000);renderStatus(s);return s}
function fillProjects(){const select=$('project'),current=select.value;select.replaceChildren();const first=document.createElement('option');first.value='';first.textContent=projects.length?'Выберите проект':'Сначала создайте проект';select.append(first);for(const p of projects){const o=document.createElement('option');o.value=p.id;o.textContent=p.name;select.append(o)}if(projects.some(p=>p.id===current))select.value=current;$('upload').disabled=!select.value}
async function loadProjects(){projects=await api('/v1/projects',{},20000);fillProjects()}
$('project').onchange=()=>{$('upload').disabled=!$('project').value};
$('start-chat').onclick=()=>location.assign('/app');
$('create-project').onclick=async()=>{const name=$('project-name').value.trim();if(!name){setState('Введите название проекта.','bad');return}const b=$('create-project');b.disabled=true;setState('Создаю проект…');try{const p=await api('/v1/projects',{method:'POST',body:JSON.stringify({name,description:'',instructions:''})},30000);$('project-name').value='';setState('Проект создан. Можно перейти в X1 или добавить файл.','ok');await loadProjects();$('project').value=p.id;$('upload').disabled=false;await refreshStatus()}catch(e){setState(e.message,'bad')}finally{b.disabled=false}};
$('upload').onclick=async()=>{const projectId=$('project').value,file=$('file').files[0];if(!projectId){setState('Выберите проект.','bad');return}if(!file){setState('Выберите файл.','bad');return}const b=$('upload');b.disabled=true;setState('Загружаю и индексирую файл…');const c=new AbortController(),timer=setTimeout(()=>c.abort(),120000);try{const r=await fetch('/v1/projects/'+encodeURIComponent(projectId)+'/files?filename='+encodeURIComponent(file.name),{method:'POST',headers:{Authorization:'Bearer '+token,'Content-Type':file.type||'application/octet-stream'},body:file,credentials:'omit',signal:c.signal});let data=null;try{data=await r.json()}catch(_e){}if(r.status===401){sessionStorage.clear();location.replace('/login');return}if(!r.ok)throw new Error(errorText(data,r.status));$('file').value='';if(data&&data.status==='ready'){setState('Файл готов. X1 уже может использовать его в проекте.','ok');await refreshStatus()}else setState('Файл принят: '+String(data&&data.status||'обрабатывается'));}catch(e){setState(e.name==='AbortError'?'Индексация заняла много времени. Прогресс не потерян — проверьте файл в X1.':e.message,'bad')}finally{clearTimeout(timer);b.disabled=false}};
$('skip').onclick=async()=>{try{await api('/v1/account/onboarding/dismiss',{method:'POST'},20000)}catch(_e){}location.assign('/app')};
$('show-again').onclick=async()=>{try{const s=await api('/v1/account/onboarding/reopen',{method:'POST'},20000);renderStatus(s);setState('Стартовый экран снова будет показываться как незавершённая подсказка.','ok')}catch(e){setState(e.message,'bad')}};
(async()=>{try{await Promise.all([refreshStatus(),loadProjects()])}catch(e){setState(e.message,'bad')}})();
</script></body></html>'''.replace("__NONCE__", nonce)
    response = HTMLResponse(document)
    response.headers.update({
        "Content-Security-Policy": (
            "default-src 'none'; base-uri 'none'; frame-ancestors 'none'; form-action 'none'; "
            f"style-src 'nonce-{nonce}'; script-src 'nonce-{nonce}'; connect-src 'self'; img-src 'self' data:"
        ),
        "Cache-Control": "no-store",
        "Pragma": "no-cache",
        "Referrer-Policy": "no-referrer",
        "X-Robots-Tag": "noindex, nofollow, noarchive, nosnippet",
        "X-Content-Type-Options": "nosniff",
        "X-Frame-Options": "DENY",
        "Permissions-Policy": "camera=(), microphone=(), geolocation=(), payment=()",
        "Cross-Origin-Opener-Policy": "same-origin",
        "Cross-Origin-Resource-Policy": "same-origin",
    })
    return response
