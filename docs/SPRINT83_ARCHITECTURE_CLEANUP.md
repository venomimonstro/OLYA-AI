# Sprint 83 — Architecture Cleanup — DONE

Sprint 83 removes parallel ownership of cross-cutting business state without rewriting stable adapters merely for aesthetics.

## Canonical ownership
`app/domain_ownership.py` is the machine-readable map for auth, billing, quota entitlement, plan policy, admin overrides, capabilities, inference admission, request deadlines, agent completion/recovery, image beta contract, product analytics, background jobs and recovery integrity.

## Cleanup performed
- Runtime route collisions are rejected by `scripts.architecture_cleanup_audit.py`.
- `/app` remains owned by `app.user_ui`; `user_workspace_base` is only a template dependency and is not registered independently.
- Legacy `PATCH /v1/admin/users/{user_id}` remains for compatibility but no longer mutates `UserQuota` directly. Plan/limit writes are adapted to `AdminUserControl`, then effective quota is rebuilt through `get_or_create_quota`.
- Architecture cleanup is part of the full regression gate.

## Deliberate compatibility boundary
Stable legacy HTTP adapters may remain until a versioned API removal policy exists. They must delegate to canonical domain services and may not own parallel persistence rules.
