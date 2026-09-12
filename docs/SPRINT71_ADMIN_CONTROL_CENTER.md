# Sprint 71 — Admin Control Center

Sprint 71 consolidates the operator view without introducing a parallel admin state machine.

## Delivered

- `GET /v1/admin/operations/control-center` aggregates canonical account, traffic, economics, resource, billing, API, complaint/regression, safety/risk and overload signals.
- `/admin` is rebuilt as a responsive read-only control center with release, health, billing, API, queue/load, resource, performance and admin-audit visibility.
- Existing `/admin/beta`, `/admin/launch` and `/admin/media` remain dedicated deep-dive surfaces and are linked from the control center.
- The main dashboard intentionally does not mutate users, plans or restrictions; those actions belong to Sprint 72.
- Admin token stays in `sessionStorage` for the current tab only.
- The admin surface now uses nonce-based CSP and removes `unsafe-inline` script/style execution.
- `admin_control_center_audit` and Sprint 71 regression coverage are included in the full release regression.

## Operational contract

The control-center endpoint is aggregate/read-only. Domain writes remain owned by their existing services/routes so dashboard code cannot become a second source of truth.
