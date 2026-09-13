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
<meta name="robots" content="noindex,nofollow,noarchive"><meta name="theme-color" content="#0b0e14">
<title>Вход администратора — OLYA AI</title>
<style nonce="__NONCE__">
*{box-sizing:border-box}html,body{min-height:100%;margin:0}body{display:grid;place-items:center;padding:24px;background:radial-gradient(circle at 50% -10%,#252b3a 0,#11151e 34%,#080a0f 70%);color:#f5f7fb;font:15px/1.5 Inter,system-ui,-apple-system,Segoe UI,Arial,sans-serif}.shell{width:min(440px,100%)}.brand{display:flex;align-items:center;gap:11px;margin:0 0 18px 4px;font-weight:850;letter-spacing:-.02em}.mark{width:34px;height:34px;display:grid;place-items:center;border-radius:11px;background:linear-gradient(135deg,#f4f5f8,#aeb7c8);color:#10131a;font-size:13px;box-shadow:0 10px 35px #0008}.card{padding:28px;border:1px solid #29313d;border-radius:20px;background:rgba(15,19,27,.92);box-shadow:0 30px 80px #0008,0 1px 0 #ffffff0a inset;backdrop-filter:blur(14px)}h1{margin:0 0 7px;font-size:27px;letter-spacing:-.035em}p{margin:0 0 22px;color:#98a3b3}.notice{padding:10px 12px;border:1px solid #29313d;border-radius:10px;background:#111722;color:#9ca7b8;font-size:12px;margin-bottom:18px}label{display:block;margin:14px 0 7px;color:#dce2eb;font-weight:650}input{width:100%;padding:13px 14px;border:1px solid #303947;border-radius:11px;background:#0b0f16;color:#fff;outline:none}input:focus{border-color:#727f92;box-shadow:0 0 0 3px #66758a22}button{width:100%;margin-top:19px;padding:13px 16px;border:0;border-radius:11px;background:#f4f5f7;color:#11151b;font:inherit;font-weight:800;cursor:pointer}button:hover{background:#fff}button:disabled{opacity:.55;cursor:wait}.status{min-height:22px;margin-top:13px;color:#9ca7b8;font-size:13px}.status.err{color:#ff9b9b}.status.ok{color:#7be0aa}.foot{margin-top:15px;text-align:center;font-size:12px;color:#768191}.foot a{color:#aeb8c8;text-decoration:none}.foot a:hover{text-decoration:underline}@media(max-width:520px){body{padding:14px}.card{padding:22px;border-radius:17px}}
</style></head><body><main class="shell"><div class="brand"><span class="mark">OA</span><span>OLYA AI · Admin</span></div><section class="card"><h1>Вход администратора</h1><p>Отдельный защищённый вход в панель управления.</p><div class="notice">Доступ разрешён только аккаунтам с ролью администратора.</div><form id="admin-login"><label for="email">Email администратора</label><input id="email" name="email" type="email" maxlength="320" autocomplete="username" required autofocus><label for="password">Пароль</label><input id="password" name="password" type="password" minlength="10" maxlength="256" autocomplete="current-password" required><button id="submit" type="submit">Войти в админпанель</button><div class="status" id="status" aria-live="polite"></div></form></section><div class="foot"><a href="/">← На главную OLYA AI</a></div></main>
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
