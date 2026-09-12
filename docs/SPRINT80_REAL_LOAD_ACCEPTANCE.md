# Sprint 80 — 10-User Real Load Acceptance — HARNESS DONE / TARGET RUN REQUIRED

Sprint 80 добавляет обязательный executable acceptance harness для реального сервера. Сам production load run не симулируется и не объявляется пройденным без целевого X1/Qwen окружения.

- `scripts/load_acceptance.py` требует минимум 10 authenticated tokens и отдельно проверяет каждый через `GET /v1/auth/me`.
- Gate требует минимум 10 **разных user_id**. Десять сессий одного аккаунта не считаются десятью пользователями.
- Bearer tokens не отправляются на внешний target по plain HTTP: для любого non-loopback URL harness требует HTTPS до первого сетевого запроса. HTTP разрешён только для `127.0.0.1`, `localhost` и `::1`.
- До нагрузки harness получает `GET /version` и сравнивает server-owned build fingerprint с fingerprint текущего checkout. Старый или чужой app image не может породить валидный load evidence.
- Все пользователи одновременно выполняют реальные `POST /v1/chat` запросы минимум в одном раунде. Payload валидируется regression-аудитом через текущую `ChatRequest` Pydantic-схему.
- Измеряются median/p95/max latency, throughput, error rate, queue waiting и overload responses.
- Каждый запрос несёт `X-X1-Deadline-Ms`, поэтому тест не создаёт бесконечную нагрузку.
- Harness fail-closed: <10 аккаунтов, повторяющиеся session tokens/user_id, небезопасный transport, несовпадение build fingerprint, превышение p95/error-rate или отсутствие success дают non-zero exit.
- Токены берутся только из `X1_LOAD_TOKENS` environment и не записываются в отчёт/репозиторий. User ID также не публикуются, сохраняется только их количество.
- Отчёт `x1-real-load-acceptance-v2` атомарно записывается в `backups/load-acceptance-latest.json`; он содержит `git_head`, `source_fingerprint`, `target_build_fingerprint` и target URL. Sprint86 принимает его только для того же production target URL и точного совпадения кандидата.
- Контракт harness входит в обычный regression через `load_acceptance_audit`; сам сетевой load test туда не включён.
- Реальный green result должен быть получен на target server **после последнего изменения кода**, до выпуска RC и Sprint86 Production Acceptance.
