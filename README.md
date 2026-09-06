# OLYA-AI / X1

X1 — локальная self-hosted AI-платформа для РФ, рассчитанная на CPU/RAM-сервер без покупки внешнего LLM inference API. Основной runtime использует локальную модель через llama.cpp; вокруг неё реализованы чат, проекты, файлы, research, quality verification, документы, изображения, агентная разработка, sandbox, Git/GitHub, Commerce/API, эксплуатационная диагностика и release gates.

## Текущая версия

**0.37.0 — Sprint 37: Closed-Beta Operations & Adaptive Capacity Control.**

Ключевые принципы:
- GitHub `main` — единственный канонический исходный код;
- production inference локальный;
- ресурсы измеряются по реальному CPU/RAM runtime, а не по условным model tokens;
- пользовательские данные сохраняются в persistent storage;
- публичный релиз блокируется при красном release gate;
- меняющиеся факты не должны выдаваться как проверенные без свежего research evidence;
- новые beta-пользователи допускаются волнами, а не все сразу;
- capacity limits меняются только через измеренный, версионированный и подтверждённый план.

## Production install и release gate

```bash
bash scripts/install.sh
python3 scripts/release_gate.py --runtime --live-inference
```

Target-node calibration:

```bash
python3 scripts/capacity_calibrate.py --samples 1
```

Калибровка проверяет 8K / 12K / 16K context tiers, p95 latency, peak llama RSS, swap growth и свободную память. Результат сохраняется в `backups/capacity-latest.json`.

## Closed-beta operations

Операторский экран: **`/admin/beta`**.

Основные API:
- `GET /v1/admin/beta/control` — текущий admission decision, beta telemetry, capacity report и активный capacity plan;
- `POST /v1/admin/beta/waves` — новая волна;
- `POST /v1/admin/beta/waves/{id}/open|evaluate|pause|resume|close` — lifecycle волны;
- `GET /v1/admin/beta/trends` — сравнение исторических snapshot и anomaly detection;
- `GET /v1/admin/beta/feedback` — жалобы пользователей beta с cohort/wave context;
- `POST /v1/admin/beta/feedback/{id}/confirm` — подтверждённый beta-дефект передаётся в общий Complaint Regression;
- `POST /v1/admin/beta/capacity-plans/propose` — создаёт draft из реальной beta + target-node calibration;
- `POST /v1/admin/beta/capacity-plans/{id}/approve` — явное подтверждение администратором;
- `POST /v1/admin/beta/capacity-plans/{id}/activate` — live-safe применение либо подготовка restart artifact;
- `POST /v1/admin/beta/capacity-plans/rollback` — append-only rollback к ранее активному плану.

Каждая волна имеет ограничение по числу новых участников и собственный compute budget. Admission автоматически закрывается при исчерпании бюджета либо при деградации success-rate, frustration, p95 queue/duration или verified-success/CPU efficiency.

В production встроен лёгкий beta-operations scheduler: он не запускает inference, раз в заданный интервал проверяет состояние, сохраняет не более одного beta snapshot за сутки и переоценивает активную волну. D1/D7/D30 остаются измеряемыми продуктовыми сигналами, а не искусственными release thresholds.

## Capacity plans

План хранится версионированно в БД вместе с сигналами, guardrails и diff относительно предыдущего плана. Увеличение context/concurrency сверх boot envelope не применяется «на горячую»: создаётся secret-free `backups/capacity-plan-vN.env`, а предыдущий known-good plan остаётся active до реального restart/apply. Без активного plan, совпадающего с runtime, public release readiness остаётся красным.

Финальные числовые лимиты Free/X1/Pro/Max/Business не выдумываются заранее: они должны быть рассчитаны после реальной beta-выборки **50–100 пользователей и минимум 500 задач**.

## Production readiness

`GET /v1/admin/reliability/release-readiness` требует зелёных database/schema/config/storage/inference/migrations, queue, route contract, Complaint Regression gate, backup/restore drill, full release gate, свежую target-node calibration и активный capacity plan, совпадающий с runtime.

## Тестирование

Полный release regression включает текущие тесты и immutable historical bundle Sprint 0–26 (45 модулей):

```bash
python3 scripts/release_gate.py
```

На production node Docker `gate` profile изолирован от production `x1_data`.

## Roadmap

Подробный статус спринтов находится в `ROADMAP.md`.
