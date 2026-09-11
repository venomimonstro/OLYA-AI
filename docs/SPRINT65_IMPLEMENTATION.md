# Sprint 65 — Canonical ORM Recovery & Source Integrity

## Result

The canonical model registry is readable, import-safe and reproducible again. The truncated `app/_models_impl.py.gz` transfer artifact and its runtime `exec` path are removed.

## Registry design

- `app/models_core.py` owns the foundational user, project, file, task, conversation, quota and job tables.
- `app/models_migrations.py` owns tables whose canonical schema is the Alembic chain.
- `app/models_sprint*.py` retain domain extensions introduced by later sprints.
- `app/models.py` imports those layers into the single public registry backed by `app.db.Base`.

`scripts/generate_orm_models.py` replays migration declarations into a schema recorder and emits deterministic model source. Application-side defaults that are intentionally absent from DDL are listed explicitly in `ORM_DEFAULTS`. `--check` fails when generated source drifts.

## Integrity gates

`scripts/canonical_source_audit.py` now parses and compiles every readable model source and verifies generator synchronization. `scripts/static_contract_audit.py` rejects any return of model payload execution.

Regression coverage creates and drops the complete 94-table graph on SQLite, checks every table has a primary key, verifies generation drift, and persists recovered core entities with their application defaults.

## Remaining repository debt

The full historical suite now runs past ORM import. It still exposes pre-existing transfer damage outside this sprint: two compressed service wrappers, an incomplete legacy regression archive, a broken Alembic predecessor reference and several static product contracts. Those findings remain fail-closed and should be addressed in the following recovery sprint.
