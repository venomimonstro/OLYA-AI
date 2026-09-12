from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

from sqlalchemy import select

from app.models import BillingCheckout, BillingSubscription, PaymentRecord, UserQuota
from scripts.billing_contract_audit import audit as audit_billing


def _checkout(client, headers, plan="x1", key="checkout-request-0001"):
    response=client.post("/v1/commerce/billing/checkout",headers=headers,json={"plan":plan,"idempotency_key":key})
    assert response.status_code==201,response.text
    return response.json()


def _settle(client,user_id,checkout,secret,*,event="evt-payment-1",idem="provider-payment-1",kind="payment",amount=None):
    return client.post("/v1/commerce/payments/ingest",headers={"X-X1-Payment-Secret":secret},json={"provider":"test-provider","provider_event_id":event,"idempotency_key":idem,"kind":kind,"amount_minor":checkout["amount_minor"] if amount is None else amount,"currency":checkout["currency"],"user_id":user_id,"metadata":{"checkout_id":checkout["id"]}})


def test_checkout_is_idempotent_and_server_priced(register_user,client):
    _,headers=register_user("sprint69-checkout@example.com")
    plans=client.get("/v1/commerce/billing/plans",headers=headers)
    assert plans.status_code==200
    x1=next(x for x in plans.json() if x["name"]=="x1")
    first=_checkout(client,headers)
    second=_checkout(client,headers)
    assert first["id"]==second["id"]
    assert first["amount_minor"]==x1["amount_minor"]
    assert first["currency"]==x1["currency"]
    conflict=client.post("/v1/commerce/billing/checkout",headers=headers,json={"plan":"pro","idempotency_key":"checkout-request-0001"})
    assert conflict.status_code==409


def test_verified_payment_activates_plan_once(register_user,client,db_session):
    user,headers=register_user("sprint69-paid@example.com")
    settings=client.app.state.settings; old=settings.payment_ingest_secret; settings.payment_ingest_secret="sprint69-secret"
    try:
        checkout=_checkout(client,headers,key="checkout-paid-0001")
        paid=_settle(client,user["user_id"],checkout,settings.payment_ingest_secret)
        assert paid.status_code==200,paid.text
        sub=client.get("/v1/commerce/billing/subscription",headers=headers).json()
        assert sub["status"]=="active" and sub["plan"]=="x1"
        period_end=sub["current_period_end"]
        assert client.get("/v1/commerce/usage",headers=headers).json()["plan"]=="x1"
        replay=_settle(client,user["user_id"],checkout,settings.payment_ingest_secret)
        assert replay.status_code==200
        assert client.get("/v1/commerce/billing/subscription",headers=headers).json()["current_period_end"]==period_end
        assert db_session.query(PaymentRecord).count()==1
        duplicate=_settle(client,user["user_id"],checkout,settings.payment_ingest_secret,event="evt-payment-2",idem="provider-payment-2")
        assert duplicate.status_code==409
        assert db_session.query(PaymentRecord).count()==1
    finally: settings.payment_ingest_secret=old


def test_checkout_rejects_price_tampering_atomically(register_user,client,db_session):
    user,headers=register_user("sprint69-tamper@example.com")
    settings=client.app.state.settings; old=settings.payment_ingest_secret; settings.payment_ingest_secret="sprint69-secret"
    try:
        checkout=_checkout(client,headers,key="checkout-tamper-0001")
        bad=_settle(client,user["user_id"],checkout,settings.payment_ingest_secret,amount=checkout["amount_minor"]+1)
        assert bad.status_code==409
        row=db_session.get(BillingCheckout,checkout["id"])
        assert row.status=="pending" and row.payment_record_id is None
        assert db_session.query(PaymentRecord).count()==0
        assert client.get("/v1/commerce/billing/subscription",headers=headers).json() is None
    finally: settings.payment_ingest_secret=old


def test_cancel_resume_refund_and_replays_are_idempotent(register_user,client,db_session):
    user,headers=register_user("sprint69-refund@example.com")
    settings=client.app.state.settings; old=settings.payment_ingest_secret; settings.payment_ingest_secret="sprint69-secret"
    try:
        checkout=_checkout(client,headers,key="checkout-refund-0001")
        assert _settle(client,user["user_id"],checkout,settings.payment_ingest_secret).status_code==200
        cancelled=client.post("/v1/commerce/billing/subscription/cancel",headers=headers)
        assert cancelled.status_code==200 and cancelled.json()["cancel_at_period_end"] is True
        resumed=client.post("/v1/commerce/billing/subscription/resume",headers=headers)
        assert resumed.status_code==200 and resumed.json()["cancel_at_period_end"] is False
        refund=_settle(client,user["user_id"],checkout,settings.payment_ingest_secret,event="evt-refund-1",idem="provider-refund-1",kind="refund")
        assert refund.status_code==200,refund.text
        assert client.get("/v1/commerce/billing/subscription",headers=headers).json()["status"]=="refunded"
        assert client.get("/v1/commerce/usage",headers=headers).json()["plan"]=="free"
        assert _settle(client,user["user_id"],checkout,settings.payment_ingest_secret,event="evt-refund-1",idem="provider-refund-1",kind="refund").status_code==200
        assert db_session.query(PaymentRecord).count()==2
        second_refund=_settle(client,user["user_id"],checkout,settings.payment_ingest_secret,event="evt-refund-2",idem="provider-refund-2",kind="refund")
        assert second_refund.status_code==409
        assert db_session.query(PaymentRecord).count()==2
        replay=_settle(client,user["user_id"],checkout,settings.payment_ingest_secret)
        assert replay.status_code==200
        assert client.get("/v1/commerce/billing/subscription",headers=headers).json()["status"]=="refunded"
        assert client.get("/v1/commerce/usage",headers=headers).json()["plan"]=="free"
    finally: settings.payment_ingest_secret=old


def test_expired_subscription_is_reconciled_on_quota_read(register_user,client,db_session):
    user,headers=register_user("sprint69-expiry@example.com")
    settings=client.app.state.settings; old=settings.payment_ingest_secret; settings.payment_ingest_secret="sprint69-secret"
    try:
        checkout=_checkout(client,headers,key="checkout-expiry-0001")
        assert _settle(client,user["user_id"],checkout,settings.payment_ingest_secret).status_code==200
        sub=db_session.scalar(select(BillingSubscription).where(BillingSubscription.user_id==user["user_id"]))
        sub.current_period_end=datetime.now(timezone.utc)-timedelta(seconds=1); db_session.commit()
        assert client.get("/v1/commerce/usage",headers=headers).json()["plan"]=="free"
        db_session.refresh(sub); assert sub.status=="expired"
        assert db_session.get(UserQuota,user["user_id"]).plan=="free"
    finally: settings.payment_ingest_secret=old


def test_billing_contract_is_release_gated():
    report=audit_billing(); assert report["status"]=="passed",report["errors"]
    runner=(Path(__file__).resolve().parents[1]/"scripts/run_full_regression.py").read_text("utf-8")
    assert "scripts.billing_contract_audit" in runner
