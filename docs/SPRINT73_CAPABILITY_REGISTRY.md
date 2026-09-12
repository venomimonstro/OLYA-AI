# Sprint 73 — Capability Registry — DONE

Sprint 73 вводит единый server-owned read contract для доступности возможностей X1.

- `app/services/capabilities.py` содержит стабильный registry ID, availability, reason и requirements.
- Registry не создаёт новый набор feature flags: он собирает существующую истину из runtime config, billing/quota, Safety restrictions, image worker и sandbox.
- Пользовательский endpoint: `GET /v1/capabilities`.
- Администраторский endpoint: `GET /v1/admin/capabilities`, включая проверку конкретного user ID.
- `live=true` выполняет live sandbox probe; обычный ответ не делает тяжёлую runtime-проверку.
- `/admin/capabilities` визуализирует тот же server-owned registry, не вычисляя доступность в браузере.
- Secrets, worker tokens, API keys и model paths в registry payload не публикуются.
- Capability contract покрывает Chat, Projects, Files/RAG, Documents, Research fetch/search, Image generation/editing, Sandbox, Development, API и Billing.
- Safety restriction (`all/chat/research/tools/images`) входит в requirements соответствующей capability.
- Для runtime admission остаются доменные hard guards; registry является каноническим read-model для UI/API availability и не ослабляет существующую безопасность.
- Добавлен `capability_registry_audit` и Sprint 73 tests в полный regression gate.
