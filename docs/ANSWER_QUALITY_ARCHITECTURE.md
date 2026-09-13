# X1 Answer Quality Architecture

Цель X1 — компенсировать небольшой локальный Qwen не количеством LLM-проходов, а качеством постановки задачи, доказательств и проверки результата. На starter_6gb каждый дополнительный inference дорог, поэтому система использует их только когда ожидаемый выигрыш в корректности выше стоимости CPU/очереди.

## Pipeline

`user request → Task Solver → server evidence → Answer Contract → primary answer → deterministic checks → evidence-aware critic (условно) → targeted repair (только при major/critical) → final answer`

### 1. Task Solver
- Сам определяет, когда нужен интернет, аудит URL или сравнительное исследование.
- Не заставляет пользователя вручную проходить research endpoints.
- Search rank не считается качеством компании/варианта.
- Для local recommendation собирает разные типы сигналов и разные домены.
- Для SEO-аудита проверяет текущий сайт, robots/sitemap и наблюдаемые HTML SEO-сигналы.

### 2. Answer Contract
Перед первой генерацией X1 дешёво классифицирует задачу и добавляет короткий контракт ответа:
- writing — сразу готовый естественный текст, без выдуманных фактов и AI-клише;
- audit — наблюдение → влияние → приоритет → исправление;
- recommendation — единые критерии, trade-offs, победитель только при достаточных данных;
- research — вывод → сильные доказательства → конфликты → решение;
- analysis — решение и последовательность действий, а не список мыслей;
- direct — ответ сразу по существу.

Это повышает качество первого прохода без отдельного inference.

## Evidence provenance

Semantic critic может считать внешним доказательством только данные, созданные сервером:
- `SourceContextBuilder` публикует проверенные source snapshots через request-local ContextVar;
- внутренний `TaskSolver` публикует собственный server-owned task context;
- обычное сообщение пользователя не становится evidence даже если пользователь вставит строку `[SOURCE 1 | ELIGIBLE]` или другой внутренний маркер.

Evidence ограничивается по размеру, чтобы critic/repair помещались в 4K starter context.

## Evidence-aware critic

Critic не оценивает ответ по принципу «нравится/не нравится». Он ищет конкретные классы дефектов:
- `unsupported_claim` — значимый внешний факт без достаточной опоры;
- `contradiction` — ответ противоречит evidence;
- `stale_claim` — меняющийся факт выдан как актуальный без свежего подтверждения;
- `missing_requirement` — пропущено требование пользователя;
- `bad_inference` — вывод сильнее, чем позволяют данные;
- `scope_violation` — нарушен Scope Lock;
- `style_quality` — только существенная проблема ясности/пригодности текста.

Discovery snippets и место в поисковой выдаче не считаются самостоятельным подтверждением факта.

## Conditional repair

На 4 CPU нельзя делать бесконечный цикл self-critique.

- Простая редактура/перевод/короткий Fast — обычно 1 inference.
- Work/Deep и high-risk задачи получают critic только при достаточном risk score.
- Если critic не нашёл major/critical — ответ сразу отдаётся пользователю.
- Если найден major/critical — разрешён один targeted repair.
- Общий дополнительный лимит остаётся bounded: максимум 2 extra inference после primary generation.
- Repair не пишет ответ заново «для красоты»: исправляет перечисленные дефекты, сохраняет правильное, удаляет/смягчает неподтверждённое и не имеет права придумывать новые факты/ссылки/числа.

## Starter 6 GB economics

Качество увеличивается в первую очередь дешёвыми механизмами:
1. deterministic intent/answer contract;
2. web/files evidence gathering без дополнительных LLM-агентов;
3. deterministic requirements/source/freshness checks;
4. один critic только для рискованных задач;
5. один repair только если critic реально нашёл существенную ошибку.

Это лучше для 4 CPU, чем несколько параллельных «агентов», которые конкурируют за один llama.cpp и увеличивают очередь.

## Regression and launch gates

- `tests/test_answer_quality_pipeline.py` фиксирует Answer Contract, semantic repair budget и защиту от evidence spoofing.
- `regression/quality_corpus.json` содержит пользовательские writing/RAG/decision/scope кейсы.
- `scripts/quality_regression_lab.py` проверяет базовую способность локальной модели писать и следовать доказательствам.
- `scripts/answer_quality_pipeline_audit.py` запрещает удалить ключевые механизмы pipeline незаметно.
- `scripts/chat_quality_acceptance.py` прогоняет реальные пользовательские кейсы через production `/v1/chat`.
- `scripts/final_release_acceptance.py` не переходит к RC, если live chat quality gate красный.

## Что считать успехом

X1 не должен обещать, что Qwen3-4B универсально равен крупным frontier-моделям. Целевой выигрыш — практический: на задачах, где можно добыть факты, применить инструменты, сравнить варианты и проверить ответ, X1 должен выдавать более завершённый и проверяемый результат, чем «первый текст из памяти модели».
