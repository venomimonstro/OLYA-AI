# OLYA-AI / X1

X1 — self-hosted AI-платформа с локальным inference на Qwen + llama.cpp. Внешний LLM API для ответов не требуется. В системе есть пользовательский чат, серверная история, Fast/Work/Deep, проверка качества, интернет-исследования через приватный SearXNG, проекты/файлы/документы, закрытая агентная разработка, sandbox, Git, Commerce/API, System Health, backup/restore и production release gates.

## Текущая версия

**0.40.0 + Sprint 43 — Qwen3.6 + real token streaming/cancellation.**

GitHub `main` является каноническим исходным кодом. Production-конфигурация fail-closed: дефолтные секреты, SQLite в production, сломанные обязательные routers, красный release gate, неподтверждённый GGUF или небезопасный memory budget не считаются рабочей установкой.

## Основная модель

Production-модель зафиксирована единым `model-manifest.json`:

- `Qwen3.6-35B-A3B-Q4_K_M`;
- GGUF: `ggml-org/Qwen3.6-35B-A3B-GGUF`;
- immutable revision и SHA-256 обязательны;
- downloader проверяет точный размер и SHA-256;
- автоматическое скрытое переключение на худший quant запрещено;
- прежний `Qwen3-30B-A3B-Q4_K_M.gguf` не удаляется во время миграции и может использоваться transactional rollback старого revision.

Имя модели, имя файла, host policy и checksum берутся из одного manifest-контракта. Installer переписывает model identity в `.env` атомарно, а updater восстанавливает прежний `.env` при rollback.

## Требования к серверу

Для полного поддерживаемого Qwen3.6 CPU/RAM-профиля:

- Linux x86_64, рекомендуются Debian/Ubuntu;
- целевая машина первой production-волны — **32 GiB RAM**; installer требует не менее 31 GiB фактически обнаруженной памяти для этого профиля;
- не менее **60 GB свободного диска**; во время миграции требуется дополнительное место, если сохраняется предыдущий GGUF для rollback;
- Docker + Docker Compose v2;
- желательно AVX2/AVX512.

Профиль 32 GiB намеренно использует **8K physical context**, один generation slot и минимум 8 GiB host/control-plane reserve вне llama.cpp. На минимальном 8K-профиле llama ограничивается подтверждённым 23-GiB envelope, а sandbox/project-runtime — 1 GiB. Старые 2-GiB sandbox limits при upgrade автоматически приводятся к безопасному 32-GiB профилю. На 48–63 GiB manifest допускает до 12K, на 64+ GiB — до 16K после installer normalization и target-node проверки.

Image worker остаётся отдельным optional profile и не должен включаться на минимальном сервере без прохождения memory-budget Doctor.

Control-plane без Qwen (`--no-inference`) требует минимум 8 GiB RAM.

## Установка на чистый сервер одной командой

Запускать от root либо пользователя с `sudo`:

```bash
curl -fsSL https://raw.githubusercontent.com/venomimonstro/OLYA-AI/main/scripts/bootstrap.sh -o /tmp/x1-bootstrap.sh && sudo bash /tmp/x1-bootstrap.sh
```

Bootstrap клонирует/обновляет `main` и передаёт управление `scripts/install.sh`. Дальше автоматически выполняются:

1. проверка Linux/x86_64/RAM/CPU/disk;
2. установка host prerequisites;
3. чтение model/host policy из `model-manifest.json`;
4. генерация production secrets без перезаписи уже безопасных секретов;
5. миграция старого `x1_data` в persistent host data без удаления исходной копии;
6. resumable-загрузка pinned `Qwen3.6-35B-A3B-Q4_K_M.gguf` с exact-size + SHA-256 verification;
7. pull pinned PostgreSQL/SearXNG/llama.cpp images;
8. сборка X1 и отдельного sandbox-worker/runtime;
9. PostgreSQL + Alembic migration-to-head;
10. запуск приватного SearXNG, sandbox boundary, Qwen и X1;
11. повторная проверка integrity GGUF после startup;
12. sandbox execution probe;
13. backup + restore drill;
14. полный release regression;
15. live long-context/capacity probe;
16. E2E user journey;
17. security/chaos/overload anti-cases;
18. финальный Doctor.

Если обязательный этап красный, installer завершается ошибкой и **не объявляет установку успешной**.

## Настоящий streaming и Stop

Sprint 43 заменяет имитацию streaming через heartbeat + один финальный JSON на реальный путь:

`llama.cpp stream=true → X1 SSE → browser ReadableStream → live assistant bubble`.

- пользователю показываются `delta.content` chunks по мере генерации;
- hidden reasoning не выводится как текст ответа;
- UI показывает queue wait, TTFT и tokens/sec;
- кнопка `Стоп` вызывает отмену HTTP stream;
- отмена downstream task закрывает upstream httpx stream к llama.cpp;
- generation semaphore освобождается при cancellation;
- отменённый запрос не сохраняется как успешно завершённый assistant message;
- если verification исправляет уже показанный ответ, SSE `replace` синхронизирует экран с канонической серверной историей.

## Пользовательский интерфейс

После установки публичный сайт находится на `/`, регистрация — `/register`, вход — `/login`, рабочее пространство пользователя — `/app`.

После регистрации/входа пользователь сразу переводится в `/app`. Там доступны канонические серверные диалоги, Auto/Fast/Work/Deep, Auto/Strict/Off verification, internet research `Авто / Всегда / Выкл`, streaming-ответы со Stop, проекты и другие продуктовые контуры.

Ответы по-прежнему вставляются в DOM через `textContent`, а не через небезопасный HTML. CSP использует nonce, access token браузерной сессии хранится в `sessionStorage`.

## Стабильность под нагрузкой

X1 не пытается держать 100 000 дорогих inference-задач в RAM. Контуры ограничены ступенчато:

`HTTP admission → auth/resource gates → research queue → inference queue → local Qwen`.

Inference имеет bounded concurrency/queue/timeout. PostgreSQL использует bounded pool, `pool_pre_ping`, connect timeout, statement timeout, lock timeout и idle-transaction timeout. Долгий Qwen inference не должен удерживать DB connection.

## Корректность ответов

- меняющиеся факты не считаются подтверждёнными без свежего research evidence;
- SSRF/private-IP/redirect protections действуют при source fetch;
- источник рассматривается как недоверенные данные, а не инструкции модели;
- client-supplied assistant transcript не может переписать каноническую серверную историю;
- strict verification включает deterministic checks и critic/repair gates;
- Adaptive Intelligence Router выбирает Fast/Work/Deep и включает thinking только когда сложность задачи это оправдывает.

## Закрытая разработка и Git

Web-app не получает `/var/run/docker.sock`. Привилегированная граница вынесена в отдельный `sandbox-worker` с authenticated internal API. Runtime ограничивается image allowlist, CPU/RAM/PID envelope, `cap-drop=ALL`, `no-new-privileges`, read-only root filesystem и `network none`.

Git collaboration хранится обычным проверяемым Python-кодом. Service Git отключает hooks, запрещает `file`/`ext` transport protocols, не помещает GitHub token в remote URL/argv и выполняет secret scan перед push.

## Backup, restore и update

Перед опасными операциями используется quiescence, verified backup и restore drill. Обновление существующей установки идёт через transactional updater. При ошибке updater восстанавливает предыдущий Git revision, данные и точную предыдущую `.env`; старый model artifact во время миграции не удаляется.

## Полный production gate

На target node:

```bash
python3 scripts/release_gate.py --runtime --live-inference --user-journey --chaos
```

Результат сохраняется в `backups/release-gate-latest.json`. Для публичного запуска дополнительно требуется зелёный `/v1/admin/reliability/release-readiness`, свежая target-node calibration и корректный active capacity/rollout plan.

## Ограничение проверки репозитория

Наличие кода и regression-тестов в `main` не заменяет запуск на реальном сервере. Статус **ONE-COMMAND PRODUCTION VERIFIED** присваивается только после успешного полного release gate на конкретном 32-GiB+ target node с реальным Qwen3.6 GGUF, PostgreSQL, Docker, SearXNG и sandbox runtime.

Подробный продуктовый roadmap: `docs/SPRINTS_41_58_PRODUCT_QUALITY.md`.
