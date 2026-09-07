# OLYA AI — спринты 41–58: качество, стабильность и 32 ГБ CPU/RAM

Дата проектирования: 2026-09-07.

## Цель

Сделать OLYA AI не просто оболочкой над локальной LLM, а устойчивой системой, которая компенсирует слабые стороны моделей: потерю контекста, галлюцинации, медленный first token, лишнее thinking, циклы tool calling, непрозрачные лимиты, внезапный `server busy`, ошибки при работе с кодом и документами.

Базовая целевая машина первой production-волны: 32 ГБ RAM, CPU inference, без обязательной GPU.

Целевая основная модель: Qwen3.6-35B-A3B Q4_K_M после прохождения Sprint 42 acceptance. До завершения миграции текущая production-модель не должна меняться частично.

## Что повторяется в негативных отзывах на AI-продукты

1. Модель забывает требования и детали длинного диалога.
2. Модель отвечает на предполагаемый вопрос вместо буквального запроса пользователя и самовольно расширяет scope.
3. Thinking может занимать десятки секунд даже там, где задача простая.
4. UI долго ничего не показывает либо зависает в середине ответа.
5. Лимит наступает неожиданно; пользователю непонятно, сколько ресурса осталось и почему запрос дорогой.
6. `server busy`, saturation и падение одного контура ломают рабочий процесс целиком.
7. Галлюцинация или ошибочный ответ расходует лимит, после чего пользователь вынужден платить вычислениями за исправление ошибки системы.
8. Агент зацикливается, делает malformed tool calls или повторяет один и тот же неуспешный шаг.
9. Coding-agent меняет лишние файлы, пропускает часть требований либо утверждает, что работа сделана, хотя тесты не проходили.
10. Compaction длинного контекста выбрасывает критические решения проекта.
11. После обновления модели/шаблона качество может измениться без видимой причины.
12. На локальных Qwen неправильные context/KV/thread/tool настройки способны сделать сильную модель практически непригодной.

---

## Sprint 41 — Adaptive Intelligence Router + anti-waste foundation

**Цель:** каждый запрос получает минимально достаточный compute budget.

- заменить примитивную маршрутизацию Fast/Work/Deep на scoring;
- учитывать длину, тип задачи, риск ошибки, код/архитектуру/исследование;
- запретить Deep только из-за одного случайного ключевого слова;
- включать reasoning в Work только для действительно сложных Work-задач;
- фиксировать причину маршрута и complexity score для диагностики;
- регрессионные тесты на простые, сложные и конфликтные запросы.

**Acceptance:** простые rewrite/translation не получают thinking; аудит/архитектура/research получают Deep; средняя аналитика получает Work с reasoning только при достаточной сложности; физический context ceiling никогда не превышается.

## Sprint 42 — Qwen3.6-35B-A3B migration для 32 ГБ

- закрепить Qwen3.6-35B-A3B Q4_K_M по immutable revision + SHA-256;
- обновить downloader, Compose, installer, doctor, release contracts;
- 32 ГБ: 8K production default context, безопасный memory reserve для ОС/DB/API/search;
- Q4_K_S/IQ4_XS оформить как аварийные low-RAM варианты, а не скрытый автоматический downgrade;
- benchmark old/new на одном наборе задач;
- rollback на прошлую модель одной операцией.

**Acceptance:** install с нуля + upgrade проходят; нет OOM при стресс-тесте; качество не ниже установленного regression baseline.

## Sprint 43 — настоящий token streaming и cancellation

- llama.cpp `stream=true`;
- проксирование chunks до браузера через SSE;
- TTFT, tokens/sec и queue time telemetry;
- Stop отменяет upstream inference, а не только закрывает UI;
- disconnect клиента освобождает generation slot.

**Acceptance:** первый полезный token виден максимально рано; отменённый запрос не продолжает жечь CPU.

## Sprint 44 — Scope Lock и instruction adherence

- компиляция явных требований пользователя в отдельный task contract;
- разделение `must`, `must_not`, scope, output format;
- детектор самовольного расширения scope;
- перед финалом deterministic check: все обязательные пункты либо выполнены, либо явно отмечены как невыполнимые;
- не дописывать непрошенные разделы в простых задачах.

**Acceptance:** regression corpus запросов «сделай только X» не превращается в X+Y+Z.

## Sprint 45 — память без деградации длинных чатов

- Hot Context: последние релевантные сообщения;
- Rolling Summary: структурированное резюме старой части;
- Decision Memory: решения/ограничения проекта отдельно от пересказа;
- Semantic Memory: retrieval фактов вместо постоянного stuffing;
- versioned summary с provenance;
- механизм исправления устаревшей памяти.

**Acceptance:** после нескольких compaction циклов ключевые проектные решения остаются воспроизводимыми.

## Sprint 46 — RAG 2.0 и hierarchical compression

- retrieval по файлам/проекту до сборки prompt;
- diversity/MMR-like отбор вместо набора похожих chunks;
- hierarchical summary для больших документов;
- token budget на каждый источник;
- защита от prompt injection в RAG;
- ссылки chunk → исходный файл/страница.

**Acceptance:** большие файлы не требуют огромного context window и не вытесняют сам вопрос пользователя.

## Sprint 47 — Freshness Router + доказуемый интернет

- backend классифицирует потребность в актуальных данных;
- current office holders, цены, законы, новости, расписания, версии ПО и т.п. требуют поиска;
- исторические/теоретические вопросы не запускают сеть без необходимости;
- минимум независимых источников для high-risk factual claims;
- ответ показывает источники, дату получения и статус свежести.

**Acceptance:** браузер не принимает решение об актуальности regex-ом; модель не угадывает current facts из весов.

## Sprint 48 — Conditional Verification вместо тройной генерации

- deterministic validator сначала;
- critic только при риске/ошибке/strict mode;
- repair только если critic/deterministic gate нашёл дефект;
- stop-after-one-successful-repair;
- расходы verification пишутся отдельно.

**Acceptance:** нормальный запрос обычно = один inference; дефектный ответ не считается verified.

## Sprint 49 — Tool Calling Reliability Engine

- schema-first tools;
- строгая валидация args;
- tool result IDs и идемпотентность;
- loop detector по повторяющемуся action signature;
- maximum attempts per action;
- failure budget и recovery strategy;
- malformed tool call не завершает задачу ложным success.

**Acceptance:** агент не может бесконечно повторять один вызов; некорректный tool call восстанавливается контролируемо.

## Sprint 50 — Coding Agent: доказательство результата

- repository map и selective context;
- plan → change → static check → test → inspect → repair;
- запрет модификаций вне approved scope;
- diff budget;
- обязательный test evidence перед `done`;
- rollback failed patch;
- checkpoints больших задач.

**Acceptance:** агент не сообщает `готово`, если acceptance/test не прошёл.

## Sprint 51 — Compaction-safe autonomous development

- structured handoff state вместо prose summary;
- неизменяемый goal/constraints ledger;
- completed/pending/failed work ledger;
- subagent budget и лимит параллельности;
- recovery после restart;
- resume from checkpoint.

**Acceptance:** длинный coding run переживает compaction/restart без потери задачи.

## Sprint 52 — документы без визуальных дефектов

- короткие DB transactions вокруг DOCX/PDF QA;
- render worker isolation;
- structural + visual QA;
- таблицы, переносы, переполнение, пустые страницы;
- автоматический repair ограниченное число раз;
- FileResponse/stream downloads.

**Acceptance:** released document имеет сохранённые QA evidence и не держит DB lock во время LibreOffice.

## Sprint 53 — прозрачная экономика и лимиты

- пользователю показывается usage budget понятными единицами;
- отдельные расходы Fast/Work/Deep/research/image/sandbox;
- предупреждение до достижения лимита;
- никакого неожиданного hard-stop после дорогого ошибочного ответа;
- админ видит compute per successful answer и wasted compute.

**Acceptance:** пользователь до отправки/во время работы понимает режим и остаток ресурса.

## Sprint 54 — graceful overload вместо `server busy`

- bounded queues на всех дорогих контурах;
- admission before DB allocation;
- circuit breakers;
- priority fairness;
- graceful degradation research/images/sandbox независимо от core chat;
- retry-after и resumable jobs;
- защита от thundering herd.

**Acceptance:** перегрузка не вызывает OOM и не валит весь продукт.

## Sprint 55 — Server Optimization Profiles

Три профиля админа:

### Super Low
- минимальные context/output budgets;
- одна тяжёлая операция;
- research/documents/sandbox строго serial;
- короткие очереди;
- aggressive summarization;
- images off/on-demand.

### Optimal
- default для 32 ГБ;
- 8K model context ceiling;
- один generation slot;
- ограниченный параллельный research;
- conditional thinking/critic;
- нормальный RAG budget.

### Maximum
- использует только безопасный boot envelope текущего хоста;
- не может программно превысить физический memory/context ceiling;
- предназначен для 48/64+ ГБ после restart/runtime reconfiguration.

**Acceptance:** смена профиля не создаёт race с двумя независимыми semaphore/gate; активные операции безопасно доживают.

## Sprint 56 — UI/UX доверия и скорости

- Markdown/code/table renderer с sanitation;
- Copy code;
- источники раскрываются, а не показываются только числом;
- load older history;
- progress states: queued/researching/thinking/generating/verifying;
- Stop;
- понятные ошибки с возможностью повторить;
- mobile-first regression.

**Acceptance:** пользователь всегда понимает, что делает система и как восстановиться после ошибки.

## Sprint 57 — Model/Prompt Regression Lab

- золотой набор реальных запросов;
- категории: factual, writing, code, RAG, long chat, tools, scope, Russian language;
- snapshot model/template/runtime metadata;
- A/B старой и новой модели;
- качество, latency, TTFT, token waste, tool success;
- автоматический release blocker при регрессии.

**Acceptance:** обновление модели/шаблона не может тихо ухудшить production.

## Sprint 58 — 360 Production Hardening / Release Candidate

- security audit всех trust boundaries;
- Docker socket architecture: отдельный/rootless daemon или минимальный authenticated proxy;
- SSRF/path traversal/archive bomb/secrets/RCE regression;
- DB lock/pool/transaction audit;
- 100k virtual arrivals + реальные bounded load tests;
- chaos: DB/search/llama/sandbox restart, disk full, timeout, broken document;
- backup + restore drill;
- canary rollout + auto rollback;
- финальный release gate.

**Acceptance:** релиз проходит полный runtime acceptance на эталонной 32-ГБ машине; критические regression cases = 0.

---

## Порядок выполнения

41 → 42 → 43 → 44 → 45 → 46 → 47 → 48 → 49 → 50 → 51 → 52 → 53 → 54 → 55 → 56 → 57 → 58.

Нельзя ускорять roadmap за счёт одновременной частичной смены production-модели и runtime-контрактов. Каждая миграция модели должна быть атомарной и иметь rollback.
