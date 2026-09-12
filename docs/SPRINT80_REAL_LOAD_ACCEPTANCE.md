# Sprint 80 — 10-User Real Load Acceptance — HARNESS DONE / TARGET RUN REQUIRED

Sprint 80 добавляет обязательный executable acceptance harness для реального сервера. Сам production load run не симулируется и не объявляется пройденным без целевого X1/Qwen окружения.

- `scripts/load_acceptance.py` требует минимум 10 уникальных authenticated session tokens.
- Все пользователи одновременно выполняют реальные `POST /v1/chat` запросы, минимум в одном раунде.
- Измеряются median/p95/max latency, throughput, error rate, queue waiting и overload responses.
- Каждый запрос несёт `X-X1-Deadline-Ms`, поэтому тест не создаёт бесконечную нагрузку.
- Harness fail-closed: <10 пользователей, повторяющиеся tokens, превышение p95/error-rate или отсутствие success дают non-zero exit.
- Токены берутся только из `X1_LOAD_TOKENS` environment и не записываются в результаты/репозиторий.
- Контракт harness входит в обычный regression через `load_acceptance_audit`; сам сетевой load test туда не включён.
- Реальный green result должен быть получен на target server перед Sprint86 Production Acceptance.
