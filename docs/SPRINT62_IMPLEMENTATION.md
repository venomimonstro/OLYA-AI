# Sprint 62 — Chat MVP Quality Lock

## Goal

Sprint 62 makes the ordinary X1 chat the most reliable MVP surface. The target contract is not merely “the model answers”; it is:

```text
accepted user turn
  → durable logical request id
  → bounded queue
  → local Qwen generation
  → optional verification
  → canonical assistant/usage commit
  → recoverable terminal result
```

A browser network failure must not silently become a user cancellation. A user pressing **Stop** must remain an explicit cancellation. A process restart must not create a second logical request when the first answer was already committed.

## Durable logical request

`ChatRequest.client_request_id` is an optional strict identifier. The Sprint 60 UI supplies it for every new message.

`app/models_sprint62.py` adds `chat_runs` with:

- `user_id`;
- `project_id` / `conversation_id`;
- `client_request_id`;
- payload fingerprint (`input_hash`);
- `running / succeeded / failed / cancelled / interrupted` status;
- runtime id and attempt;
- bounded recovery result;
- error metadata and timestamps.

The database enforces uniqueness of `(user_id, client_request_id)`. Reusing one request id with different payload is rejected rather than interpreted as a new request.

Migration lineage:

```text
f62c1b7e3d20
    ↓
f61b0a4d2c90
```

## Execution manager

`app/services/chat_runtime.py` is the transport/recovery coordinator. It deliberately assumes the current low-resource deployment model of a single Uvicorn application worker while PostgreSQL supplies durable recovery state.

Responsibilities:

- one in-memory execution per logical `(user, client_request_id)`;
- multiple streaming subscribers may attach to the same execution;
- a slow subscriber cannot backpressure Qwen;
- if a subscriber falls behind, its queue is replaced with a full current text snapshot;
- terminal result replay avoids a second inference call;
- explicit cancellation terminates the managed task;
- an old-runtime `running` row can be resumed with the same run id after application restart;
- terminal recovery rows have bounded retention.

## Disconnect is not Stop

Modern `/app` requests always send `client_request_id`.

For those requests:

```text
network / browser SSE disconnect
    ↓
detach subscriber only
    ↓
managed inference keeps running
    ↓
reconnect same client_request_id
    ↓
server sends complete partial-text snapshot
    ↓
future tokens / terminal result continue
```

The UI stores the pending logical request in `sessionStorage` as `x1_pending_chat_run` and attempts bounded recovery.

**Stop** is separate:

```text
POST /v1/chat/runs/{client_request_id}/cancel
```

and produces terminal `cancelled` state.

Legacy clients that omit `client_request_id` retain the old disconnect-cancels behavior for compatibility.

## Recovery API

```text
GET  /v1/chat/runs/{client_request_id}
POST /v1/chat/runs/{client_request_id}/cancel
```

Both require the ordinary authenticated user and are scoped by user id. They are release-critical product routes in `scripts/product_surface_audit.py`.

## Canonical accepted user turn

The user's accepted turn is persisted before entering the scarce local inference queue. This solves two problems:

1. a disconnect or llama.cpp failure cannot erase the question the server had accepted;
2. retry after failure can reuse that trailing canonical user turn instead of manufacturing duplicates.

`_persist_accepted_user_turn()` only deduplicates when the current last canonical message is the same user text. If an assistant answer already follows it, repeating the same text is treated as a new intentional turn.

## Llama restart behavior

A local llama.cpp restart/connect failure is retried once only when no content has been emitted yet.

```text
failure before TTFT → one short retry
failure after visible token → no automatic replay
```

Replaying after the first visible token could concatenate two different stochastic continuations, so post-TTFT failures are explicit and recoverable instead.

## Exact-once crash safety after success

The most important hardening addition is `app/services/chat_run_atomicity.py`.

Before this guard there was a narrow crash window:

```text
assistant Message + successful UsageEvent COMMIT
        ↓
process dies here
        ↓
ChatExecutionManager did not yet persist ChatRun=succeeded
        ↓
restart could repeat the same inference
```

Sprint 62 closes that window with a SQLAlchemy `before_flush` transaction invariant.

When a successful `UsageEvent.request_id` refers to a `ChatRun` and a canonical assistant `Message` exists in the same conversation, that `ChatRun` becomes `succeeded` **inside the same database transaction**.

The listener handles both normal ordering and the verification path where `AnswerAudit` caused an intermediate flush before `UsageEvent` was added.

A compact schema-valid recovery response is stored immediately. In the healthy path, `ChatExecutionManager` replaces it milliseconds later with the full response/telemetry. If the process dies first, the compact response is enough to return the already committed answer without a second Qwen call.

Successful terminal state is sticky: a very late Stop/error callback cannot downgrade an already committed `succeeded` run.

## Long conversation behavior

Sprint 62 keeps the existing bounded architecture instead of sending hundreds of raw messages to Qwen:

- hot conversation messages remain canonical;
- Sprint 45 rolling summary/memory represents older context;
- project memory and RAG are bounded separately;
- `ContextCompiler` reserves prompt space for the current request;
- a 300-message regression checks that the newest request survives compaction and the final prompt remains inside its configured character budget.

The client UI paginates historical messages in pages of up to 100 for display; display history and model context are deliberately separate concerns.

## Streaming behavior

The browser remains token-streaming and supports:

- `status`;
- `token`;
- `replace`;
- `heartbeat`;
- `result`;
- `error`;
- `cancelled`.

`replace` is used both by verification repair and by subscriber resynchronisation/reconnect. A reconnect receives the full already accumulated text before later tokens.

## Database/resource behavior

The HTTP request dependency session is not retained as the execution session for the long-running managed chat. The manager runner creates its own bounded `SessionLocal` context, while `_chat_impl` explicitly commits accepted state before waiting on Qwen.

The existing layered capacity controls remain:

```text
HTTP overload lane
 → authenticated user capability/quota
 → per-user inference governor
 → global inference governor
 → one local Qwen generation on the 32-GiB profile
```

Sprint 62 does not increase Qwen concurrency.

## Regression coverage

`tests/test_sprint62_chat_quality_lock.py` covers:

- strict request id contract;
- payload-sensitive idempotency fingerprints;
- terminal result replay;
- request-id conflict;
- explicit cancellation;
- previous-runtime resume with the same run id;
- slow subscriber full-text resync;
- accepted-user-turn durability/deduplication;
- llama retry only before visible output;
- no replay after first token;
- bounded 300-message context;
- UI reconnect and Stop wiring;
- migration lineage;
- account export privacy contract.

`tests/test_sprint62_chat_atomicity.py` additionally covers:

- assistant + successful usage makes the run terminal in the same transaction;
- an intermediate verification flush does not break the invariant;
- failed usage does not fake success;
- compact recovery result validates as `ChatResponse`;
- successful terminal state cannot be overwritten by a late cancel.

## Target-node acceptance

Sprint 62 is not considered production-verified until the real deployment demonstrates all of the following with local Qwen:

```text
1. first new-user message → streaming answer
2. Fast / Work / Deep
3. verification auto / strict / off
4. research-backed answer
5. project + RAG-backed answer
6. 300+ message conversation display/history
7. network disconnect during generation → reconnect same request → same result
8. explicit Stop → cancelled, no successful assistant result
9. llama restart before TTFT → bounded recovery
10. llama failure/restart after TTFT → no concatenated second generation
11. app restart with a running request → same run id recovery
12. forced process death immediately after canonical answer commit → no duplicate answer/UsageEvent
13. overload / queue rejection → retryable controlled UX
```

## Scope boundary

Sprint 62 deliberately does not turn development-agent commands into an exactly-once distributed workflow. Project development commands still enter the specialized engineering/development pipeline, whose Proof-of-Result, sandbox and autonomous-state semantics are hardened separately and receive their product lock in Sprint 76.

Likewise, priority scheduling across Fast/Work/Deep/background workloads belongs to Sprint 74 and end-to-end deadline budgeting belongs to Sprint 75.
