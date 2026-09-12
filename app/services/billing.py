from __future__ import annotations

from datetime import datetime, timedelta, timezone
from urllib.parse import quote, urlsplit

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models import BillingCheckout, BillingSubscription, PaymentRecord, User, UserQuota
from app.services.measured_plans import apply_runtime_plan_to_quota, runtime_plan_catalog

BILLABLE_PLANS = ("x1", "pro", "max", "business")

class BillingError(RuntimeError): pass
class BillingValidationError(BillingError): pass
class BillingConflictError(BillingError): pass
class BillingNotFoundError(BillingError): pass

def utcnow(): return datetime.now(timezone.utc)
def _aware(value): return None if value is None else value if value.tzinfo else value.replace(tzinfo=timezone.utc)

def billing_price_minor(settings, plan: str) -> int:
    if plan == "free": return 0
    if plan not in BILLABLE_PLANS: raise BillingValidationError("Unknown billing plan")
    return max(0, int(getattr(settings, f"billing_price_{plan}_minor", 0)))

def billing_plan_catalog(db: Session, settings) -> list[dict]:
    policies={str(x["name"]):x for x in runtime_plan_catalog(db,settings)}; result=[]
    for name in ("free",*BILLABLE_PLANS):
        policy=policies.get(name)
        if not policy: continue
        amount=billing_price_minor(settings,name)
        result.append({
            "name":name,
            "amount_minor":amount,
            "currency":str(settings.billing_currency).upper(),
            "period_days":max(1,int(settings.billing_period_days)),
            "purchase_enabled":name!="free" and amount>0,
            "monthly_cpu_seconds":int(policy.get("monthly_cpu_seconds") or 0),
            "resource_budget_microunits":int(policy.get("resource_budget_microunits") or 0),
            "monthly_request_units":int(policy.get("monthly_request_units") or 0),
            "daily_request_units":int(policy.get("daily_request_units") or 0),
            "request_unit_weights":{str(k):int(v) for k,v in (policy.get("request_unit_weights") or {}).items()},
            "max_concurrent_inference":int(policy.get("max_concurrent_inference") or 1),
            "max_concurrent_jobs":int(policy.get("max_concurrent_jobs") or 1),
        })
    return result

def checkout_url(settings, checkout_id: str) -> str | None:
    template=str(getattr(settings,"billing_checkout_url_template","") or "").strip()
    if not template or "{checkout_id}" not in template: return None
    url=template.replace("{checkout_id}",quote(checkout_id,safe=""))
    try: parsed=urlsplit(url)
    except ValueError: return None
    if parsed.scheme not in {"http","https"} or not parsed.netloc or parsed.username or parsed.password: return None
    if str(getattr(settings,"env","development")).lower() in {"production","prod","stable"} and parsed.scheme!="https": return None
    return url

def _by_identity(db,user_id,key): return db.scalar(select(BillingCheckout).where(BillingCheckout.user_id==user_id,BillingCheckout.idempotency_key==key))

def create_checkout(db:Session,user:User,settings,*,plan:str,idempotency_key:str)->BillingCheckout:
    plan=plan.strip().lower(); key=idempotency_key.strip()
    if plan not in BILLABLE_PLANS: raise BillingValidationError("Only paid plans can be purchased")
    if plan not in {str(x["name"]) for x in runtime_plan_catalog(db,settings)}: raise BillingValidationError("Plan is not available")
    amount=billing_price_minor(settings,plan)
    if amount<=0: raise BillingConflictError("Plan purchasing is disabled")
    existing=_by_identity(db,user.id,key)
    if existing:
        if existing.plan!=plan: raise BillingConflictError("Idempotency key is bound to another plan")
        return existing
    now=utcnow(); row=BillingCheckout(user_id=user.id,plan=plan,amount_minor=amount,currency=str(settings.billing_currency).upper(),status="pending",idempotency_key=key,expires_at=now+timedelta(minutes=max(5,int(settings.billing_checkout_ttl_minutes))))
    try:
        with db.begin_nested(): db.add(row); db.flush()
    except IntegrityError:
        winner=_by_identity(db,user.id,key)
        if not winner: raise
        if winner.plan!=plan: raise BillingConflictError("Idempotency key is bound to another plan")
        return winner
    return row

def expire_checkouts(db,user_id,now=None):
    current=_aware(now) or utcnow(); changed=0
    for row in db.scalars(select(BillingCheckout).where(BillingCheckout.user_id==user_id,BillingCheckout.status=="pending")).all():
        if (_aware(row.expires_at) or current)<=current: row.status="expired"; row.updated_at=current; changed+=1
    return changed

def list_checkouts(db,user_id,limit=100):
    expire_checkouts(db,user_id)
    return list(db.scalars(select(BillingCheckout).where(BillingCheckout.user_id==user_id).order_by(BillingCheckout.created_at.desc()).limit(limit)).all())

def get_subscription(db,user_id,lock=False):
    query=select(BillingSubscription).where(BillingSubscription.user_id==user_id)
    return db.scalar(query.with_for_update() if lock else query)

def reconcile_user_subscription(db:Session,user:User,settings,*,quota:UserQuota|None=None):
    sub=get_subscription(db,user.id)
    if not sub:return None
    now=utcnow(); end=_aware(sub.current_period_end)
    if sub.status=="active" and end and end<=now:
        sub.status="expired"; sub.cancel_at_period_end=False; sub.updated_at=now
        target=quota or db.get(UserQuota,user.id)
        if target is not None and target.plan!="free": apply_runtime_plan_to_quota(db,user,settings,"free")
    elif sub.status=="active":
        target=quota or db.get(UserQuota,user.id)
        if target is None or target.plan!=sub.plan: apply_runtime_plan_to_quota(db,user,settings,sub.plan)
    elif quota is not None and quota.plan!="free": apply_runtime_plan_to_quota(db,user,settings,"free")
    return sub

def cancel_subscription(db,user,settings):
    sub=reconcile_user_subscription(db,user,settings)
    if not sub: raise BillingNotFoundError("Subscription not found")
    if sub.status!="active": raise BillingConflictError("Only active subscription can be cancelled")
    sub.cancel_at_period_end=True; sub.updated_at=utcnow(); return sub

def resume_subscription(db,user,settings):
    sub=reconcile_user_subscription(db,user,settings)
    if not sub: raise BillingNotFoundError("Subscription not found")
    if sub.status!="active": raise BillingConflictError("Only active subscription can be resumed")
    sub.cancel_at_period_end=False; sub.updated_at=utcnow(); return sub

def _payment_checkout(db,record):
    checkout_id=str((record.metadata_json or {}).get("checkout_id") or "").strip()
    if not checkout_id:return None
    row=db.scalar(select(BillingCheckout).where(BillingCheckout.id==checkout_id).with_for_update())
    if not row: raise BillingConflictError("Billing checkout does not exist")
    return row

def apply_payment_record(db:Session,record:PaymentRecord,settings):
    checkout=_payment_checkout(db,record)
    if checkout is None:return None
    if not record.user_id or record.user_id!=checkout.user_id: raise BillingConflictError("Payment user mismatch")
    if int(record.amount_minor)!=int(checkout.amount_minor): raise BillingConflictError("Payment amount mismatch")
    if str(record.currency).upper()!=str(checkout.currency).upper(): raise BillingConflictError("Payment currency mismatch")
    now=utcnow()
    if record.kind=="payment":
        if checkout.status=="refunded":
            if checkout.payment_record_id==record.id:return get_subscription(db,checkout.user_id,True)
            raise BillingConflictError("Refunded checkout cannot accept another payment")
        if checkout.status=="paid":
            if checkout.payment_record_id==record.id:return get_subscription(db,checkout.user_id,True)
            raise BillingConflictError("Checkout already settled")
        if checkout.status!="pending": raise BillingConflictError("Checkout is not payable")
        if (_aware(checkout.expires_at) or now)<=now: checkout.status="expired"; checkout.updated_at=now; raise BillingConflictError("Checkout expired")
        user=db.get(User,checkout.user_id)
        if not user: raise BillingConflictError("Billing user does not exist")
        sub=get_subscription(db,user.id,True); days=max(1,int(settings.billing_period_days))
        if sub is None:
            sub=BillingSubscription(user_id=user.id,plan=checkout.plan,status="active",cancel_at_period_end=False,current_period_start=now,current_period_end=now+timedelta(days=days),last_payment_record_id=record.id); db.add(sub); db.flush()
        else:
            old_end=_aware(sub.current_period_end); extend=sub.status=="active" and sub.plan==checkout.plan and old_end and old_end>now
            sub.plan=checkout.plan; sub.status="active"; sub.cancel_at_period_end=False; sub.current_period_start=sub.current_period_start if extend else now; sub.current_period_end=(old_end if extend else now)+timedelta(days=days); sub.last_payment_record_id=record.id; sub.updated_at=now
        checkout.status="paid"; checkout.payment_record_id=record.id; checkout.updated_at=now; record.status="applied"; record.reconciled_at=now; apply_runtime_plan_to_quota(db,user,settings,checkout.plan); return sub
    if record.kind=="refund":
        if checkout.status=="refunded":
            if checkout.refund_record_id==record.id:return get_subscription(db,checkout.user_id,True)
            raise BillingConflictError("Checkout already refunded")
        if checkout.status!="paid" or not checkout.payment_record_id: raise BillingConflictError("Checkout is not refundable")
        original=checkout.payment_record_id; sub=get_subscription(db,checkout.user_id,True); checkout.status="refunded"; checkout.refund_record_id=record.id; checkout.updated_at=now; record.status="applied"; record.reconciled_at=now
        if sub and sub.last_payment_record_id==original:
            user=db.get(User,checkout.user_id); sub.status="refunded"; sub.cancel_at_period_end=True; sub.current_period_end=now; sub.updated_at=now
            if user: apply_runtime_plan_to_quota(db,user,settings,"free")
        return sub
    raise BillingValidationError("Unsupported payment kind")

def list_billing_payments(db,user_id,limit=100):
    rows=list(db.scalars(select(PaymentRecord).where(PaymentRecord.user_id==user_id).order_by(PaymentRecord.created_at.desc()).limit(max(limit*3,100))).all())
    return [x for x in rows if str((x.metadata_json or {}).get("checkout_id") or "").strip()][:limit]
