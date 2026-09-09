# Sprint 60 — Product Surface / Unified Workspace

## Цель

Sprint 60 превращает пользовательскую часть X1 из набора отдельных технических страниц в один минималистичный рабочий интерфейс `/app`.

Основной принцип: пользователь видит продуктовые сущности, а не внутренние контроллеры и сервисы.

```text
/app
 ├─ Чат
 ├─ Проекты
 ├─ Файлы
 ├─ Изображения
 ├─ API
 └─ Аккаунт
```

Ни одна из этих секций не реализует вторую копию бизнес-логики. UI использует существующие API-контроллеры.

## Реализовано

### Единый shell

`app/user_ui.py` теперь является общей пользовательской оболочкой. Навигация работает без отдельного frontend framework и не вводит новый build/runtime stack.

### Чат

Сохранены существующие возможности Sprint 43/47/48/53/56:

- streaming SSE;
- Stop/AbortController;
- Fast / Work / Deep / Auto;
- verification Auto / Strict / Off;
- Internet Auto / Always / Off;
- research sources;
- budget preflight;
- Markdown/code/table rendering через DOM API;
- Copy code;
- история и загрузка старых сообщений;
- Retry.

При создании нового разговора пользователь может выбрать проект. `project_id` фиксируется в `Conversation` и передается в Chat.

### Проекты

Раздел использует:

- `GET /v1/projects`;
- `POST /v1/projects`.

Пользователь может создать проект и сразу открыть новый чат в его контексте.

Sprint 60 намеренно не меняет scope/security contract существующих project conversations. Полная работа проекта как центрального рабочего пространства запланирована в Sprint 63.

### Файлы

Раздел использует существующий файловый controller:

- `GET /v1/projects/{project_id}/files`;
- `POST /v1/projects/{project_id}/files`.

Файл передается как raw body с исходным content type. Backend по-прежнему отвечает за размер, quota, безопасное имя, storage capacity, isolated parser и RAG chunks.

UI не дублирует parsing/RAG logic.

### Изображения

Раздел сначала вызывает `GET /v1/images/status`.

Photo Studio активируется только если backend сообщает `editing.available=true`.

Если локальный backend/model/worker/QA недоступны, кнопка остается disabled и UI показывает причину. Это сохраняет правило X1: никаких внешних image/vision API и никаких фальшивых доступных функций.

### API

Sprint 60 делает API видимым продуктовым разделом и показывает существующие usage/API keys через:

- `GET /v1/commerce/usage`;
- `GET /v1/commerce/api-keys`.

Полная self-service API Console остается задачей Sprint 68. Sprint 60 не создает параллельный API engine.

### Аккаунт

Показываются профиль и compute budget. Добавлен UI-доступ к `GET /v1/account/export` с формированием локального JSON-файла в браузере.

## Вёрстка

UI остается server-rendered + vanilla JavaScript:

- desktop sidebar;
- mobile overlay sidebar;
- responsive cards;
- responsive forms;
- code/table horizontal overflow;
- единая типографика и состояния;
- без `innerHTML` для model output;
- без внешних JS/CSS/CDN.

Это сознательный выбор для MVP: меньше supply-chain зависимостей, меньше frontend build complexity и меньше RAM/CPU overhead.

## Security contract

Сохранены:

- CSP nonce;
- `default-src 'none'`;
- `connect-src 'self'`;
- `frame-ancestors 'none'`;
- no-store/no-referrer/nosniff;
- DOM-only Markdown rendering;
- authenticated same-origin API calls;
- image capability fail-closed.

## Regression

Добавлен `tests/test_sprint60_product_surface.py`.

Он фиксирует:

- все шесть продуктовых разделов;
- wiring основных кнопок;
- использование существующих controllers;
- fail-closed Photo Studio;
- streaming/Stop/Research contract;
- mobile rules;
- отсутствие `innerHTML`;
- CSP/security headers;
- обязательное присутствие product/image audits в full regression.

Также остается обязательным `scripts/product_surface_audit.py`, который проверяет router registration, essential routes, duplicate routes и статические UI→API связи.

## Что Sprint 60 намеренно не делает

Чтобы не смешивать продуктовые этапы:

- onboarding — Sprint 61;
- полный Chat quality lock — Sprint 62;
- project-centric history/context — Sprint 63;
- расширенный Files/RAG UX — Sprint 64;
- API hardening — Sprint 67;
- полноценная API Console — Sprint 68;
- единый capability registry — Sprint 73.

## Acceptance

Sprint 60 считается кодово завершенным, когда:

1. `/app` содержит все основные пользовательские поверхности;
2. основные функции доступны из navigation без знания внутренних URL;
3. отключенная image editing не выглядит работающей;
4. Chat streaming/Stop/research/budget не регрессировали;
5. UI не исполняет HTML модели;
6. mobile layout имеет отдельный responsive contract;
7. `tests/test_sprint60_product_surface.py` и `scripts.product_surface_audit` входят в regression.

Полный browser/runtime acceptance требует реального запуска на target node и проверки Chrome/Android.