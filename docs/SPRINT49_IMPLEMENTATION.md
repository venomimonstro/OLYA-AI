# Sprint 49 — Tool Calling Reliability

Дата реализации: 2026-09-08.

## Цель

OLYA AI должна использовать инструменты как контролируемые server capabilities, а не как произвольные команды, которым сервер доверяет только потому, что их сгенерировала Qwen.

Sprint 49 создаёт общий reliability boundary для Sprint 50 Coding Agent и других будущих агентов.

## Pipeline

`Qwen native tool turn -> strict OpenAI-compatible parser -> ToolRegistry allowlist -> Pydantic args validation -> ToolSession admission -> bounded execution -> ToolResult/tool_call_id -> next model turn`

Модель никогда не является authorization boundary.

## Native Qwen / llama.cpp tool turn

`LlamaClient.tool_turn()` поддерживает OpenAI-compatible `tools` и `tool_calls`.

Политика:

- `tool_choice=auto`;
- `parallel_tool_calls=false`;
- максимум 32 зарегистрированных tool schemas;
- максимум 8 calls в одном model turn;
- tool name имеет строгий формат;
- `tool_call_id` имеет строгий bounded формат;
- duplicate call ids в одном ответе запрещены;
- arguments обязаны быть JSON object;
- malformed JSON не попадает в executor;
- turn без текста и без tool calls считается invalid inference response.

Parallel side effects намеренно выключены: на одном CPU/RAM сервере предсказуемая последовательность важнее теоретической скорости.

## Registry allowlist

Tool можно вызвать только если он зарегистрирован в `ToolRegistry`.

Неизвестное имя вызывает `unknown_tool`.

Каждый `ToolSpec` содержит:

- name;
- description;
- строгую Pydantic argument schema;
- handler;
- `read` или `write` effect;
- timeout;
- retry policy;
- maximum result size.

`StrictToolArgs` запрещает неизвестные поля (`extra=forbid`). OpenAI schema также содержит `additionalProperties=false`.

## Idempotency

Каждый вызов получает fingerprint:

`SHA256(tool_name + canonical_normalized_arguments)`.

После успешного вызова результат хранится в session cache.

Если модель повторяет ту же операцию с новым `tool_call_id`, физический handler повторно не выполняется: возвращается `cached` result.

Это действует и для write-tools.

При этом повторы учитываются loop detector-ом. После допустимого cached replay дальнейшее повторение того же fingerprint блокируется.

## tool_call_id

Call id связывает assistant tool request с конкретным `role=tool` result.

Один и тот же call id нельзя повторно использовать с другими arguments. Это fail-closed validation error.

## Retry policy

Read-only tools могут иметь небольшой bounded retry (`0..3`).

Write tools:

- всегда `max_retries=0`;
- никогда автоматически не replay после timeout/error;
- успешный повтор обслуживается из fingerprint cache.

## Uncertain write state

Особенно важен timeout side-effecting операции.

Python не может безопасно force-kill произвольный worker thread. Поэтому timeout не доказывает, что операция не произошла.

После timeout/exception write-tool:

1. fingerprint операции помечается uncertain;
2. replay того же fingerprint блокируется;
3. ToolSession переходит в `write_state_uncertain`;
4. любые дальнейшие writes в этой session блокируются до внешней reconciliation;
5. read-only tools можно использовать для проверки фактического состояния.

Это предотвращает двойную запись после delayed completion.

## Loop detection

ToolSession ограничивает:

- общее число физических tool executions;
- число запросов одного fingerprint;
- последовательные вызовы одного и того же tool;
- повторный `tool_call_id` с другими arguments.

Типовые циклы вида:

`git.status -> git.status -> git.status -> git.status`

или

`workspace.read(a.py) -> workspace.read(a.py) -> ...`

останавливаются до бесконечного расхода Qwen/CPU.

## Bounded agent loop

`app/services/tool_agent.py` реализует общий serial agent loop.

Defaults:

- до 8 model/tool steps;
- hard maximum 12;
- до 2 recoverable tool errors;
- terminal stop на tool loop;
- terminal stop при exhausted tool budget;
- terminal stop при blocked uncertain write replay.

Malformed/ordinary bounded tool error может быть один раз показан модели, чтобы она исправила arguments. Ошибочные recovery loops ограничены.

## Result size

Tool output не может бесконтрольно заполнить context.

Каждый ToolSpec имеет `result_max_chars`.

Большой string обрезается с explicit marker. Большой structured result возвращается как валидный объект с:

- `truncated=true`;
- preview;
- original character count.

В prompt не отправляется сломанный кусок JSON.

## Safe coding registry

`app/services/coding_tools.py` создаёт минимальный tool set для Coding Agent:

- `workspace.map`;
- `workspace.read`;
- `workspace.write`;
- `git.status`.

В Sprint 49 deliberately НЕ предоставляются:

- arbitrary shell;
- network access;
- Git commit;
- Git push;
- secrets;
- host filesystem outside workspace;
- Docker socket.

Workspace paths проходят существующие `safe_relative_path/resolve_inside` protections и дополнительный approved-scope allowlist.

`workspace.write` использует `expected_sha256` optimistic concurrency guard, поэтому агент не должен молча перезаписывать файл, изменившийся после чтения/плана.

## Почему shell не включён

Sprint 49 — reliability boundary, а не выдача новых capability.

Sandbox execution уже существует в проекте как отдельная privilege boundary. Sprint 50 подключит тестовые/исполнительные tools только через эту границу и с отдельными policy limits.

## Security properties

- model output не определяет доступные tools;
- unknown tool fail-closed;
- unknown arguments fail-closed;
- path scope server-owned;
- writes serial;
- no write retries;
- uncertain side effect freezes writes;
- no parallel tool calls;
- no auto shell/network/push;
- bounded tool output;
- bounded steps/errors/calls.

## Regression coverage

`tests/test_sprint49_tool_reliability.py` проверяет:

- unknown tool rejection;
- strict args / additionalProperties=false;
- prohibition of write retries;
- successful write idempotency across different call ids;
- duplicate fingerprint loop detection;
- call-id reuse conflict;
- uncertain write timeout and write freeze;
- workspace approved scope;
- optimistic SHA write;
- absence of shell/network/commit/push from coding registry;
- native llama serial tool protocol;
- bounded agent loop.

## Связь со Sprint 50

Sprint 49 не пытается заменить существующий engineering plan/review pipeline. Он создаёт исполнительную основу.

Sprint 50 сможет строить доказуемый Coding Agent поверх:

`plan -> ToolSession -> inspect -> edit -> sandbox tests -> verify diff -> proof of result`

и не будет заново изобретать validation/retry/idempotency/loop protection для каждого инструмента.

## Production acceptance

Кодовая реализация в GitHub не равна runtime certification.

На target node необходимо проверить:

- Qwen3.6 native tool-call compatibility с pinned llama.cpp;
- malformed tool-call corpus;
- loop corpus;
- timeout/retry behavior;
- side-effect idempotency;
- workspace scope escapes;
- memory/latency на длинных tool traces.

Особенно нужно подтвердить поведение текущего Qwen3.6/llama.cpp build на реальных tool-call turns перед включением Coding Agent для пользователей.
