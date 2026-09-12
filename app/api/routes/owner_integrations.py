from __future__ import annotations

import secrets
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field
from sqlalchemy import distinct, func, select
from sqlalchemy.orm import Session

from app.db import get_db
from app.models import BillingCheckout, BillingSubscription, PaymentRecord, SupportTicket, UsageEvent, User
from app.services.admin import audit, require_admin
from app.services.operations_analytics import operations_summary
from app.services.owner_integrations import integration_snapshot, public_analytics_config, send_email, update_owner_settings
from app.services.product_analytics import product_analytics

router = APIRouter(tags=["owner-integrations"])


class OwnerIntegrationUpdate(BaseModel):
    public_base_url: str | None = Field(default=None, max_length=500)
    smtp_enabled: bool | None = None
    smtp_host: str | None = Field(default=None, max_length=255)
    smtp_port: int | None = Field(default=None, ge=1, le=65535)
    smtp_username: str | None = Field(default=None, max_length=320)
    smtp_password: str | None = Field(default=None, max_length=1000)
    smtp_from_email: str | None = Field(default=None, max_length=320)
    smtp_from_name: str | None = Field(default=None, max_length=160)
    smtp_use_tls: bool | None = None
    smtp_use_ssl: bool | None = None
    auth_email_verification_required: bool | None = None
    metrika_enabled: bool | None = None
    metrika_counter_id: str | None = Field(default=None, max_length=32)
    metrika_webvisor: bool | None = None
    yoomoney_enabled: bool | None = None
    yoomoney_receiver: str | None = Field(default=None, max_length=40)
    yoomoney_notification_secret: str | None = Field(default=None, max_length=1000)
    yookassa_enabled: bool | None = None
    yookassa_shop_id: str | None = Field(default=None, max_length=64)
    yookassa_secret: str | None = Field(default=None, max_length=1000)
    payment_default_provider: str | None = Field(default=None, max_length=24)


class SMTPTestRequest(BaseModel):
    recipient: str | None = Field(default=None, max_length=320)


@router.get("/v1/public/analytics-config")
def analytics_config(db: Session = Depends(get_db)) -> dict:
    return public_analytics_config(db)


@router.get("/v1/admin/integrations")
def get_integrations(request: Request, admin: User = Depends(require_admin), db: Session = Depends(get_db)) -> dict:
    _ = admin
    return integration_snapshot(db, request.app.state.settings)


@router.put("/v1/admin/integrations")
def put_integrations(
    payload: OwnerIntegrationUpdate,
    request: Request,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
) -> dict:
    data = payload.model_dump(exclude_unset=True)
    try:
        update_owner_settings(db, request.app.state.settings, admin.id, data)
    except ValueError as exc:
        db.rollback()
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    audit(db, admin, "owner.integrations.update", "owner_integration_settings", "global", {"fields": sorted(k for k in data if not k.endswith(("password", "secret")))})
    db.commit()
    return integration_snapshot(db, request.app.state.settings)


@router.post("/v1/admin/integrations/smtp/test")
def test_smtp(
    payload: SMTPTestRequest,
    request: Request,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
) -> dict:
    recipient = (payload.recipient or admin.email).strip().lower()
    try:
        send_email(
            db,
            request.app.state.settings,
            to_email=recipient,
            subject="X1 AI — проверка SMTP",
            text="SMTP настроен корректно. Это тестовое письмо из панели владельца X1 AI.",
        )
    except Exception as exc:
        raise HTTPException(status_code=503, detail=f"SMTP test failed: {type(exc).__name__}") from exc
    audit(db, admin, "owner.smtp.test", "integration", "smtp", {"recipient": recipient})
    db.commit()
    return {"status": "sent", "recipient": recipient}


def _owner_finance(db: Session, settings, since: datetime) -> dict:
    now = datetime.now(timezone.utc)
    active_subs = list(
        db.scalars(
            select(BillingSubscription).where(
                BillingSubscription.status == "active",
                BillingSubscription.current_period_end > now,
                BillingSubscription.plan != "free",
            )
        ).all()
    )
    plan_counts: dict[str, int] = {}
    mrr_minor = 0
    for sub in active_subs:
        plan = str(sub.plan)
        plan_counts[plan] = plan_counts.get(plan, 0) + 1
        mrr_minor += max(0, int(getattr(settings, f"billing_price_{plan}_minor", 0)))
    total_users = int(db.scalar(select(func.count()).select_from(User)) or 0)
    paid_users = len(active_subs)
    checkouts = list(db.scalars(select(BillingCheckout).where(BillingCheckout.created_at >= since)).all())
    checkout_counts: dict[str, int] = {}
    for row in checkouts:
        checkout_counts[row.status] = checkout_counts.get(row.status, 0) + 1
    payments = list(db.scalars(select(PaymentRecord).where(PaymentRecord.created_at >= since)).all())
    by_provider: dict[str, dict[str, int]] = {}
    refunds_minor = 0
    paid_minor = 0
    for row in payments:
        p = by_provider.setdefault(str(row.provider), {"records": 0, "payments_minor": 0, "refunds_minor": 0})
        p["records"] += 1
        if row.kind == "refund":
            p["refunds_minor"] += int(row.amount_minor or 0)
            refunds_minor += int(row.amount_minor or 0)
        else:
            p["payments_minor"] += int(row.amount_minor or 0)
            if row.status in {"applied", "paid", "reconciled", "succeeded"}:
                paid_minor += int(row.amount_minor or 0)
    mrr_rub = round(mrr_minor / 100, 2)
    arppu = round(mrr_rub / max(1, paid_users), 2)
    arpu = round(mrr_rub / max(1, total_users), 2)
    server_cost = float(getattr(settings, "monthly_server_cost_rub", 0.0))
    return {
        "mrr_rub": mrr_rub,
        "active_paid_users": paid_users,
        "active_paid_by_plan": plan_counts,
        "arppu_rub": arppu,
        "arpu_rub": arpu,
        "server_cost_rub": server_cost,
        "mrr_after_server_rub": round(mrr_rub - server_cost, 2),
        "break_even_paid_users_at_current_arppu": None if arppu <= 0 else int((server_cost + arppu - 0.01) // arppu),
        "checkout_created": len(checkouts),
        "checkout_paid": checkout_counts.get("paid", 0),
        "checkout_conversion": round(checkout_counts.get("paid", 0) / max(1, len(checkouts)), 4),
        "checkout_by_status": checkout_counts,
        "recognized_payment_rub_window": round(paid_minor / 100, 2),
        "refund_rub_window": round(refunds_minor / 100, 2),
        "payments_by_provider": by_provider,
    }


@router.get("/v1/admin/owner-dashboard")
def owner_dashboard(
    request: Request,
    days: int = Query(default=30, ge=1, le=365),
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
) -> dict:
    _ = admin
    now = datetime.now(timezone.utc)
    since = now - timedelta(days=days)
    active_24h = int(db.scalar(select(func.count(distinct(UsageEvent.user_id))).where(UsageEvent.created_at >= now - timedelta(hours=24))) or 0)
    active_7d = int(db.scalar(select(func.count(distinct(UsageEvent.user_id))).where(UsageEvent.created_at >= now - timedelta(days=7))) or 0)
    total_users = int(db.scalar(select(func.count()).select_from(User)) or 0)
    new_users = int(db.scalar(select(func.count()).select_from(User).where(User.created_at >= since)) or 0)
    tickets = dict(db.execute(select(SupportTicket.status, func.count()).group_by(SupportTicket.status)).all())
    product = product_analytics(db, days=days, monthly_server_cost_rub=float(request.app.state.settings.monthly_server_cost_rub))
    ops = operations_summary(db, window_hours=days * 24, monthly_server_cost_rub=float(request.app.state.settings.monthly_server_cost_rub))
    integrations = integration_snapshot(db, request.app.state.settings)
    return {
        "generated_at": now.isoformat(),
        "window_days": days,
        "accounts": {
            "total_users": total_users,
            "new_users": new_users,
            "active_24h": active_24h,
            "active_7d": active_7d,
            "activation_rate": product.get("activation", {}).get("activation_rate"),
            "d1_retention": product.get("retention", {}).get("d1", {}).get("rate"),
            "d7_retention": product.get("retention", {}).get("d7", {}).get("rate"),
        },
        "finance": _owner_finance(db, request.app.state.settings, since),
        "quality": {
            "request_success_rate": ops.get("traffic", {}).get("success_rate"),
            "frustration_rate": ops.get("traffic", {}).get("frustration_rate"),
            "task_success_rate": product.get("task_success", {}).get("terminal_success_rate"),
            "compute_waste_rate": ops.get("compute_economics", {}).get("waste_rate"),
        },
        "performance": {
            "p95_duration_ms": ops.get("traffic", {}).get("p95_duration_ms"),
            "p95_queue_ms": ops.get("traffic", {}).get("p95_queue_ms"),
            "cpu_seconds_per_success": ops.get("traffic", {}).get("cpu_seconds_per_success"),
            "inference_minutes": ops.get("traffic", {}).get("inference_minutes"),
            **{k: v for k, v in (ops.get("resources") or {}).items() if not isinstance(v, (dict, list))},
        },
        "support": {
            "open": sum(int(tickets.get(x, 0)) for x in ("open", "in_progress", "waiting_user", "waiting_admin")),
            "waiting_admin": int(tickets.get("open", 0)) + int(tickets.get("waiting_admin", 0)),
            "by_status": {str(k): int(v) for k, v in tickets.items()},
        },
        "integrations": integrations,
        "metrika_goals": integrations.get("metrika", {}).get("goal_catalog", []),
    }


PAGE = r'''<!doctype html><html lang="ru"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>X1 Admin · Integrations</title><style nonce="__NONCE__">
:root{--bg:#090b10;--panel:#111620;--line:#29313d;--text:#f4f6fa;--muted:#94a0b1;--ok:#65d392;--bad:#ff8080;--accent:#d34747}*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--text);font:14px/1.5 Inter,system-ui,Arial,sans-serif}.top{padding:12px max(14px,calc((100% - 1120px)/2));display:flex;gap:8px;align-items:center;border-bottom:1px solid var(--line)}.top a{color:var(--text);text-decoration:none;border:1px solid var(--line);padding:8px 11px;border-radius:9px}.brand{font-weight:850}.grow{flex:1}.wrap{width:min(1120px,100%);margin:auto;padding:18px 14px 60px}.grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:12px}.card{background:var(--panel);border:1px solid var(--line);border-radius:13px;padding:14px}.card h2{margin:0 0 9px;font-size:17px}.field{display:grid;gap:5px;margin:9px 0}.field span,.muted{color:var(--muted)}input,select{width:100%;padding:9px;border:1px solid var(--line);border-radius:8px;background:#0c1118;color:var(--text)}label.check{display:flex;gap:8px;align-items:center;margin:8px 0}label.check input{width:auto}button{border:1px solid var(--line);border-radius:9px;padding:9px 12px;background:#171d27;color:#eef2f8;cursor:pointer}.primary{background:var(--accent);border-color:transparent}.row{display:flex;gap:8px;align-items:center;flex-wrap:wrap}.status{margin:10px 0;min-height:22px}.ok{color:var(--ok)}.bad{color:var(--bad)}code{overflow-wrap:anywhere}.goals{display:grid;gap:5px}.goal{padding:7px 0;border-top:1px solid var(--line)}@media(max-width:760px){.grid{grid-template-columns:1fr}.top{padding:10px}.wrap{padding:12px 10px 45px}}
</style></head><body><header class="top"><div class="brand">X1 Admin · Integrations</div><div class="grow"></div><a href="/admin">Control Center</a><a href="/admin/support">Support</a></header><main class="wrap"><section class="card"><div class="row"><input id="token" type="password" autocomplete="off" placeholder="Admin Bearer token"><button class="primary" id="connect">Открыть</button><button id="save">Сохранить</button></div><div class="status" id="status">Секреты никогда не возвращаются браузеру; пустое поле секрета сохраняет прежнее значение.</div></section><div class="grid" style="margin-top:12px"><section class="card"><h2>Публичный адрес и SMTP</h2><div class="field"><span>Public base URL (HTTPS)</span><input id="base" placeholder="https://ai.example.ru"></div><label class="check"><input id="smtp-enabled" type="checkbox"> SMTP включён</label><div class="field"><span>SMTP host</span><input id="smtp-host"></div><div class="row"><div class="field"><span>Port</span><input id="smtp-port" type="number" min="1" max="65535"></div><div class="field"><span>Username</span><input id="smtp-user"></div></div><div class="field"><span>Password / app password</span><input id="smtp-pass" type="password" autocomplete="new-password" placeholder="оставьте пустым, чтобы не менять"></div><div class="field"><span>From email</span><input id="smtp-from" type="email"></div><div class="field"><span>From name</span><input id="smtp-name"></div><label class="check"><input id="smtp-tls" type="checkbox"> STARTTLS</label><label class="check"><input id="smtp-ssl" type="checkbox"> SSL/TLS сразу</label><label class="check"><input id="verify-required" type="checkbox"> Требовать подтверждение email при регистрации</label><button id="smtp-test">Отправить тест админу</button><div id="smtp-state" class="muted"></div></section><section class="card"><h2>Яндекс Метрика</h2><label class="check"><input id="metrika-enabled" type="checkbox"> Включить Метрику</label><div class="field"><span>ID счётчика</span><input id="metrika-id" inputmode="numeric"></div><label class="check"><input id="metrika-webvisor" type="checkbox"> Webvisor</label><p class="muted">События передаются через reachGoal. UserID связывается только с внутренним UUID аккаунта; email/текст чатов в Метрику не отправляются.</p><div class="goals" id="goals"></div></section><section class="card"><h2>ЮMoney</h2><label class="check"><input id="ym-enabled" type="checkbox"> Принимать оплату через кошелёк ЮMoney</label><div class="field"><span>Номер кошелька receiver</span><input id="ym-receiver"></div><div class="field"><span>Секрет HTTP-уведомлений</span><input id="ym-secret" type="password" autocomplete="new-password" placeholder="оставьте пустым, чтобы не менять"></div><p class="muted">URL уведомлений: <code id="ym-url">—</code></p><div id="ym-state" class="muted"></div></section><section class="card"><h2>ЮKassa</h2><label class="check"><input id="yk-enabled" type="checkbox"> Принимать оплату через ЮKassa</label><div class="field"><span>shopId</span><input id="yk-shop"></div><div class="field"><span>Secret key</span><input id="yk-secret" type="password" autocomplete="new-password" placeholder="оставьте пустым, чтобы не менять"></div><p class="muted">Webhook: <code id="yk-url">—</code></p><div class="field"><span>Провайдер по умолчанию</span><select id="provider"><option value="yookassa">ЮKassa</option><option value="yoomoney">ЮMoney</option></select></div><div id="yk-state" class="muted"></div></section></div></main><script nonce="__NONCE__">
const $=id=>document.getElementById(id);$('token').value=sessionStorage.getItem('x1AdminToken')||'';let snapshot=null;async function api(path,opts={}){const t=sessionStorage.getItem('x1AdminToken')||'';if(!t)throw new Error('Введите admin token');const h={...(opts.headers||{}),Authorization:'Bearer '+t};if(opts.body)h['Content-Type']='application/json';const r=await fetch(path,{...opts,headers:h,credentials:'omit'});let d=null;try{d=await r.json()}catch{}if(!r.ok)throw new Error(typeof d?.detail==='string'?d.detail:'HTTP '+r.status);return d}function goalRows(rows){const root=$('goals');root.replaceChildren();for(const g of rows||[]){const e=document.createElement('div');e.className='goal';const c=document.createElement('code');c.textContent=g.id;const m=document.createElement('div');m.className='muted';m.textContent=g.meaning;e.append(c,m);root.append(e)}}function render(d){snapshot=d;$('base').value=d.public_base_url||'';$('smtp-enabled').checked=!!d.smtp?.enabled;$('smtp-host').value=d.smtp?.host||'';$('smtp-port').value=d.smtp?.port||587;$('smtp-user').value=d.smtp?.username||'';$('smtp-from').value=d.smtp?.from_email||'';$('smtp-name').value=d.smtp?.from_name||'X1 AI';$('smtp-tls').checked=!!d.smtp?.use_tls;$('smtp-ssl').checked=!!d.smtp?.use_ssl;$('verify-required').checked=!!d.auth_email_verification_required;$('metrika-enabled').checked=!!d.metrika?.enabled;$('metrika-id').value=d.metrika?.counter_id||'';$('metrika-webvisor').checked=!!d.metrika?.webvisor;$('ym-enabled').checked=!!d.payments?.yoomoney?.enabled;$('ym-receiver').value=d.payments?.yoomoney?.receiver||'';$('yk-enabled').checked=!!d.payments?.yookassa?.enabled;$('yk-shop').value=d.payments?.yookassa?.shop_id||'';$('provider').value=d.payments?.default_provider||'yookassa';$('smtp-state').textContent='SMTP: '+(d.smtp?.ready?'готов':'не готов')+(d.smtp?.password_set?' · пароль сохранён':'');$('ym-state').textContent='ЮMoney: '+(d.payments?.yoomoney?.ready?'готов':'не готов')+(d.payments?.yoomoney?.secret_set?' · secret сохранён':'');$('yk-state').textContent='ЮKassa: '+(d.payments?.yookassa?.ready?'готов':'не готов')+(d.payments?.yookassa?.secret_set?' · secret сохранён':'');const base=(d.public_base_url||'').replace(/\/$/,'');$('ym-url').textContent=base?base+d.payments.yoomoney.notification_path:'задайте Public base URL';$('yk-url').textContent=base?base+d.payments.yookassa.webhook_path:'задайте Public base URL';goalRows(d.metrika?.goal_catalog)}async function load(){snapshot=await api('/v1/admin/integrations');render(snapshot);$('status').textContent='Настройки загружены.'}function payload(){const p={public_base_url:$('base').value.trim(),smtp_enabled:$('smtp-enabled').checked,smtp_host:$('smtp-host').value.trim(),smtp_port:Number($('smtp-port').value||587),smtp_username:$('smtp-user').value.trim(),smtp_from_email:$('smtp-from').value.trim(),smtp_from_name:$('smtp-name').value.trim(),smtp_use_tls:$('smtp-tls').checked,smtp_use_ssl:$('smtp-ssl').checked,auth_email_verification_required:$('verify-required').checked,metrika_enabled:$('metrika-enabled').checked,metrika_counter_id:$('metrika-id').value.trim(),metrika_webvisor:$('metrika-webvisor').checked,yoomoney_enabled:$('ym-enabled').checked,yoomoney_receiver:$('ym-receiver').value.trim(),yookassa_enabled:$('yk-enabled').checked,yookassa_shop_id:$('yk-shop').value.trim(),payment_default_provider:$('provider').value};if($('smtp-pass').value)p.smtp_password=$('smtp-pass').value;if($('ym-secret').value)p.yoomoney_notification_secret=$('ym-secret').value;if($('yk-secret').value)p.yookassa_secret=$('yk-secret').value;return p}$('connect').onclick=()=>{sessionStorage.setItem('x1AdminToken',$('token').value.trim());load().catch(e=>$('status').textContent=e.message)};$('save').onclick=async()=>{try{$('status').textContent='Сохраняю…';const d=await api('/v1/admin/integrations',{method:'PUT',body:JSON.stringify(payload())});$('smtp-pass').value='';$('ym-secret').value='';$('yk-secret').value='';render(d);$('status').textContent='Сохранено.'}catch(e){$('status').textContent=e.message}};$('smtp-test').onclick=async()=>{try{$('status').textContent='Отправляю тест…';const d=await api('/v1/admin/integrations/smtp/test',{method:'POST',body:JSON.stringify({})});$('status').textContent='Тестовое письмо отправлено: '+d.recipient}catch(e){$('status').textContent=e.message}};if(sessionStorage.getItem('x1AdminToken'))load().catch(e=>$('status').textContent=e.message);
</script></body></html>'''


@router.get("/admin/integrations", response_class=HTMLResponse, include_in_schema=False)
def integrations_page() -> HTMLResponse:
    nonce = secrets.token_urlsafe(18)
    response = HTMLResponse(PAGE.replace("__NONCE__", nonce))
    response.headers.update({
        "Content-Security-Policy": f"default-src 'none'; base-uri 'none'; frame-ancestors 'none'; form-action 'none'; style-src 'nonce-{nonce}'; script-src 'nonce-{nonce}'; connect-src 'self'",
        "Cache-Control": "no-store",
        "Pragma": "no-cache",
        "Referrer-Policy": "no-referrer",
        "X-Content-Type-Options": "nosniff",
        "X-Frame-Options": "DENY",
    })
    return response
