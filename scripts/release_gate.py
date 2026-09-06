#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[1]


def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


def run(name: str, argv: list[str], *, timeout: int, required: bool = True, env: dict[str, str] | None = None) -> dict[str, Any]:
    started = datetime.now(timezone.utc)
    try:
        completed = subprocess.run(
            argv,
            cwd=ROOT,
            env=env,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=timeout,
            shell=False,
        )
        return {
            "name": name,
            "status": "passed" if completed.returncode == 0 else "failed",
            "required": required,
            "exit_code": completed.returncode,
            "duration_seconds": round((datetime.now(timezone.utc) - started).total_seconds(), 3),
            "stdout": completed.stdout[-8000:],
            "stderr": completed.stderr[-8000:],
        }
    except subprocess.TimeoutExpired as exc:
        return {
            "name": name,
            "status": "failed",
            "required": required,
            "exit_code": None,
            "duration_seconds": round((datetime.now(timezone.utc) - started).total_seconds(), 3),
            "stdout": (exc.stdout or "")[-8000:] if isinstance(exc.stdout, str) else "",
            "stderr": (exc.stderr or "")[-8000:] if isinstance(exc.stderr, str) else "",
            "error": "timeout",
        }
    except OSError as exc:
        return {
            "name": name,
            "status": "failed" if required else "not_run",
            "required": required,
            "exit_code": None,
            "duration_seconds": round((datetime.now(timezone.utc) - started).total_seconds(), 3),
            "stdout": "",
            "stderr": str(exc)[:2000],
            "error": type(exc).__name__,
        }


def git_head() -> str:
    if not shutil.which("git") or not (ROOT / ".git").exists():
        return ""
    result = subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True, capture_output=True, shell=False)
    return result.stdout.strip() if result.returncode == 0 else ""


def project_version() -> str:
    text = (ROOT / "pyproject.toml").read_text("utf-8")
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("version") and "=" in stripped:
            return stripped.split("=", 1)[1].strip().strip('"\'')
    return "unknown"


def parse_alembic_heads(check: dict[str, Any]) -> dict[str, Any]:
    if check["status"] != "passed":
        return check
    heads = [line.split()[0].strip() for line in check.get("stdout", "").splitlines() if line.strip()]
    check["heads"] = heads
    if len(heads) != 1:
        check["status"] = "failed"
        check["stderr"] = (check.get("stderr") or "") + f"\nExpected exactly one Alembic head, got {heads!r}"
    return check


def docker_available() -> bool:
    if not shutil.which("docker"):
        return False
    try:
        return subprocess.run(
            ["docker", "compose", "version"], cwd=ROOT, stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=10, shell=False,
        ).returncode == 0
    except Exception:
        return False


def latest_backup_from_output(output: str) -> str:
    for line in reversed(output.splitlines()):
        candidate = line.strip()
        if candidate and Path(candidate).is_dir():
            return candidate
    return ""


def write_report(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str) + "\n", "utf-8")
    os.replace(tmp, path)


def final_ready_probe(url: str = "http://127.0.0.1:8000/ready", timeout: float = 15.0) -> dict[str, Any]:
    started = datetime.now(timezone.utc)
    try:
        request = Request(url, headers={"Accept": "application/json", "User-Agent": "X1-Release-Gate/1"})
        with urlopen(request, timeout=timeout) as response:
            data = json.loads(response.read().decode("utf-8"))
            code = int(response.status)
        status = str(data.get("status") or "")
        passed = code == 200 and status == "stable"
        return {
            "name": "final_ready",
            "status": "passed" if passed else "failed",
            "required": True,
            "http_status": code,
            "ready_status": status,
            "score": data.get("score"),
            "components": data.get("components") or {},
            "duration_seconds": round((datetime.now(timezone.utc) - started).total_seconds(), 3),
        }
    except HTTPError as exc:
        try:
            body = exc.read().decode("utf-8", errors="replace")[-4000:]
        except Exception:
            body = ""
        return {"name": "final_ready", "status": "failed", "required": True, "http_status": exc.code, "stderr": body}
    except (URLError, OSError, ValueError, json.JSONDecodeError) as exc:
        return {"name": "final_ready", "status": "failed", "required": True, "error": type(exc).__name__, "stderr": str(exc)[:2000]}


def main() -> int:
    parser = argparse.ArgumentParser(description="X1 production release gate")
    parser.add_argument("--runtime", action="store_true", help="also verify the running Docker deployment, backup and restore")
    parser.add_argument("--live-inference", action="store_true", help="run one bounded long-context request against llama.cpp")
    parser.add_argument("--pytest-timeout", type=int, default=1200)
    parser.add_argument("--command-timeout", type=int, default=180)
    parser.add_argument("--report", default="backups/release-gate-latest.json")
    args = parser.parse_args()

    started_at = utcnow()
    checks: list[dict[str, Any]] = []
    python_env = dict(os.environ)
    python_env["PYTHONPATH"] = str(ROOT)
    docker = docker_available()
    containerized_gate = bool(docker and (ROOT / ".env").exists())

    if containerized_gate:
        checks.append(run(
            "gate_image_build",
            ["docker", "compose", "--profile", "gate", "build", "gate"],
            timeout=max(args.command_timeout, 900),
        ))
        gate_prefix = ["docker", "compose", "--profile", "gate", "run", "--rm", "--no-deps", "gate"]
        checks.append(run("compileall", [*gate_prefix, "python", "-m", "compileall", "-q", "app", "scripts", "tests"], timeout=args.command_timeout))
        checks.append(parse_alembic_heads(run("alembic_heads", [*gate_prefix, "python", "-m", "alembic", "heads"], timeout=args.command_timeout)))
        checks.append(run("long_context_offline", [*gate_prefix, "python", "-m", "scripts.long_context_probe"], timeout=args.command_timeout))
        checks.append(run("pytest_full", [*gate_prefix, "python", "-m", "scripts.run_full_regression"], timeout=max(60, args.pytest_timeout)))
    else:
        checks.append(run("compileall", [sys.executable, "-m", "compileall", "-q", "app", "scripts", "tests"], timeout=args.command_timeout, env=python_env))
        checks.append(parse_alembic_heads(run("alembic_heads", [sys.executable, "-m", "alembic", "heads"], timeout=args.command_timeout, env=python_env)))
        checks.append(run("long_context_offline", [sys.executable, "-m", "scripts.long_context_probe"], timeout=args.command_timeout, env=python_env))
        checks.append(run("pytest_full", [sys.executable, "-m", "scripts.run_full_regression"], timeout=max(60, args.pytest_timeout), env=python_env))

    if docker:
        checks.append(run("compose_config", ["docker", "compose", "config", "--quiet"], timeout=args.command_timeout))
    else:
        checks.append({"name": "compose_config", "status": "failed" if args.runtime else "not_run", "required": bool(args.runtime), "detail": "Docker Compose is unavailable"})

    if args.runtime:
        backup = run("backup", ["bash", "scripts/backup.sh"], timeout=max(args.command_timeout, 600))
        checks.append(backup)
        backup_path = latest_backup_from_output(backup.get("stdout", "")) if backup["status"] == "passed" else ""
        if backup_path:
            checks.append(run("restore_drill", ["bash", "scripts/restore_drill.sh", backup_path], timeout=max(args.command_timeout, 900)))
        else:
            checks.append({"name": "restore_drill", "status": "failed", "required": True, "detail": "No verified backup produced"})

        checks.append(run(
            "http_load_smoke",
            ["docker", "compose", "exec", "-T", "app", "python", "scripts/load_smoke.py", "--url", "http://127.0.0.1:8000", "--requests", "60", "--concurrency", "8"],
            timeout=max(args.command_timeout, 180),
        ))
        if args.live_inference:
            checks.append(run(
                "long_context_live",
                ["docker", "compose", "exec", "-T", "app", "python", "-m", "scripts.long_context_probe", "--context-tokens", os.environ.get("X1_DEEP_CONTEXT_TOKENS", "16384"), "--live-url", "http://llama:8080"],
                timeout=max(args.command_timeout, 600),
            ))
        else:
            checks.append({"name": "long_context_live", "status": "not_run", "required": False, "detail": "Pass --live-inference on the target node before public launch"})

    failed_required = [item["name"] for item in checks if item.get("required", True) and item.get("status") != "passed"]
    status = "passed" if not failed_required else "failed"
    payload = {
        "format": "x1-release-gate-v1",
        "status": status,
        "version": project_version(),
        "git_head": git_head(),
        "mode": "runtime" if args.runtime else "static",
        "live_inference_requested": bool(args.live_inference),
        "containerized_gate": containerized_gate,
        "historical_regression_modules": 45,
        "started_at": started_at,
        "finished_at": utcnow(),
        "failed_required_checks": failed_required,
        "checks": checks,
    }
    report_path = Path(args.report)
    if not report_path.is_absolute():
        report_path = ROOT / report_path

    write_report(report_path, payload)
    if args.runtime and status == "passed":
        ready = final_ready_probe()
        checks.append(ready)
        if ready["status"] != "passed":
            failed_required.append("final_ready")
            payload["status"] = "failed"
        payload["failed_required_checks"] = failed_required
        payload["checks"] = checks
        payload["finished_at"] = utcnow()
        write_report(report_path, payload)

    print(json.dumps(payload, ensure_ascii=False, indent=2, default=str))
    return 0 if payload["status"] == "passed" else 2


if __name__ == "__main__":
    raise SystemExit(main())
