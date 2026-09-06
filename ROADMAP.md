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
- `scripts/capacity_calibrate.py` measures the deployed llama container rather than using estimated limits;
- candidate contexts: 8192 / 12288 / 16384;
- each candidate records live inference success, p95 latency, peak llama RSS, swap growth and minimum host MemAvailable;
- automatic recommendation chooses the largest context that passes memory/swap/latency guardrails;
- recommended concurrency remains conservative for the CPU node and queue size/timeout are derived from measured service latency;
- calibration produces `capacity-latest.json` and a review-only `capacity-recommended.env`;
- release gate now runs capacity calibration during `--runtime --live-inference`;
- public release readiness blocks when target-node capacity data are missing/stale/failed;
- beta analytics now include D30, p50/p95/p99 latency and queue, quality-supported request metrics and explicit active/paused/removed participant counts;
- `GET /v1/admin/beta/current` returns live metrics without mutating history;
- `GET /v1/admin/beta/calibration` combines measured server capacity with measured beta behavior;
- measured monthly compute limits are not recalculated until the beta data gate is met: 50–100 participants and >=500 tasks;
- D1/D7/D30 are reported as product signals; no arbitrary retention release threshold is invented.

### Sprint 36 target-node gate

On the actual production CPU/RAM node:

```bash
python3 scripts/release_gate.py --runtime --live-inference
```

The generated capacity report must be current and `GET /v1/admin/reliability/release-readiness` must return no blockers.

## Sprint 37 — NEXT

Closed-Beta Operations & Adaptive Capacity Control:
- cohort rollout waves instead of admitting all users at once;
- per-wave capacity budgets and automatic admission pause when queue/error/frustration guardrails break;
- real daily D1/D7/D30 trend comparison;
- anomaly detection for regressions in quality, latency, queue, compute efficiency and frustration;
- capacity-plan versioning and safe operator approval before changing `.env` limits;
- rollback to last-known-good capacity plan;
- beta feedback triage integrated with Complaint Regression;
- final measured Free/X1/Pro/Max/Business limits based on actual production CPU economics rather than assumptions.
