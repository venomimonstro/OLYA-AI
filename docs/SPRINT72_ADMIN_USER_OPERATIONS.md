# Sprint 72 — Admin User Operations — DONE

Цель: безопасно управлять пользовательским доступом без подмены billing truth и без просмотра лишнего пользовательского контента.

Реализовано:

- mobile-first `/admin/users` с поиском по email, имени и ID;
- отдельный read model пользователя: account state, billing subscription, effective quota, sessions, safety restrictions и admin audit trail;
- typed confirmation по email + обязательная причина для suspend/reactivate, revoke sessions и clear override;
- suspend немедленно отзывает активные сессии; администратор не может приостановить собственный аккаунт;
- массовый revoke sessions защищён от отзыва текущего admin-сеанса;
- `AdminUserControl` хранит явный plan/quota override отдельно от billing subscription;
- override может иметь срок действия до 366 дней и optimistic `version`, поэтому параллельные правки fail closed с 409;
- canonical quota path всегда сначала восстанавливает paid subscription / Free, затем measured policy и только после этого активный admin override;
- истёкший или снятый override автоматически возвращает пользователя к billing/free entitlement;
- существующий Safety `UserRestriction` остаётся единственным механизмом capability restrictions; User Operations показывает его состояние, но не создаёт параллельную систему запретов;
- секреты сессий, password hash и token hash не выводятся;
- CSP страницы использует nonce без `unsafe-inline`; admin token хранится только в `sessionStorage`;
- новая миграция зарегистрирована как explicit ORM-owned table в source-integrity generator;
- `admin_user_operations_audit` и Sprint 72 tests добавлены в полный regression gate.

Важно: старые `/v1/admin/users/{id}` endpoints сохранены для обратной совместимости. Новый интерфейс использует только Sprint 72 fail-closed operations API.
