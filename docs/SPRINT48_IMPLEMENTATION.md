# Sprint 48 — Conditional Verification

Дата реализации: 2026-09-08.

## Цель

Повысить качество ответов OLYA AI без схемы `primary -> critic` для каждого запроса. На 32-ГБ CPU/RAM production-профиле второй Qwen inference должен запускаться только тогда, когда риск или уже найденный дефект это оправдывает.

## Pipeline

### Verification Off

`primary -> answer`

Никаких quality inference. Scope/system policy всё равно остаются частью prompt, но post-generation verification отключена по явному выбору пользователя.

### Verification Auto

Базовый путь:

`primary -> deterministic gates -> answer`

Если deterministic gate доказал конкретный дефект:

`primary -> deterministic fail -> repair -> deterministic recheck -> answer`

Если deterministic gates чистые, но risk score повышен:

`primary -> deterministic -> conditional critic -> answer`

Если Critic нашёл major/critical defect и остался verification budget:

`primary -> deterministic -> critic -> targeted repair -> deterministic recheck -> answer`

Максимум — два дополнительных LLM-вызова после primary. Циклы `critic -> repair -> critic -> repair` запрещены.

### Verification Strict

Strict всегда просит semantic Critic, но остаётся под тем же hard cap двух дополнительных inference. Если сначала пришлось исправлять deterministic defect, оставшийся budget допускает Critic, но не бесконечную повторную редактуру.

## Risk planner

`app/services/conditional_verification.py` рассчитывает risk score без отдельной модели.

Учитываются:

- Fast / Work / Deep route;
- явные output requirements;
- Scope Lock;
- freshness-sensitive question;
- отсутствие verified fresh evidence;
- security/audit/architecture/code/legal/financial/medical/research задачи;
- длинный ответ;
- deterministic `unverified`;
- deterministic failures.

Простая трансформация вроде исправления опечаток при чистом deterministic audit не должна запускать Critic.

## Почему deterministic идёт первым

JSON validity, placeholders, max/min length, explicit contains/not-contains, Scope Lock и source URL grounding можно проверить обычным кодом. Нет смысла оплачивать Qwen Critic для ошибки, которую сервер уже доказал детерминированно.

## Critic policy

Critic используется для семантических дефектов, которые нельзя надёжно доказать regex/JSON/parser-проверкой:

- противоречие запросу;
- пропущенная существенная часть задачи;
- внутреннее противоречие;
- неподтверждённое утверждение;
- нарушение сложной инструкции.

Minor issue не запускает новый Repair. Только `major` и `critical` являются repairable critic findings.

## Targeted Repair

Major/critical findings Critic преобразуются в explicit failed checks через `audit_with_critic_issues()`. После этого используется тот же единый `AnswerQualityEngine.repair_messages`, что и для deterministic defects.

Таким образом в проекте нет двух несовместимых repair prompt formats.

После repair deterministic gates запускаются повторно. Повторный Critic не запускается, чтобы исключить loop и непредсказуемый расход CPU.

## Streaming consistency

Если пользователь уже видел primary tokens, исправленный ответ отправляется через существующий Sprint 43 SSE `replace`. Каноническая server history и UI поэтому не расходятся.

## Compute reservation

До primary inference backend делает предварительный risk-plan.

- low-risk Auto: резервируется один primary inference;
- explicit requirements / Scope Lock: дополнительно резервируется один возможный repair;
- high-risk Auto: резервируется до двух дополнительных quality calls;
- Strict: резервируется до двух дополнительных calls.

Это не означает, что зарезервированные calls обязательно будут выполнены. Реальные `verification_extra_inferences` считаются отдельно.

## Telemetry

`ChatUsage` теперь содержит:

- `verification_risk_score`;
- `verification_extra_inferences`;
- `critic_used`;
- `repair_applied`.

Те же данные сохраняются внутри `AnswerAudit.critic.conditional_verification`.

Это позволит Sprint 53 считать:

- CPU cost of verification;
- долю запросов, где Critic реально понадобился;
- repair rate;
- wasted verification compute;
- quality/cost tradeoff по Auto и Strict.

## Safety / failure semantics

- Critic unavailable не уничтожает primary answer;
- ответ получает warning и не притворяется полностью подтверждённым;
- repair unavailable сохраняет исходный ответ с найденным дефектом/warning;
- freshness without evidence остаётся `unverified` даже если Critic не нашёл стилистических проблем;
- cancellation продолжает разматывать общий inference scope и освобождает generation semaphore.

## Regression coverage

`tests/test_sprint48_conditional_verification.py` проверяет:

- один inference для простой Auto-трансформации;
- Critic для high-risk audit;
- повышенный риск current fact без evidence;
- deterministic repair без Critic-before-repair;
- Strict critic policy;
- major/critical threshold;
- преобразование critic findings в repair targets;
- hard cap двух дополнительных inference;
- Scope Lock / requirements quota reservation;
- API/AnswerAudit telemetry;
- отсутствие старого `strict-only critic` gate.

## Production acceptance

Кодовая реализация не заменяет запуск на target node. Для окончательного принятия Sprint 48 нужно измерить на реальном Qwen3.6:

- долю Auto requests с 1/2/3 total inference;
- p50/p95 latency;
- CPU seconds per successful answer;
- critic false-positive rate;
- repair success rate;
- качество на complaint/regression corpus.

Полный runtime acceptance выполняется существующим release gate на production-подобном 32-GiB узле.
