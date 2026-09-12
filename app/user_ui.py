from __future__ import annotations

from fastapi import APIRouter
from fastapi.responses import HTMLResponse

from app.user_workspace_base import workspace as _base_workspace

router = APIRouter(tags=["user-workspace"])


def _replace_once(document: str, old: str, new: str, label: str) -> str:
    count = document.count(old)
    if count != 1:
        raise RuntimeError(f"Sprint 70 workspace template drift for {label}: expected 1 marker, found {count}")
    return document.replace(old, new, 1)


@router.get("/app", response_class=HTMLResponse, include_in_schema=False)
def workspace() -> HTMLResponse:
    base = _base_workspace()
    document = base.body.decode("utf-8")

    document = _replace_once(
        document,
        "</style></head><body>",
        ".account-section{margin-top:20px}.account-plan{display:flex;flex-direction:column;gap:8px}.account-plan.current{border-color:#566174}.plan-price{font-size:25px;font-weight:850;letter-spacing:-.03em}.plan-price small{font-size:12px;font-weight:500;color:var(--muted)}.account-progress{width:100%;height:10px;accent-color:var(--accent);margin:9px 0}.billing-list{display:grid;gap:7px;margin-top:10px}.billing-row{border-top:1px solid var(--line);padding-top:8px}.billing-row b{display:block}.billing-state{min-height:20px;color:var(--muted);margin-top:10px}</style></head><body>",
        "account styles",
    )

    old_api = '<p class="muted">Self-service API Console развивается поверх существующих scopes, rate limits и telemetry без отдельного API-движка.</p>'
    new_api = '<div class="formrow"><button class="primary" id="api-console-open">Открыть API Console</button></div><p class="muted">Self-service API Console использует существующие scopes, rate limits, contexts и telemetry без отдельного API-движка.</p>'
    document = _replace_once(document, old_api, new_api, "API Console entry")
    document = _replace_once(
        document,
        "keys.length?keys.length+' активных/исторических ключей. Управление ключами будет вынесено в отдельную API Console.':'API-ключей пока нет.'",
        "keys.length?keys.length+' активных/исторических ключей. Откройте API Console для создания, ротации и telemetry.':'API-ключей пока нет. Создать первый можно в API Console.'",
        "API Console status copy",
    )

    old_account = '<section class="view" id="view-account"><div class="content"><h1>Аккаунт</h1><p class="lead">Профиль, текущий ресурс и переносимость данных.</p><div class="cards"><div class="card"><h2>Профиль</h2><div id="account-profile" class="muted">Загружаю…</div></div><div class="card"><h2>Ресурс</h2><div id="account-budget" class="muted">Загружаю…</div></div></div><div class="formrow"><button class="secondary" id="account-export">Экспортировать мои данные</button><button class="secondary" id="onboarding-open">Начало работы</button></div></div></section>'
    new_account = '<section class="view" id="view-account"><div class="content"><h1>Аккаунт и тариф</h1><p class="lead">Профиль, текущий план, понятный лимит запросов и управление подпиской без скрытых списаний.</p><div class="cards"><div class="card"><h2>Профиль</h2><div id="account-profile" class="muted">Загружаю…</div></div><div class="card"><h2>Подписка</h2><div id="account-subscription" class="muted">Загружаю…</div><div class="formrow"><button class="secondary" id="subscription-cancel" disabled>Отключить продление</button><button class="secondary" id="subscription-resume" disabled>Возобновить</button></div></div><div class="card"><h2>Лимит запросов</h2><div id="account-request-units" class="muted">Загружаю…</div><progress class="account-progress" id="account-request-progress" value="0" max="100"></progress></div><div class="card"><h2>Вычислительный бюджет</h2><div id="account-budget" class="muted">Загружаю…</div></div><div class="card"><h2>Измеренный ресурс</h2><div id="account-commerce-usage" class="muted">Загружаю…</div><progress class="account-progress" id="account-resource-progress" value="0" max="100"></progress></div></div><div class="account-section"><h2>Тарифы</h2><p class="muted">Fast = 1 единица, Work = 2, Deep = 4. Дневной лимит защищает очередь от резкого всплеска, месячный — ваш тариф.</p><div class="cards" id="account-plans"></div><div class="billing-state" id="billing-state"></div></div><div class="account-section cards"><div class="card"><h2>Последние checkout</h2><div class="billing-list" id="account-checkouts"></div></div><div class="card"><h2>Платежи</h2><div class="billing-list" id="account-payments"></div></div></div><div class="formrow"><button class="secondary" id="account-export">Экспортировать мои данные</button><button class="secondary" id="onboarding-open">Начало работы</button></div></div></section>'
    document = _replace_once(document, old_account, new_account, "account plan surface")

    document = _replace_once(
        document,
        "meCache=null,workspaceProjectId=null;const steps=",
        "meCache=null,workspaceProjectId=null,accountSubscription=null,accountUsage=null,accountCheckoutKeys={};const steps=",
        "account state",
    )
    document = _replace_once(
        document,
        "if(name==='account'){refreshBudget();renderAccount()}",
        "if(name==='account')loadAccountPlan()",
        "account view loader",
    )

    old_render = "function renderAccount(){if(!meCache)return;$('account-profile').textContent=(meCache.display_name||'Без имени')+' · '+meCache.email}"
    new_render = r'''function renderAccount(){if(!meCache)return;$('account-profile').textContent=(meCache.display_name||'Без имени')+' · '+meCache.email}
function moneyMinor(value,currency){return (Number(value||0)/100).toLocaleString('ru-RU',{minimumFractionDigits:0,maximumFractionDigits:2})+' '+String(currency||'RUB')}
function billingDate(value){return value?new Date(value).toLocaleString('ru-RU'):'—'}
function checkoutKey(plan){if(!accountCheckoutKeys[plan]){let id='';try{id=crypto.randomUUID().replaceAll('-','')}catch(_e){id=Date.now().toString(36)+Math.random().toString(36).slice(2)}accountCheckoutKeys[plan]='account_'+id}return accountCheckoutKeys[plan]}
function renderSubscription(sub,usage){accountSubscription=sub;accountUsage=usage;const box=$('account-subscription'),cancel=$('subscription-cancel'),resume=$('subscription-resume');cancel.disabled=true;resume.disabled=true;if(!sub){box.textContent='Текущий план: '+String((usage&&usage.plan)||'free').toUpperCase()+'. Платной подписки нет.';return}let text=String(sub.plan||'free').toUpperCase()+' · '+String(sub.status||'unknown')+' · до '+billingDate(sub.current_period_end);if(sub.cancel_at_period_end)text+=' · продление отключено';box.textContent=text;if(sub.status==='active'){cancel.disabled=Boolean(sub.cancel_at_period_end);resume.disabled=!sub.cancel_at_period_end}}
function renderRequestUnits(unitUsage){const box=$('account-request-units'),bar=$('account-request-progress');if(!unitUsage){box.textContent='Данные лимита запросов недоступны.';bar.value=0;return}const used=Math.max(0,Number(unitUsage.monthly_request_units_used||0)),limit=Math.max(0,Number(unitUsage.monthly_request_units_limit||0)),remaining=Math.max(0,Number(unitUsage.monthly_request_units_remaining||0)),dailyRemaining=Math.max(0,Number(unitUsage.daily_request_units_remaining||0)),dailyLimit=Math.max(0,Number(unitUsage.daily_request_units_limit||0));box.textContent='Использовано '+used.toLocaleString('ru-RU')+' из '+limit.toLocaleString('ru-RU')+' ед. за месяц · осталось '+remaining.toLocaleString('ru-RU')+' · сегодня '+dailyRemaining.toLocaleString('ru-RU')+' из '+dailyLimit.toLocaleString('ru-RU');bar.value=limit?Math.min(100,used/limit*100):0}
function renderCommerceUsage(usage){const total=Math.max(0,Number(usage&&usage.plan_resource_budget_microunits||0)),spent=Math.max(0,Number(usage&&usage.total_cost_microunits||0)),remaining=Math.max(0,Number(usage&&usage.remaining_resource_microunits||0)),percent=total?Math.min(100,spent/total*100):0;$('account-commerce-usage').textContent='Использовано '+spent.toLocaleString('ru-RU')+' · осталось '+remaining.toLocaleString('ru-RU')+' из '+total.toLocaleString('ru-RU')+' ресурсных единиц';$('account-resource-progress').value=percent}
function renderPlanCatalog(plans,usage,sub){const root=$('account-plans'),current=String(usage&&usage.plan||'free');root.replaceChildren();for(const plan of plans){const card=document.createElement('div');card.className='card account-plan'+(plan.name===current?' current':'');const title=document.createElement('h3');title.textContent=String(plan.name||'').toUpperCase();const price=document.createElement('div');price.className='plan-price';price.textContent=plan.name==='free'?'0 ₽':moneyMinor(plan.amount_minor,plan.currency);if(plan.name!=='free'){const small=document.createElement('small');small.textContent=' / '+Number(plan.period_days||30)+' дней';price.append(small)}const limits=document.createElement('div');limits.className='muted';limits.textContent=Number(plan.monthly_request_units||0).toLocaleString('ru-RU')+' ед./мес · '+Number(plan.daily_request_units||0).toLocaleString('ru-RU')+' ед./день · 1 запрос к модели одновременно';card.append(title,price,limits);if(plan.name===current){const badge=document.createElement('span');badge.className='status ok';badge.textContent='Текущий';card.append(badge)}else if(plan.purchase_enabled){const button=document.createElement('button');button.className='primary';button.textContent='Выбрать '+String(plan.name).toUpperCase();button.onclick=()=>startPlanCheckout(plan.name,button);card.append(button)}root.append(card)}if(sub&&sub.status==='active')$('billing-state').textContent='Смена платного тарифа применяется только после подтверждённого платежа. Автоматический перерасчёт остатка периода не выполняется.'}
function renderBillingRows(id,rows,kind){const root=$(id);root.replaceChildren();if(!rows||!rows.length){const empty=document.createElement('div');empty.className='muted';empty.textContent=kind==='checkout'?'Checkout пока нет.':'Подтверждённых billing-событий пока нет.';root.append(empty);return}for(const row of rows.slice(0,8)){const item=document.createElement('div');item.className='billing-row';const title=document.createElement('b');if(kind==='checkout')title.textContent=String(row.plan||'').toUpperCase()+' · '+moneyMinor(row.amount_minor,row.currency)+' · '+String(row.status||'');else title.textContent=String(row.kind||'payment')+' · '+moneyMinor(row.amount_minor,row.currency)+' · '+String(row.status||'');const meta=document.createElement('div');meta.className='muted';meta.textContent=(kind==='checkout'?String(row.id||''):String(row.provider||''))+' · '+billingDate(row.created_at);item.append(title,meta);root.append(item)}}
async function loadAccountPlan(){renderAccount();$('billing-state').textContent='Загружаю тариф и подписку…';try{const [usage,plans,sub,checkouts,payments,unitUsage]=await Promise.all([api('/v1/commerce/usage',{},20000),api('/v1/commerce/billing/plans',{},20000),api('/v1/commerce/billing/subscription',{},20000),api('/v1/commerce/billing/checkouts?limit=20',{},20000),api('/v1/commerce/billing/payments?limit=20',{},20000),api('/v1/usage/summary',{},20000),refreshBudget()]);$('billing-state').textContent='';renderSubscription(sub,usage);renderRequestUnits(unitUsage);renderCommerceUsage(usage);renderPlanCatalog(plans,usage,sub);renderBillingRows('account-checkouts',checkouts,'checkout');renderBillingRows('account-payments',payments,'payment');if(!$('billing-state').textContent)$('billing-state').textContent='Данные тарифа актуальны.'}catch(e){$('billing-state').textContent=e.message}}
async function startPlanCheckout(plan,button){button.disabled=true;$('billing-state').textContent='Создаю server-owned checkout…';try{const checkout=await api('/v1/commerce/billing/checkout',{method:'POST',body:JSON.stringify({plan:plan,idempotency_key:checkoutKey(plan)})},30000);$('billing-state').textContent='Checkout '+checkout.id+' создан на '+moneyMinor(checkout.amount_minor,checkout.currency)+'. Доступ изменится только после подтверждения оплаты.';if(checkout.checkout_url){const target=new URL(checkout.checkout_url,location.href);if(!['http:','https:'].includes(target.protocol))throw new Error('Платёжный URL отклонён браузером.');location.assign(target.href);return}await loadAccountPlan()}catch(e){$('billing-state').textContent=e.message}finally{button.disabled=false}}
async function changeSubscription(action){const button=$(action==='cancel'?'subscription-cancel':'subscription-resume');button.disabled=true;$('billing-state').textContent=action==='cancel'?'Отключаю продление…':'Возобновляю продление…';try{await api('/v1/commerce/billing/subscription/'+action,{method:'POST'},20000);await loadAccountPlan();$('billing-state').textContent=action==='cancel'?'Автопродление отключено. Оплаченный период остаётся активным.':'Автопродление возобновлено.'}catch(e){$('billing-state').textContent=e.message}finally{button.disabled=false}}'''
    document = _replace_once(document, old_render, new_render, "account billing logic")

    document = _replace_once(
        document,
        "$('account-export').onclick=exportAccount;",
        "$('api-console-open').onclick=()=>location.href='/v1/commerce/console';$('subscription-cancel').onclick=()=>changeSubscription('cancel');$('subscription-resume').onclick=()=>changeSubscription('resume');$('account-export').onclick=exportAccount;",
        "account event bindings",
    )

    response = HTMLResponse(document, status_code=base.status_code)
    for key, value in base.headers.items():
        if key.lower() not in {"content-length", "content-type"}:
            response.headers[key] = value
    return response
