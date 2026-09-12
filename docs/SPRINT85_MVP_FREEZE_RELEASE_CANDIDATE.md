# Sprint 85 — MVP Freeze / Release Candidate — GATE DONE, RC NOT ISSUED

The MVP feature boundary is frozen after Sprint 84. Post-freeze changes are limited to release, security, recovery, data-integrity, deployment, acceptance, documentation and regression work.

## Release-candidate gate
The existing `scripts/rc_release_candidate.py` remains the single RC issuer. Current report format: `x1-release-candidate-v4`.

It requires:
- clean git working tree; uncommitted/untracked release inputs are rejected;
- host RAM at or above `host_policy.minimum_detected_ram_gib` from `model-manifest.json`; larger 48/64+ GiB target servers are valid and use their normal model context tiers;
- static RC security audit and Sprint 84 business-logic contract audit;
- full runtime release gate with live inference, user journey, capacity calibration and chaos modes;
- runtime chaos evidence and backup/restore-drill evidence;
- model-regression evidence tied to current model/source inputs;
- Sprint 80 `x1-real-load-acceptance-v2` from at least 10 distinct authenticated users;
- exact git-HEAD and deterministic source-fingerprint match for the target-load evidence;
- deterministic recomputation of the running `app` container source fingerprint, which must equal the current checkout.

The app image embeds `/app/BUILD_PROVENANCE.json`; provenance covers application code, scripts, migrations, regression corpus, model manifest, `Dockerfile` and `docker-compose.yml`. A stale image cannot issue a valid RC merely because the host checkout is newer.

`docs/MVP_FREEZE.json` is the machine-readable freeze policy. `scripts/mvp_freeze_audit.py` is part of the full regression gate.

## Current status
The RC gate implementation is complete, but no release candidate is claimed or issued by this repository change. A real target-node run is still required. In particular, `backups/load-acceptance-latest.json` must be produced by `scripts/load_acceptance.py` on the exact deployed candidate and must pass all gates.
