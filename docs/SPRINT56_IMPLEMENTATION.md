# Sprint 56 — UI/UX доверия и скорости

Дата реализации: 2026-09-08.

## Цель

Сделать пользовательский workspace понятным во время долгих CPU-задач и безопасно отображать структурированные ответы Qwen без исполнения model-authored HTML.

Архитектура UX:

`budget preflight -> research when required -> queued -> thinking -> generating -> verifying -> final answer + evidence`

## Безопасный Markdown renderer

`app/user_ui.py` больше не показывает assistant output как один сырой `textContent`, но и не доверяет HTML модели.

Renderer создаёт разрешённые DOM nodes через `document.createElement` и заполняет их через `textContent`.

Поддерживаются:

- headings H1-H3;
- paragraphs;
- unordered/ordered lists;
- blockquotes;
- inline code;
- bold;
- fenced code blocks;
- Markdown tables.

Model output не передаётся в `innerHTML`.

Поэтому строка вроде `<script>...</script>` остаётся текстом и не становится исполняемым DOM.

## Code blocks

Каждый fenced code block получает:

- language label, если язык указан;
- горизонтальный scroll;
- кнопку `Копировать`;
- подтверждение `Скопировано`.

Clipboard получает исходный code string, а не HTML representation.

## Таблицы

Markdown tables превращаются в реальные `table/th/td` элементы.

Контейнер таблицы имеет horizontal overflow, поэтому широкая таблица не ломает мобильный viewport.

## Источники

Во время research UI сохраняет структурированный список `collected.sources`.

После финального ответа добавляется раскрываемый блок `Источники`.

Для каждого источника показываются:

- title;
- URL;
- кликабельная ссылка только для `http/https`;
- `target=_blank`;
- `rel=noopener noreferrer`.

Таким образом пользователь видит не только число источников, но может проверить evidence сам.

## История

Backend уже поддерживал cursor pagination через `before`.

Sprint 56 использует этот контракт в интерфейсе.

При открытии разговора загружаются последние 100 сообщений. Если получено 100, появляется кнопка:

`Загрузить более ранние сообщения`

Следующая страница запрашивается с `before=<oldest_created_at>` и вставляется наверх с сохранением scroll position.

История больше не обрезается для пользователя без возможности восстановить старые turns.

## Progress states

В composer отображаются состояния:

- `В очереди`;
- `Источники`;
- `Обдумывание`;
- `Ответ`;
- `Проверка`.

Сигналы выводятся из уже существующих backend операций и SSE transport:

- research API -> researching;
- SSE accepted / queue heartbeat -> queued;
- heartbeat без очереди -> thinking;
- token -> generating;
- replacement/final result -> verifying.

Это не раскрывает hidden chain-of-thought. `Обдумывание` означает только фазу обработки до токенов.

## Streaming

Sprint 43 streaming сохранён.

Накопленный plain Markdown string безопасно перерендеривается при поступлении token chunks. Пользователь продолжает видеть постепенный ответ, а после Sprint 48 repair событие `replace` перерисовывает итоговую безопасную разметку.

## Stop

Кнопка `Отправить` во время активной генерации остаётся кнопкой `Стоп`.

AbortController останавливает stream, а уже полученный текст остаётся на экране.

## Ошибки и Retry

Ошибка теперь не является тупиковым текстом.

UI сохраняет последний submission и создаёт кнопку `Повторить`.

Если сервер прислал HTTP `Retry-After`, значение сохраняется в error object и показывается пользователю.

Sprint 54 structured overload detail также превращается в понятную причину, а не в общий `server busy`.

## Budget UX

Sprint 53 preflight сохранён.

До chat inference интерфейс проверяет compute budget, показывает выбранный режим и не запускает запрос, который уже не помещается в оставшийся месячный compute envelope.

## Mobile-first

Для viewport <= 760 px:

- sidebar становится overlay;
- header переносит controls;
- сообщения используют почти всю ширину;
- широкие tables/code скроллятся внутри своих контейнеров;
- размер текста/code снижается умеренно, без горизонтального разрушения layout;
- composer остаётся доступным в нижней части экрана.

## Security invariants

Сохранены:

- strict CSP with nonce;
- `default-src 'none'`;
- `frame-ancestors 'none'`;
- no-store;
- no-referrer;
- nosniff;
- DENY framing;
- restricted Permissions-Policy.

External source URLs не используются для HTML injection.

## Regression coverage

Добавлен `tests/test_sprint56_trusted_ui.py`.

Он проверяет:

- отсутствие `innerHTML` в model rendering;
- DOM-only Markdown;
- Copy code;
- tables;
- structured sources;
- cursor history;
- progress states;
- Stop;
- retry/Retry-After;
- mobile rules;
- streaming;
- CSP/security headers.

## Target-node acceptance

Перед production RC необходимо дополнительно проверить в реальном браузере:

1. длинный streaming Markdown response;
2. незакрытый code fence во время streaming;
3. wide table на Android/iOS;
4. 300+ messages history pagination;
5. XSS fixtures (`script`, event handlers, malformed tags);
6. Stop во время queue/thinking/generation;
7. 503 с Retry-After;
8. research с 1/2/3 sources;
9. Sprint 48 `replace` после streamed draft;
10. screen widths 320/360/390/768/desktop.

Production acceptance не считается доказанным только static regression-тестами.
