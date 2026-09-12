# Sprint 84 — Business Logic Contract — DONE

Sprint 84 converts the main cross-domain business assumptions into one executable, read-only contract.

## Contract owner
`app.services.business_contract.evaluate_business_contract()` validates persisted state across billing, quota entitlement, admin overrides, organizations, API keys, projects, tasks and autonomous development.

## Invariants
- Active paid subscription and active admin override determine the expected effective plan; `UserQuota` may not disagree with that canonical entitlement.
- Plans and organization plans must exist in the measured runtime plan catalog.
- Organization members and API-key organization access must remain valid after membership changes.
- API keys must have valid scopes, rate limits, expiry/revocation state and an existing owner.
- Project/member ownership and roles must remain valid.
- A completed task must have a completion timestamp and satisfy its canonical evidence gate.
- A completed development plan/ledger must pass the independent agent completion proof and may not have immutable-contract drift.

## Release integration
`GET /v1/admin/reliability/business-contract` exposes the read-only report to administrators. `release-readiness` includes `business.logic_contract` as a mandatory blocker and returns the current contract report.

The audit is included in `scripts/run_full_regression.py`. No database repair is performed automatically by this contract; violations remain explicit release blockers.
