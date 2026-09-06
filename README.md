# OLYA-AI / X1

X1 — локальная self-hosted AI-платформа для РФ, рассчитанная на CPU/RAM-сервер без покупки внешнего LLM inference API. Основной runtime использует локальную модель через llama.cpp; вокруг неё реализованы чат, проекты, файлы, research, quality verification, документы, изображения, агентная разработка, sandbox, Git/GitHub, Commerce/API, эксплуатационная диагностика и release gates.

## Текущая версия

**0.36.0 — Sprint 36: Target-Node Capacity & Closed-Beta Launch Calibration.**

Ключевые принципы:
- GitHub `main` — единственный канонический исходный код;
- без CI и без ветвления для текущей разработки проекта;
- production inference локальный;
- ресурсы измеряются по реальному CPU/RAM runtime, а не по условным model tokens;
- пользовательские данные, документы, изображения и code workspaces сохраняются в persistent storage;
- публичный релиз блокируется при красном release gate;
- меняющиеся факты не должны выдаваться как проверенные без свежего research evidence.

## Production install

```bash
bash scripts/install.sh
```

Установщик создаёт production `.env`, генерирует секреты, поднимает PostgreSQL, применяет Alembic, запускает локальный inference, создаёт backup и выполняет release gate.

Перед публичным трафиком на целевом сервере должен пройти:

```bash
python3 scripts/release_gate.py --runtime --live-inference
```

Sprint 36 дополнительно запускает target-node capacity calibration:

```bash
python3 scripts/capacity_calibrate.py --samples 1
```

Калибровка проверяет 8K / 12K / 16K context tiers, p95 latency, peak llama RSS, swap growth и свободную память. Результат сохраняется в `backups/capacity-latest.json`, а безопасные рекомендуемые значения — в `backups/capacity-recommended.env`. Они не применяются автоматически.

## Closed beta

Администратор управляет beta cohort через `/v1/admin/beta/*`.

Основные endpoints:
- `GET /v1/admin/beta/current` — текущие D1/D7/D30, success, frustration, p50/p95/p99, compute metrics;
- `POST /v1/admin/beta/snapshots` — сохраняет исторический snapshot;
- `GET /v1/admin/beta/calibration` — объединяет target-node capacity report и реальные beta-метрики в рекомендованный production plan.

Пересчёт боевых compute limits допускается только после достаточной выборки: **50–100 участников и минимум 500 задач**. D1/D7/D30 показываются как продуктовые сигналы и не превращаются в выдуманные release thresholds.

## Production readiness

`GET /v1/admin/reliability/release-readiness` требует зелёных:
- database/schema/config/storage/inference/migrations;
- queue health;
- route contract;
- complaint regression release gate;
- verified backup;
- restore drill;
- full regression release gate;
- свежая target-node capacity calibration.

## Тестирование

Полный release regression включает текущие тесты и immutable historical bundle Sprint 0–26 (45 модулей):

```bash
python3 scripts/release_gate.py
```

На production node используется Docker `gate` profile, изолированный от production `x1_data`.

## Roadmap

Подробный статус спринтов находится в `ROADMAP.md`.
