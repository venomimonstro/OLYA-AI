# Sprint 43 — настоящий token streaming и cancellation

Дата реализации: 2026-09-07.

## Что изменено

1. `LlamaClient` использует `stream=true` и читает OpenAI-compatible SSE от llama.cpp.
2. Основной `/v1/chat` и потоковый `/v1/chat/stream` используют один и тот же inference transport.
3. `/v1/chat/stream` проксирует реальные `token` chunks в браузер, а не heartbeat с одним финальным ответом.
4. Hidden reasoning не выводится пользователю: наружу идут только `delta.content` chunks.
5. Добавлена transport telemetry: queue wait, TTFT, completion-token count и tokens/sec.
6. Браузер `/app` читает SSE через streaming `fetch` и обновляет один assistant bubble по мере получения chunks.
7. Кнопка `Стоп` вызывает `AbortController.abort()`. Отмена downstream запроса отменяет server task; cancellation выходит из httpx stream context и закрывает upstream соединение к llama.cpp.
8. `ResourceGovernor` освобождает generation slot при `CancelledError`; это покрыто regression-тестом.
9. Если strict/requirements verification после уже показанного первичного ответа делает repair, сервер отправляет `replace` event. UI заменяет live-текст канонической исправленной версией, поэтому серверная история и экран не расходятся.
10. Cancelled requests учитываются как unsuccessful compute usage и не записываются как завершённый assistant answer.

## Telemetry contract

`ChatUsage` дополнен полями:

- `queue_ms` — ожидание generation slot;
- `ttft_ms` — время от начала upstream llama request до первого полезного `content` chunk;
- `output_tokens` — completion tokens из llama usage/timings, с безопасным fallback;
- `tokens_per_second` — reported llama throughput либо вычисленный fallback.

## Cancellation invariant

При закрытии SSE-потока или отмене response generator:

`browser fetch -> FastAPI StreamingResponse -> chat worker task -> LlamaClient.generate -> httpx stream close -> llama.cpp request disconnect`.

CPU generation не должна продолжаться после того, как downstream task отменён. Generation semaphore освобождается через `finally` в `ResourceGovernor.slot()`.

## Regression coverage

`tests/test_sprint43_streaming.py` проверяет:

- `stream=true` в llama request;
- реальную доставку нескольких chunks;
- TTFT/tokens/sec telemetry;
- закрытие upstream stream при task cancellation;
- освобождение governor slot после cancellation;
- SSE token/replace/result contract;
- наличие browser AbortController/Stop;
- backward-compatible defaults `ChatUsage`.

## Дополнительный hardening Sprint 42

Upgrade-path 32-ГБ профиля теперь автоматически снижает старый 2-ГБ sandbox/project-runtime envelope до 1 ГБ и не позволяет минимальному 8K-профилю занимать лишнюю RAM сверх минимально подтверждённого llama cap. На 48/64+ ГБ этот clamp не применяется.

## Что требует target-node проверки

Код и regression tests не заменяют реальный запуск на production-сервере. Финальная acceptance Sprint 43 требует полного:

```bash
python3 scripts/release_gate.py --runtime --live-inference --user-journey --chaos
```

на целевом 32-ГБ узле с реальным Qwen3.6 GGUF.
