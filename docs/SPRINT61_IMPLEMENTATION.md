# Sprint 61 — Onboarding / First Value

## Goal

A newly registered X1 user must not land in an unexplained empty workspace. Sprint 61 adds a short first-value path while keeping the product source of truth on the server:

```text
register/login
    ↓
server onboarding status
    ↓
/welcome when guidance is still visible
    ├─ chat → /app → successful local Qwen answer
    ├─ project → real POST /v1/projects
    └─ file → real project upload/parser/RAG pipeline
    ↓
authoritative milestone reconciliation
    ↓
onboarding completed after the first successful product action
```

The onboarding page does not mark success because a button was clicked. Success is reconstructed from persisted product facts.

## Durable state

`app/models_sprint61.py` adds two tables.

### `user_onboarding`

One row per account:

- `started_at`
- `first_chat_at`
- `first_successful_answer_at`
- `first_project_at`
- `first_file_at`
- `completed_at`
- `dismissed_at`
- `reopened_at`
- `show_again`

This state survives browser refresh, session replacement, application restart and context compaction.

### `product_events`

A deliberately small append-only first-value ledger. Sprint 61 writes only bounded milestone events with deterministic dedupe keys:

- `registered`
- `first_chat_started`
- `first_successful_answer`
- `first_project_created`
- `first_file_uploaded`
- `onboarding_completed`
- `onboarding_dismissed`
- `onboarding_reopened`

A user cannot inflate these first-value events by repeatedly refreshing the onboarding endpoint.

The table is intentionally suitable as a foundation for Sprint 78 product analytics, but Sprint 61 does not introduce a general arbitrary event-ingestion API.

## Why reconciliation instead of callbacks everywhere

`app/services/onboarding.py` derives milestones from authoritative rows:

| Milestone | Source of truth |
|---|---|
| registration | `User.created_at` |
| chat started | first owned `Conversation` |
| successful AI answer | first successful `UsageEvent` |
| project created | first owned `Project` |
| file uploaded | first `ProjectFile` uploaded by the user with `status=ready` |

This design deliberately avoids adding onboarding writes to Chat, Project, Files and future API controllers. If a process crashes after the business transaction succeeds, the next onboarding status request reconstructs the missing milestone.

The user row is locked with `SELECT ... FOR UPDATE` during reconciliation so concurrent onboarding requests cannot create duplicate state on PostgreSQL. Product-event dedupe keys add a second database-level idempotency boundary.

## Completion semantics

A bare conversation is not considered first value.

Onboarding auto-completes after the earliest of:

- a successful AI answer;
- a successfully created owned project;
- a successfully parsed/ready uploaded file.

`first_chat_at` is still recorded independently for funnel analysis.

A user can dismiss onboarding without pretending they achieved value. `dismissed_at` and `completed_at` remain separate.

A completed or dismissed user can explicitly reopen the guide. `show_again` is server-owned, so the decision survives new sessions and browsers.

## API

The endpoints are part of the already registered account controller, so no new top-level router-registration dependency is required:

```text
GET  /v1/account/onboarding
POST /v1/account/onboarding/dismiss
POST /v1/account/onboarding/reopen
```

All require the normal authenticated X1 session. They are intentionally outside expensive AI rollout admission because reading onboarding state does not consume scarce model/sandbox/image capacity.

## UI

`app/onboarding_ui.py` is included by `app/public_ui.py` and provides:

```text
GET /welcome
```

The page contains three first-value paths:

1. **Start chat** — opens the existing Sprint 60 `/app` chat.
2. **Create project** — calls the existing Projects API.
3. **Upload file** — uses the existing project-file upload endpoint and therefore the normal parser, size, storage, role and RAG rules.

There is no duplicate project/file business logic in the UI.

The page also shows persisted milestone progress, supports Dismiss and Reopen, and is protected with CSP/noindex/no-store headers.

## Login/registration routing

After successful registration or login, `app/public_ui.py` calls the authenticated onboarding-status endpoint using the newly issued session token:

```text
visible=true  → /welcome
visible=false → /app
```

If the onboarding-status request itself is temporarily unavailable, the fallback is `/app`; an onboarding telemetry failure must not lock a valid user out of the product.

## Privacy

`ProductEvent` and `UserOnboarding` are user-associated records, therefore `/v1/account/export` now includes both. Onboarding/product analytics are not hidden from the data export.

No prompt content, assistant answer text or file contents are copied into `ProductEvent`. Only compact IDs and milestone metadata are stored.

## Migration

Alembic revision:

```text
f61b0a4d2c90
    ↓
f60a93c7d511
```

Sprint 61 does not modify the compressed base `User` model. New state is an extension table, which keeps the migration small and lowers rollout risk.

## Regression coverage

`tests/test_sprint61_onboarding_first_value.py` covers:

- a clean account receives visible onboarding;
- registration event is persisted once;
- real project creation is reconciled into the first-value state;
- successful first value auto-completes onboarding;
- repeated reconciliation does not duplicate milestone events;
- dismiss is server-persisted;
- reopen is server-persisted;
- `/welcome` is registered and protected by CSP/noindex;
- registration UI consults server onboarding status;
- migration lineage remains linear;
- reconciliation reads authoritative project/file/chat/usage facts.

## Acceptance requiring a target node

Static and TestClient regressions are not a substitute for the real production journey. Before MVP acceptance, execute on the target deployment:

```text
new browser/session
→ register
→ /welcome
→ start chat
→ receive a successful Qwen response
→ logout
→ login
→ /app (onboarding no longer forced)
```

Repeat with the project path and file path, plus process restart between the product action and the next onboarding status request to prove recovery semantics.

## Scope boundary

Sprint 61 intentionally does not implement:

- full project-centric workspace redesign (Sprint 63);
- complete Files/RAG UX (Sprint 64);
- commercial API console (Sprint 68);
- broad retention analytics (Sprint 78).

It provides the durable first-value layer those later product sprints can reuse.
