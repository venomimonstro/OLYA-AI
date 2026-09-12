# Sprint 80 — 10-User Real Load Acceptance — HARNESS DONE / TARGET RUN REQUIRED

Sprint 80 добавляет обязательный executable acceptance harness для реального сервера. Сам production load run не симулируется и не объявляется пройденным без целевого X1/Qwen окружения.

- `scripts/load_acceptance.py` требует минимум 10 authenticated tokens и отдельно проверяет каждый через `GET /v1/auth/me`.
- Gate требует минимум 10 **разных user_id**. Десять сессий одного аккаунта не считаются десятью пользователями.
- Все пользователи одновременно выполняют реальные `POST /v1/chat` запросы минимум в одном раунде.
- Измеряются median/p95/max latency, throughput, error rate, queue waiting и overload responses.
- Каждый запрос несёт `X-X1-Deadline-Ms`, поэтому тест не создаёт бесконечную нагрузку.
- Harness fail-closed: <10 аккаунтов, повторяющиеся session tokens/user_id, превышение p95/error-rate или отсутствие success дают non-zero exit.
- Токены берутся только из `X1_LOAD_TOKENS` environment и не записываются в отчёт/репозиторий. User ID также не публикуются, сохраняется только их количество.
- Отчёт атомарно записывается в `backups/load-acceptance-latest.json`, содержит `git_head` кандидата и используется Sprint85/86 только при точном совпадении исходной ревизии.
- Контракт harness входит в обычный regression через `load_acceptance_audit`; сам сетевой load test туда не включён.
- Реальный green result должен быть получен на target server **после последнего изменения кода**, до выпуска RC и Sprint86 Production Acceptance.
