# Sprint 85 — MVP Freeze / Release Candidate — GATE DONE, RC NOT ISSUED

The MVP feature boundary is frozen after Sprint 84. Post-freeze changes are limited to release, security, recovery, data-integrity, deployment, acceptance, documentation and regression work.

## Release-candidate gate
The existing `scripts/rc_release_candidate.py` remains the single RC issuer. It now requires:
- static RC security audit;
- Sprint 84 business-logic contract audit;
- full runtime release gate with live inference, user journey and chaos modes;
- runtime chaos evidence;
- model-regression evidence tied to current model/source inputs;
- backup/restore-drill evidence;
- Sprint 80 real load acceptance with at least 10 distinct users;
- exact git-HEAD match for release-gate and target-load evidence.

`docs/MVP_FREEZE.json` is the machine-readable freeze policy. `scripts/mvp_freeze_audit.py` is part of the full regression gate.

## Current status
The RC gate implementation is complete, but no release candidate is claimed or issued by this repository change. A real target-node run is still required. In particular, `backups/load-acceptance-latest.json` must be produced by `scripts/load_acceptance.py` on the candidate revision and must pass all gates.
