# Sprint 50 — Coding Agent: доказательство результата

Дата реализации: 2026-09-08.

## Цель

Coding Agent не имеет права считать задачу завершённой только потому, что Qwen сгенерировала правдоподобный patch или написала фразу «тесты пройдены».

Sprint 50 добавляет server-owned pipeline:

`goal -> selective inspect -> scoped edit -> sandbox verification -> diagnose/repair -> final diff inspect -> server proof -> done/rollback`

## Основа

Sprint 50 использует Sprint 49 Tool Calling Reliability Engine:

- strict schemas;
- registry allowlist;
- tool_call_id;
- serial execution;
- idempotent writes;
- loop detection;
- bounded steps/errors;
- no arbitrary shell/network/push.

## Coding Agent runtime

`app/services/coding_agent.py` реализует `run_coding_agent()`.

Сервер задаёт:

- goal;
- explicit approved paths;
- server-approved verification commands;
- required checks;
- diff budget;
- sandbox backend/image/resources;
- maximum tool/model steps;
- rollback policy.

Модель не может расширить эти полномочия своим ответом.

## Selective repository context

Agent использует существующие инструменты:

- `workspace.map`;
- `workspace.read`;
- `workspace.write`;
- `git.status`.

`workspace.map/read/write` видят только approved scope. Весь репозиторий не stuffing-ится в model context.

## Approved-scope writes

`workspace.write`:

- проверяет safe relative path;
- запрещает выход из approved paths;
- использует optimistic `expected_sha256`;
- имеет pre-write checkpoint hook;
- имеет post-write touched-file ledger.

Произвольная запись вне scope невозможна через Coding Agent registry.

## Checkpoint / rollback

Перед первым изменением каждого файла сохраняется его исходное содержимое.

Для нового файла checkpoint хранит `None`.

Rollback:

- восстанавливает исходные файлы атомарной заменой;
- удаляет новые файлы, созданные агентом;
- не откатывает неизвестные участки файловой системы;
- имеет snapshot budget 12 MiB, чтобы large task не съел RAM бесконтрольно.

Если proof gate не пройден и `rollback_on_failure=true`, patch откатывается автоматически.

Sprint 51 сделает checkpoint persistent/resumable между restart/compaction. Sprint 50 checkpoint bounded и живёт в одном execution run.

## Server-approved verification

Модель не получает arbitrary shell.

Сервер передаёт именованные checks, например:

- `syntax -> [python, -m, py_compile, app/x.py]`;
- `unit -> [pytest, -q, tests/test_x.py, -p, no:cacheprovider]`;
- `targeted -> [...]`.

Модель может вызвать только:

`verification.run({check: "unit"})`

Имя проверяется по server allowlist. Сам argv модель не задаёт.

## Isolated read-only sandbox

Verification запускается через существующий `run_in_container()`:

- network policy = deny;
- configured sandbox image only;
- CPU/RAM/PID limits;
- bounded timeout;
- workspace mount = read-only;
- scratch mount отдельно writable;
- `PYTHONDONTWRITEBYTECODE=1`;
- `PYTHONNOUSERSITE=1`;
- HOME внутри runtime scratch.

Для remote sandbox добавлен end-to-end `read_only_workspace` contract:

`app/services/sandbox.py -> /execute -> app/sandbox_worker_api.py -> Docker bind mount ro`.

Это не просто client flag: worker сам использует его при формировании Docker command.

## State-sensitive verification — stale-cache fix

Sprint 49 кэшировал успешные tool results по fingerprint. Для проверки кода это опасно:

1. агент запускает `unit`;
2. unit проходит;
3. агент меняет код;
4. повторяет `unit`;
5. старый cached green result нельзя считать новым test evidence.

Поэтому `ToolSpec` получил `cacheable`.

`verification.run` и `proof.inspect_diff` работают с `cacheable=False`.

Write idempotency Sprint 49 остаётся без изменений.

## Patch digest

Каждый verification evidence содержит SHA-256 digest текущего agent patch state.

Digest строится из:

- sorted touched paths;
- текущего SHA-256 каждого touched file;
- missing marker для удалённого/отсутствующего файла.

Финальный proof принимает check только если:

- его последняя запись passed;
- evidence patch digest совпадает с финальным patch digest.

Следовательно сценарий `tests passed -> edit -> done` fail-closed.

## Final diff inspection

Добавлен отдельный tool:

`proof.inspect_diff`

Он возвращает:

- Git status;
- bounded Git diff;
- agent patch digest;
- agent changed paths;
- server-counted changed lines.

`done` требует, чтобы `proof.inspect_diff` был успешно вызван **после последнего `workspace.write`**.

Даже зелёные tests не дают done без финальной инспекции patch.

## Diff budget

Server-owned limits:

- max changed files;
- max changed lines.

Changed lines считаются относительно pre-write checkpoint через unified diff только для файлов, которых реально касался agent.

Это предотвращает самовольный rewrite сотен файлов при задаче «исправь одну функцию».

## Proof of Result

`CodingProof` содержит:

- `done`;
- `reason`;
- `checkpoint_id`;
- `patch_digest`;
- `changed_paths`;
- `diff_lines`;
- `diff_inspected`;
- `required_checks`;
- `passed_checks`;
- полный bounded verification evidence;
- `rolled_back`.

`done=true` вычисляется сервером только когда одновременно:

1. agent loop завершился final answer;
2. создан реальный patch;
3. patch в diff budget;
4. каждый required check прошёл на финальном patch digest;
5. final diff просмотрен после последнего write.

Текст Qwen не участвует в вычислении этих условий.

## Failure semantics

Причины незавершения включают:

- `no_patch_produced`;
- `diff_budget_exceeded`;
- `required_verification_missing_failed_or_stale`;
- `final_diff_not_inspected`;
- ToolSession stop reasons (`tool_loop_detected`, `tool_budget_exhausted`, `max_steps_exhausted`, ...).

Если proof не пройден, пользовательский ответ не сообщает «готово».

## Repair loop

Agent получает результаты failed `verification.run` как tool evidence и может:

1. прочитать релевантный код;
2. исправить patch;
3. повторно запустить required checks;
4. посмотреть финальный diff;
5. завершить задачу.

Loop ограничен Sprint 49 budgets. Нет бесконечного repair cycle.

## Security boundary

Sprint 50 не добавляет Coding Agent:

- arbitrary shell;
- произвольный argv;
- интернет;
- secrets;
- Docker socket;
- Git commit;
- Git push;
- filesystem вне approved workspace.

Verification command определяется сервером, а не LLM.

## Regression coverage

`tests/test_sprint50_coding_agent.py` проверяет:

- non-cacheable verification действительно выполняется повторно;
- checkpoint восстанавливает существующий файл;
- rollback удаляет новый файл;
- patch digest меняется после post-test edit;
- stale test evidence перестаёт считаться passed;
- diff lines считаются;
- diff inspect до последнего write не принимается;
- diff inspect после write принимается;
- remote sandbox contract содержит read-only workspace;
- proof gate содержит required verification/diff/rollback checks;
- Coding Agent не вызывает commit/push/arbitrary subprocess напрямую.

## Связь с существующим Engineering Execution

До Sprint 50 в проекте уже был legacy execution pipeline `generate patch -> apply -> verification -> rollback`. Он остаётся совместимым и полезен как deterministic execution path.

Sprint 50 добавляет новый agentic proof engine поверх Sprint 49 для задач, где нужны несколько итераций inspect/edit/test/repair. Он не ослабляет legacy verification и не заменяет его ложным model-reported success.

При дальнейшей интеграции development orchestration должен считать Coding Agent завершённым только по `CodingProof.done`, а не по финальному тексту модели.

## Production acceptance

Кодовая реализация в GitHub не равна runtime certification.

На эталонном 32-GiB узле необходимо проверить:

- Qwen3.6 делает корректную последовательность inspect/edit/check/diff;
- pinned llama.cpp стабильно поддерживает native tool turns;
- remote sandbox принимает read-only mount contract;
- pytest/static checks работают при read-only workspace;
- failing test приводит к repair или rollback;
- post-test edit требует повторного test evidence;
- diff budget блокирует oversized patch;
- cancellation не оставляет неконтролируемый sandbox execution;
- p50/p95 coding latency и CPU budget приемлемы.

Acceptance Sprint 50: система не имеет пути, где Qwen-фраза «готово» сама по себе превращает coding task в verified success.