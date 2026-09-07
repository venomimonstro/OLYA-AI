# OLYA-AI / X1

X1 — self-hosted AI-платформа с локальным inference на Qwen + llama.cpp. Внешний LLM API для ответов не требуется. В системе есть пользовательский чат, серверная история, Fast/Work/Deep, проверка качества, интернет-исследования через приватный SearXNG, проекты/файлы/документы, закрытая агентная разработка, sandbox, Git, Commerce/API, System Health, backup/restore и production release gates.

## Текущая версия

**0.40.0 — Full Stability Audit & Defect Elimination.**

GitHub `main` является каноническим исходным кодом. Production-конфигурация fail-closed: дефолтные секреты, SQLite в production, сломанные обязательные routers, красный release gate или небезопасный memory budget не считаются рабочей установкой.

## Требования к серверу

Для полного поддерживаемого Qwen-профиля:

- Linux x86_64, рекомендуются Debian/Ubuntu;
- практический минимум — **32 GB RAM** (installer требует не менее 30 GiB фактически обнаруженной памяти);
- не менее **60 GB свободного диска**;
- Docker + Docker Compose v2; при Debian/Ubuntu installer установит их сам;
- желательно AVX2/AVX512.

Installer резервирует минимум 8 GiB вне llama.cpp для PostgreSQL, X1, SearXNG, sandbox и ОС. Llama memory cap не поднимается выше 24 GiB. Doctor дополнительно проверяет суммарные memory limits фактически запущенных контейнеров; поэтому, например, небезопасное включение тяжёлого image-worker на минимальном сервере будет обнаружено до публичного релиза.

Control-plane без Qwen (`--no-inference`) требует минимум 8 GiB RAM.

## Установка на чистый сервер одной командой

Запускать от root либо пользователя с `sudo`:

```bash
curl -fsSL https://raw.githubusercontent.com/venomimonstro/OLYA-AI/main/scripts/bootstrap.sh -o /tmp/x1-bootstrap.sh && sudo bash /tmp/x1-bootstrap.sh
```

Bootstrap клонирует/обновляет `main` и передаёт управление `scripts/install.sh`. Дальше автоматически выполняются:

1. проверка Linux/x86_64/RAM/CPU/disk;
2. установка host prerequisites;
3. генерация production secrets без перезаписи уже безопасных секретов;
4. миграция старого `x1_data` в persistent host data без удаления исходной копии;
5. resumable-загрузка официального `Qwen3-30B-A3B-Q4_K_M.gguf` и SHA-256 verification;
6. pull pinned PostgreSQL/SearXNG/llama.cpp images;
7. сборка X1 и отдельного sandbox-worker/runtime;
8. PostgreSQL + Alembic migration-to-head;
9. запуск приватного SearXNG, sandbox boundary, Qwen и X1;
10. sandbox execution probe;
11. backup + restore drill;
12. полный release regression;
13. live long-context/capacity probe;
14. E2E user journey;
15. security/chaos/overload anti-cases;
16. финальный Doctor.

Если обязательный этап красный, installer завершается ошибкой и **не объявляет установку успешной**.

## Пользовательский интерфейс

После установки публичный сайт находится на `/`, регистрация — `/register`, вход — `/login`, рабочее пространство пользователя — **`/app`**.

После регистрации/входа пользователь сразу переводится в `/app`. Там доступны:

- канонические серверные диалоги и история;
- Auto/Fast/Work/Deep;
- Auto/Strict/Off verification;
- internet research `Авто / Всегда / Выкл`;
- research flow `plan → discover → collect → grounded chat`;
- понятные состояния перегрузки/очереди/недоступности.

Ответы модели и внешние source snippets не вставляются в DOM как HTML. Токен браузерной сессии хранится только в `sessionStorage`; число одновременно активных сессий аккаунта ограничено.

## Стабильность под нагрузкой

X1 не пытается держать 100 000 дорогих inference-задач в RAM. Контуры ограничены ступенчато:

`HTTP admission → auth/resource gates → research queue → inference queue → local Qwen`.

Inference имеет bounded concurrency/queue/timeout. Network research также имеет отдельный governor: по умолчанию 4 активные операции и до 32 ожидающих до создания FastAPI DB dependency. Переполнение получает retryable `503`, а не превращается в исчерпание PostgreSQL connections или OOM.

PostgreSQL использует bounded pool, `pool_pre_ping`, connect timeout, statement timeout, lock timeout и idle-transaction timeout. Долгий Qwen inference не удерживает DB connection.

## Корректность ответов

- меняющиеся факты не считаются подтверждёнными без свежего research evidence;
- SSRF/private-IP/redirect protections действуют при source fetch;
- источник рассматривается как недоверенные данные, а не инструкции модели;
- client-supplied assistant transcript не может переписать каноническую серверную историю;
- strict verification включает deterministic checks и critic/repair gates;
- пользовательский интерфейс в режиме `Интернет: всегда` не подменяет неудавшийся поиск уверенным ответом из памяти модели.

## Закрытая разработка

Web-app не получает `/var/run/docker.sock`. Привилегированная граница вынесена в отдельный `sandbox-worker` с authenticated internal API. Runtime ограничивается image allowlist, CPU/RAM/PID envelope, `cap-drop=ALL`, `no-new-privileges`, read-only root filesystem и `network none`. Число одновременно исполняемых sandbox-задач и preview ограничено; просроченные preview очищаются.

## Backup, restore и update

Перед опасными операциями используется quiescence, verified backup и restore drill. Обновление существующей installation идёт через transactional updater с rollback, а не через безусловный `git pull` поверх работающей системы.

Doctor и System Health проверяют БД/migrations, Qwen, persistent data, SearXNG, sandbox privilege boundary, memory envelope, backup/release evidence и другие критические связи.

## Полный production gate

На target node:

```bash
python3 scripts/release_gate.py --runtime --live-inference --user-journey --chaos
```

Результат сохраняется в `backups/release-gate-latest.json`. Для публичного запуска дополнительно требуется зелёный `/v1/admin/reliability/release-readiness`, свежая target-node calibration и корректный active capacity/rollout plan.

## Админские экраны

- `/admin` — System Health / checkpoints;
- `/admin/beta` — closed-beta operations;
- `/admin/launch` — progressive public rollout/circuit breakers;
- `/media-admin` — media/image operations.

## Ограничение проверки репозитория

Наличие кода и regression-тестов в `main` не заменяет запуск на реальном сервере. Статус **ONE-COMMAND PRODUCTION VERIFIED** присваивается только после успешного полного release gate на конкретном target node с реальным Qwen GGUF, PostgreSQL, Docker, SearXNG и sandbox runtime.

Подробный статус разработки: `ROADMAP.md`.
