# Sprint 66 — Source & Release Recovery Closure

## Result

The remaining recoverable source and release-chain transfer damage is closed. Engineering execution, image runtime and project runtime are readable Python modules; production no longer depends on compressed source execution.

## Migration and ORM closure

- `bff29ea4eab8` is the self-contained root revision for the 16 foundational tables.
- The existing migration chain now upgrades a fresh database to one head and 94 application tables.
- Migration-derived ORM generation records foreign keys and excludes the foundational tables owned by `models_core.py`.
- Explicit indexes and constraints in Sprint 27/38 models match their migration DDL.
- `alembic check` reports no pending operations after a clean upgrade.

## Route and source integrity

FastAPI 0.141 lazy router wrappers made `app.routes` incomplete for X1 release gates. Application registration now exposes concrete routes, rebuilds API handlers with the application dependency-override provider, and explicitly registers onboarding and image-editing surfaces.

Canonical/static audits reject runtime `exec` source wrappers and validate all recovered modules as ordinary parseable Python. Sprint 66 regression coverage exercises both audits, concrete essential routes, the migration root/head and clean-schema drift detection.

## Preserved fail-closed debt

`tests/legacy_sprint0_26.tar.gz` remains an incomplete historical transfer artifact. Missing historical test modules were not fabricated. The archive gate continues to report this explicitly until an authentic upstream copy is recovered.
