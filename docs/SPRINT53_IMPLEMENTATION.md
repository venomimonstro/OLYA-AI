# Sprint 53 — прозрачная экономика и лимиты

Дата реализации: 2026-09-08.

## Цель

Пользователь должен понимать выбранный режим, ожидаемый резерв и остаток ресурса до дорогого inference. Администратор должен видеть реальный compute per successful answer, wasted compute и структуру затрат по режимам/verification.

Основной pipeline:

`message -> route/verification preflight -> budget snapshot -> inference -> canonical UsageEvent -> compute breakdown -> user/admin analytics`

## Пользовательский budget snapshot

Добавлены endpoints:

- `GET /v1/usage/budget`;
- `POST /v1/usage/budget-preview`.

Snapshot содержит:

- plan;
- месячный compute limit;
- использовано секунд;
- осталось секунд;
- utilization percent;
- warning level;
- разложение chat compute;
- measured image/sandbox/GPU usage;
- research/image/sandbox activity;
- resource cost microunits.

## Preflight перед отправкой

`budget-preview` использует те же backend-компоненты, что и реальный chat:

- `choose_route()`;
- `classify_freshness()`;
- `plan_verification()`.

Поэтому UI не угадывает стоимость по клиентскому regex.

Projection сообщает:

- выбранный Fast/Work/Deep;
- максимум reserve seconds;
- возможный extra verification budget;
- estimated resource cost;
- помещается ли резерв в остаток;
- сколько останется после резерва.

## UI

`/app` показывает бюджет в header.

Перед отправкой сообщения UI вызывает `/v1/usage/budget-preview` и отображает:

- выбранный сервером режим;
- резерв;
- прогнозируемый остаток;
- предупреждение по лимиту.

Если projection уже не помещается в остаток, inference не запускается вслепую.

После успешного ответа и после cancellation UI обновляет `/v1/usage/budget`.

## Предупреждения

Thresholds:

- 70% — notice;
- 85% — high;
- 95% — critical.

Предупреждение появляется до исчерпания ресурса, а не только на следующем 429.

## Нет неожиданного hard-stop после дорогого ответа

Quota admission остаётся pre-inference.

После того как запрос принят в inference, Sprint 53 не добавляет повторную `ensure_compute_available()` внутрь generation/critic/repair loop.

То есть дорогое выполнение не обрывается посередине только потому, что фактическое время немного превысило предварительный reserve. Фактический расход записывается, а следующий запрос получает новый актуальный budget verdict.

## Compute breakdown ledger

Добавлена таблица:

`compute_breakdown_events`

Поля:

- request_id;
- user/project/conversation;
- mode;
- primary_ms;
- critic_ms;
- repair_ms;
- total_inference_ms;
- wasted_ms;
- success;
- verification extra inference count;
- metadata.

`request_id` уникален: один chat request не может быть дважды посчитан в breakdown.

## Источник истины

Общий `total_inference_ms` берётся из канонического `UsageEvent.inference_ms`.

Failed/cancelled request получает:

`wasted_ms = total_inference_ms`

Successful request:

`wasted_ms = 0`

Это даёт точный wasted compute для неуспешных chat inference.

## Critic / Repair attribution

Sprint 48 исторически сохраняет:

- total inference time;
- extra inference count;
- critic_used;
- repair_applied;

но не отдельный stopwatch каждого дополнительного sub-call.

Поэтому Sprint 53 не выдаёт приблизительную разбивку за точную. Для таких событий Critic/Repair доля рассчитывается как `call_equivalent_estimate`, а metadata явно хранит attribution method.

Общий total compute при этом остаётся точным.

В одном из следующих runtime-инструментальных улучшений можно перейти на dedicated per-subcall stopwatch без изменения схемы таблицы.

## Fast / Work / Deep

Breakdown агрегируется по `UsageEvent.mode`:

- fast;
- work;
- deep.

Для каждого режима доступны requests, total compute, wasted compute и compute per successful answer.

## Research / image / sandbox

Не создаётся второй конкурирующий billing ledger.

Используется существующая measured resource модель:

- `ImageGeneration` — image worker duration;
- `ProjectSandboxRun` — sandbox duration;
- `ResourceExpenseEvent` — GPU/other measured resources;
- `ResearchRun` — activity count;
- `UsageEvent` — chat CPU inference.

Это предотвращает двойное списание одного и того же CPU.

## Admin analytics

`/v1/admin/operations/summary` теперь содержит `compute_economics`:

- total inference ms;
- successful requests;
- compute ms per successful answer;
- primary ms;
- critic ms;
- repair ms;
- verification share;
- wasted ms;
- waste rate;
- by_mode;
- top compute users.

Администратор может видеть пользователей/режимы, которые съедают больше всего локального CPU, и сколько compute не приводит к успешному ответу.

## Database migration

Revision:

`f53b21e7c4a0`

Parent:

`f51c0a11d9e2`

Sprint 52 не добавлял DB migration, поэтому Alembic chain остаётся линейной.

## Regression coverage

`tests/test_sprint53_budget_transparency.py` проверяет:

- thresholds 70/85/95;
- bounded Fast/Work/Deep reserve;
- наличие budget/preflight API;
- backend route/verification planning;
- UI preflight до `streamChat`;
- видимый budget header;
- compute breakdown persistence;
- уникальность request id;
- отсутствие mid-response quota recheck;
- admin compute per success;
- wasted compute;
- verification share;
- mode breakdown;
- linear migration.

## Acceptance на target node

Для production acceptance необходимо:

1. применить migration `f53b21e7c4a0`;
2. прогнать Fast/Work/Deep запросы;
3. вызвать Auto Critic и Repair;
4. отменить streaming request;
5. симулировать failed inference;
6. проверить exact total/wasted compute;
7. проверить warning на 70/85/95%;
8. проверить UI preflight;
9. убедиться, что accepted long request не обрывается mid-response из-за quota;
10. сверить admin summary с raw UsageEvent;
11. проверить image/sandbox measured resources;
12. наблюдать PostgreSQL growth compute breakdown table.

Production acceptance считается пройденным только после реального target-node прогона.