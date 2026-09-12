# Sprints 71–86 — remaining MVP plan

После завершения Sprint 72 до production acceptance остаётся 14 спринтов.

## Sprint 71 — DONE — Admin Control Center
Единая операционная панель администратора: здоровье, нагрузка, очереди, capacity, billing/API/complaints/release-сигналы без дублирования существующих admin services.

## Sprint 72 — DONE — Admin User Operations
Безопасные операции над пользователями: поиск, account state, сессии, billing-aware plan/quota overrides, действующие safety restrictions, audit trail и fail-closed destructive actions.

## Sprint 73 — Capability Registry
Единый registry возможностей X1 с availability/reason/requirements вместо разрозненных feature flags и предположений UI.

## Sprint 74 — Priority Inference Scheduler
Справедливый приоритетный scheduler для Fast/Work/Deep/API/background workload с starvation protection и учётом тарифа без обхода resource governors.

## Sprint 75 — End-to-End Deadline Budget
Один deadline budget через HTTP admission, research, queue, inference, verification и tools, чтобы запрос не продолжал дорогую работу после исчерпания пользовательского времени.

## Sprint 76 — Agent MVP Lock
Фиксация минимально надёжного agent/development контура: bounded autonomy, доказуемые tool results, stop/recovery и запрет ложного completion.

## Sprint 77 — Image Studio Beta Lock
Фиксация beta-контракта генерации/редактирования изображений: capability truth, privacy, queue/resource limits, QA и честный unavailable state.

## Sprint 78 — Product Analytics
Server-owned продуктовая аналитика activation, first value, retention, task success, frustration, plan conversion и resource economics без хранения лишнего пользовательского контента.

## Sprint 79 — Frontend Polish / Minimal UI
Финальная консолидация пользовательского UI: mobile-first, единая навигация/состояния, accessibility, loading/error/recovery и удаление временных UX-слоёв.

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
