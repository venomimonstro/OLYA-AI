# OLYA AI / X1 — Production Release Runbook

Этот runbook описывает только финальный production acceptance. Он не заменяет gates и не разрешает запуск при красном результате.

## 1. Подготовить exact candidate
- Работать из `main` без локальных изменений и untracked release inputs.
- Обновить target server до нужного commit и полностью пересобрать/restart app image. `GET /version` должен соответствовать текущему checkout; старый image будет отклонён автоматически.
- Production host должен удовлетворять `model-manifest.json -> host_policy.minimum_detected_ram_gib`. Верхнего искусственного лимита RAM нет.

## 2. Подготовить production transport и billing
- Для внешнего `X1_PRODUCTION_BASE_URL` нужен HTTPS. HTTP допустим только для loopback acceptance.
- Настроить `X1_BILLING_CHECKOUT_URL_TEMPLATE` как валидный production HTTPS URL с `{checkout_id}`.
- Настроить непустой `X1_PAYMENT_INGEST_SECRET` и реальные серверные цены минимум одного платного тарифа.
- Не сохранять production secrets в Git.

## 3. Подготовить 10 независимых acceptance accounts
- Нужны минимум 10 разных пользовательских аккаунтов, не 10 сессий одного пользователя.
- Получить Bearer token каждого аккаунта и передать их только через environment:
  `X1_LOAD_TOKENS=token1,token2,...,token10`
- Harness проверяет каждый token через `/v1/auth/me` и отклоняет повторяющиеся `user_id`.

## 4. Подготовить admin acceptance access
- Передать действующий admin Bearer token только через:
  `X1_PRODUCTION_ADMIN_TOKEN=...`
- Указать production URL:
  `X1_PRODUCTION_BASE_URL=https://your-domain.example`

## 5. Запустить полный финальный контур
Рекомендуемый вариант — одна команда:

`python3 scripts/final_release_acceptance.py --base-url "$X1_PRODUCTION_BASE_URL"`

Если Image Studio входит в публичный launch contract:

`python3 scripts/final_release_acceptance.py --base-url "$X1_PRODUCTION_BASE_URL" --require-images`

Оркестратор выполняет строго по порядку:
1. Sprint80 real load acceptance;
2. Sprint85 release-candidate gate;
3. Sprint86 production acceptance.

При первой ошибке дальнейший выпуск прекращается.

## 6. Что должно быть зелёным
Финальный acceptance требует, среди прочего:
- clean git working tree;
- совпадающий git HEAD для release/load evidence;
- совпадающий deterministic source fingerprint у checkout, load evidence, RC, running app container и public `/version`;
- ≥10 разных authenticated users в реальной нагрузке;
- full regression/security/recovery/runtime gates;
- model regression;
- backup + restore drill;
- runtime chaos;
- PostgreSQL, Qwen/llama.cpp, SearXNG, sandbox worker и document worker;
- `/ready` со статусом `stable`;
- green release-readiness и Sprint84 business contract;
- live required capabilities;
- рабочий production billing checkout/payment-ingest configuration;
- HTTPS для внешней production endpoint.

## 7. Evidence
Отчёты сохраняются локально на target node в `backups/` и исключены из Git:
- `load-acceptance-latest.json`;
- `release-gate-latest.json`;
- `rc-release-candidate-latest.json`;
- `production-acceptance-latest.json`;
- restore/model/chaos evidence.

Никакие Bearer/payment secrets в acceptance reports не записываются.

## 8. Launch decision
Публичный запуск разрешён только если `backups/production-acceptance-latest.json` содержит одновременно:
- `"status": "passed"`;
- `"accepted_for_launch": true`.

Если gate красный, исправляется конкретный `failed_checks` и весь affected evidence выполняется заново на текущем candidate. Нельзя вручную редактировать JSON evidence или переносить зелёный отчёт с предыдущего commit.
