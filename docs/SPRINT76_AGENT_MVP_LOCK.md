# Sprint 76 — Agent MVP Lock — DONE

Sprint 76 фиксирует существующий development/engineering agent loop как bounded, recoverable и evidence-driven контур вместо добавления ещё одного автономного движка.

- Agent completion больше не доверяет `plan.status` или тексту модели.
- `agent_completion_blockers()` независимо проверяет все sprints/work items, canonical Task status, task evidence gate и существующий engineering execution.
- Если engineering execution существует, завершение допускается только при `status=verified`; planned/blocked/needs_repair/rolled_back не считаются успехом.
- Autonomous ledger публикует `completion_gate` с blockers и proof source; contract drift переводит ledger в blocked.
- Subagent total/parallel budgets остаются жёсткими; blocked/completed ledger не может запускать новые subagents.
- `continue` не запускает новый автономный шаг после request deadline.
- Pause/resume/status/rollback остаются явными control commands.
- Checkpoints, immutable contract hash и stale-slot recovery сохраняются как restart/recovery contract.
- Canonical Task completion по-прежнему требует verified evidence для обязательных criteria и соблюдения step/compute budgets.
- Patch execution сохраняет approved scope, pre-change snapshot, verification и rollback.
- Existing ToolSession остаётся bounded по call budget/repetition/retries и блокирует replay после неопределённого write outcome.
- Добавлены `agent_mvp_lock_audit` и Sprint 76 tests в full regression gate.
