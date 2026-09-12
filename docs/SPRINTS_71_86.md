# Sprints 71–86 — remaining MVP plan

После Sprint 80 остаётся 6 спринтов до production acceptance. Sprint 80 harness готов, но реальный green load result требует target server и будет обязательным входом Sprint86.

## Sprint 71 — DONE — Admin Control Center
## Sprint 72 — DONE — Admin User Operations
## Sprint 73 — DONE — Capability Registry
## Sprint 74 — DONE — Priority Inference Scheduler
## Sprint 75 — DONE — End-to-End Deadline Budget
## Sprint 76 — DONE — Agent MVP Lock
## Sprint 77 — DONE — Image Studio Beta Lock
## Sprint 78 — DONE — Product Analytics
## Sprint 79 — DONE — Frontend Polish / Minimal UI

## Sprint 80 — HARNESS DONE — 10-User Real Load Acceptance
Executable real-server harness требует ≥10 уникальных authenticated users и fail-closed по p95/error-rate/queue/throughput. Green target run ещё не заявлен.

## Sprint 81 — Security MVP Pen-Test Simulation
Системная симуляция атак по auth/API/files/research/sandbox/Git/billing/admin с regression cases для найденных дефектов.

## Sprint 82 — Crash / Recovery / Data Integrity
Kill/restart/DB/network/disk сценарии, exactly-once критических операций, восстановление незавершённых jobs и проверка отсутствия повреждения данных.

## Sprint 83 — Architecture Cleanup
Удаление временных compatibility layers, dead code и дублирующихся путей; подтверждение одного владельца для каждого доменного процесса.

## Sprint 84 — Business Logic Contract
Фиксация инвариантов тарифов, billing, quota, organizations, API, projects, tasks, agents и release policy как executable contract tests.

## Sprint 85 — MVP Freeze / Release Candidate
Feature freeze, полный regression/security/recovery/load gate, миграции, backup/restore и формирование одного release candidate без новых функций.

## Sprint 86 — MVP Production Acceptance
Финальная проверка на целевом production-сервере с реальной Qwen, PostgreSQL, SearXNG и workers; запуск допускается только при зелёных acceptance gates.
