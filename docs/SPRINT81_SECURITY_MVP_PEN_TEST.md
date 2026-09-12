# Sprint 81 — Security MVP Pen-Test Simulation — DONE

Sprint 81 превращает ключевые security assumptions в исполняемую regression matrix.

- Auth: hashed bearer sessions, revocation/expiry/inactive-user guards.
- API: strict key shape, scope checks и atomic per-minute rate limit.
- Files/workspace: absolute/parent traversal rejected, host commands run with `shell=False`.
- Research: URL credentials, localhost/private/special-use IP SSRF blocked before network fetch.
- Sandbox: read-only root, cap-drop, no-new-privileges, process/memory/CPU limits и network none.
- Git: only github.com, credentials rejected in URLs, file/ext protocols disabled, hooks disabled, secret scanning/redaction.
- Billing: payment user/amount/currency must match server-owned checkout.
- Admin: current authenticated user + explicit `is_admin` gate.
- HTTP body/deadline admission remains fail-closed.
- `security_mvp_simulation` executes concrete traversal/SSRF/Git URL abuse cases in addition to static guard checks and is part of full regression.
