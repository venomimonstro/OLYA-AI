# Sprint 78 — Product Analytics — DONE

Sprint 78 добавляет server-owned продуктовую аналитику без нового сбора prompt/message content.

- Activation и time-to-first-value считаются по `UserOnboarding`, который сам восстанавливается из durable product facts.
- D1/D7 retention считается по успешным `UsageEvent` относительно даты регистрации.
- Task success использует canonical `Task` terminal states.
- Frustration использует существующие `FrustrationEvent`, а не текст чатов.
- Paid conversion считается по активным `BillingSubscription` и разбивается по тарифам.
- Product event funnel использует bounded `ProductEvent` с dedupe keys.
- Resource economics повторно использует `operations_summary`: revenue/server allocation, CPU per success, context efficiency и compute waste.
- API: `GET /v1/admin/product-analytics?days=...`, только admin.
- UI: `/admin/analytics`, mobile-first, nonce CSP, no-store.
- Analytics service не импортирует Message/Conversation и явно публикует privacy contract `stores_message_content=false`, `stores_prompt_content=false`.
- Добавлены `product_analytics_audit` и Sprint 78 tests в full regression gate.
