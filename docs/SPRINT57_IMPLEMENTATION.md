# Sprint 57 — Model/Prompt Regression Lab

Дата реализации: 2026-09-08.

## Цель

Не допускать тихого ухудшения production после изменения модели, GGUF, llama.cpp sampling, system/template prompts, router, Scope Lock, verification или tool calling.

Regression Lab использует accepted baseline предыдущей принятой версии как сторону A и текущий live runtime как сторону B.

Это позволяет сравнивать версии на одном недорогом CPU/RAM сервере без одновременного хранения и запуска двух 20+ ГБ моделей.

Архитектура:

`golden corpus -> runtime fingerprint -> live candidate run -> deterministic grading -> metric aggregation -> accepted baseline comparison -> pass/block`

## Golden corpus

Файл:

`regression/golden_corpus.json`

Формат:

`x1-model-regression-corpus-v1`

Покрываются обязательные категории roadmap:

- factual;
- writing;
- code;
- RAG;
- long chat;
- tools;
- scope/instruction adherence;
- Russian language.

Корпус содержит critical cases. Любой провал critical case блокирует candidate независимо от среднего score.

Примеры critical invariants:

- точный арифметический ответ;
- столица без лишней генерации;
- strict JSON contract;
- RAG не должен выдумывать отсутствующий факт;
- русский язык;
- точный native tool name + arguments;
- отсутствие hallucinated tool names.

## Почему grader детерминированный

Regression Lab не запускает второй LLM как судью.

Это важно для текущего 32-ГБ CPU/RAM deployment:

- нет дополнительного inference cost;
- judge не меняет мнение между версиями;
- release decision воспроизводим;
- плохая новая модель не может сама поставить себе высокий балл.

Поддерживаемые checks:

- `contains_all`;
- `not_contains`;
- `regex`;
- strict `json_object`;
- `cyrillic_ratio`;
- output length ceiling;
- exact tool name;
- exact normalized tool arguments.

## Runtime fingerprint

Каждый report фиксирует:

- Git HEAD;
- pinned model metadata;
- SHA model manifest;
- corpus version/SHA;
- active server profile evidence;
- SHA ключевых model/prompt/runtime файлов.

Fingerprint включает:

- `app/inference/client.py`;
- `app/inference/router.py`;
- `app/services/context.py`;
- `app/services/quality.py`;
- `app/services/scope_lock.py`;
- `app/services/conditional_verification.py`;
- `app/services/tool_reliability.py`;
- `model-manifest.json`.

Агрегированный `template_runtime_sha256` позволяет однозначно увидеть, что A/B запускались с разными prompt/runtime contracts даже если имя GGUF осталось прежним.

## Метрики

Для text cases сохраняются:

- pass/fail;
- deterministic score;
- TTFT;
- end-to-end generation latency;
- completion/output tokens;
- bounded answer text для диагностики;
- individual check results.

Для tool cases:

- requested tool name;
- parsed JSON arguments;
- exact expected call match;
- tool success rate;
- latency;
- output tokens.

Aggregate report содержит:

- pass rate;
- mean score;
- critical failures;
- p95 TTFT;
- p95 latency;
- mean output tokens;
- tool success rate;
- per-category pass rate/score.

`mean_output_tokens` используется как детерминированный proxy token waste: если новая версия отвечает существенно длиннее при тех же заданиях, release gate видит рост расхода.

## Regression thresholds

Candidate блокируется если:

1. провален хотя бы один critical case;
2. общий pass rate ниже `max(0.90, baseline - 0.03)`;
3. mean score ниже `max(0.90, baseline - 0.03)`;
4. tool success ниже baseline;
5. p95 TTFT выше baseline более чем примерно на 35% с noise allowance 750 мс;
6. p95 latency выше baseline более чем примерно на 35% с noise allowance 2 сек;
7. mean output tokens выше baseline примерно более чем на 25% + небольшой absolute allowance;
8. pass rate отдельной категории падает более чем на 20 процентных пунктов.

Absolute allowance нужен для CPU inference, где небольшие scheduler/host fluctuations нормальны.

## Baseline lifecycle

Baseline:

`backups/model-regression-baseline.json`

Последний candidate:

`backups/model-regression-latest.json`

Обычный запуск НИКОГДА не обновляет baseline.

### Первичное принятие baseline

Только после ручного acceptance текущего production runtime:

```bash
docker compose exec -T app python -m scripts.model_regression_lab \
  --live-url http://llama:8080 \
  --record-baseline
```

Baseline не создаётся, если critical case провален или pass rate ниже 90%.

Если baseline уже существует, команда отказывается его перезаписывать.

### Явная замена baseline

Только после осознанного принятия новой версии:

```bash
docker compose exec -T app python -m scripts.model_regression_lab \
  --live-url http://llama:8080 \
  --record-baseline \
  --force-baseline
```

`--force-baseline` не должен использоваться как способ «починить» красный release gate.

## Candidate gate

Перед model/prompt rollout:

```bash
docker compose exec -T app python -m scripts.model_regression_lab \
  --live-url http://llama:8080
```

Exit code:

- `0` — candidate прошёл;
- `2` — regression;
- `3` — accepted baseline отсутствует;
- `4` — попытка молча перезаписать baseline;
- `5` — candidate недостаточно качественный для baseline.

Таким образом regression может использоваться как обычный release blocker в shell/installer/RC gate.

## Static validation

`scripts/run_full_regression.py` перед pytest всегда выполняет:

`python -m scripts.model_regression_lab --validate-only`

Это проверяет:

- формат corpus;
- уникальность case IDs;
- обязательные категории;
- наличие checks/messages;
- fingerprint files.

Даже без live Qwen сломанный/удалённый golden corpus блокирует общий regression suite.

## A/B semantics

A — immutable accepted baseline предыдущей production-конфигурации.

B — текущий candidate.

Baseline report сохраняет не только aggregate, но и:

- per-case results;
- model metadata;
- corpus SHA;
- runtime/template SHA;
- server profile.

Поэтому сравнение не превращается в безымянные числа.

## Не тестируем через production user quota

Runner обращается непосредственно к private llama.cpp endpoint внутри Docker network.

Причины:

- regression не расходует пользовательские месячные лимиты;
- не создаёт фиктивные conversations/messages;
- не загрязняет UsageEvent;
- измеряет именно model/template layer.

Router/RAG/application regressions дополнительно остаются в pytest, long-context, component acceptance и Sprint 58 end-to-end suite.

## Regression coverage

Добавлен:

`tests/test_sprint57_model_regression_lab.py`

Проверяются:

- обязательные категории;
- critical corpus;
- deterministic checks;
- strict JSON;
- critical blocker;
- TTFT/latency blocker;
- output-token blocker;
- tool success blocker;
- category aggregation;
- corpus validation в full regression;
- model/prompt fingerprint;
- запрет silent baseline overwrite.

## Ограничения

Статические тесты не доказывают реальное качество Qwen.

Перед Sprint 58 RC необходимо на целевом 32-ГБ сервере:

1. создать accepted baseline на известной хорошей версии;
2. выполнить candidate run повторно;
3. проверить repeatability минимум 3 прогонами;
4. заменить один prompt искусственно плохим и убедиться, что gate красный;
5. увеличить verbosity/max tokens и убедиться, что token-waste guardrail срабатывает;
6. сломать tool schema/name и убедиться, что tool success blocker срабатывает;
7. сравнить TTFT/latency без фоновой нагрузки и при контролируемом load;
8. сохранить final report как часть Sprint 58 release evidence.

Production acceptance Sprint 57 считается полным только после такого live target-node прогона.
