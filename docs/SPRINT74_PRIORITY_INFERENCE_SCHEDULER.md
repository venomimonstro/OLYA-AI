# Sprint 74 — Priority Inference Scheduler — DONE

Sprint 74 переводит общий локальный inference admission с FIFO-семафора на bounded priority/fair scheduler без обхода существующих ограничений.

- `ResourceGovernor` остаётся единственным общим владельцем `max_concurrent`, `max_queue` и queue timeout.
- Классы приоритета: Fast, Work, API, Deep, Background.
- Тариф влияет только на небольшой queue-order boost; он не увеличивает concurrency сам по себе и не отменяет quota/user governor.
- Aging улучшает score каждую секунду ожидания и не ограничен сверху, поэтому Deep/background workload не может бесконечно голодать.
- `ensure_compute_available()` передаёт scheduler’у canonical plan, user principal и channel через request-local `ContextVar` только после compute/resource budget checks.
- API автоматически определяется существующим `current_channel_override()`; остальные запросы классифицируются по reserve budget Fast/Work/Deep.
- Старые вызовы `governor.slot()` автоматически участвуют в scheduler без копирования policy по endpoint’ам.
- Scheduler snapshot показывает active/waiting, очереди по priority/channel, oldest wait, grants, timeouts и rejections.
- Добавлены `priority_scheduler_audit` и Sprint 74 tests в full regression gate.
