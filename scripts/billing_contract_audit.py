#!/usr/bin/env python3
from __future__ import annotations

import json
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
REQUIRED={
    ("GET","/v1/commerce/billing/plans"),("GET","/v1/commerce/billing/subscription"),
    ("POST","/v1/commerce/billing/checkout"),("GET","/v1/commerce/billing/checkouts"),
    ("POST","/v1/commerce/billing/subscription/cancel"),("POST","/v1/commerce/billing/subscription/resume"),
    ("GET","/v1/commerce/billing/payments"),("POST","/v1/commerce/payments/ingest"),
}

def audit()->dict:
    from app.core.config import get_settings
    from app.db import Base
    from app.main import app
    from app.schemas.commerce import BillingCheckoutCreate
    errors=[]
    routes={(method,str(getattr(route,"path",""))) for route in app.routes for method in set(getattr(route,"methods",set()) or set())}
    for method,path in sorted(REQUIRED-routes):errors.append({"code":"billing_route_missing","method":method,"path":path})
    tables=set(Base.metadata.tables)
    for table in ("billing_subscriptions","billing_checkouts"):
        if table not in tables:errors.append({"code":"billing_table_missing","table":table})
    checkout_table=Base.metadata.tables.get("billing_checkouts")
    if checkout_table is not None and "refund_record_id" not in checkout_table.c:errors.append({"code":"billing_refund_idempotency_column_missing"})
    if set(BillingCheckoutCreate.model_fields)!={"plan","idempotency_key"}:errors.append({"code":"checkout_accepts_untrusted_pricing_fields"})
    settings=get_settings()
    if len(str(settings.billing_currency))!=3:errors.append({"code":"billing_currency_invalid"})
    if not 1<=int(settings.billing_period_days)<=366:errors.append({"code":"billing_period_invalid"})
    if not 5<=int(settings.billing_checkout_ttl_minutes)<=1440:errors.append({"code":"billing_checkout_ttl_invalid"})
    for name in ("x1","pro","max","business"):
        if int(getattr(settings,f"billing_price_{name}_minor",0))<0:errors.append({"code":"billing_price_negative","plan":name})
    service=(ROOT/"app/services/billing.py").read_text("utf-8"); commerce=(ROOT/"app/api/routes/commerce.py").read_text("utf-8"); quota=(ROOT/"app/services/quota.py").read_text("utf-8"); generator=(ROOT/"scripts/generate_orm_models.py").read_text("utf-8")
    requirements={"server_amount_check":"record.amount_minor)!=int(checkout.amount_minor)","server_currency_check":"record.currency).upper()!=str(checkout.currency).upper()","checkout_user_check":"record.user_id!=checkout.user_id","payment_idempotency":"checkout.payment_record_id==record.id","refund_idempotency":"checkout.refund_record_id==record.id","quota_application":"apply_runtime_plan_to_quota"}
    for code,needle in requirements.items():
        if needle not in service:errors.append({"code":code+"_missing"})
    if "apply_payment_record(db,row,request.app.state.settings)" not in commerce:errors.append({"code":"payment_settlement_not_wired"})
    if "reconcile_user_subscription" not in quota:errors.append({"code":"subscription_expiry_not_wired_to_quota"})
    for table in ("billing_checkouts","billing_subscriptions"):
        if f'"{table}"' not in generator:errors.append({"code":"billing_table_not_owned_by_orm_generator","table":table})
    return {"format":"x1-billing-contract-audit-v1","status":"passed" if not errors else "failed","required_routes":[{"method":m,"path":p} for m,p in sorted(REQUIRED)],"errors":errors}

def main()->int:
    result=audit();print(json.dumps(result,ensure_ascii=False,indent=2));return 0 if result["status"]=="passed" else 2

if __name__=="__main__":raise SystemExit(main())
