# Sprint 52 — документы без визуальных дефектов

Дата реализации: 2026-09-08.

## Цель

Сделать выпуск DOCX доказуемым: документ считается готовым только после structural QA, изолированного LibreOffice render, PDF/raster geometry QA и проверки целостности конкретной revision.

Главная архитектурная проблема предыдущего контура: `/v1/documents/{id}/qa` держал транзакцию и `FOR UPDATE` на `DocumentArtifact` во время LibreOffice/Poppler. Медленный render мог удерживать PostgreSQL lock десятки секунд. Дополнительно LibreOffice запускался внутри API container.

Sprint 52 заменяет это на:

`short DB claim -> render worker -> structural/visual QA -> bounded repair -> re-render -> short DB commit`

## Короткие DB transactions

QA теперь состоит из двух транзакционных фаз.

### Phase 1 — claim

Под `FOR UPDATE` сервер:

- проверяет права;
- фиксирует current revision;
- проверяет DOCX SHA-256;
- запрещает второй одновременный QA этой же revision;
- переводит revision в `qa_status=running`;
- сохраняет `qa_started` event;
- делает `COMMIT`.

После commit PostgreSQL row lock освобождён.

LibreOffice, Poppler, raster QA и deterministic repair выполняются уже без удержания DB transaction.

### Phase 2 — persist

После QA сервер снова берёт row lock и проверяет:

- `artifact.current_revision == revision_number`;
- DB всё ещё содержит исходный `source_docx_sha`;
- результат относится к той же revision.

Если за время render пользователь создал новую revision или старый DOCX был изменён, старый QA получает `409` и не может перезаписать новое состояние.

## Render worker isolation

Добавлен отдельный service:

`document-worker`

Файлы:

- `app/document_worker_api.py`;
- `Dockerfile.document-worker`.

Worker содержит LibreOffice Writer, Poppler и базовые шрифты.

API вызывает его по внутреннему Docker network через:

`POST /render`

с заголовком:

`X-X1-Document-Token`

Worker не имеет Docker socket, БД, Git credentials, model files или внешних user secrets.

Shared capability — только `/app/data`, необходимый для чтения конкретного DOCX и записи PDF/PNG текущей revision.

Все worker paths должны:

- быть relative;
- не содержать `..`;
- находиться под `documents/`;
- после `resolve()` оставаться внутри `X1_DATA_ROOT`.

## Production configuration

Production использует:

- `X1_DOCUMENT_RENDER_BACKEND=remote`;
- `X1_DOCUMENT_RENDER_WORKER_URL=http://document-worker:8091`;
- отдельный random `X1_DOCUMENT_RENDER_WORKER_TOKEN`;
- `X1_DOCUMENT_RASTER_DPI=110`;
- `X1_DOCUMENT_QA_MAX_REPAIRS=1`.

Чистый development default остаётся `local`, чтобы локальный запуск без Compose не требовал отдельный worker.

Installer:

- генерирует worker token;
- build-ит document-worker;
- запускает его до app;
- ждёт `/health`;
- включает worker logs в release-gate diagnostics.

Production app fail-closed, если remote token остался default.

## Structural QA 2.0

`structural_qa()` теперь проверяет:

- DOCX открывается;
- документ не пуст;
- число таблиц;
- число строк каждой таблицы;
- число колонок;
- unresolved `TODO/TBD/FIXME/{{...}}` не только в paragraphs, но и в table cells;
- layout-risk для широких таблиц и очень длинных cells.

При генерации таблиц:

- включён autofit;
- первая строка header может быть bold;
- header row помечается `w:tblHeader`, чтобы Word/LibreOffice повторяли её на следующих страницах многостраничной таблицы.

## Visual QA

После DOCX -> PDF worker сразу rasterizes все страницы через Poppler.

API затем проверяет PDF двумя независимыми deterministic способами.

### PDF/text geometry

`pdftotext -bbox-layout` используется для получения координат текста.

Проверяются:

- `text_outside_page`;
- `text_clipping_risk`;
- горизонтальный `text_overlap` внутри одной text line.

### Raster QA

PNG каждой страницы проверяется на:

- полностью пустую страницу;
- content touching page edge;
- content suspiciously close to page edge;
- raster integrity/page count;
- большие практически чёрные tiles (`suspicious_dark_block`) — индикатор black square/broken glyph/render corruption.

Для каждой страницы сохраняются:

- raster SHA-256;
- pixel size;
- content bounding box;
- distance from content to page edge.

`visual_model_status` теперь сообщает `deterministic_raster_geometry` — система не делает вид, что использовала vision LLM, если её не было.

## Bounded auto-repair

Default maximum:

`1 repair`

Hard code ceiling:

`2`

Repair запускается только для детерминированно ремонтируемых layout issues:

- `content_touches_page_edge`;
- `text_clipping_risk`;
- `text_outside_page`;
- `text_overlap`.

Repair:

- уменьшает horizontal margins;
- включает compact table layout;
- уменьшает font внутри таблиц до безопасного размера;
- убирает лишние paragraph spacing в cells;
- при таблице 7+ колонок переводит section в landscape.

После repair документ обязательно полностью проходит новый:

`DOCX -> PDF -> PNG -> QA`

Старый render никогда не считается доказательством repaired DOCX.

Blank page и другие неоднозначные дефекты автоматически не «чинятся» эвристикой: QA завершается failed вместо рискованного изменения содержания.

## Repair + renderer busy

Закрыт race:

`render fail -> repair DOCX -> second render receives 429 busy`

В этом случае QA возвращается в `pending`, а SHA repaired DOCX записывается атомарно только если:

- current revision всё ещё та же;
- DB всё ещё содержит исходный source SHA.

Следующий retry не трактует собственный repair как внешнюю подмену файла.

## QA evidence

`DocumentQAEvent` сохраняет отдельные gates:

- `qa_started`;
- `structural`;
- `render_attempt_N`;
- `repair_N`;
- render failure/other final evidence.

`DocumentRevision.qa_report` содержит:

- structural result;
- render result;
- page/raster evidence;
- text geometry evidence;
- repair history;
- source/final DOCX SHA;
- renderer metadata.

Release разрешён только если final QA passed и SHA текущих DOCX/PDF совпадают с сохранёнными.

## Streaming download

Released DOCX больше не читается целиком через `path.read_bytes()` в API RAM.

Используется Starlette/FastAPI `FileResponse`, поэтому большой документ отдаётся файловым response механизмом вместо полной загрузки в память request worker.

## Resource policy для 32 ГБ

Document worker:

- memory limit: 768 MiB;
- default concurrent renders: 1.

Это намеренная serial policy: LibreOffice и rasterization не должны одновременно конкурировать с Qwen3.6 за несколько гигабайт RAM.

Worker не создаёт дополнительную AI-модель и не использует GPU.

## Doctor

`scripts/doctor.py` теперь проверяет:

- default document worker token;
- наличие отдельного `document-worker` в Compose;
- shared data mount;
- отсутствие Docker socket в document worker;
- `/health` worker-а;
- участие его memory limit в общем active container budget.

## Regression coverage

`tests/test_sprint52_document_qa.py` проверяет:

- placeholders внутри tables;
- table shape;
- repeating header XML;
- bounded deterministic repair;
- отсутствие mutation для non-repairable issue;
- commit до render;
- revision/hash recheck после render;
- FileResponse;
- worker path isolation;
- отсутствие Docker socket;
- production worker secret;
- installer integration;
- geometry/raster/black-block guards.

## Acceptance, который ещё нужно провести на target node

Кодовая реализация не заменяет реальный render acceptance. На production-like 32-ГБ узле нужно прогнать corpus DOCX:

1. длинный обычный документ;
2. таблица на 20-50 страниц;
3. wide 7-12 column table;
4. длинные русские/английские строки;
5. ручной page break near page boundary;
6. документ с intentional blank page;
7. документ с clipping fixture;
8. broken-glyph/black-square fixture;
9. concurrent QA requests;
10. создание новой revision во время старого render;
11. restart document-worker во время conversion;
12. 429 busy после первого repair;
13. release + streamed download;
14. memory/CPU observation alongside live Qwen inference.

Production acceptance Sprint 52 считается пройденным только после фактической проверки этих сценариев на target-node.
