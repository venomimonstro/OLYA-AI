# X1 Billing / Subscriptions

Sprint 69 добавляет provider-neutral billing поверх существующего `PaymentRecord`. X1 не доверяет цене, валюте или entitlement из браузера либо metadata webhook.

## Поток

```text
session user
 -> POST /v1/commerce/billing/checkout {plan, idempotency_key}
 -> BillingCheckout(server amount/currency/expiry)
 -> external payment provider
 -> POST /v1/commerce/payments/ingest + X-X1-Payment-Secret
 -> PaymentRecord
 -> exact checkout/user/amount/currency validation
 -> BillingSubscription
 -> measured UserQuota
```

Один checkout может быть подтверждён только одним payment record. Повтор того же provider event идемпотентен. Другой payment event для уже оплаченного checkout отклоняется. Refund последнего оплаченного периода снимает entitlement и возвращает Free; replay старого payment после refund не восстанавливает доступ.

## Runtime settings

Все настройки используют префикс `X1_`:

- `BILLING_CURRENCY=RUB`
- `BILLING_PERIOD_DAYS=30`
- `BILLING_CHECKOUT_TTL_MINUTES=30`
- `BILLING_CHECKOUT_URL_TEMPLATE=` — опциональный URL платежного адаптера с `{checkout_id}`; production принимает только HTTPS.
- `BILLING_PRICE_X1_MINOR=30000`
- `BILLING_PRICE_PRO_MINOR=70000`
- `BILLING_PRICE_MAX_MINOR=150000`
- `BILLING_PRICE_BUSINESS_MINOR=400000`
- `PAYMENT_INGEST_SECRET=` — обязательный shared secret внешнего provider adapter для подтверждения payment/refund events.

Суммы задаются в minor units. Цена checkout фиксируется на сервере в момент его создания. Изменение текущей конфигурации цен не меняет уже созданный checkout.

## Provider adapter

Конкретный PSP не встроен в core. Адаптер должен:

1. получить `checkout_id`, amount и currency из server-created checkout;
2. создать платёж у выбранного провайдера;
3. после подтверждения провайдером вызвать `/v1/commerce/payments/ingest`;
4. передать `metadata.checkout_id`, canonical `user_id`, provider event id, exact amount/currency и `X-X1-Payment-Secret`;
5. использовать стабильные provider event/idempotency identifiers при retry.

Нельзя применять тариф напрямую из callback URL, query string или provider metadata. Единственный источник entitlement — server-owned `BillingCheckout` + подтверждённый `PaymentRecord`.
