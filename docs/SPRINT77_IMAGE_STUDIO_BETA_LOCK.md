# Sprint 77 — Image Studio Beta Lock — DONE

Sprint 77 фиксирует Image Studio как честный beta surface, который не обещает доступность отдельно от backend.

- Новый `/v1/images/beta-contract` собирается из canonical Capability Registry для generation/editing.
- Studio запускается только когда `images.edit` реально available; при недоступном capability contract UI fail-closed.
- Перед каждой edit-задачей Studio повторно проверяет beta contract, поэтому устаревший readiness не используется.
- Worker heartbeat, backend/model/vision QA, storage quota и active-job limit входят в server-owned availability.
- Reference content остаётся private/no-store, публичные raw-reference URL не создаются.
- Обучение на изображении по умолчанию выключено и требует явного `allow_training` в feedback.
- Generated content выдаётся только после `status=ready` и `qa_status=passed`; failed/unverified результат в Studio не показывается.
- Strict edit QA остаётся server-owned; UI отправляет `strict_quality=true` и preserve-outside-mask.
- Polling beta UI ограничен 6 минутами, после чего задача не объявляется failed/completed — пользователь видит честный timeout ожидания.
- CSP переведён на `default-src 'none'` + nonce scripts/styles, no-store и no-referrer.
- Добавлены `image_studio_beta_audit` и Sprint 77 tests в full regression gate.
