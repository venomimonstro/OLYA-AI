# Sprint 75 — End-to-End Deadline Budget — DONE

Sprint 75 вводит один request-rooted monotonic deadline budget вместо набора независимых timeout, которые могли суммарно продолжать работу после пользовательского дедлайна.

- Deadline создаётся на внешней ASGI-границе до auth и body parsing.
- `X-X1-Deadline-Ms` может только сократить server timeout и ограничен 1–900 секунд.
- Внутренние auth/API layers переиспользуют исходный budget и не могут вернуть уже потраченное время.
- Priority inference queue ограничивает ожидание оставшимся request budget.
- Llama streaming generation и native tool-selection turn завершаются по remaining budget.
- Research fetch, включая DNS/network phase, имеет общий deadline поверх локального fetch timeout.
- Host verification commands и container/remote sandbox operations clamp локальный timeout по remaining budget.
- Cleanup sandbox resources намеренно разрешён после deadline, чтобы timeout не оставлял контейнеры висеть.
- Доменные timeout остаются более строгими, если они короче общего дедлайна.
- Добавлены `deadline_budget_audit` и Sprint 75 tests в full regression gate.
