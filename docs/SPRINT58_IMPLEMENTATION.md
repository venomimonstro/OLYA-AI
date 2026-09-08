# Sprint 58 — 360 Production Hardening / Release Candidate

Дата реализации: 2026-09-08.

## Цель

Закрыть roadmap 41–58 единым production boundary и не позволять считать сборку Release Candidate только по факту успешного запуска приложения.

RC считается принятым только если на эталонном 32-ГБ узле одновременно пройдены security, model regression, runtime, load, chaos, backup/restore и rollout gates.

## 1. Docker socket boundary

До Sprint 58 `sandbox-worker` имел прямой bind `/var/run/docker.sock` и Docker CLI. Это означало, что компрометация worker-а потенциально давала полный контроль над host Docker.

Новая архитектура:

`app -> sandbox-worker -> authenticated docker-runtime-proxy -> /var/run/docker.sock`

Только `docker-runtime-proxy` имеет socket.

`sandbox-worker`:

- больше не содержит Docker CLI;
- не монтирует Docker socket;
- не использует `subprocess` для Docker;
- отправляет команду proxy по приватному Compose DNS;
- аутентифицируется существующим installer-generated sandbox token.

Proxy не публикует порт наружу.

## 2. Minimal Docker grammar

Proxy не является generic Docker API.

Разрешены только операции, необходимые sandbox runtime:

- `docker version`;
- inspect pinned sandbox image;
- list X1-labelled containers;
- constrained `docker run`;
- formatted inspect managed container;
- exec managed preview;
- forced remove managed container.

Запрещены:

- `--privileged`;
- host PID/IPC/UTS/user namespace;
- devices;
- arbitrary volumes;
- `--entrypoint`;
- runtime/GPU override;
- cap-add;
- host aliases/DNS override;
- seccomp unconfined;
- arbitrary Docker commands;
- Docker socket mount inside child container.

Critical flags допускаются ровно один раз, чтобы duplicate flag не мог переопределить ранее безопасное значение.

Каждый sandbox container обязан иметь:

- `--network none`;
- read-only root filesystem;
- `--cap-drop=ALL`;
- `no-new-privileges`;
- UID/GID `10001:10001`;
- resource limits;
- pinned runtime image;
- X1 ownership label;
- ровно два bind mount, оба внутри configured host X1 data root.

## 3. SSRF

Существующий ResearchFetcher остаётся fail-closed и включён в Sprint 58 security gate.

Проверяются:

- только http/https;
- запрет credentials in URL;
- стандартные порты;
- localhost/.local;
- private/loopback/link-local/multicast/reserved/unspecified IP;
- DNS resolution до fetch;
- повторная validation каждого redirect;
- фактически подключённый peer address после соединения;
- `trust_env=False`;
- byte/content-type limits.

Таким образом защита учитывает не только URL parsing, но и DNS rebinding/redirect сценарии.

## 4. Files / archives / path traversal / RCE

Release security audit закрепляет существующие guarantees:

- workspace path не может содержать absolute path, `.`, `..`, `.git`, `.ssh`, `.gnupg`;
- resolved path обязан остаться внутри workspace;
- ZIP имеет entry-count и unpacked-byte ceiling;
- symlinks в ZIP запрещены;
- host execution по умолчанию разрешает только deterministic `python -m py_compile` для файлов внутри workspace;
- остальные команды требуют isolated sandbox;
- subprocess всегда `shell=False`.

## 5. DB pool / lock / transaction

RC audit требует:

- bounded `pool_size`;
- bounded `max_overflow`;
- finite `pool_timeout`;
- `pool_pre_ping`;
- PostgreSQL `statement_timeout`;
- `lock_timeout`;
- `idle_in_transaction_session_timeout`.

Sprint 52 отдельно устранил LibreOffice render под долгим `FOR UPDATE`.

## 6. Static 360 audit

Добавлен:

`python -m scripts.rc_security_audit`

Он fail-closed проверяет основные trust boundaries.

`scripts/run_full_regression.py` теперь запускает его до pytest вместе с Sprint 57 golden-corpus validation.

Поэтому возвращение Docker socket в sandbox-worker, снятие SSRF checks или DB deadlines ломает обычный regression gate.

## 7. Реальный chaos recovery

Добавлен host-level:

`python -m scripts.rc_runtime_chaos`

Он последовательно перезапускает:

- PostgreSQL;
- SearXNG;
- Docker runtime proxy;
- sandbox-worker;
- document-worker;
- llama.cpp.

После каждого restart приложение обязано снова пройти HTTP health recovery.

Сервисы перезапускаются последовательно, а не одновременно, чтобы определить конкретный broken recovery path.

## 8. Disk full

RC runner не заполняет production filesystem до нуля — такой тест сам по себе может повредить машину.

Вместо этого ENOSPC создаётся в отдельном Docker container с bounded 4-MiB tmpfs и network=none/read-only/no-new-privileges. Это доказывает сам failure primitive безопасно.

Storage quota/free-space guards приложения дополнительно остаются покрыты regression tests файлов/images/documents.

## 9. 100k virtual arrivals

Existing `release_gate.py --runtime` запускает multi-user load с:

`--virtual-users 100000`

Virtual arrivals не означают создание 100k resident inference tasks. Проверяется именно правильное поведение bounded queues/admission: resident expensive work остаётся ограниченным, excess traffic получает retry/shed semantics.

Также выполняются реальные bounded HTTP load probes.

## 10. Model/Prompt regression

Sprint 57 является обязательной частью runtime `component_acceptance`.

RC не проходит если:

- accepted baseline отсутствует;
- candidate report failed;
- critical golden case failed;
- tool success/quality/TTFT/latency/token guardrail регрессировал.

## 11. Backup / restore

`release_gate.py --runtime` уже выполняет:

- backup;
- получение конкретного backup path;
- restore drill;
- fail если verified backup не был создан.

Final RC дополнительно требует наличие успешного restore evidence.

## 12. Canary + auto rollback

Production watchdog использует progressive rollout.

Если launch evaluation перестал быть stable:

1. rollout freeze;
2. при `public_launch_auto_rollback=true` создаётся rollback rollout;
3. exposure возвращается на безопасную версию.

Sprint 58 security audit закрепляет наличие обоих механизмов.

## 13. Final RC gate

Новая финальная команда:

```bash
python3 -m scripts.rc_release_candidate
```

Она требует reference host приблизительно 32 GiB (31–40 GiB detected RAM), если оператор явно не использовал `--allow-nonreference-host` только для диагностического запуска.

RC gate последовательно требует:

1. `rc_security_audit`;
2. полный `release_gate.py --runtime --live-inference --user-journey --chaos`;
3. host-level runtime chaos;
4. successful release-gate evidence;
5. successful Sprint 57 model regression evidence;
6. successful restore drill evidence;
7. successful runtime chaos evidence;
8. подтверждение, что runtime/component/load/live/user-journey/chaos/capacity режимы реально были запрошены.

Формат результата:

`x1-release-candidate-v1`

RC status `passed` возможен только если `failed_checks=[]`.

## 14. Regression coverage

Добавлен:

`tests/test_sprint58_production_hardening.py`

Он покрывает:

- единственный Docker socket boundary;
- отсутствие socket/CLI/subprocess у sandbox-worker;
- canonical Docker run grammar;
- privileged/duplicate-network/host-namespace/security-opt bypass;
- mount escape;
- arbitrary Docker command rejection;
- SSRF local/special networks;
- path traversal;
- ZIP expansion bomb;
- DB pool/deadlines;
- final RC required modes/evidence;
- real chaos restart matrix;
- isolated ENOSPC;
- 100k virtual arrival requirement;
- backup/restore requirement;
- canary auto rollback;
- security audit inclusion in full regression.

## 15. Что не считается доказанным в GitHub-only разработке

Код Sprint 58 не означает автоматический production acceptance.

До фактического запуска final RC gate на целевой машине нельзя утверждать, что:

- Compose build полностью успешен;
- Docker proxy может открыть host socket с конкретными host permissions;
- Qwen live regression passed;
- 100k virtual/bounded load passed;
- DB/search/llama/sandbox restart recovery passed;
- backup/restore drill passed;
- reference 32-GiB host не ушёл в swap/OOM;
- critical regression cases = 0.

Единственный authoritative результат для RC — сохранённый `backups/rc-release-candidate-latest.json` со `status=passed` после target-node execution.
