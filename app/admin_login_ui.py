from __future__ import annotations

import secrets

from fastapi import APIRouter
from fastapi.responses import HTMLResponse

router = APIRouter(tags=["admin-login-ui"])


@router.get("/admin/login", response_class=HTMLResponse, include_in_schema=False)
def admin_login_page() -> HTMLResponse:
    nonce = secrets.token_urlsafe(18)
    document = r'''<!doctype html>
<html lang="ru"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover">
<meta name="robots" content="noindex,nofollow,noarchive"><meta name="theme-color" content="#ffffff">
<title>Вход администратора — OLYA AI</title>
<style nonce="__NONCE__">
*{box-sizing:border-box}html,body{min-height:100%;margin:0;color-scheme:light}body{display:grid;place-items:center;padding:24px;background:#fff;color:#202123;font:15px/1.5 Inter,ui-sans-serif,system-ui,-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;-webkit-font-smoothing:antialiased}.shell{width:min(410px,100%)}.brand{text-align:center;margin:0 0 32px;font-size:19px;font-weight:750;letter-spacing:-.03em;color:#202123}.card{padding:0 8px;background:#fff}h1{margin:0 0 8px;text-align:center;font-size:30px;letter-spacing:-.045em;font-weight:650}p{margin:0 auto 27px;max-width:340px;text-align:center;color:#737378}.notice{padding:10px 12px;border:1px solid #e4e4e7;border-radius:11px;background:#f8f8f9;color:#6f6f74;font-size:12px;margin-bottom:20px}label{display:block;margin:14px 0 7px;color:#343438;font-weight:600}input{width:100%;min-height:52px;padding:0 14px;border:1px solid #d9d9dc;border-radius:12px;background:#fff;color:#202123;outline:none;font:inherit;font-size:16px;transition:border-color .16s ease,box-shadow .16s ease}input:focus{border-color:#8b8b91;box-shadow:0 0 0 3px rgba(17,24,39,.07)}button{width:100%;min-height:50px;margin-top:20px;padding:0 16px;border:1px solid #111827;border-radius:12px;background:#111827;color:#fff;font:inherit;font-weight:700;cursor:pointer;transition:background .16s ease,transform .16s ease}button:hover{background:#262d3a;transform:translateY(-1px)}button:disabled{opacity:.55;cursor:wait;transform:none}.status{min-height:22px;margin-top:13px;color:#77777c;font-size:13px;text-align:center}.status.err{color:#c24141}.status.ok{color:#15805d}.foot{margin-top:22px;text-align:center;font-size:12px;color:#85858b}.foot a{color:#5f5f64;text-decoration:none}.foot a:hover{text-decoration:underline}*:focus-visible{outline:2px solid #111827;outline-offset:2px}@media(max-width:520px){body{padding:14px}.card{padding:0}h1{font-size:27px}}
</style></head><body><main class="shell"><div class="brand">OLYA AI</div><section class="card"><h1>Вход администратора</h1><p>Отдельный защищённый вход в панель управления.</p><div class="notice">Доступ разрешён только аккаунтам с ролью администратора.</div><form id="admin-login"><label for="email">Email администратора</label><input id="email" name="email" type="email" maxlength="320" autocomplete="username" required autofocus><label for="password">Пароль</label><input id="password" name="password" type="password" minlength="10" maxlength="256" autocomplete="current-password" required><button id="submit" type="submit">Войти</button><div class="status" id="status" aria-live="polite"></div></form></section><div class="foot"><a href="/">← На главную OLYA AI</a></div></main>
<script nonce="__NONCE__">
(function(){
const form=document.getElementById('admin-login'),box=document.getElementById('status'),button=document.getElementById('submit');
function detail(data){if(typeof data?.detail==='string')return data.detail;if(data?.detail?.message)return data.detail.message;return 'Не удалось войти в админпанель';}
function target(){const raw=new URLSearchParams(location.search).get('next')||'';if(raw.startsWith('/admin')&&!raw.startsWith('/admin/login'))return raw;return '/admin/owner';}
form.addEventListener('submit',async function(e){
  e.preventDefault();button.disabled=true;box.className='status';box.textContent='Проверяем права администратора…';
  try{
    const payload={email:document.getElementById('email').value.trim(),password:document.getElementById('password').value};
    const r=await fetch('/v1/auth/admin/login',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(payload),credentials:'same-origin'});
    let data={};try{data=await r.json()}catch(_e){}
    if(!r.ok||!data?.access_token||data?.is_admin!==true)throw new Error(detail(data));
    sessionStorage.setItem('x1AdminToken',data.access_token);
    sessionStorage.setItem('x1_access_token',data.access_token);
    sessionStorage.setItem('x1_user_id',data.user_id||'');
    box.className='status ok';box.textContent='Доступ подтверждён. Открываю админпанель…';
    location.replace(target());
  }catch(err){box.className='status err';box.textContent=err?.message||'Ошибка соединения';button.disabled=false;}
});
})();
</script></body></html>'''.replace("__NONCE__", nonce)
    response = HTMLResponse(document)
    response.headers.update({
        "Content-Security-Policy": "default-src 'none'; base-uri 'none'; frame-ancestors 'none'; form-action 'self'; " + f"style-src 'nonce-{nonce}'; script-src 'nonce-{nonce}'; connect-src 'self'",
        "Cache-Control": "no-store, no-cache, must-revalidate",
        "Pragma": "no-cache",
        "Referrer-Policy": "no-referrer",
        "X-Content-Type-Options": "nosniff",
        "X-Frame-Options": "DENY",
        "Permissions-Policy": "camera=(), microphone=(), geolocation=(), payment=()",
        "X-Robots-Tag": "noindex, nofollow, noarchive, nosnippet",
    })
    return response
