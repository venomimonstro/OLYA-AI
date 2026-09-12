# Sprints 71–86 — MVP plan status

Кодовые контракты Sprint 71–86 реализованы. Production launch всё ещё требует внешних target-node прогонов; отсутствие фактического прогона не считается успешным acceptance.

## Sprint 71 — DONE — Admin Control Center
## Sprint 72 — DONE — Admin User Operations
## Sprint 73 — DONE — Capability Registry
## Sprint 74 — DONE — Priority Inference Scheduler
## Sprint 75 — DONE — End-to-End Deadline Budget
## Sprint 76 — DONE — Agent MVP Lock
## Sprint 77 — DONE — Image Studio Beta Lock
## Sprint 78 — DONE — Product Analytics
## Sprint 79 — DONE — Frontend Polish / Minimal UI
## Sprint 80 — HARNESS DONE / TARGET RUN REQUIRED — 10-User Real Load Acceptance
## Sprint 81 — DONE — Security MVP Pen-Test Simulation
## Sprint 82 — DONE — Crash / Recovery / Data Integrity
## Sprint 83 — DONE — Architecture Cleanup
## Sprint 84 — DONE — Business Logic Contract
## Sprint 85 — GATE DONE / RC NOT ISSUED — MVP Freeze / Release Candidate

Feature freeze и единый RC gate реализованы. RC выдаётся только после полного regression/security/recovery/runtime gate, backup/restore, model evidence и реального Sprint80 load acceptance. Evidence привязан к текущему git HEAD и deterministic source fingerprint; running app image обязан совпадать с checkout. RAM gate использует минимум из `model-manifest.json`, поэтому валидны и 32, и 48, и 64+ GiB production hosts.

## Sprint 86 — HARNESS DONE / PRODUCTION RUN REQUIRED — MVP Production Acceptance

`production_acceptance.py` проверяет кандидата на целевом сервере с реальной Qwen, PostgreSQL, SearXNG, sandbox/document workers, release-readiness, business contract и live capability registry. Дополнительно обязательны clean working tree, совпадение candidate/runtime/public build fingerprint, HTTPS для внешнего production URL и рабочая billing-конфигурация. `accepted_for_launch=true` допустим только при зелёных фактических проверках.
