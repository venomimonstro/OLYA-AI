# Sprint 54 — Graceful Overload

Дата реализации: 2026-09-08.

## Цель

Сохранить доступность core chat на одном недорогом CPU/RAM узле при всплесках нагрузки и сбоях вторичных подсистем. Перегрузка должна быть ограниченной, диагностируемой и локальной для конкретной capability.

Архитектура:

`HTTP request -> pre-DB fair admission lane -> route/auth/DB -> subsystem governor/job -> downstream`

## Pre-DB admission

До выполнения FastAPI route dependencies дорогие POST-запросы классифицируются в независимые lanes:

- `chat`: `/v1/chat`, `/v1/chat/stream`;
- `research`: network discover/collect/source fetch;
- `images`: создание новой image generation;
- `sandbox`: выполнение project sandbox run.

Lane ограничивает:

- число запросов, дошедших до route/DB;
- общий размер ожидающей очереди;
- максимальную долю очереди одного principal;
- время ожидания.

Это дополняет, а не заменяет существующие inference/job/sandbox governors.

## Fairness без DB

До открытия SQLAlchemy Session нельзя безопасно загружать User из БД. Поэтому fairness-key строится как усечённый SHA-256 заголовка Authorization.

Bearer token:

- не логируется;
- не сохраняется;
- не декодируется admission layer;
- используется только как process-local fairness identity.

Default `max_queued_per_principal=2` не позволяет одному пользователю заполнить всю глобальную очередь десятками одинаковых запросов.

## FIFO

`FairOverloadLane` использует собственную bounded FIFO очередь, а не полагается на неформальную справедливость `asyncio.Semaphore`.

Cancelled/timeout waiter удаляется из очереди и освобождает свою per-principal долю.

## Circuit breakers

Каждая capability имеет отдельный breaker:

- chat;
- research;
- images;
- sandbox.

Default:

- 5 последовательных downstream failures;
- cooldown 20 секунд;
- затем один half-open probe.

Успешный probe закрывает breaker. Неуспешный снова открывает его.

При открытии breaker уже ожидающие запросы не пропускаются дальше: очередь немедленно получает контролируемый `OverloadRejected`. Это предотвращает thundering herd после серии одинаковых downstream failures.

Открытый research breaker не блокирует chat. Image/sandbox breakers также независимы.

## HTTP contract

Admission rejection возвращает HTTP 503 с обязательным `Retry-After` и структурированным detail:

```json
{
  "code": "graceful_overload",
  "subsystem": "research",
  "reason": "queue_full",
  "retry_after": 5,
  "resumable": true
}
```

Причины:

- `queue_full`;
- `fairness`;
- `queue_timeout`;
- `circuit_open`;
- `half_open`;
- `admission_not_ready`.

`Retry-After` для circuit-open рассчитывается из оставшегося cooldown, а не является случайным числом.

Research/sandbox операции, для которых серверное run-state уже создано, можно повторить после `Retry-After`. Новая image generation, отклонённая до создания generation/job, требует обычного повторного create request.

## Независимая деградация

Secondary subsystem failure не меняет состояние других lanes. Это означает:

- недоступный SearXNG не закрывает локальную Qwen;
- упавший image backend не блокирует текстовый чат;
- перегруженный sandbox не блокирует research или документы;
- quota/user-level 429 не считается downstream failure breaker-а.

## Защита PostgreSQL

Chat ранее попадал во внутренний inference governor только после создания route dependencies. Sprint 54 добавляет внешний chat HTTP lane, поэтому одновременно только bounded число chat requests доходит до DB/auth/context compilation; остальные ожидают до DB allocation.

На 32-ГБ профиле default:

- chat active HTTP: 4, queue: 32;
- research active HTTP: 4, queue: 24;
- images active HTTP: 2, queue: 8;
- sandbox active HTTP: 2, queue: 8;
- per principal queued: 2.

Главный inference governor остаётся `max_concurrent_generations=1`.

## Operator observability

Добавлен:

`GET /v1/admin/operations/overload`

Для каждой lane возвращаются:

- active;
- waiting;
- max_concurrent;
- max_queue;
- breaker_state;
- breaker_failures.

Endpoint не выполняет месячную SQL-агрегацию и показывает актуальное process-local admission состояние.

Responses, пропущенные через lane, получают диагностические headers:

- `X-X1-Overload-Lane`;
- `X-X1-Queue-Waiting`.

## Regression coverage

`tests/test_sprint54_graceful_overload.py` проверяет:

- bearer token не раскрывается fairness key;
- per-principal queue share;
- bounded global queue;
- Retry-After semantics;
- независимость circuit breakers;
- open breaker не пропускает работу;
- наличие pre-DB routing для chat/research/images/sandbox;
- operator overload endpoint;
- bounded configuration.

## Acceptance на target node

На production-like узле нужно дополнительно провести нагрузочный сценарий:

1. один пользователь создаёт burst из 50 chat requests;
2. второй пользователь отправляет один Fast запрос во время burst;
3. SearXNG принудительно выключается;
4. core chat продолжает отвечать;
5. research breaker открывается и выдаёт Retry-After;
6. SearXNG возвращается, один half-open probe закрывает breaker;
7. image-worker/sandbox-worker повторяются независимо;
8. измеряется число занятых PostgreSQL connections при полной chat queue;
9. cancelled clients не оставляют ghost waiters;
10. после cooldown отсутствует thundering herd.

Production acceptance считается подтверждённым только после этого реального stress/chaos прогона.
