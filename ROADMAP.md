# OLYA-AI / X1 Roadmap

## Sprint 0–26 — DONE

Базовая локальная AI-платформа: FastAPI, llama.cpp, auth, projects/roles, chat/history, files/retrieval, task state, quality verification, research/search, local-business analysis, admin/safety, frustration/performance, DOCX/PDF engine, image runtime/QA, code workspace, project runtime, isolated sandbox foundation, engineering agents, autonomous execution, Git/GitHub и chat-native project development.

## Sprint 27 — DONE

Commerce / Business / API: resource-derived plans, organizations, budgets, immutable resource ledger, provider-neutral payments, API keys/scopes/rate limits, persistent API contexts и telemetry.

## Sprint 28 — DONE

Installer / Recovery / Safe Updates: one-command installation, doctor/autotune, backup integrity, restore safety, migration guard, update/rollback и degraded subsystem readiness.

## Sprint 29 — DONE

Closed Beta foundation: beta cohorts, snapshots, D1/D7, success rate, task completion, frustration, p95 latency/queue, compute-minutes/user и data-sufficiency gate 50–100 users / 500 tasks.

## Sprint 30 — DONE

Complaint Regression: подтверждённая жалоба становится regression case; повтор дефекта может блокировать Stable release.

## Sprint 31–33 — DONE

Reliability / Operations / Active Checkpoints: persistent health checkpoints, root-cause propagation, stale detection, database/schema/inference/storage/queue/pipeline probes, route/migration/backup contracts и Admin System Health.

## Sprint 34 — DONE

Production hardening: persistent data, authoritative backup, consistent DB credentials, auth throttling, bounded queue wait, streamed uploads, sandbox fail-closed networking, code-runner hardening, image worker, persistent llama transport, Deep context alignment, freshness policy и production HTTP hardening.

## Sprint 35 — DONE (implementation)

Production Release Gate: isolated test container, historical Sprint 0–26 regression bundle (45 modules), compile/import, Alembic head, full regression, backup, non-destructive restore drill, load smoke, live long-context llama.cpp check, final readiness and Admin Release Readiness.

## Sprint 36 — DONE (implementation; real target-node data required)

Target-Node Capacity & Closed-Beta Launch Calibration:
- live target-node 8K / 12K / 16K context probes;
- p95 latency, llama RSS, swap growth and MemAvailable guardrails;
- current capacity report required by public release readiness;
- D1/D7/D30, p50/p95/p99, frustration, quality-supported request metrics and verified-success/CPU-minute;
- measured compute-plan recalculation only after 50–100 participants and >=500 tasks.

## Sprint 37 — DONE (implementation; real beta telemetry required for final numeric limits)

Closed-Beta Operations & Adaptive Capacity Control:
- persistent `BetaWave` lifecycle: planned → open → observing/paused → closed;
- staged participant admission instead of opening the beta to everyone at once;
- per-wave participant cap and independent CPU compute budget;
- admission is fail-closed when the target-node calibration is stale/failed;
- admission automatically pauses when wave success-rate, frustration, p95 queue/duration, verified-success/CPU efficiency or compute-budget guardrails fail;
- explicit audited admin override is available for exceptional admissions;
- production beta scheduler stores no more than one historical snapshot per configured interval and re-evaluates the active wave without running inference;
- D1/D7/D30 history plus anomaly detection for quality, latency, queue, compute efficiency and frustration;
- persistent versioned `CapacityPlan`: draft → approved → active, with signals, guardrails and diff from the previous plan;
- approval is blocked while measured calibration is incomplete or has blockers;
- live-safe reductions can be applied without restart; increases beyond the boot llama/governor envelope remain pending until the generated secret-free env artifact is applied and the runtime restarted;
- a pending-restart plan never replaces the currently active known-good plan in database state;
- rollback is append-only: X1 creates a new capacity-plan version based on a previously active plan;
- public release readiness requires an active capacity plan that matches the running configuration;
- beta feedback is joined with cohort/wave context and confirmed beta defects flow into the existing Complaint Regression system rather than a parallel bug tracker;
- `/admin/beta` exposes admission state, beta signals, trends, waves, capacity plans and feedback triage.

Final Free/X1/Pro/Max/Business numeric limits are deliberately **not fabricated** in Sprint 37. They must be calculated from the actual target node plus the required real beta sample.

## Sprint 38 — DONE

Progressive Public Launch & Measured Plan Finalization:
- convert the completed closed-beta measurements into final measured Free/X1/Pro/Max/Business resource envelopes and unit economics;
- introduce canary public rollout waves after closed beta instead of one global launch switch;
- automated comparison of canary vs last-known-good quality, latency, frustration and verified-success/CPU-minute;
- rollback/freeze of public expansion when canary guardrails regress;
- quota fairness under mixed Fast/Work/Deep/API/image workloads so one workload cannot starve the node;
- abuse/cost circuit breakers tied to measured CPU economics;
- operator incident runbook and launch dashboard for capacity, queue, quality, complaints and rollback state;
- require a fresh Sprint 35–37 release/capacity/restore evidence set before each public expansion step;
- after sufficient real data, persist the production tariff limits as a versioned commercial capacity policy rather than static assumptions.

## Sprints 39–40 — DONE (implementation; target-node acceptance still required)

One-command install/runtime chaos, release recovery guards, security/load closure and final acceptance orchestration.

## Sprints 41–58 — DONE

Adaptive Qwen3.6 routing, immutable model migration, real streaming/Stop, scope lock, long-term memory, RAG 2.0, freshness research, conditional verification, tool reliability, coding proof, autonomous development, document QA, budget transparency, graceful overload, measured server profiles, trusted UI, model regression and installer hardening.

## Sprint 59 — DONE

Local image editing with reference privacy, identity/vision QA and bounded worker execution.

## Sprint 60 — DONE

Unified user product shell for Chat, Projects, Files, Images, API and Account.

## Sprint 61 — DONE

Server-owned onboarding and measurable first value.

## Sprint 62 — DONE (implementation; target-node acceptance still required)

Recoverable exactly-once logical chat requests, disconnect/reconnect, explicit Stop, restart recovery and atomic canonical result commit.

## Sprint 63 — DONE

Projects as Product Core: bounded project workspace, project-scoped chat history, role-aware instructions and unified previews for Files, Memory, Tasks and Development.

## Sprint 64 — DONE

Files & RAG UX:

- explicit upload/processing/ready/error states;
- safe retry and manager-only delete/version management;
- current/history version navigation;
- visible file citations and source fragments used by Chat;
- recovery after parsing timeout, application restart and stale processing state;
- clear unsupported/empty/encrypted-file errors without pretending the file is available to RAG.

## Sprint 65 — DONE

Canonical ORM Recovery & Source Integrity:

- removed the truncated binary model payload and runtime `exec` bootstrap;
- restored a readable core registry and all migration-backed ORM models;
- added deterministic Alembic-to-ORM regeneration with a drift check;
- verified one complete 94-table metadata graph on a clean database;
- made source-integrity audits validate readable Python and generated-model synchronization.

## Sprint 66 — DONE

Source & Release Recovery Closure:

- restored engineering, image and project-runtime services as readable Python without compressed runtime execution;
- restored the missing Alembic root revision and a clean one-head migration chain;
- made migration-derived ORM generation preserve foreign keys and eliminated Alembic/ORM drift;
- made the FastAPI 0.141 route registry concrete and release-auditable while preserving dependency overrides;
- repaired image-editing/onboarding registration and stale product-surface contracts;
- added fresh-database, source-integrity and route-registry regression coverage.
