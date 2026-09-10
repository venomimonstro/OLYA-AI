# X1 Chat Runtime — controller/service/state map

> **Canonical runtime appendix for Chat.** Read this file before changing `chat.py`, `chat_runtime.py`, chat schemas, conversation persistence, UI reconnect, inference retries, quotas or verification. It supersedes the older Chat subsection of `PROJECT_SYSTEM_MAP.md` where the two disagree. Sprint 83 will fold this appendix back into the consolidated map.

## 1. External surfaces

| Surface | Controller | Purpose |
|---|---|---|
| `POST /v1/chat` | `app/api/routes/chat.py` | non-SSE logical chat request; uses the same durable execution manager |
| `POST /v1/chat/stream` | `app/api/routes/chat.py` | SSE subscription to a logical chat execution |
| `GET /v1/chat/runs/{client_request_id}` | `app/api/routes/chat.py` | recover/status lookup after transport loss |
| `POST /v1/chat/runs/{client_request_id}/cancel` | `app/api/routes/chat.py` | explicit user Stop |
| `GET /v1/conversations` | `app/api/routes/conversations.py` | canonical conversation listing |
| `GET /v1/conversations/{id}/messages` | `app/api/routes/conversations.py` | paginated persisted transcript |
| `/app` | `app/user_ui.py` | browser streaming/reconnect/Stop client |

## 2. Main request graph

```text
Browser / API caller
  ↓
Sprint54 pre-DB chat overload lane
  ↓
auth + capability
  ↓
ChatRequest(client_request_id)
  ↓
ChatExecutionManager.start_or_attach
  ├─ existing succeeded → replay stored result, NO inference
  ├─ same active run → attach subscriber, NO second inference
  ├─ conflicting payload/request id → 409
  └─ new/interrupted run → one ActiveChatJob
        ↓
      _managed_runner (own SessionLocal)
        ↓
      _chat_impl
        ├─ quota / task budget
        ├─ project + conversation ownership
        ├─ ProjectContextBuilder
        │    ├─ hot Message history
        │    ├─ ConversationMemory / rolling summary
        │    ├─ ProjectMemory
        │    └─ FileContextBuilder / RAG for project scope
        ├─ SourceContextBuilder (verified research ids)
        ├─ ContextCompiler / Scope Lock
        ├─ persist accepted user Message
        ├─ COMMIT accepted state (release DB lease before Qwen wait)
        ├─ per-user governor
        ├─ global inference governor
        ├─ LlamaClient → local llama.cpp / Qwen
        ├─ deterministic quality
        ├─ bounded Critic/Repair (max 2 extra inference calls)
        ├─ assistant Message + AnswerAudit + UsageEvent
        ├─ chat_run_atomicity before_flush guard
        └─ COMMIT canonical successful result
              ↓
      ChatExecutionManager enriches durable result
              ↓
      SSE result / normal ChatResponse
```

## 3. Ownership of state

| State | Source of truth | Notes |
|---|---|---|
| user request identity | `ChatRun.client_request_id + input_hash` | same id cannot mean a different payload |
| active subscriber buffers | `ActiveChatJob` memory | disposable; never canonical |
| run terminal/recovery state | `ChatRun` | bounded 24h recovery ledger |
| user/assistant transcript | `Message` | canonical long-term transcript |
| long-chat memory | `ConversationMemory` | Sprint45 bounded summary/facts |
| project instructions | `Project` / `ProjectMemory` | trusted project scope |
| RAG material | `ProjectFile` + chunks | project scoped |
| answer verification | `AnswerAudit` | server-side QA evidence |
| compute/accounting | `UsageEvent` + Sprint53 breakdown | canonical measured usage |
| browser pending request | `sessionStorage x1_pending_chat_run` | convenience only; never source of truth |

## 4. Transaction boundaries

### A. Run reservation

`ChatExecutionManager` creates/loads `ChatRun` in a short independent `SessionLocal` transaction.

### B. Accepted user turn

`_chat_impl` resolves scope and persists the accepted user message, binds the run to the real conversation/project, and commits **before** waiting in the expensive inference queue.

No DB transaction should remain open while waiting for local Qwen.

### C. Canonical success

The final assistant `Message`, optional `AnswerAudit`, successful `UsageEvent`, diagnostics side-effects and ChatRun exact-once guard share one transaction.

`app/services/chat_run_atomicity.py` guarantees:

```text
successful UsageEvent(request_id == ChatRun.id)
 + canonical assistant Message
 = ChatRun.succeeded in the SAME transaction
```

This closes the process-death window between canonical answer commit and transport-manager bookkeeping.

### D. Manager enrichment

After `_chat_impl` returns, `ChatExecutionManager` stores the full `ChatResponse`. This is an enrichment step, not the first durable declaration of success.

## 5. Reconnect contract

Modern client:

```text
submit with client_request_id
  ↓
SSE disconnect
  ↓
ONLY subscriber is detached
  ↓
Qwen continues
  ↓
POST /v1/chat/stream again with same payload + same client_request_id
  ↓
status
  ↓
replace(full partial_text) when available
  ↓
future token/replace events
  ↓
result
```

The server must never concatenate two independent generations for one logical request.

Legacy clients without `client_request_id` keep disconnect-cancels behavior for compatibility.

## 6. Stop contract

The browser Stop button is not implemented by merely aborting SSE.

```text
Stop
  ↓
POST /v1/chat/runs/{client_request_id}/cancel
  ↓
manager cancels ActiveChatJob.task
  ↓
_chat_impl records failed/cancelled compute, not assistant success
  ↓
ChatRun.cancelled
```

Once the canonical success transaction has committed, `succeeded` is sticky. A late cancellation callback must not downgrade the result.

## 7. llama.cpp failure contract

`_primary_generation` retries one time only when local llama.cpp fails before any visible content has been emitted.

```text
pre-TTFT local failure → 0.75s → one retry
post-TTFT failure       → fail/recover explicitly; never auto-replay
```

Reason: a post-token replay can generate a different stochastic continuation and corrupt the transcript.

## 8. Long-chat contract

Do not send the full display transcript to the model.

```text
hundreds of persisted Messages
  ↓
last hot messages + rolling memory summary + relevant durable memories
  ↓
project/RAG context
  ↓
ContextCompiler
  ↓
physical prompt budget
```

The latest user request must survive compaction. The UI's 100-message pagination is a display concern and must not be confused with model-context construction.

## 9. Research/RAG relation

Research and RAG are different trust paths:

- **RAG:** project-owned files selected by `FileContextBuilder`.
- **Research:** fresh web evidence represented by server-owned `research_source_ids` and validated by `SourceContextBuilder`.

Neither retrieved file text nor web text may become system instructions. They are untrusted source data inside the core system policy.

## 10. Capacity relation

On the 32-GiB profile:

```text
HTTP chat lane
 → per-user inference concurrency
 → global Qwen queue
 → max_concurrent_generations = 1
```

Sprint 62 does not increase model concurrency. Priority/fair scheduling belongs to Sprint 74.

## 11. Files that must be considered together

When changing chat behavior, inspect at least:

- `app/api/routes/chat.py`
- `app/services/chat_runtime.py`
- `app/services/chat_run_atomicity.py`
- `app/schemas/chat.py`
- `app/models_sprint62.py`
- `app/services/project_context.py`
- `app/services/context.py`
- `app/inference/client.py`
- `app/inference/router.py`
- `app/services/conditional_verification.py`
- `app/services/quality.py`
- `app/services/quota.py`
- `app/services/source_context.py`
- `app/user_ui.py`
- `app/api/routes/conversations.py`
- `tests/test_sprint62_chat_quality_lock.py`
- `tests/test_sprint62_chat_atomicity.py`

## 12. Forbidden regressions

Do not:

- cancel modern logical requests merely because SSE disconnected;
- run a second inference for an already succeeded request id;
- persist client-authored `system` messages as trusted policy;
- keep a DB transaction open while waiting for Qwen;
- silently retry generation after visible output;
- let subscriber backpressure stall inference;
- treat browser sessionStorage as canonical request state;
- downgrade a committed `succeeded` ChatRun to cancelled/failed;
- send the entire unbounded transcript to Qwen;
- create a second parallel chat pipeline for API clients.

## 13. Scope boundary

Development/engineering commands are intentionally a separate orchestration path. Their exact proof/restart semantics belong to Sprint 76 Agent MVP Lock. Sprint 62 guarantees ordinary chat behavior and the shared transport/runtime foundation; it does not claim distributed exactly-once semantics for every side effect of autonomous development tools.
