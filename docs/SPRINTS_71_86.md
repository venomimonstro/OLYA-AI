# Sprints 71–86 — remaining MVP plan

После завершения Sprint 79 до production acceptance остаётся 7 спринтов.

## Sprint 71 — DONE — Admin Control Center
Единая операционная панель администратора: здоровье, нагрузка, очереди, capacity, billing/API/complaints/release-сигналы без дублирования существующих admin services.

## Sprint 72 — DONE — Admin User Operations
Безопасные операции над пользователями: поиск, account state, сессии, billing-aware plan/quota overrides, действующие safety restrictions, audit trail и fail-closed destructive actions.

## Sprint 73 — DONE — Capability Registry
Единый server-owned registry возможностей X1 с availability/reason/requirements, account state, effective quota/compute budget, Safety context и live sandbox probe без публикации секретов конфигурации.

## Sprint 74 — DONE — Priority Inference Scheduler
Bounded priority/fair scheduler для Fast/Work/API/Deep/background с plan-aware queue ordering, starvation protection и сохранением canonical quota/user/resource governors.

## Sprint 75 — DONE — End-to-End Deadline Budget
Один request-rooted monotonic deadline через auth, queue, local inference, research, verification и sandbox tools без возврата уже потраченного времени.

## Sprint 76 — DONE — Agent MVP Lock
Bounded и recoverable development/engineering loop с immutable contract, budgets/checkpoints, evidence-only completion, verified execution gate, pause/resume/rollback и запретом ложного completion.

## Sprint 77 — DONE — Image Studio Beta Lock
Canonical image beta contract поверх Capability Registry: honest availability, private sources, worker/quota/QA gates, explicit training consent и fail-closed Studio UI.

## Sprint 78 — DONE — Product Analytics
Privacy-minimized server-owned activation/first-value/retention/task-success/frustration/paid-conversion/resource-economics analytics и `/admin/analytics`.

## Sprint 79 — DONE — Frontend Polish / Minimal UI
Executable mobile/recovery/navigation/token-storage UX contract для `/app`, `/studio` и admin surfaces; удаление Sprint70 composition layer зарезервировано для Sprint83.

## Sprint 80 — 10-User Real Load Acceptance
Реальный acceptance на первой волне минимум 10 пользователей: concurrency, p95, queue, failure/recovery, CPU/RAM/swap и стоимость успешного запроса.

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
