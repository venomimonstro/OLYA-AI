# Sprint 45 — долговременная память при физическом контексте 8K

Дата реализации: 2026-09-07.

## Цель

OLYA AI не должна работать по принципу «последние N сообщений = вся память». На 32-ГБ production-профиле Qwen3.6 намеренно ограничена физическим контекстом 8K, поэтому длинные диалоги требуют отдельного bounded memory layer.

## Четыре слоя

### 1. Hot Context

Последние 16 канонических server-side сообщений сохраняются в prompt без суммаризации. Client-supplied assistant history по-прежнему не получает доверие канонической истории.

### 2. Rolling Summary

Сообщения старше Hot Context компактируются детерминированно в bounded summary до 4200 символов. Summary явно помечен как справочная история, а не подтверждённый факт. При конфликте выигрывают свежий запрос пользователя и Decision Memory.

### 3. Decision Memory

Из user-turns консервативно извлекаются явные долговременные решения и ограничения: «решили», «используем», «не используем», «запрещено», «фиксируем» и аналогичные формулировки. Ответы модели никогда автоматически не становятся Decision Memory.

### 4. Semantic Memory

Decision/Fact Memory индексируется нормализованными keywords. На запросе выполняется дешёвое relevance scoring по пересечению ключевых терминов. Для коротких запросов вроде «продолжи» применяется маленький recency fallback последних ключевых решений.

Отдельная embedding-модель в Sprint 45 намеренно не запускается: на 32 ГБ она ухудшила бы RAM/операционную экономику. Интерфейс памяти допускает будущую замену keyword scorer на vector retrieval.

## Persistence

Добавлена таблица `conversation_memories`:

- conversation/project scope;
- `summary | decision | fact`;
- стабильный dedup key;
- keywords;
- source role/message link;
- confidence;
- timestamps.

Alembic revision: `f45a10c2d8e1`, линейно поверх `f38f09c1b437`.

На один conversation сохраняется не более 240 decision/fact entries. Rolling summary хранится одной записью `summary/rolling`.

## Защита от закрепления галлюцинаций

- automatic durable extraction получает только текст пользователя;
- assistant output разрешён только внутри rolling conversational summary;
- summary в prompt помечен как non-authoritative;
- текущий user request и system policy имеют более высокий приоритет;
- вопросы и обычные transient-команды не превращаются в долговременную память.

## Prompt budget

`ContextCompiler` теперь резервирует минимум 55% доступного prompt-char budget под текущий/hot conversation. Project context и long-term memory делят оставшийся bounded budget и не могут вытеснить текущий запрос.

Это важно для 8K профиля: сама память не должна создавать новый long-context overflow.

## Экономика

Успешный retrieval не требует дополнительного Qwen inference. Извлечение решений, summary compaction и semantic scoring выполняются обычным CPU-кодом/SQL. Дополнительная стоимость — небольшие PostgreSQL reads/writes.

## Regression coverage

`tests/test_sprint45_long_term_memory.py` проверяет:

- извлечение explicit decisions;
- отказ от запоминания вопросов/transient turns;
- deduplicated bounded keywords;
- разделение Decision/Fact/Summary;
- memory prompt precedence;
- сохранение текущего запроса при огромном memory payload;
- ORM schema/unique identity;
- интеграцию четырёх слоёв;
- линейный Alembic head.

## Production acceptance

Как и предыдущие спринты, кодовая реализация не заменяет target-node acceptance. Перед публичным запуском требуется полный release gate с реальной PostgreSQL и Qwen3.6.
