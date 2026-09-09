#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ENV_PATH = ROOT / ".env"
GPU_OVERRIDE = ROOT / "docker-compose.image-gpu.yml"


def _env() -> dict[str, str]:
    values: dict[str, str] = {}
    if ENV_PATH.is_file():
        for raw in ENV_PATH.read_text("utf-8", errors="replace").splitlines():
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            values[key.strip()] = value.strip()
    return values


def _run(args: list[str], *, timeout: int = 300, check: bool = False) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(args, cwd=ROOT, text=True, capture_output=True, timeout=timeout, stdin=subprocess.DEVNULL, shell=False)
    if check and result.returncode != 0:
        raise RuntimeError((result.stderr or result.stdout or "command failed")[-2000:])
    return result


def _compose(values: dict[str, str]) -> list[str]:
    backend = values.get("X1_IMAGE_EDIT_BACKEND", "disabled").strip().lower()
    args = ["docker", "compose"]
    if backend == "qwen-image-edit":
        if not GPU_OVERRIDE.is_file():
            raise RuntimeError("docker-compose.image-gpu.yml is missing")
        args += ["-f", "docker-compose.yml", "-f", str(GPU_OVERRIDE)]
    return args


def _configured(values: dict[str, str]) -> bool:
    return values.get("X1_IMAGE_BACKEND", "disabled").lower() != "disabled" or values.get("X1_IMAGE_EDIT_BACKEND", "disabled").lower() != "disabled"


def _preflight_host(values: dict[str, str]) -> dict:
    if not ENV_PATH.is_file():
        raise RuntimeError(".env is missing")
    if not _configured(values):
        raise RuntimeError("Image generation/editing is disabled in .env")
    edit_backend = values.get("X1_IMAGE_EDIT_BACKEND", "disabled").strip().lower()
    if edit_backend not in {"disabled", "diffusers", "qwen-image-edit"}:
        raise RuntimeError(f"Unsupported X1_IMAGE_EDIT_BACKEND={edit_backend}")
    if edit_backend != "disabled" and values.get("X1_IMAGE_EDIT_REQUIRE_VISION_QA", "true").lower() not in {"0", "false", "no"}:
        qa_url = values.get("X1_IMAGE_VISION_QA_URL", "").strip()
        if not qa_url:
            raise RuntimeError("X1_IMAGE_VISION_QA_URL is required for production image editing")
    gpu = edit_backend == "qwen-image-edit"
    if gpu:
        nvidia = _run(["docker", "info", "--format", "{{json .Runtimes}}"], timeout=20)
        if nvidia.returncode != 0 or "nvidia" not in nvidia.stdout.lower():
            raise RuntimeError("Qwen Image Edit requires the NVIDIA Container Runtime")
        if not values.get("X1_IMAGE_EDIT_MODEL_PATH", "").strip():
            raise RuntimeError("X1_IMAGE_EDIT_MODEL_PATH must point to the local Qwen Image Edit checkpoint")
    return {"configured": True, "edit_backend": edit_backend, "gpu_runtime_required": gpu}


def _container_running(compose: list[str]) -> bool:
    ps = _run([*compose, "--profile", "images", "ps", "-q", "image-worker"], timeout=20)
    if ps.returncode != 0 or not ps.stdout.strip():
        return False
    inspect = _run(["docker", "inspect", "--format", "{{.State.Running}}", ps.stdout.splitlines()[0].strip()], timeout=10)
    return bool(inspect.returncode == 0 and inspect.stdout.strip().lower() == "true")


def _heartbeat(compose: list[str]) -> dict:
    probe = _run([
        *compose, "--profile", "images", "exec", "-T", "image-worker", "python", "-c",
        "from app.db import SessionLocal; from app.services.image_worker_state import image_worker_snapshot; db=SessionLocal(); s=image_worker_snapshot(db,stale_seconds=90); db.close(); import json; print(json.dumps(s))",
    ], timeout=25)
    if probe.returncode != 0:
        return {"alive": False, "error": (probe.stderr or probe.stdout)[-1000:]}
    try:
        return json.loads(probe.stdout.strip().splitlines()[-1])
    except (ValueError, IndexError):
        return {"alive": False, "error": "invalid heartbeat response"}


def up(values: dict[str, str]) -> int:
    info = _preflight_host(values)
    compose = _compose(values)
    qa_url = values.get("X1_IMAGE_VISION_QA_URL", "")
    if "llama" in qa_url:
        _run(["docker", "compose", "--profile", "inference", "up", "-d", "llama"], timeout=300, check=True)
    _run([*compose, "--profile", "images", "build", "image-worker"], timeout=1800, check=True)
    _run([*compose, "--profile", "images", "up", "-d", "image-worker"], timeout=300, check=True)
    for _ in range(60):
        if _container_running(compose):
            snap = _heartbeat(compose)
            if snap.get("alive"):
                print(json.dumps({"status": "ready", **info, "heartbeat": snap}, ensure_ascii=False, indent=2))
                return 0
        time.sleep(2)
    logs = _run([*compose, "--profile", "images", "logs", "--tail=120", "image-worker"], timeout=30)
    print((logs.stdout + logs.stderr)[-6000:], file=sys.stderr)
    raise RuntimeError("image-worker did not become healthy")


def status(values: dict[str, str]) -> int:
    compose = _compose(values)
    running = _container_running(compose)
    snap = _heartbeat(compose) if running else {"alive": False, "status": "container_not_running"}
    payload = {"configured": _configured(values), "container_running": running, "heartbeat": snap}
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0 if (not payload["configured"] or (running and snap.get("alive"))) else 2


def down(values: dict[str, str]) -> int:
    compose = _compose(values)
    result = _run([*compose, "--profile", "images", "stop", "image-worker"], timeout=120)
    if result.returncode != 0:
        print(result.stderr or result.stdout, file=sys.stderr)
        return result.returncode
    print(json.dumps({"status": "stopped"}))
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Safely manage the optional OLYA AI image worker")
    parser.add_argument("command", choices=("up", "status", "down", "preflight"))
    args = parser.parse_args()
    values = _env()
    try:
        if args.command == "up":
            return up(values)
        if args.command == "status":
            return status(values)
        if args.command == "down":
            return down(values)
        print(json.dumps(_preflight_host(values), ensure_ascii=False, indent=2))
        return 0
    except (RuntimeError, subprocess.TimeoutExpired, OSError) as exc:
        print(json.dumps({"status": "failed", "error": str(exc)[:2000]}, ensure_ascii=False), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
