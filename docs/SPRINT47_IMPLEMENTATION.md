# Sprint 47 — Freshness Router + Evidence

Дата реализации: 2026-09-08.

## Цель

OLYA AI не должна выдавать изменяемые факты из памяти Qwen как будто они проверены сейчас. Решение о необходимости интернета должно приниматься backend-кодом, а не браузерным regex.

## Backend Freshness Router

`app/services/freshness.py` классифицирует запрос в одну из категорий:

- `stable`;
- `news`;
- `weather`;
- `market`;
- `price`;
- `availability`;
- `schedule`;
- `official_role`;
- `law`;
- `software_version`;
- `recent_general`.

Результат содержит:

- требуется ли fresh research;
- категорию;
- причину;
- максимально допустимый возраст snapshot;
- минимальное число независимых host/domain;
- confidence классификации.

Классификация детерминированная и не запускает отдельный LLM inference.

## Anti-oversearch

Исторические запросы и стабильные объяснения не должны автоматически запускать интернет.

Примеры:

- `Что такое инфляция?` -> stable;
- `Что такое цена и как она формируется?` -> stable;
- `Кто был президентом США в 1995 году?` -> stable;
- `Какой сейчас курс доллара?` -> market/current;
- `Какая последняя версия PostgreSQL?` -> software_version/current.

Это снижает latency и CPU/network waste.

## Freshness windows

Production defaults зависят от типа данных:

- weather: 15 минут, 1 source host;
- market/rates: 15 минут, 2 independent hosts;
- availability: 15 минут, 1 host;
- news: 30 минут, 2 hosts;
- prices: 1 час, 2 hosts;
- schedules/opening hours: 1 час, 1 host;
- official roles: 6 часов, 2 hosts;
- law/regulation: 24 часа, 2 hosts;
- software versions/releases: 24 часа, 2 hosts;
- generic explicit latest/current: 24 часа, 2 hosts.

`SourceContextBuilder` применяет эти limits к фактическому `ResearchSource.fetched_at`.

## Evidence policy

Для current claim источник считается пригодным только если:

1. source принадлежит пользователю/доступному проекту;
2. status `ready`;
3. source trust layer не пометил snapshot quarantined;
4. snapshot не старше category freshness window;
5. выполнено требование independent-host diversity.

Если diversity или freshness не прошли, URLs не входят в verified set. Quality gate затем не может поставить current claim статус `supported` только на основании памяти модели.

## UI Auto / Always / Off

До Sprint 47 браузер содержал локальный `needsFresh()` regex. Это было неправильной trust boundary.

Теперь:

- `Интернет: авто` -> UI создаёт backend ResearchRun и читает `run.plan.freshness`;
- `stable` -> discovery/collect не запускаются;
- `current` -> выполняются discovery + collect;
- `Интернет: всегда` -> research выполняется принудительно независимо от verdict;
- `Интернет: выкл` -> сеть не запускается, но chat backend всё равно добавит freshness warning для current-fact запроса без evidence.

То есть выключение web не превращает неподтверждённую память модели в свежий факт.

## Direct API safety

Если API-клиент вызывает `/v1/chat` напрямую без research sources для current query, `SourceContextBuilder` добавляет `FRESHNESS_SENTINEL` и policy message. Ответ может использовать стабильный background, но current claim не считается проверенным.

Для автоматического research API-клиент использует существующий pipeline:

`POST /v1/research/runs -> discover -> collect -> research_source_ids -> /v1/chat`.

## Security

Sprint 47 сохраняет прежние SSRF/private-IP/redirect protections, source trust и prompt-injection quarantine. Старый или quarantined источник может быть показан как background, но не подтверждает текущий факт.

## Regression coverage

`tests/test_sprint47_freshness_router.py` покрывает:

- weather;
- currency/market;
- prices;
- current officials/CEO;
- software releases;
- current law;
- general recency;
- historical/stable anti-oversearch;
- backend planner integration;
- отсутствие browser-owned `needsFresh()` regex;
- category-aware evidence policy.

## Ограничение

Freshness Router определяет необходимость исследования, но не гарантирует, что интернет содержит качественный ответ. Если search/collection не дают достаточного evidence, система должна сохранить статус insufficient/unverified, а не подменять его уверенным ответом Qwen.

Полный acceptance требует target-node release gate с реальным SearXNG, сетью и PostgreSQL.
