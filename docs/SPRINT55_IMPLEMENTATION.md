# Sprint 55 — Server Optimization Profiles

Дата реализации: 2026-09-08.

## Цель

Свести настройки памяти, контекста, очередей и параллельности в один безопасный boot envelope для конкретного физического сервера.

Профиль не является набором независимых runtime-переключателей. Это принципиально: нельзя безопасно заменить llama context, SQLAlchemy pool, semaphores и worker memory limits по отдельности во время активной нагрузки.

Архитектура:

`physical host -> safe model ceiling -> selected profile -> one env envelope -> Compose/process boot`

## Профили

### Super Low

Предназначен для максимально экономного режима на поддерживаемом 32-ГБ классе:

- context: до 4096;
- Qwen generation slots: 1;
- llama memory: minimum safe production allocation;
- inference queue: 12;
- PostgreSQL pool: 4 + 2 overflow;
- research concurrency: 1;
- document render concurrency: 1;
- sandbox max memory: 512 MiB;
- project runtime max memory: 512 MiB;
- короткие HTTP overload queues;
- images по умолчанию не включаются профилем.

Профиль рассчитан на минимальную конкуренцию вторичных задач с Qwen.

### Optimal

Default для 32 ГБ:

- context: до 8192;
- Qwen generation slots: 1;
- inference queue: 32;
- PostgreSQL pool: 8 + 4 overflow;
- research concurrency: 4;
- document rendering serial;
- sandbox/project runtime до 1024 MiB;
- Sprint 54 HTTP lanes сохраняют умеренную параллельность.

Это основной production-профиль проекта.

### Maximum

Maximum не означает unlimited.

Он всегда ограничивается:

- `safe_context_for_ram_gib()` из pinned model manifest;
- `LLAMA_MEMORY_CAP_GIB`;
- `NON_LLAMA_RESERVE_GIB`;
- одним Qwen generation slot для текущей CPU/RAM архитектуры.

На 32 ГБ Maximum не может открыть больше 8192 context.

На классе ~48 ГБ доступен ceiling 12288.

На 64+ ГБ — 16384.

Дополнительная память используется для большего context и control-plane headroom: PostgreSQL, research, documents и sandbox. Второй Qwen generation намеренно не включается, потому что на CPU он создаёт конкуренцию за memory bandwidth и может ухудшить latency обоих запросов.

## Единый envelope

`app/services/server_profiles.py` рассчитывает одновременно:

- Qwen context;
- llama memory;
- llama threads;
- inference queue;
- PostgreSQL pool;
- app/db/SearXNG/worker container memory;
- sandbox/project runtime memory;
- document concurrency;
- research concurrency/queue;
- Sprint 54 HTTP admission limits.

Все значения экспортируются одним `env()` contract.

Это исключает ситуацию, когда админ отдельно увеличил context, отдельно sandbox RAM и отдельно DB pool, а суммарный worst-case уже превышает физическую память.

## Физический ceiling

Источник истины для Qwen остаётся Sprint 42 model manifest.

Maximum не может программно превысить safe context tier текущего host RAM.

Также `llama_memory_gib` никогда не превышает manifest memory cap.

## Compose memory envelopes

`docker-compose.yml` больше не содержит жёсткие memory limits для основных control-plane сервисов.

Используются:

- `X1_APP_MEMORY_LIMIT_MB`;
- `X1_DB_MEMORY_LIMIT_MB`;
- `X1_SEARX_MEMORY_LIMIT_MB`;
- `X1_SANDBOX_WORKER_MEMORY_LIMIT_MB`;
- `X1_DOCUMENT_WORKER_MEMORY_LIMIT_MB`.

Llama получает:

- `X1_LLAMA_MEMORY_LIMIT`;
- `X1_DEEP_CONTEXT_TOKENS`;
- `X1_MAX_CONCURRENT_GENERATIONS`.

Таким образом Docker envelope и application envelope рассчитываются одной политикой.

## Применение профиля

Добавлен host utility:

`python3 scripts/apply_server_profile.py --profile optimal`

или:

`python3 scripts/apply_server_profile.py --profile super_low`

или:

`python3 scripts/apply_server_profile.py --profile maximum`

Скрипт:

1. читает физическую RAM из `/proc/meminfo` на host;
2. читает CPU core count;
3. вычисляет safe envelope;
4. атомарно обновляет только profile-managed keys в `.env`;
5. сохраняет `data/server-profile-active.json`.

Для staged admin request:

`python3 scripts/apply_server_profile.py --staged && docker compose up -d`

## Почему profile switch не делается in-place

Sprint 55 специально запрещает runtime replacement отдельных governor/semaphore объектов.

Опасный вариант выглядел бы так:

`old semaphore has active request -> admin creates new semaphore -> new requests enter new semaphore -> фактическая concurrency удвоилась`.

Вместо этого:

- текущий boot envelope immutable;
- админ выбирает новый профиль;
- request записывается в `data/server-profile-request.json`;
- активные операции продолжают работать на текущих лимитах;
- host utility атомарно применяет новый env;
- `docker compose up -d` создаёт один новый согласованный boot envelope.

Так acceptance «активные операции безопасно доживают» достигается без двух параллельных наборов gates.

## Active vs staged

Хранятся два разных файла:

- `data/server-profile-active.json` — envelope, применённый host utility;
- `data/server-profile-request.json` — профиль, выбранный админом для следующего controlled restart.

API никогда не выдаёт staged профиль за уже активный.

## Admin API

Добавлены:

`GET /v1/admin/operations/server-profile`

Показывает configured/active/staged состояние.

`GET /v1/admin/operations/server-profile/preview?profile=maximum`

Показывает точный envelope для физического host, сохранённого в active boot evidence.

`POST /v1/admin/operations/server-profile`

Payload:

```json
{"profile":"super_low"}
```

Ответ явно содержит:

- `restart_required=true`;
- `active_operations_unchanged=true`.

API только stage-ит профиль и не меняет живые semaphores/DB pool/llama runtime.

## Images

Все три базовых профиля имеют `images_default_enabled=false`.

Причина: текущий основной проект рассчитан на CPU/RAM Qwen и 32-ГБ стартовый сервер. Image worker имеет отдельный тяжёлый runtime и не должен автоматически стартовать только потому, что админ выбрал Maximum.

Если изображения включены отдельно, они остаются самостоятельной capability с собственным admission и budget policy.

## Regression coverage

`tests/test_sprint55_server_profiles.py` проверяет:

- 32 ГБ Super Low = 4K;
- 32 ГБ Optimal = 8K;
- 32 ГБ Maximum не превышает 8K;
- 48 ГБ Maximum = 12K ceiling;
- 64 ГБ Maximum = 16K ceiling;
- llama memory cap;
- Qwen generation slots остаются 1;
- secondary parallelism Super Low ниже Optimal;
- один coherent env contract;
- active и staged profile не смешиваются;
- runtime API не создаёт новый ResourceGovernor/FairOverloadLane;
- Docker memory limits используют profile env;
- host applicator пишет env атомарно.

## Что ещё проверить на target node

Production acceptance требует реального прогона минимум на одном 32-ГБ узле, а для Maximum желательно также 48/64 ГБ:

1. применить Super Low;
2. `docker compose config` и doctor;
3. live Qwen inference;
4. параллельный research/document/sandbox;
5. RSS/container memory observation;
6. staged переход Super Low -> Optimal;
7. убедиться, что до restart старые limits остаются активны;
8. выполнить controlled `docker compose up -d`;
9. проверить active profile evidence;
10. повторить Optimal -> Maximum;
11. убедиться, что 32-ГБ host не получил >8K;
12. на 48/64 ГБ подтвердить 12K/16K context и отсутствие OOM.

До такого target-node теста нельзя утверждать, что Maximum даёт лучшую реальную throughput/latency на конкретном CPU: профиль задаёт безопасный envelope, но оптимум CPU threads и secondary concurrency требует измерения на железе.
