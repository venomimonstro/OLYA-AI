#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[1]


def _run(name: str, argv: list[str], env: dict[str, str]) -> dict:
    print(f"[X1 final] {name}", flush=True)
    result = subprocess.run(argv, cwd=ROOT, env=env, stdin=subprocess.DEVNULL, shell=False)
    return {"name": name, "status": "passed" if result.returncode == 0 else "failed", "exit_code": result.returncode}


def _required_environment(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise RuntimeError(f"Required environment variable is missing: {name}")
    return value


def main() -> int:
    parser = argparse.ArgumentParser(description="Run X1 target load, RC gate and final production acceptance in the required order")
    parser.add_argument("--base-url", default=os.environ.get("X1_PRODUCTION_BASE_URL", ""), help="deployed target URL; public targets must use HTTPS")
    parser.add_argument("--require-images", action="store_true", help="require image worker and image generation/editing at final acceptance")
    parser.add_argument("--load-rounds", type=int, default=2)
    parser.add_argument("--load-timeout", type=float, default=120.0)
    parser.add_argument("--load-p95-limit-ms", type=int, default=120_000)
    parser.add_argument("--load-max-error-rate", type=float, default=0.05)
    parser.add_argument("--rc-timeout", type=int, default=7200)
    args = parser.parse_args()

    try:
        _required_environment("X1_LOAD_TOKENS")
        _required_environment("X1_PRODUCTION_ADMIN_TOKEN")
    except RuntimeError as exc:
        print(json.dumps({"format": "x1-final-release-acceptance-v1", "status": "failed", "error": str(exc)}, ensure_ascii=False, indent=2))
        return 2

    base_url = args.base_url.strip()
    if not base_url:
        print(json.dumps({"format": "x1-final-release-acceptance-v1", "status": "failed", "error": "--base-url or X1_PRODUCTION_BASE_URL is required"}, ensure_ascii=False, indent=2))
        return 2
    parsed = urlsplit(base_url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username or parsed.password:
        print(json.dumps({"format": "x1-final-release-acceptance-v1", "status": "failed", "error": "Invalid production base URL"}, ensure_ascii=False, indent=2))
        return 2

    env = dict(os.environ)
    env["X1_LOAD_BASE_URL"] = base_url
    env["X1_PRODUCTION_BASE_URL"] = base_url
    steps: list[dict] = []

    load = _run(
        "target_load_acceptance",
        [
            sys.executable,
            "scripts/load_acceptance.py",
            "--base-url",
            base_url,
            "--rounds",
            str(max(1, min(args.load_rounds, 20))),
            "--timeout",
            str(max(5.0, args.load_timeout)),
            "--p95-limit-ms",
            str(max(1000, args.load_p95_limit_ms)),
            "--max-error-rate",
            str(max(0.0, min(args.load_max_error_rate, 0.5))),
        ],
        env,
    )
    steps.append(load)
    if load["status"] != "passed":
        print(json.dumps({"format": "x1-final-release-acceptance-v1", "status": "failed", "failed_step": load["name"], "steps": steps}, ensure_ascii=False, indent=2))
        return 2

    rc = _run(
        "release_candidate",
        [sys.executable, "scripts/rc_release_candidate.py", "--timeout", str(max(1800, args.rc_timeout))],
        env,
    )
    steps.append(rc)
    if rc["status"] != "passed":
        print(json.dumps({"format": "x1-final-release-acceptance-v1", "status": "failed", "failed_step": rc["name"], "steps": steps}, ensure_ascii=False, indent=2))
        return 2

    production_argv = [sys.executable, "scripts/production_acceptance.py", "--base-url", base_url]
    if args.require_images:
        production_argv.append("--require-images")
    production = _run("production_acceptance", production_argv, env)
    steps.append(production)

    status = "passed" if production["status"] == "passed" else "failed"
    payload = {
        "format": "x1-final-release-acceptance-v1",
        "status": status,
        "accepted_for_launch": status == "passed",
        "target": base_url,
        "require_images": bool(args.require_images),
        "steps": steps,
        "reports": {
            "load": "backups/load-acceptance-latest.json",
            "release_candidate": "backups/rc-release-candidate-latest.json",
            "production": "backups/production-acceptance-latest.json",
        },
    }
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0 if payload["accepted_for_launch"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
