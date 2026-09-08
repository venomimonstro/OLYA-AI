# Sprint 46 — RAG 2.0 для больших файлов и кодовых баз

Дата реализации: 2026-09-08.

## Цель

OLYA AI должна отвечать по документам и репозиториям, которые значительно больше физического 8K-контекста Qwen3.6, не загружая весь файл в prompt и не требуя отдельной embedding-модели на 32-ГБ сервере.

## Pipeline

`upload -> isolated parse -> structure-aware chunks -> bounded SQL candidates -> hybrid ranking -> dedupe/diversity -> neighbor expansion -> hierarchical outline -> secret redaction / injection quarantine -> FILE_REF evidence -> Qwen`

## Поддерживаемые данные

Помимо существовавших PDF/DOCX/TXT/MD/JSON добавлена структурная обработка:

- XLSX без постоянно запущенного LibreOffice/openpyxl worker;
- CSV/TSV;
- Python/JS/TS/PHP;
- Go/Rust/Java/Kotlin/C/C++/C#/Ruby;
- SQL/shell;
- TOML/INI/CONF/XML и web-source formats.

DOCX сохраняет heading markers, XLSX — границы листов и строки, code/text chunking учитывает headings и границы функций/классов.

## Retrieval

Sprint 46 заменяет простой `ILIKE + overlap ratio` на CPU-friendly hybrid retrieval:

1. до 12 значимых query terms;
2. bounded SQL candidate set;
3. BM25-подобный term-frequency score;
4. query coverage;
5. term density;
6. exact/quoted phrase boost;
7. filename boost;
8. near-duplicate suppression;
9. diversity cap по файлам;
10. neighbor expansion вокруг сильных anchor chunks.

Так несколько почти одинаковых overlap chunks не занимают весь RAG budget, а найденный фрагмент получает локальный контекст до/после него.

## Hierarchical context

Для выбранных файлов строится компактный navigation outline по заголовкам/листам. Он помечается как навигация, а не как независимое доказательство. Затем идут конкретные evidence chunks.

## Traceability

Каждый переданный Qwen фрагмент имеет точный locator:

`FILE_REF[file_id=...;name=...;version=...;chunk=...;page=...]`

Для форматов без страниц locator остаётся на file/version/chunk. Prompt явно требует сохранять FILE_REF при утверждениях, основанных на файле.

## Prompt-injection boundary

Файлы остаются недоверенным источником данных. Перед Qwen:

- секретоподобные значения редактируются;
- известные русские/английские prompt-injection markers обнаруживаются;
- подозрительный excerpt явно помечается как quarantined document text;
- команды/role claims внутри документа не получают право менять system policy, tools, permissions или user goal.

Это особенно важно для загруженных репозиториев, README, HTML и документов из внешних источников.

## 32-ГБ экономика

Sprint 46 намеренно не добавляет Chroma/FAISS/sentence-transformers и не держит вторую neural embedding model в RAM. Ranking выполняется SQL + Python CPU-кодом. Это экономит RAM для Qwen3.6 Q4_K_M и не создаёт второй inference runtime.

Архитектура не запрещает добавить embeddings позже на 48/64+ ГБ сервере; текущий retrieval API можно заменить/дополнить vector scorer без изменения FileContextBuilder.

## Context budget

По умолчанию FileContextBuilder ограничивает retrieval 8 chunks и примерно 7600 символами evidence. Общий ContextCompiler Sprint 45 дополнительно не позволяет file/project memory вытеснить текущий user turn и Hot Context.

## Regression coverage

`tests/test_sprint46_rag_v2.py` проверяет:

- XLSX parsing;
- structure-aware chunks;
- hybrid relevance ranking;
- near-duplicate detection;
- RU/EN prompt-injection detection;
- bounded query terms;
- traceable FILE_REF contract;
- diversity/neighbor guards;
- отсутствие обязательной vector/torch зависимости.

## Следующий уровень

Vector embeddings имеет смысл включать только после измерения реальной retrieval accuracy на corpus проекта и RAM/latency budget. Для 32 ГБ основной production profile остаётся CPU-friendly hybrid RAG 2.0.

## Production acceptance

Полный acceptance требует target-node regression/release gate с PostgreSQL и реальными пользовательскими файлами разных размеров. Наличие кода в GitHub не заменяет этот запуск.
