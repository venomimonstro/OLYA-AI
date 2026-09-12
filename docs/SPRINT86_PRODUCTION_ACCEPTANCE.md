# Sprint 86 — MVP Production Acceptance — HARNESS DONE / PRODUCTION RUN REQUIRED

Sprint 86 provides the final fail-closed launch decision for the exact source revision and app image deployed on the target server.

## Command
`python3 scripts/production_acceptance.py`

Required secrets are supplied only through environment variables. The default admin-token variable is `X1_PRODUCTION_ADMIN_TOKEN`; the token is never written to the report. For a public target, `X1_PRODUCTION_BASE_URL` must use HTTPS; plain HTTP is accepted only for loopback acceptance (`127.0.0.1`, `localhost`, `::1`).

## Required evidence
The harness refuses launch unless the current clean git checkout has matching, green:
- `backups/rc-release-candidate-latest.json` (`x1-release-candidate-v4`);
- `backups/load-acceptance-latest.json` (`x1-real-load-acceptance-v2`, at least 10 distinct authenticated users);
- `backups/release-gate-latest.json` (`x1-release-gate-v4`);
- restore-drill and runtime-chaos evidence.

RC/load evidence must match both current `git_head` and deterministic `source_fingerprint` where applicable. The harness also recomputes the fingerprint inside the running `app` container and checks the public `GET /version` value, so a stale or modified runtime cannot be accepted.

## Live target probes
Production acceptance verifies running Docker services, PostgreSQL readiness, local Qwen/llama.cpp health, SearXNG, sandbox worker and document worker. It also requires `/ready` to be stable, administrator release-readiness to be green, the Sprint84 business contract to pass, and required live capabilities to be available. Image generation/editing can be made mandatory with `--require-images`.

Billing is a launch gate, not only a UI flag: the running app must have a valid production checkout URL template, a non-empty payment-ingest secret and at least one enabled paid plan. A configuration where paid plans are displayed but cannot actually be purchased is rejected.

The output format is `x1-production-acceptance-v2` in `backups/production-acceptance-latest.json`. `accepted_for_launch=true` is emitted only when every required check passes.

## Current status
This repository change installs the acceptance harness and its regression contract. It does **not** claim that production acceptance has been run or passed on the target server.
