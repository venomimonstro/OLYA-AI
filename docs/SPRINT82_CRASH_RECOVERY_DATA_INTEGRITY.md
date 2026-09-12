# Sprint 82 — Crash / Recovery / Data Integrity — DONE

Sprint 82 объединяет существующие durability механизмы в один release-readable integrity contract.

- Background jobs используют idempotency key, lease owner/token, compare-and-set transition и bounded attempts.
- Expired jobs после retry exhaustion переводятся в failed, а maintenance до retention deletion согласует зависимые image/edit rows.
- Stale file processing переводится в recoverable state; stale document QA возвращается в pending с `qa_recovered` event.
- Autonomous agent slots очищаются после stale heartbeat, а durable checkpoints остаются источником resume.
- Chat success защищён atomic listener: успешный UsageEvent + assistant Message terminalizes ChatRun в той же DB transaction; поздний cancel не может downgrade committed success.
- Stale non-terminal ChatRun становится `interrupted` и может быть безопасно resumed в пределах attempt budget.
- `recovery_integrity_snapshot()` показывает expired leases, stale chat runs, stale agent slots, orphan image jobs и freshness maintenance checkpoint как release blockers.
- `reconcile_and_snapshot()` выполняет безопасный maintenance reconcile и сразу возвращает integrity state в caller transaction.
- Добавлены `recovery_integrity_audit` и Sprint82 tests в full regression.
