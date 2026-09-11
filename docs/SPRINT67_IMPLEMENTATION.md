# Sprint 67 — Public API Hardening

## Result

The existing external API is hardened without creating a second inference pipeline. API Chat still delegates to the canonical authenticated Chat controller, while admission, identity, context and accounting guarantees are enforced at the API boundary.

## Credential lifecycle

- API credentials must use exactly one of `X-API-Key` or `Authorization: Bearer`.
- Tokens are rejected before hashing unless they match the generated bounded `x1k_` grammar.
- Organization-bound keys require current owner/manager access on every request and fail closed after demotion or removal.
- Active keys per owner are bounded by `X1_API_MAX_ACTIVE_KEYS_PER_USER`.
- Rotation revokes the old secret and creates the replacement in one transaction; plaintext is returned only in the creation response.
- Past expiration timestamps are rejected.

## Admission and idempotency

The fixed-minute limiter now returns accurate `X-RateLimit-Limit`, `X-RateLimit-Remaining`, `X-RateLimit-Reset` and boundary-aware `Retry-After` headers. Window creation remains race-safe through a savepoint.

API Chat accepts `Idempotency-Key` and `client_request_id`, rejects conflicting identities, and forwards one validated logical ID to the Sprint 62 durable Chat run. Telemetry IDs are deterministically scoped to the API key and logical request. Replays reuse the existing telemetry row and cannot add a second resource expense.

## Context boundary

Persistent contexts support list, create, get and delete. Every operation is isolated by owner and organization. Context count is bounded by `X1_API_MAX_CONTEXTS_PER_OWNER`; metadata is limited to 16 KiB; ambiguous context plus direct project/conversation targeting is rejected.

## Release gates

`scripts/api_contract_audit.py` verifies required method/path pairs, duplicate operations, rate/key/context settings, one-time secret exposure and Chat idempotency. It runs directly in `scripts/release_gate.py` and from `scripts/run_full_regression.py` alongside the product/source audits.

`tests/test_sprint67_api_hardening.py` covers credentials, rate headers, tenant isolation, context deletion, rotation, organization revocation, telemetry replay, payload bounds and release-gate wiring.
