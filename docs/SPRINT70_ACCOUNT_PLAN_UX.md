# Sprint 70 — Account & Plan UX — DONE

Sprint 70 связывает существующий пользовательский `/app` с server-owned Billing из Sprint 69 без второго payment pipeline.

- Account показывает профиль, текущий measured plan, compute budget и resource usage.
- Subscription показывает status, оплаченный период и cancel-at-period-end / resume.
- Plan cards загружают цены и лимиты только из `/v1/commerce/billing/plans`.
- Checkout отправляет только `plan` и `idempotency_key`; amount/currency остаются server-owned.
- Доступ не считается оплаченным до подтверждённого payment event.
- Видны последние checkout и user-scoped payment events.
- API-раздел получил прямой переход в Self-Service API Console.
- Browser UI не получает payment ingestion secret и не меняет billing entitlement напрямую.
- `scripts.account_plan_ux_audit` включён в полный regression gate.
- `tests/test_sprint70_account_plan_ux.py` фиксирует UI/API/security contracts.

Следующий план: `docs/SPRINTS_71_86.md`.
