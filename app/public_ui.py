from __future__ import annotations

import html
import secrets

from fastapi import APIRouter
from fastapi.responses import HTMLResponse

router = APIRouter(tags=["public-site"])


def _page(title: str, body: str, *, script: str = "") -> HTMLResponse:
    nonce = secrets.token_urlsafe(18)
    safe_title = html.escape(title)
    document = f"""<!doctype html>
<html lang=\"ru\">
<head>
<meta charset=\"utf-8\">
<meta name=\"viewport\" content=\"width=device-width,initial-scale=1\">
<title>{safe_title}</title>
<style nonce=\"{nonce}\">
:root{{--bg:#090b10;--card:#11151d;--muted:#9aa4b2;--text:#f5f7fb;--accent:#d34747;--line:#252b36;--good:#5ecb8a}}
*{{box-sizing:border-box}}body{{margin:0;background:radial-gradient(circle at 20% 0,#1a1d27 0,#090b10 45%);color:var(--text);font:16px/1.55 Inter,system-ui,-apple-system,Segoe UI,Arial,sans-serif}}
a{{color:inherit;text-decoration:none}}.wrap{{max-width:1120px;margin:auto;padding:0 24px}}nav{{height:72px;display:flex;align-items:center;justify-content:space-between;border-bottom:1px solid var(--line)}}.brand{{font-weight:800;letter-spacing:.08em}}.actions{{display:flex;gap:10px}}.btn{{display:inline-flex;align-items:center;justify-content:center;min-height:44px;padding:0 18px;border-radius:12px;border:1px solid var(--line);background:#151923;color:#fff;font-weight:700;cursor:pointer}}.btn.primary{{background:var(--accent);border-color:var(--accent)}}.hero{{padding:88px 0 64px;display:grid;grid-template-columns:1.2fr .8fr;gap:44px;align-items:center}}h1{{font-size:clamp(42px,7vw,78px);line-height:.98;margin:0 0 24px;letter-spacing:-.045em}}h2{{font-size:30px;margin:0 0 12px}}p{{color:var(--muted)}}.lead{{font-size:20px;max-width:720px}}.panel,.feature,.form-card{{background:rgba(17,21,29,.86);border:1px solid var(--line);border-radius:20px}}.panel{{padding:28px}}.metric{{padding:16px 0;border-bottom:1px solid var(--line)}}.metric:last-child{{border:0}}.metric b{{display:block;font-size:20px}}.features{{display:grid;grid-template-columns:repeat(3,1fr);gap:16px;padding:24px 0 80px}}.feature{{padding:22px}}.feature b{{font-size:18px}}.form-shell{{max-width:480px;margin:56px auto 100px}}.form-card{{padding:28px}}label{{display:block;font-weight:650;margin:16px 0 7px}}input{{width:100%;padding:13px 14px;border-radius:10px;border:1px solid #323947;background:#0b0e14;color:#fff;font:inherit;outline:none}}input:focus{{border-color:#777f90}}.full{{width:100%;margin-top:20px}}.status{{min-height:24px;margin-top:14px;color:var(--muted)}}.status.ok{{color:var(--good)}}.status.err{{color:#ff8c8c}}.fine{{font-size:13px}}footer{{border-top:1px solid var(--line);padding:28px 0;color:var(--muted)}}@media(max-width:800px){{.hero{{grid-template-columns:1fr;padding-top:56px}}.features{{grid-template-columns:1fr}}nav{{height:auto;padding:16px 0;gap:12px;align-items:flex-start}}.actions{{flex-wrap:wrap;justify-content:flex-end}}}}
</style>
</head>
<body>{body}<script nonce=\"{nonce}\">{script}</script></body></html>"""
    response = HTMLResponse(document)
    response.headers.update(
        {
            "Content-Security-Policy": (
                "default-src 'none'; base-uri 'none'; frame-ancestors 'none'; form-action 'self'; "
                f"style-src 'nonce-{nonce}'; script-src 'nonce-{nonce}'; connect-src 'self'; img-src 'self' data:"
            ),
            "Referrer-Policy": "no-referrer",
            "X-Content-Type-Options": "nosniff",
            "X-Frame-Options": "DENY",
            "Permissions-Policy": "camera=(), microphone=(), geolocation=(), payment=()",
            "Cross-Origin-Opener-Policy": "same-origin",
            "Cross-Origin-Resource-Policy": "same-origin",
        }
    )
    return response


def _nav() -> str:
    return """<div class=\"wrap\"><nav><a class=\"brand\" href=\"/\">X1 AI</a><div class=\"actions\"><a class=\"btn\" href=\"/login\">Войти</a><a class=\"btn primary\" href=\"/register\">Создать аккаунт</a></div></nav></div>"""


@router.get("/", response_class=HTMLResponse, include_in_schema=False)
def landing() -> HTMLResponse:
    body = _nav() + """
<main class=\"wrap\">
<section class=\"hero\">
<div><h1>Нейросеть, которая работает на вашем контуре</h1><p class=\"lead\">X1 объединяет локальную Qwen-модель, поиск по интернету, работу с проектами, документами и закрытую разработку. Модель работает на вашем сервере; внешний интернет используется только там, где вы явно запускаете исследование.</p><div class=\"actions\"><a class=\"btn primary\" href=\"/register\">Начать работу</a><a class=\"btn\" href=\"/login\">У меня есть аккаунт</a></div></div>
<div class=\"panel\"><div class=\"metric\"><b>Локальный inference</b><span>Qwen через llama.cpp без покупки стороннего LLM API.</span></div><div class=\"metric\"><b>Проверяемые ответы</b><span>Freshness-gate, source trust и quality-checks отделяют подтверждённое от непроверенного.</span></div><div class=\"metric\"><b>Закрытая разработка</b><span>Изолированный sandbox с ограничениями CPU, RAM, процессов и сети.</span></div></div>
</section>
<section><h2>Что умеет X1</h2><div class=\"features\"><div class=\"feature\"><b>Чат и проекты</b><p>История, проектный контекст, файлы, задачи и режимы Fast / Work / Deep.</p></div><div class=\"feature\"><b>Интернет-исследования</b><p>Собственный SearXNG, SSRF-защита, контроль свежести и защита от poisoned sources.</p></div><div class=\"feature\"><b>Разработка</b><p>Агенты могут писать и проверять код в закрытом контейнере без доступа к Docker socket веб-приложения.</p></div><div class=\"feature\"><b>Документы</b><p>DOCX/PDF, структурная и визуальная QA-проверка перед выдачей готового файла.</p></div><div class=\"feature\"><b>Контроль нагрузки</b><p>Ограниченные очереди, квоты, circuit breakers и автоматическая деградация вместо падения сервера.</p></div><div class=\"feature\"><b>Прозрачность</b><p>System Health, release gate, checkpoints, backup/restore drill и диагностика причин сбоев.</p></div></div></section>
</main><footer><div class=\"wrap\">X1 AI · Local-first AI platform</div></footer>"""
    response = _page("X1 AI — локальная нейросеть для работы и разработки", body)
    response.headers["Cache-Control"] = "public, max-age=300"
    return response


def _auth_form(kind: str) -> HTMLResponse:
    register = kind == "register"
    title = "Создать аккаунт" if register else "Войти в X1"
    endpoint = "/v1/auth/register" if register else "/v1/auth/login"
    extra = "<label for=\"name\">Имя</label><input id=\"name\" maxlength=\"120\" autocomplete=\"name\">" if register else ""
    switch = "Уже зарегистрированы? <a href=\"/login\">Войти</a>" if register else "Нет аккаунта? <a href=\"/register\">Создать</a>"
    body = _nav() + f"""<main class=\"wrap\"><section class=\"form-shell\"><div class=\"form-card\"><h2>{title}</h2><p>Токен сессии хранится только в текущей вкладке браузера.</p><form id=\"auth-form\">{extra}<label for=\"email\">Email</label><input id=\"email\" type=\"email\" maxlength=\"320\" required autocomplete=\"email\"><label for=\"password\">Пароль</label><input id=\"password\" type=\"password\" minlength=\"12\" maxlength=\"256\" required autocomplete=\"{'new-password' if register else 'current-password'}\"><button class=\"btn primary full\" type=\"submit\">{title}</button><div id=\"status\" class=\"status\" aria-live=\"polite\"></div></form><p class=\"fine\">{switch}</p></div></section></main>"""
    script = f"""
const existing=sessionStorage.getItem('x1_access_token');if(existing)location.replace('/app');
const form=document.getElementById('auth-form');const statusBox=document.getElementById('status');
form.addEventListener('submit',async(e)=>{{e.preventDefault();statusBox.className='status';statusBox.textContent='Проверяем данные…';const payload={{email:document.getElementById('email').value,password:document.getElementById('password').value}};const name=document.getElementById('name');if(name)payload.display_name=name.value;try{{const r=await fetch('{endpoint}',{{method:'POST',headers:{{'Content-Type':'application/json'}},body:JSON.stringify(payload),credentials:'omit'}});let data={{}};try{{data=await r.json()}}catch(_e){{}}if(!r.ok){{const detail=typeof data.detail==='string'?data.detail:'Не удалось выполнить запрос';throw new Error(detail)}}sessionStorage.setItem('x1_access_token',data.access_token);sessionStorage.setItem('x1_user_id',data.user_id);statusBox.className='status ok';statusBox.textContent='Готово. Открываю X1…';form.reset();location.assign('/app');}}catch(err){{statusBox.className='status err';statusBox.textContent=err&&err.message?err.message:'Ошибка соединения';}}}});
"""
    response = _page(f"{title} — X1 AI", body, script=script)
    response.headers["Cache-Control"] = "no-store"
    response.headers["Pragma"] = "no-cache"
    return response


@router.get("/register", response_class=HTMLResponse, include_in_schema=False)
def register_page() -> HTMLResponse:
    return _auth_form("register")


@router.get("/login", response_class=HTMLResponse, include_in_schema=False)
def login_page() -> HTMLResponse:
    return _auth_form("login")
