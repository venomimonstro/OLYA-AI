# OLYA AI / X1 — Production Release Runbook

Этот runbook описывает только финальный production acceptance. Он не заменяет gates и не разрешает запуск при красном результате.

## 1. Подготовить exact candidate
- Работать из `main` без локальных изменений и untracked release inputs.
- Обновить target server до нужного commit и полностью пересобрать/restart app image. `GET /version` должен соответствовать текущему checkout; старый image будет отклонён автоматически.
- Production host должен удовлетворять выбранному server profile/model manifest. Для starter_6gb используется отдельный low-RAM профиль, а не full worker stack.

## 2. Подготовить production transport и billing
- Для внешнего `X1_PRODUCTION_BASE_URL` нужен HTTPS. HTTP допустим только для loopback acceptance.
- Настроить production billing providers/checkout и payment ingest согласно выбранной схеме ЮMoney/ЮKassa.
- Не сохранять production secrets в Git.

## 3. Подготовить 10 независимых acceptance accounts
- Нужны минимум 10 разных пользовательских аккаунтов, не 10 сессий одного пользователя.
- Получить Bearer token каждого аккаунта и передать их только через environment:
  `X1_LOAD_TOKENS=token1,token2,...,token10`
- Harness проверяет каждый token через `/v1/auth/me` и отклоняет повторяющиеся `user_id`.
- После load acceptance четыре live quality-кейса распределяются по разным acceptance accounts, чтобы quality gate сам не создавал ложный quota failure. При необходимости можно передать отдельный `X1_QUALITY_TOKEN` с достаточным тарифным ресурсом.

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
1. Sprint80 real load acceptance на ≥10 разных аккаунтах;
2. live `/v1/chat` quality acceptance: готовый русский текст, сохранение фактов, решение без лишних вопросов, отсутствие выдуманной метрики и фактический запуск semantic critic на сложном кейсе;
3. Sprint85 release-candidate gate;
4. Sprint86 production acceptance.

При первой ошибке дальнейший выпуск прекращается.

## 6. Что должно быть зелёным
Финальный acceptance требует, среди прочего:
- clean git working tree;
- совпадающий git HEAD для release/load evidence;
- совпадающий deterministic source fingerprint у checkout, load evidence, RC, running app container и public `/version`;
- ≥10 разных authenticated users в реальной нагрузке;
- реальный user-visible chat quality gate через канонический `/v1/chat`;
- answer-quality pipeline: task-specific answer contract, server-owned evidence provenance, evidence-aware critic и bounded conditional repair;
- full regression/security/recovery/runtime gates;
- model regression + quality corpus validation;
- backup + restore drill;
- runtime chaos;
- PostgreSQL, Qwen/llama.cpp и SearXNG; тяжёлые workers — в соответствии с выбранным launch profile;
- `/ready` со статусом, соответствующим release contract;
- green release-readiness и Sprint84 business contract;
- live required capabilities выбранного профиля;
- рабочий production billing checkout/payment-ingest configuration;
- HTTPS для внешней production endpoint.

## 7. Evidence
Отчёты сохраняются локально на target node в `backups/` и исключены из Git:
- `load-acceptance-latest.json`;
- `chat-quality-acceptance-latest.json`;
- `quality-regression-latest.json` при отдельном live quality-lab прогоне;
- `release-gate-latest.json`;
- `rc-release-candidate-latest.json`;
- `production-acceptance-latest.json`;
- restore/model/chaos evidence.

Никакие Bearer/payment secrets в acceptance reports не записываются.

## 8. Launch decision
Публичный запуск разрешён только если финальный оркестратор завершился с кодом 0 и `backups/production-acceptance-latest.json` содержит одновременно:
- `"status": "passed"`;
- `"accepted_for_launch": true`.

При этом `backups/chat-quality-acceptance-latest.json` также обязан содержать `"passed": true`. Технически живой сервер с плохим пользовательским качеством не считается готовым к запуску.

Если gate красный, исправляется конкретный `failed_checks` и весь affected evidence выполняется заново на текущем candidate. Нельзя вручную редактировать JSON evidence или переносить зелёный отчёт с предыдущего commit.
