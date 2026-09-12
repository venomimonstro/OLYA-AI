# X1 final stability audit — 2026-09-12

## Scope

Final pre-launch review of runtime registration, authentication, owner UX, OAuth, SMTP/email lifecycle, billing/payment verification, support, analytics, abuse protection, starter-node resource safety, migrations, update/rollback and release gating.

## Critical findings fixed during this audit

### 1. Launch operations router was not registered in the real application

The SMTP/payment/support/owner bundle existed but was not included by `app/main.py`. This could leave completed launch functionality absent at runtime. `app.api.routes.launch_bundle` is now included in the product-router registration path and the final audit explicitly requires the real routes to exist in `app.routes`.

### 2. Global inference governor did not receive the configured per-principal queue limit

The application now passes `inference_max_queued_per_principal` into the production `ResourceGovernor`, preserving the intended one-account queue isolation on the small node.

### 3. Verification-required registration still created a local session

Protected APIs already had a server-side email-verification guard, so this was not a complete authorization bypass. It was nevertheless the wrong lifecycle: an unverified user should not receive a usable session at all. Verification-required registration is now sessionless until the email is verified.

### 4. OAuth-specific authentication rate limiting

The first implementation used a shared fake email value for all Yandex OAuth starts, which could throttle unrelated users together. OAuth starts now use global + per-IP buckets while email/password flows retain per-email limits.

### 5. Admin session usability

The Control Center can use the already authenticated X1 admin session and promotes it to the admin sessionStorage key only after protected admin endpoints accept it. This removes routine copy/paste of bearer tokens while still relying on server-side `is_admin` authorization.

## Yandex ID OAuth

Implemented owner-controlled Yandex OAuth 2.0 login:

- owner toggle, Client ID and encrypted Client Secret;
- exact callback URI shown in Admin Integrations;
- Authorization Code flow;
- PKCE S256;
- cryptographic state stored as a hash server-side;
- HttpOnly, SameSite=Lax state cookie, Secure in production;
- single-use OAuth state with a 10-minute TTL and row locking;
- state is committed consumed before external token/profile requests, preventing replay after upstream timeouts;
- Yandex access/refresh tokens are not stored;
- profile request uses the OAuth Authorization header;
- returned OAuth application `client_id` is checked against the configured application;
- Yandex identity is durably linked to one local user;
- concurrent account creation/linking is handled with database uniqueness and savepoints;
- inactive X1 users fail closed;
- Yandex-confirmed email is treated as verified locally;
- integration can be disabled immediately by the owner;
- public login/register button is capability-driven and disappears when the integration is disabled/not ready;
- Metrika goals include Yandex login start/success without email or chat content.

## Authentication and account lifecycle

- Password hashing: scrypt.
- Login account-enumeration timing mitigation retained.
- Auth entry has bounded global/IP/email rate limits.
- Verification-required registration depends on SMTP readiness and does not issue a local session before verification.
- Password reset is tokenized, expires and revokes existing sessions after password change.
- Logout-all revokes server sessions in bulk.
- Suspended users are rejected centrally by the authentication dependency.

## Load / stability controls

- Global local-inference concurrency remains bounded.
- Queue length is bounded.
- Per-principal queued requests are bounded.
- Priority aging prevents background starvation while interactive work remains favored.
- Per-user plan quotas include monthly compute, daily/monthly request units and measured resource budgets.
- Public rollout has per-user request-burst circuit breakers and global launch guardrails.
- API keys have atomic database-backed rate-limit windows, scopes, expiry and organization-access checks.
- File uploads, research, images and sandbox use separate admission/resource controls.
- Starter 6 GB profile intentionally keeps one local model generation at a time.

## Payment safety

- YooMoney notifications require HMAC-SHA256 verification and server-owned checkout amount/currency/label checks.
- YooKassa webhook events do not directly grant entitlement; payment state is re-read from YooKassa before applying billing state.
- Billing checkout and payment ingestion are idempotent/race-aware.
- Browser return from a payment page is not authoritative payment success.

## Owner operations

Owner surfaces include:

- MRR, ARPU/ARPPU, paid users, checkout conversion and refunds;
- activation and D1/D7 retention;
- request success, frustration, compute waste, p95 latency/queue and CPU per successful answer;
- support queue;
- SMTP, Yandex ID, Metrika and payment-provider readiness;
- technical health/release blockers and audit trail.

## Installer / update / recovery

- Starter installer can provision prerequisites on Debian/Ubuntu, generate secrets, migrate PostgreSQL, create the initial administrator, download/verify Qwen and start the profile.
- Update path preserves starter/full profile, performs pre-update backup + restore drill and supports rollback.
- Production configuration rejects default critical secrets and SQLite.

## Release status

The codebase has a stronger static release contract, including `scripts.final_launch_hardening_audit` and Yandex OAuth regression contracts. A static code review cannot truthfully prove 100% production reliability.

Public launch remains blocked until these live checks pass on the actual deployment:

1. `alembic upgrade head` on a production-like PostgreSQL snapshot.
2. Full regression suite on the exact release commit.
3. Clean one-command install on a fresh target server.
4. Upgrade + rollback drill from the previous production revision.
5. Backup + restore drill with post-restore data verification.
6. ≥10-user concurrent load acceptance on the intended 4 CPU / ~6 GB node.
7. Real HTTPS reverse proxy and certificate/renewal test.
8. SMTP delivery plus SPF/DKIM/DMARC verification and password-reset/verification flows.
9. Real Yandex OAuth acceptance scenarios from `docs/YANDEX_ID_LOGIN.md`.
10. Real YooMoney and YooKassa successful/canceled/duplicate/replayed webhook/payment tests.
11. Abuse tests for chat burst, API keys, auth, file upload and resource exhaustion.
12. Restart/kill/database/network/disk-pressure recovery tests with data-integrity verification.

Only after the exact release commit passes those gates should the owner dashboard/release gate be treated as production-ready evidence.
