# Sprint 86 — MVP Production Acceptance — HARNESS DONE / PRODUCTION RUN REQUIRED

Sprint 86 provides the final fail-closed launch decision for the exact source revision deployed on the target server.

## Command
`python3 scripts/production_acceptance.py`

Required secrets are supplied only through environment variables. The default admin-token variable is `X1_PRODUCTION_ADMIN_TOKEN`; the token is never written to the report.

## Required evidence
The harness refuses launch unless the current git HEAD has matching, green:
- `backups/rc-release-candidate-latest.json` (`x1-release-candidate-v3`);
- `backups/load-acceptance-latest.json` (`x1-real-load-acceptance-v1`, at least 10-user gate produced by Sprint80);
- `backups/release-gate-latest.json` (`x1-release-gate-v4`);
- restore-drill and runtime-chaos evidence.

## Live target probes
Production acceptance verifies running Docker services, PostgreSQL readiness, the local Qwen/llama.cpp health endpoint, SearXNG, sandbox worker and document worker. It also requires `/ready` to be stable, administrator release-readiness to be green, the Sprint84 business contract to pass, and required live capabilities to be available. Image generation/editing can be made mandatory with `--require-images`.

The output is `backups/production-acceptance-latest.json`. `accepted_for_launch=true` is emitted only when every required check passes.

## Current status
This repository change installs the acceptance harness and its regression contract. It does **not** claim that production acceptance has been run or passed on the target server.
