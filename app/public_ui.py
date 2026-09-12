from __future__ import annotations

import html
import json
import secrets

from fastapi import APIRouter, Depends
from fastapi.responses import HTMLResponse
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.db import get_db
from app.services.owner_integrations import public_analytics_config

router = APIRouter(tags=["public-site"])


def _analytics_bootstrap(db: Session, nonce: str) -> tuple[str, str]:
    config = public_analytics_config(db)
    if not config.get("enabled"):
        return "", ""
    counter = int(config["counter_id"])
    webvisor = "true" if config.get("webvisor") else "false"
    script = f'''
window.x1MetrikaGoal=function(name,params){{try{{if(window.ym)window.ym({counter},'reachGoal',name,params||{{}})}}catch(_e){{}}}};
window.x1MetrikaUser=function(id){{try{{if(window.ym&&id)window.ym({counter},'setUserID',String(id))}}catch(_e){{}}}};
(function(m,e,t,r,i,k,a){{m[i]=m[i]||function(){{(m[i].a=m[i].a||[]).push(arguments)}};m[i].l=1*new Date();k=e.createElement(t),a=e.getElementsByTagName(t)[0],k.async=1,k.src=r,k.referrerPolicy='no-referrer';a.parentNode.insertBefore(k,a)}})(window,document,'script','https://mc.yandex.ru/metrika/tag.js','ym');
ym({counter},'init',{{clickmap:true,trackLinks:true,accurateTrackBounce:true,webvisor:{webvisor}}});
'''
    csp = " https://mc.yandex.ru"
    return script, csp


def _page(title: str, description: str, body: str, *, script: str = "", db: Session) -> HTMLResponse:
    nonce = secrets.token_urlsafe(18)
    analytics_script, metrika_source = _analytics_bootstrap(db, nonce)
    safe_title = html.escape(title)
    safe_description = html.escape(description, quote=True)
    document = f"""<!doctype html>
<html lang="ru">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover">
<meta name="description" content="{safe_description}">
<meta name="theme-color" content="#090b10">
<title>{safe_title}</title>
<style nonce="{nonce}">
:root{{--bg:#090b10;--panel:#111620;--panel2:#151b25;--muted:#9ba6b7;--text:#f7f8fb;--accent:#d34747;--accent2:#b53237;--line:#29313d;--good:#68d596;--warn:#efbd63}}
*{{box-sizing:border-box}}html{{scroll-behavior:smooth}}body{{margin:0;background:radial-gradient(circle at 72% -10%,#25202b 0,#0d1016 34%,#090b10 58%);color:var(--text);font:16px/1.55 Inter,system-ui,-apple-system,Segoe UI,Arial,sans-serif;-webkit-font-smoothing:antialiased}}a{{color:inherit;text-decoration:none}}button,input{{font:inherit}}.wrap{{width:min(1160px,100%);margin:auto;padding-inline:22px}}nav{{min-height:70px;display:flex;align-items:center;gap:16px;border-bottom:1px solid var(--line)}}.brand{{font-weight:900;letter-spacing:.08em}}.navlinks{{display:flex;gap:20px;margin-left:22px;color:#c4cbd6;font-size:14px}}.actions{{margin-left:auto;display:flex;gap:9px;align-items:center}}.btn{{display:inline-flex;align-items:center;justify-content:center;min-height:45px;padding:0 18px;border-radius:11px;border:1px solid var(--line);background:#151a23;color:#fff;font-weight:760;cursor:pointer;transition:transform .12s ease,border-color .12s ease}}.btn:hover{{transform:translateY(-1px);border-color:#536071}}.btn.primary{{background:linear-gradient(180deg,var(--accent),var(--accent2));border-color:transparent}}.btn.large{{min-height:52px;padding-inline:24px}}.hero{{padding:92px 0 72px;display:grid;grid-template-columns:minmax(0,1.15fr) minmax(300px,.85fr);gap:48px;align-items:center}}.eyebrow{{display:inline-flex;border:1px solid #413943;background:#17141b;border-radius:999px;padding:6px 10px;color:#d9cbd9;font-size:13px;margin-bottom:18px}}h1{{font-size:clamp(42px,6.8vw,78px);line-height:.98;margin:0 0 24px;letter-spacing:-.052em;max-width:850px}}h2{{font-size:clamp(28px,4vw,42px);letter-spacing:-.035em;line-height:1.08;margin:0 0 13px}}h3{{font-size:19px;margin:0 0 8px}}p{{color:var(--muted)}}.lead{{font-size:20px;max-width:720px;margin-bottom:28px}}.hero-note{{font-size:13px;color:#7f8998;margin-top:12px}}.hero-card,.card,.price,.faq details,.auth-card{{background:rgba(17,22,32,.9);border:1px solid var(--line);border-radius:18px}}.hero-card{{padding:25px;box-shadow:0 26px 70px #0007}}.example{{display:grid;gap:10px}}.user-msg{{margin-left:14%;background:#1a202a;border-radius:14px;padding:12px 14px}}.ai-msg{{border-top:1px solid var(--line);padding-top:16px;color:#e8ecf2}}.answer-line{{height:9px;border-radius:10px;background:#333c49;margin:8px 0}}.answer-line.w70{{width:70%}}.trust-row{{display:grid;grid-template-columns:repeat(4,1fr);gap:10px;padding:0 0 72px}}.trust{{border-top:1px solid var(--line);padding-top:15px}}.trust b{{display:block;font-size:17px}}.section{{padding:70px 0}}.section-head{{max-width:760px;margin-bottom:30px}}.cards{{display:grid;grid-template-columns:repeat(3,1fr);gap:13px}}.card{{padding:21px}}.card p{{margin-bottom:0}}.how{{counter-reset:step;display:grid;grid-template-columns:repeat(3,1fr);gap:14px}}.step{{position:relative;padding:22px;border-top:1px solid var(--line)}}.step:before{{counter-increment:step;content:counter(step);display:grid;place-items:center;width:32px;height:32px;border-radius:10px;background:#202731;margin-bottom:13px;font-weight:800}}.pricing{{display:grid;grid-template-columns:repeat(5,1fr);gap:10px}}.price{{padding:20px;display:flex;flex-direction:column;min-height:290px}}.price.featured{{border-color:#8a4548;box-shadow:0 0 0 1px #8a454833 inset}}.price-name{{font-size:13px;text-transform:uppercase;letter-spacing:.08em;color:#bbc4d0}}.price-value{{font-size:31px;font-weight:900;margin:9px 0}}.price-value small{{font-size:12px;color:var(--muted);font-weight:500}}.price ul{{padding-left:18px;color:#c8cfda;font-size:14px;flex:1}}.price .btn{{width:100%}}.privacy{{display:grid;grid-template-columns:.9fr 1.1fr;gap:35px;align-items:start}}.checklist{{display:grid;gap:9px}}.check{{background:#10151e;border:1px solid var(--line);padding:12px 14px;border-radius:11px;color:#d8dee8}}.faq{{display:grid;gap:8px}}.faq details{{padding:16px 18px}}.faq summary{{cursor:pointer;font-weight:750}}.faq p{{margin-bottom:0}}.cta{{padding:40px;border:1px solid #41353b;background:linear-gradient(135deg,#1b171d,#111620);border-radius:24px;display:flex;gap:20px;align-items:center}}.cta>div{{flex:1}}footer{{margin-top:75px;border-top:1px solid var(--line);padding:28px 0;color:var(--muted);font-size:13px}}.form-shell{{max-width:470px;margin:55px auto 90px}}.auth-card{{padding:28px}}label{{display:block;font-weight:650;margin:15px 0 7px}}input{{width:100%;padding:13px 14px;border-radius:10px;border:1px solid #323947;background:#0b0e14;color:#fff;outline:none}}input:focus{{border-color:#778295}}.full{{width:100%;margin-top:20px}}.status{{min-height:24px;margin-top:14px;color:var(--muted)}}.status.ok{{color:var(--good)}}.status.err{{color:#ff8c8c}}.fine{{font-size:13px}}.fine a{{text-decoration:underline;text-underline-offset:3px}}.verify-box{{padding:16px;border:1px solid #55452d;background:#1b1710;border-radius:12px;margin-top:13px;color:#e8d5ad}}@media(max-width:980px){{.pricing{{grid-template-columns:repeat(2,1fr)}}.trust-row{{grid-template-columns:repeat(2,1fr)}}}}@media(max-width:800px){{.hero{{grid-template-columns:1fr;padding-top:58px}}.cards,.how,.privacy{{grid-template-columns:1fr}}.navlinks{{display:none}}.section{{padding:52px 0}}.cta{{display:block;padding:25px}}}}@media(max-width:560px){{.wrap{{padding-inline:14px}}nav{{min-height:62px}}.actions .secondary-login{{display:none}}.hero{{padding:48px 0 55px;gap:26px}}h1{{font-size:43px}}.lead{{font-size:17px}}.pricing,.trust-row{{grid-template-columns:1fr}}.price{{min-height:0}}.hero-card{{padding:18px}}}}
</style>
</head>
<body>{body}<script nonce="{nonce}">{analytics_script}\n{script}</script></body></html>"""
    response = HTMLResponse(document)
    response.headers.update(
        {
            "Content-Security-Policy": (
                "default-src 'none'; base-uri 'none'; frame-ancestors 'none'; form-action 'self'; "
                f"style-src 'nonce-{nonce}'; script-src 'nonce-{nonce}'{metrika_source}; connect-src 'self'{metrika_source}; img-src 'self' data:{metrika_source}"
            ),
            "Referrer-Policy": "strict-origin-when-cross-origin",
            "X-Content-Type-Options": "nosniff",
            "X-Frame-Options": "DENY",
            "Permissions-Policy": "camera=(), microphone=(), geolocation=(), payment=()",
            "Cross-Origin-Opener-Policy": "same-origin",
            "Cross-Origin-Resource-Policy": "same-origin",
        }
    )
    return response


def _nav() -> str:
    return """<div class=\"wrap\"><nav><a class=\"brand\" href=\"/\">X1 AI</a><div class=\"navlinks\"><a href=\"/#features\">Возможности</a><a href=\"/#pricing\">Тарифы</a><a href=\"/#privacy\">Приватность</a><a href=\"/#faq\">FAQ</a></div><div class=\"actions\"><a class=\"btn secondary-login\" href=\"/login\">Войти</a><a class=\"btn primary js-register-cta\" href=\"/register\">Попробовать бесплатно</a></div></nav></div>"""


def _rub(settings, name: str) -> str:
    amount = max(0, int(getattr(settings, f"billing_price_{name}_minor", 0)))
    return f"{amount / 100:,.0f}".replace(",", " ")


@router.get("/", response_class=HTMLResponse, include_in_schema=False)
def landing(db: Session = Depends(get_db)) -> HTMLResponse:
    settings = get_settings()
    prices = {name: _rub(settings, name) for name in ("x1", "pro", "max", "business")}
    body = _nav() + f"""
<main>
<section class=\"wrap hero\"><div><span class=\"eyebrow\">AI для повседневной работы, проектов и кода</span><h1>Спросите. Получите результат. Продолжите работу в одном месте.</h1><p class=\"lead\">X1 помогает разбирать задачи, писать и улучшать тексты, работать с файлами и проектами, искать свежую информацию в интернете и решать задачи по коду. Начните бесплатно — без привязки карты.</p><div class=\"actions\"><a class=\"btn primary large js-register-cta\" href=\"/register\">Создать бесплатный аккаунт</a><a class=\"btn large\" href=\"/#features\">Посмотреть возможности</a></div><div class=\"hero-note\">Бесплатный тариф · лимиты видны заранее · платные функции включаются только после подтверждённой оплаты</div></div><div class=\"hero-card\"><div class=\"example\"><div class=\"user-msg\">Сравни три варианта запуска продукта и дай рекомендацию с рисками.</div><div class=\"ai-msg\"><b>X1</b><div class=\"answer-line\"></div><div class=\"answer-line\"></div><div class=\"answer-line w70\"></div><p>Структурирует ответ, при необходимости подключает свежие источники и показывает ограничения вместо уверенного вымысла.</p></div></div></div></section>
<section class=\"wrap trust-row\"><div class=\"trust\"><b>Локальная модель</b><span class=\"muted\">Основной AI inference работает на сервере X1 без покупки стороннего LLM API.</span></div><div class=\"trust\"><b>Контроль расходов</b><span class=\"muted\">Лимиты запросов и ресурса видны в аккаунте до того, как возникнет перерасход.</span></div><div class=\"trust\"><b>Защита от перегрузки</b><span class=\"muted\">Очередь и приоритеты позволяют сервису замедлиться, а не упасть при всплеске.</span></div><div class=\"trust\"><b>Поддержка тикетами</b><span class=\"muted\">Вопрос сохраняется с историей и статусом — не нужно ловить менеджера в чате.</span></div></section>
<section class=\"section\" id=\"features\"><div class=\"wrap\"><div class=\"section-head\"><h2>Не просто чат — рабочее пространство</h2><p>Главные функции собраны вокруг результата пользователя, а сложность маршрутизации, квот и проверки остаётся внутри системы.</p></div><div class=\"cards\"><div class=\"card\"><h3>Чат Fast / Work / Deep</h3><p>Быстрый ответ для простого вопроса, более глубокая обработка для сложной задачи — с автоматическим режимом по умолчанию.</p></div><div class=\"card\"><h3>Свежая информация</h3><p>Когда задача зависит от текущих данных, X1 может подключить интернет-поиск и отделить источники от собственного вывода.</p></div><div class=\"card\"><h3>Проекты и файлы</h3><p>Объединяйте инструкции, диалоги и документы по задаче, чтобы не объяснять контекст заново в каждом сообщении.</p></div><div class=\"card\"><h3>Код и разработка</h3><p>Помощь с архитектурой, кодом и проверкой результата. Доступность тяжёлых execution-функций честно показывается через capabilities.</p></div><div class=\"card\"><h3>API</h3><p>Отдельные ключи, scopes и rate limits. API не получает бесконтрольный приоритет над пользователями веб-версии.</p></div><div class=\"card\"><h3>Прозрачное потребление</h3><p>Fast, Work и Deep расходуют разное число единиц. Ошибочный ответ сервера не списывается как успешный запрос.</p></div></div></div></section>
<section class=\"section\"><div class=\"wrap\"><div class=\"section-head\"><h2>Начать можно за несколько минут</h2></div><div class=\"how\"><div class=\"step\"><h3>Создайте аккаунт</h3><p>Нужны email и пароль. Если владелец сервиса включил подтверждение почты, придёт одно письмо со ссылкой.</p></div><div class=\"step\"><h3>Задайте первую задачу</h3><p>Auto сам выберет экономный режим. При необходимости можно вручную выбрать Fast, Work или Deep.</p></div><div class=\"step\"><h3>Сохраните важное в проект</h3><p>Проекты и файлы превращают разовые вопросы в рабочий процесс с накопленным контекстом.</p></div></div></div></section>
<section class=\"section\" id=\"pricing\"><div class=\"wrap\"><div class=\"section-head\"><h2>Тарифы без «безлимита», который ломает сервер</h2><p>У каждого плана есть понятный месячный и дневной ресурс. Это сохраняет предсказуемую скорость сервиса и экономику проекта.</p></div><div class=\"pricing\"><div class=\"price\"><div class=\"price-name\">Free</div><div class=\"price-value\">0 ₽</div><ul><li>30 единиц в месяц</li><li>до 6 единиц в день</li><li>чат и базовое знакомство</li></ul><a class=\"btn js-register-cta\" href=\"/register\">Начать</a></div><div class=\"price featured\"><div class=\"price-name\">X1</div><div class=\"price-value\">{prices['x1']} ₽ <small>/ 30 дней</small></div><ul><li>240 единиц в месяц</li><li>до 24 в день</li><li>для регулярного личного использования</li></ul><a class=\"btn primary js-pricing-cta\" href=\"/register?plan=x1\">Выбрать X1</a></div><div class=\"price\"><div class=\"price-name\">Pro</div><div class=\"price-value\">{prices['pro']} ₽ <small>/ 30 дней</small></div><ul><li>720 единиц в месяц</li><li>до 60 в день</li><li>для активной работы</li></ul><a class=\"btn js-pricing-cta\" href=\"/register?plan=pro\">Выбрать Pro</a></div><div class=\"price\"><div class=\"price-name\">Max</div><div class=\"price-value\">{prices['max']} ₽ <small>/ 30 дней</small></div><ul><li>1 800 единиц в месяц</li><li>до 120 в день</li><li>для тяжёлых задач</li></ul><a class=\"btn js-pricing-cta\" href=\"/register?plan=max\">Выбрать Max</a></div><div class=\"price\"><div class=\"price-name\">Business</div><div class=\"price-value\">{prices['business']} ₽ <small>/ 30 дней</small></div><ul><li>4 800 единиц в месяц</li><li>до 300 в день</li><li>организационные сценарии</li></ul><a class=\"btn js-pricing-cta\" href=\"/register?plan=business\">Выбрать Business</a></div></div><p class=\"hero-note\">Fast = 1 единица, Work = 2, Deep = 4. Дополнительно действует серверный compute-лимит как защита от аномально дорогих запросов.</p></div></section>
<section class=\"section\" id=\"privacy\"><div class=\"wrap privacy\"><div><h2>Приватность без громких обещаний</h2><p>Мы отделяем продуктовую аналитику от содержимого ваших задач. Владелец видит технические и бизнес-метрики, но продуктовая аналитика не требует отправлять тексты чатов в систему веб-аналитики.</p></div><div class=\"checklist\"><div class=\"check\">✓ Пароли хранятся как стойкие хэши, а не в открытом виде.</div><div class=\"check\">✓ API-ключи имеют scopes и rate limits.</div><div class=\"check\">✓ Платный тариф включается только после серверной проверки оплаты.</div><div class=\"check\">✓ Яндекс Метрике передаются события воронки, а не email или тексты сообщений.</div><div class=\"check\">✓ При высокой нагрузке запросы ограничиваются очередью и quota вместо бесконтрольного потребления RAM.</div></div></div></section>
<section class=\"section\" id=\"faq\"><div class=\"wrap\"><div class=\"section-head\"><h2>Частые вопросы</h2></div><div class=\"faq\"><details><summary>Почему здесь нет настоящего безлимита?</summary><p>Генерация ответа потребляет CPU-время. Честный лимит позволяет держать стоимость тарифа низкой и не превращать сервис в медленную очередь для всех пользователей.</p></details><details><summary>Что произойдёт, если одновременно придёт много запросов?</summary><p>Система запускает ограниченное число inference-задач, остальные попадают в ограниченную справедливую очередь. При полном заполнении сервер возвращает контролируемую перегрузку с возможностью повторить запрос, а не падает от OOM.</p></details><details><summary>Можно ли использовать бесплатно?</summary><p>Да. Free предназначен для знакомства и редкого использования. Карта для регистрации не нужна.</p></details><details><summary>Как понять, что оплата прошла?</summary><p>Тариф меняется только после подтверждения платёжного провайдера сервером. Возврат браузера со страницы оплаты сам по себе не считается оплатой.</p></details><details><summary>Куда писать, если возникла проблема?</summary><p>После входа доступна система тикетов: вопрос, история ответов и статус обращения сохраняются в аккаунте.</p></details></div></div></section>
<section class=\"wrap cta\"><div><h2>Попробуйте X1 на своей задаче</h2><p>Бесплатный аккаунт позволяет понять качество ответа и интерфейс до покупки тарифа.</p></div><a class=\"btn primary large js-register-cta\" href=\"/register\">Начать бесплатно</a></section>
</main><footer><div class=\"wrap\">X1 AI · local-first AI platform · <a href=\"/login\">Вход</a></div></footer>"""
    script = """
for(const a of document.querySelectorAll('.js-register-cta'))a.addEventListener('click',()=>window.x1MetrikaGoal&&window.x1MetrikaGoal('landing_register_click'));
for(const a of document.querySelectorAll('.js-pricing-cta'))a.addEventListener('click',()=>window.x1MetrikaGoal&&window.x1MetrikaGoal('pricing_click',{plan:new URL(a.href).searchParams.get('plan')||''}));
"""
    response = _page(
        "X1 AI — нейросеть для работы, проектов, файлов и кода",
        "X1 AI: чат с режимами Fast, Work и Deep, интернет-поиск, проекты, файлы и API. Бесплатный старт без привязки карты.",
        body,
        script=script,
        db=db,
    )
    response.headers["Cache-Control"] = "public, max-age=120"
    return response


def _auth_form(kind: str, db: Session) -> HTMLResponse:
    register = kind == "register"
    title = "Создать аккаунт" if register else "Войти в X1"
    endpoint = "/v1/auth/register" if register else "/v1/auth/login"
    extra = "<label for=\"name\">Имя</label><input id=\"name\" maxlength=\"120\" autocomplete=\"name\">" if register else ""
    switch = "Уже зарегистрированы? <a href=\"/login\">Войти</a>" if register else "Нет аккаунта? <a href=\"/register\">Создать</a> · <a href=\"/forgot-password\">Забыли пароль?</a>"
    body = _nav() + f"""<main class=\"wrap\"><section class=\"form-shell\"><div class=\"auth-card\"><h2>{title}</h2><p>{'Бесплатный старт — банковская карта не нужна.' if register else 'Продолжите работу с чатами и проектами.'}</p><form id=\"auth-form\">{extra}<label for=\"email\">Email</label><input id=\"email\" type=\"email\" maxlength=\"320\" required autocomplete=\"email\"><label for=\"password\">Пароль</label><input id=\"password\" type=\"password\" minlength=\"10\" maxlength=\"256\" required autocomplete=\"{'new-password' if register else 'current-password'}\"><button class=\"btn primary full\" type=\"submit\">{title}</button><div id=\"status\" class=\"status\" aria-live=\"polite\"></div></form><div id=\"verification\"></div><p class=\"fine\">{switch}</p></div></section></main>"""
    script = f"""
const existing=sessionStorage.getItem('x1_access_token');if(existing)location.replace('/app');
const form=document.getElementById('auth-form'),statusBox=document.getElementById('status'),verification=document.getElementById('verification');
function detail(data,fallback){{if(typeof data?.detail==='string')return data.detail;if(data?.detail?.message)return data.detail.message;return fallback}}
async function destination(token){{try{{const r=await fetch('/v1/account/onboarding',{{headers:{{Authorization:'Bearer '+token}},credentials:'omit'}});if(r.ok){{const s=await r.json();if(s&&s.visible)return '/welcome'}}}}catch(_e){{}}return '/app'}}
async function resend(email){{statusBox.className='status';statusBox.textContent='Отправляем письмо ещё раз…';const r=await fetch('/v1/auth/verification/resend',{{method:'POST',headers:{{'Content-Type':'application/json'}},body:JSON.stringify({{email}}),credentials:'omit'}});if(!r.ok){{let d={{}};try{{d=await r.json()}}catch{{}}throw new Error(detail(d,'Не удалось отправить письмо'))}}statusBox.className='status ok';statusBox.textContent='Если адрес зарегистрирован, письмо отправлено.'}}
form.addEventListener('submit',async(e)=>{{e.preventDefault();verification.replaceChildren();statusBox.className='status';statusBox.textContent='Проверяем данные…';const email=document.getElementById('email').value.trim();const payload={{email,password:document.getElementById('password').value}};const name=document.getElementById('name');if(name)payload.display_name=name.value;try{{const r=await fetch('{endpoint}',{{method:'POST',headers:{{'Content-Type':'application/json'}},body:JSON.stringify(payload),credentials:'omit'}});let data={{}};try{{data=await r.json()}}catch(_e){{}}if(!r.ok){{if(r.status===403&&data?.detail?.code==='email_verification_required'){{const box=document.createElement('div');box.className='verify-box';box.textContent='Сначала подтвердите email. Не нашли письмо? ';const b=document.createElement('button');b.className='btn';b.type='button';b.textContent='Отправить ещё раз';b.onclick=()=>resend(email).catch(err=>statusBox.textContent=err.message);box.append(b);verification.append(box);throw new Error('Подтвердите email и затем войдите.')}}throw new Error(detail(data,'Не удалось выполнить запрос'))}}if(data.verification_required){{sessionStorage.removeItem('x1_access_token');statusBox.className='status ok';statusBox.textContent='Аккаунт создан. Проверьте почту и подтвердите email.';const box=document.createElement('div');box.className='verify-box';box.textContent='Письмо не пришло? ';const b=document.createElement('button');b.className='btn';b.type='button';b.textContent='Отправить ещё раз';b.onclick=()=>resend(email).catch(err=>statusBox.textContent=err.message);box.append(b);verification.append(box);window.x1MetrikaGoal&&window.x1MetrikaGoal('registration_success');return}}sessionStorage.setItem('x1_access_token',data.access_token);sessionStorage.setItem('x1_user_id',data.user_id);window.x1MetrikaUser&&window.x1MetrikaUser(data.user_id);window.x1MetrikaGoal&&window.x1MetrikaGoal('{('registration_success' if register else 'login_success')}');statusBox.className='status ok';statusBox.textContent='Готово. Открываю X1…';location.assign(await destination(data.access_token))}}catch(err){{if(!statusBox.classList.contains('ok')){{statusBox.className='status err';statusBox.textContent=err&&err.message?err.message:'Ошибка соединения'}}}}}});
"""
    response = _page(f"{title} — X1 AI", "Безопасный вход и регистрация в X1 AI.", body, script=script, db=db)
    response.headers["Cache-Control"] = "no-store"
    response.headers["Pragma"] = "no-cache"
    return response


@router.get("/register", response_class=HTMLResponse, include_in_schema=False)
def register_page(db: Session = Depends(get_db)) -> HTMLResponse:
    return _auth_form("register", db)


@router.get("/login", response_class=HTMLResponse, include_in_schema=False)
def login_page(db: Session = Depends(get_db)) -> HTMLResponse:
    return _auth_form("login", db)


@router.get("/forgot-password", response_class=HTMLResponse, include_in_schema=False)
def forgot_password_page(db: Session = Depends(get_db)) -> HTMLResponse:
    body = _nav() + '''<main class="wrap"><section class="form-shell"><div class="auth-card"><h2>Восстановить пароль</h2><p>Укажите email. Если аккаунт существует, мы отправим ссылку для смены пароля.</p><form id="f"><label for="email">Email</label><input id="email" type="email" maxlength="320" required autocomplete="email"><button class="btn primary full" type="submit">Отправить ссылку</button><div id="status" class="status" aria-live="polite"></div></form><p class="fine"><a href="/login">Вернуться ко входу</a></p></div></section></main>'''
    script = r'''const f=document.getElementById('f'),s=document.getElementById('status');f.onsubmit=async e=>{e.preventDefault();s.className='status';s.textContent='Отправляем…';try{const r=await fetch('/v1/auth/password-reset/request',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({email:document.getElementById('email').value.trim()}),credentials:'omit'});if(!r.ok){let d={};try{d=await r.json()}catch{}throw new Error(typeof d.detail==='string'?d.detail:'Сервис восстановления временно недоступен')}s.className='status ok';s.textContent='Если аккаунт существует, письмо отправлено.'}catch(e){s.className='status err';s.textContent=e.message}};'''
    response = _page("Восстановление пароля — X1 AI", "Восстановление доступа к X1 AI.", body, script=script, db=db)
    response.headers["Cache-Control"] = "no-store"
    return response


@router.get("/verify-email", response_class=HTMLResponse, include_in_schema=False)
def verify_email_page(db: Session = Depends(get_db)) -> HTMLResponse:
    body = _nav() + '''<main class="wrap"><section class="form-shell"><div class="auth-card"><h2>Подтверждение email</h2><p id="status">Проверяем ссылку…</p><a class="btn primary full" href="/login" id="login" hidden>Войти</a></div></section></main>'''
    script = r'''const s=document.getElementById('status'),token=new URLSearchParams(location.search).get('token')||'';(async()=>{try{if(!token)throw new Error('В ссылке нет токена подтверждения.');const r=await fetch('/v1/auth/verify-email',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({token}),credentials:'omit'});let d={};try{d=await r.json()}catch{}if(!r.ok)throw new Error(typeof d.detail==='string'?d.detail:'Не удалось подтвердить email');s.textContent='Email подтверждён. Теперь можно войти в X1.';document.getElementById('login').hidden=false}catch(e){s.textContent=e.message}})();'''
    response = _page("Подтверждение email — X1 AI", "Подтверждение email X1 AI.", body, script=script, db=db)
    response.headers["Cache-Control"] = "no-store"
    return response


@router.get("/reset-password", response_class=HTMLResponse, include_in_schema=False)
def reset_password_page(db: Session = Depends(get_db)) -> HTMLResponse:
    body = _nav() + '''<main class="wrap"><section class="form-shell"><div class="auth-card"><h2>Новый пароль</h2><form id="f"><label for="password">Новый пароль</label><input id="password" type="password" minlength="10" maxlength="256" required autocomplete="new-password"><button class="btn primary full" type="submit">Сохранить пароль</button><div id="status" class="status" aria-live="polite"></div></form><a class="btn full" href="/login" id="login" hidden>Войти</a></div></section></main>'''
    script = r'''const f=document.getElementById('f'),s=document.getElementById('status'),token=new URLSearchParams(location.search).get('token')||'';f.onsubmit=async e=>{e.preventDefault();s.className='status';s.textContent='Сохраняем…';try{if(!token)throw new Error('В ссылке нет токена восстановления.');const r=await fetch('/v1/auth/password-reset/confirm',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({token,new_password:document.getElementById('password').value}),credentials:'omit'});let d={};try{d=await r.json()}catch{}if(!r.ok)throw new Error(typeof d.detail==='string'?d.detail:'Не удалось изменить пароль');s.className='status ok';s.textContent='Пароль изменён. Все прежние сессии завершены.';f.hidden=true;document.getElementById('login').hidden=false}catch(e){s.className='status err';s.textContent=e.message}};'''
    response = _page("Новый пароль — X1 AI", "Установка нового пароля X1 AI.", body, script=script, db=db)
    response.headers["Cache-Control"] = "no-store"
    return response
