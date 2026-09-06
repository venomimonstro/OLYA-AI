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
        completed = subprocess.run(argv, cwd=ROOT, env=env, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=timeout, shell=False)
        return {"name": name, "status": "passed" if completed.returncode == 0 else "failed", "required": required, "exit_code": completed.returncode, "duration_seconds": round((datetime.now(timezone.utc) - started).total_seconds(), 3), "stdout": completed.stdout[-12000:], "stderr": completed.stderr[-12000:]}
    except subprocess.TimeoutExpired as exc:
        return {"name": name, "status": "failed", "required": required, "exit_code": None, "duration_seconds": round((datetime.now(timezone.utc) - started).total_seconds(), 3), "stdout": (exc.stdout or "")[-12000:] if isinstance(exc.stdout, str) else "", "stderr": (exc.stderr or "")[-12000:] if isinstance(exc.stderr, str) else "", "error": "timeout"}
    except OSError as exc:
        return {"name": name, "status": "failed" if required else "not_run", "required": required, "exit_code": None, "duration_seconds": round((datetime.now(timezone.utc) - started).total_seconds(), 3), "stdout": "", "stderr": str(exc)[:2000], "error": type(exc).__name__}


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


def env_file_value(name: str, default: str = "") -> str:
    path = ROOT / ".env"
    if not path.exists():
        return os.environ.get(name, default)
    value = None
    for line in path.read_text("utf-8", errors="replace").splitlines():
        if line.startswith(name + "="):
            value = line.split("=", 1)[1].strip()
    return os.environ.get(name, value if value is not None else default)


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
        return subprocess.run(["docker", "compose", "version"], cwd=ROOT, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=10, shell=False).returncode == 0
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
            data = json.loads(response.read().decode("utf-8")); code = int(response.status)
        status = str(data.get("status") or ""); passed = code == 200 and status == "stable"
        return {"name": "final_ready", "status": "passed" if passed else "failed", "required": True, "http_status": code, "ready_status": status, "score": data.get("score"), "components": data.get("components") or {}, "duration_seconds": round((datetime.now(timezone.utc) - started).total_seconds(), 3)}
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
    parser.add_argument("--live-inference", action="store_true", help="run bounded live inference and target-node capacity calibration")
    parser.add_argument("--user-journey", action="store_true", help="simulate registration, chat, research and closed project development")
    parser.add_argument("--chaos", action="store_true", help="run security, overload and bad-answer anti-case simulations")
    parser.add_argument("--pytest-timeout", type=int, default=1200)
    parser.add_argument("--command-timeout", type=int, default=180)
    parser.add_argument("--e2e-timeout", type=int, default=3600)
    parser.add_argument("--report", default="backups/release-gate-latest.json")
    args = parser.parse_args()

    if (args.user_journey or args.chaos) and not args.runtime:
        parser.error("--user-journey/--chaos require --runtime")

    started_at = utcnow(); checks: list[dict[str, Any]] = []
    python_env = dict(os.environ); python_env["PYTHONPATH"] = str(ROOT)
    docker = docker_available(); containerized_gate = bool(docker and (ROOT / ".env").exists())

    if containerized_gate:
        checks.append(run("gate_image_build", ["docker", "compose", "--profile", "gate", "build", "gate"], timeout=max(args.command_timeout, 1200)))
        gate_prefix = ["docker", "compose", "--profile", "gate", "run", "--rm", "--no-deps", "gate"]
        checks.append(run("compileall", [*gate_prefix, "python", "-m", "compileall", "-q", "app", "scripts", "tests"], timeout=args.command_timeout))
        checks.append(run("stability_probe", [*gate_prefix, "python", "-m", "scripts.stability_probe"], timeout=args.command_timeout))
        checks.append(run("document_render_probe", [*gate_prefix, "python", "-m", "scripts.document_render_probe"], timeout=max(args.command_timeout, 180)))
        checks.append(parse_alembic_heads(run("alembic_heads", [*gate_prefix, "python", "-m", "alembic", "heads"], timeout=args.command_timeout)))
        checks.append(run("long_context_offline", [*gate_prefix, "python", "-m", "scripts.long_context_probe"], timeout=args.command_timeout))
        checks.append(run("pytest_full", [*gate_prefix, "python", "-m", "scripts.run_full_regression"], timeout=max(60, args.pytest_timeout)))
    else:
        checks.append(run("compileall", [sys.executable, "-m", "compileall", "-q", "app", "scripts", "tests"], timeout=args.command_timeout, env=python_env))
        checks.append(run("stability_probe", [sys.executable, "-m", "scripts.stability_probe"], timeout=args.command_timeout, env=python_env))
        if shutil.which("libreoffice") or shutil.which("soffice"):
            checks.append(run("document_render_probe", [sys.executable, "-m", "scripts.document_render_probe"], timeout=max(args.command_timeout, 180), env=python_env))
        else:
            checks.append({"name": "document_render_probe", "status": "not_run", "required": False, "detail": "Host LibreOffice unavailable; production Docker gate executes this probe"})
        checks.append(parse_alembic_heads(run("alembic_heads", [sys.executable, "-m", "alembic", "heads"], timeout=args.command_timeout, env=python_env)))
        checks.append(run("long_context_offline", [sys.executable, "-m", "scripts.long_context_probe"], timeout=args.command_timeout, env=python_env))
        checks.append(run("pytest_full", [sys.executable, "-m", "scripts.run_full_regression"], timeout=max(60, args.pytest_timeout), env=python_env))

    if docker:
        checks.append(run("compose_config", ["docker", "compose", "config", "--quiet"], timeout=args.command_timeout))
    else:
        checks.append({"name": "compose_config", "status": "failed" if args.runtime else "not_run", "required": bool(args.runtime), "detail": "Docker Compose is unavailable"})

    if args.runtime:
        checks.append(run("sandbox_probe", ["docker", "compose", "exec", "-T", "app", "python", "-m", "scripts.sandbox_probe"], timeout=max(args.command_timeout, 180)))
        # Component acceptance is intentionally independent from the expensive
        # Qwen user journey. It proves auth/session lifecycle, projects, memory,
        # files, real LibreOffice document QA/release, API keys/contexts, usage,
        # export and sandbox boundaries before long-running AI tests begin.
        checks.append(run("component_acceptance", ["docker", "compose", "exec", "-T", "app", "python", "-m", "scripts.component_acceptance"], timeout=max(args.command_timeout, 900)))
        backup = run("backup", ["bash", "scripts/backup.sh"], timeout=max(args.command_timeout, 600)); checks.append(backup)
        backup_path = latest_backup_from_output(backup.get("stdout", "")) if backup["status"] == "passed" else ""
        if backup_path:
            checks.append(run("restore_drill", ["bash", "scripts/restore_drill.sh", backup_path], timeout=max(args.command_timeout, 900)))
        else:
            checks.append({"name": "restore_drill", "status": "failed", "required": True, "detail": "No verified backup produced"})
        checks.append(run("http_load_smoke", ["docker", "compose", "exec", "-T", "app", "python", "scripts/load_smoke.py", "--url", "http://127.0.0.1:8000", "--requests", "120", "--concurrency", "16"], timeout=max(args.command_timeout, 240)))
        if args.live_inference:
            context_tokens = env_file_value("X1_DEEP_CONTEXT_TOKENS", "8192")
            checks.append(run("long_context_live", ["docker", "compose", "exec", "-T", "app", "python", "-m", "scripts.long_context_probe", "--context-tokens", context_tokens, "--live-url", "http://llama:8080"], timeout=max(args.command_timeout, 900)))
            checks.append(run("capacity_calibration", [sys.executable, "scripts/capacity_calibrate.py", "--samples", "1"], timeout=max(args.command_timeout, 1800), env=python_env))
        else:
            checks.append({"name": "long_context_live", "status": "not_run", "required": False, "detail": "Pass --live-inference on the target node before public launch"})
            checks.append({"name": "capacity_calibration", "status": "not_run", "required": False, "detail": "Capacity calibration requires --live-inference"})
        if args.user_journey:
            checks.append(run("e2e_user_journey", ["docker", "compose", "exec", "-T", "app", "python", "-m", "scripts.e2e_user_journey", "--development-steps", "24"], timeout=max(args.e2e_timeout, 1200)))
        else:
            checks.append({"name": "e2e_user_journey", "status": "not_run", "required": False, "detail": "Pass --user-journey to verify the complete product journey"})
        if args.chaos:
            checks.append(run("chaos_simulation", ["docker", "compose", "exec", "-T", "app", "python", "-m", "scripts.chaos_simulation", "--virtual-users", "100000"], timeout=max(args.e2e_timeout, 1200)))
        else:
            checks.append({"name": "chaos_simulation", "status": "not_run", "required": False, "detail": "Pass --chaos to run security and overload anti-cases"})

    failed_required = [item["name"] for item in checks if item.get("required", True) and item.get("status") != "passed"]
    status = "passed" if not failed_required else "failed"
    payload = {
        "format": "x1-release-gate-v3",
        "status": status,
        "version": project_version(),
        "git_head": git_head(),
        "mode": "runtime" if args.runtime else "static",
        "component_acceptance_requested": bool(args.runtime),
        "live_inference_requested": bool(args.live_inference),
        "user_journey_requested": bool(args.user_journey),
        "chaos_requested": bool(args.chaos),
        "capacity_calibration_requested": bool(args.runtime and args.live_inference),
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
        ready = final_ready_probe(); checks.append(ready)
        if ready["status"] != "passed":
            failed_required.append("final_ready"); payload["status"] = "failed"
        payload["failed_required_checks"] = failed_required; payload["checks"] = checks; payload["finished_at"] = utcnow(); write_report(report_path, payload)
    print(json.dumps(payload, ensure_ascii=False, indent=2, default=str))
    return 0 if payload["status"] == "passed" else 2


if __name__ == "__main__":
    raise SystemExit(main())
