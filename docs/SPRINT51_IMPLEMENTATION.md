# Sprint 51 — Compaction-safe autonomous development

Дата реализации: 2026-09-08.

## Цель

Сделать длинную разработку независимой от длины истории чата и от того, пережил ли процесс API/Qwen предыдущий turn.

Каноническое состояние разработки теперь хранится сервером в PostgreSQL и восстанавливается из структурированного ledger/checkpoint, а не из prose summary модели.

## Основной pipeline

`development state -> structured ledger -> versioned checkpoint -> compact resume context -> next engineering/model turn`

После restart/compaction:

`conversation/session id -> ledger -> latest checkpoint -> live DB reconciliation -> resume context`

## Immutable goal / constraints ledger

При первом создании autonomous ledger фиксируются:

- цель проекта (`DevelopmentPlan.title` как каноническая цель текущего plan);
- constraints;
- architecture contract;
- SHA-256 канонического контракта.

Эти поля не перезаписываются при последующих sync.

Если текущий DevelopmentPlan расходится с первоначальным контрактом, ledger выставляет `contract.drift_detected=true` вместо тихого изменения цели.

Это защищает от ситуации, когда после compaction модель или позднее изменение текста постепенно меняет исходную задачу.

## Structured work ledger

Из фактических ORM-объектов сервер собирает:

- `completed_work`;
- `pending_work`;
- `failed_work`;
- sprint states;
- active EngineeringRun;
- latest EngineeringExecution;
- active ArchitectureDecision records.

Модель не может объявить work item completed только своим текстом. Источник статуса — серверная БД.

## Versioned checkpoints

Добавлены таблицы:

- `autonomous_development_ledgers`;
- `autonomous_development_checkpoints`.

Checkpoint содержит:

- ledger/session/project/plan id;
- revision;
- immutable goal/constraints;
- contract SHA;
- server-owned structured state;
- state SHA-256;
- checkpoint kind.

На meaningful development control transition создаётся новая revision/checkpoint. `status` refresh без изменений не обязан создавать новую ревизию.

## Resume after compaction

`resume_payload()` возвращает schema:

`x1.autonomous-development-state.v1`

В неё входят:

- immutable goal;
- immutable constraints;
- decisions;
- completed;
- pending;
- failed;
- current engineering/execution state;
- checkpoint/revision;
- subagent budget;
- recovery markers.

`compact_resume_context()` ограничен по размеру (default 18K chars) и предназначен для prompt injection как trusted server state.

Он не читает таблицу Messages и не зависит от chat history.

## Engineering integration

`app/services/engineering.py` теперь добавляет `autonomous_resume_ledger` в `ENGINEERING STATE`.

Coordinator/Architect/Developer/Tester/Reviewer получают structured continuity state напрямую из PostgreSQL.

System instruction отдельно запрещает переписывать immutable goal/constraints из prose history.

Таким образом compaction истории не удаляет канонические проектные решения.

## Development chat integration

После каждого управляющего шага `/v1/development-chat` вызывает `sync_ledger()`.

Checkpoint kinds соответствуют фактическому action, например:

- activate_sprint;
- role:*;
- execute_implementation;
- verified;
- local_commit;
- rollback;
- pause/resume.

Response state содержит официальный `state.autonomous`.

## Restart recovery

Ledger имеет heartbeat и persistent `active_subagents`.

Если процесс погиб с занятым slot, maintenance вызывает `recover_abandoned_slots()` и очищает stale active leases после безопасного окна.

Важно: stale heartbeat определяется **до** sync refresh. Поэтому сам resume не скрывает факт прерывания.

## Subagent budget

Для каждого autonomous ledger сохраняются:

- `subagent_budget`;
- `subagent_calls_used`;
- `active_subagents`;
- `max_parallel_subagents`.

Default policy:

- total budget: 8;
- max parallel: 1.

Hard maximum для конфигурации ограничен кодом.

На целевом 32-ГБ CPU/RAM сервере параллельность 1 является осознанным default: несколько конкурирующих Qwen/subagent inference способны ухудшить latency и создать memory pressure.

`consume_subagent_slot()` fail-closed при exhausted budget или parallel limit.

`release_subagent_slot()` освобождает persistent slot.

## Почему ledger не является prose summary

Prose summary может:

- потерять отрицательное ограничение;
- изменить формулировку решения;
- смешать fact и model assumption;
- устареть после следующего execution;
- исчезнуть при compaction.

Ledger собирается из ORM state и server-owned immutable contract.

Summary может использоваться для UI, но не является source of truth.

## Database migration

Revision:

`f51c0a11d9e2`

Parent:

`f45a10c2d8e1`

Sprint 46–50 не создавали новых DB migrations, поэтому цепочка остаётся линейной.

## Failure semantics

- plan missing -> autonomous development error;
- goal missing -> fail closed;
- contract changed -> drift flag, original contract remains immutable;
- stale subagent lease -> maintenance recovery;
- budget exhausted -> no new subagent slot;
- parallel limit reached -> no concurrent autonomous subagent;
- checkpoint available after restart -> resume from server state;
- chat history missing/compacted -> ledger remains usable.

## Regression coverage

`tests/test_sprint51_autonomous_development.py` проверяет:

- hard subagent budget;
- hard parallelism limit;
- slot release;
- stale heartbeat ordering;
- immutable contract behavior;
- contract drift marker;
- development-chat checkpoint integration;
- public `state.autonomous` schema;
- engineering resume-ledger injection;
- maintenance recovery;
- linear Alembic revision;
- bounded resume context;
- отсутствие зависимости autonomous ledger от Message/chat-history.

## Что Sprint 51 сознательно не делает

Sprint не увеличивает context window Qwen и не создаёт второй model-memory сервис.

Он также не включает массовую параллельность агентов: на 32-ГБ хосте это противоречило бы resource policy.

Sprint 51 создаёт durable orchestration state, которым следующие autonomous workflows могут пользоваться без зависимости от конкретного process lifetime.

## Production acceptance

Для полного acceptance на target node необходимо проверить:

1. миграцию существующей production DB с `f45a10c2d8e1` на `f51c0a11d9e2`;
2. длинный development run с несколькими sprint/work items;
3. принудительный restart API посередине run;
4. recovery stale subagent lease;
5. продолжение того же conversation после restart;
6. сохранение goal/constraints/decisions;
7. отсутствие повторного выполнения уже completed work;
8. bounded размер resume prompt;
9. PostgreSQL growth при большом количестве checkpoints;
10. корректное поведение при concurrent resume одного development session.

Кодовая реализация не заменяет эти runtime tests на реальном 32-ГБ узле.
