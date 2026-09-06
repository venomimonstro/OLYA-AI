from __future__ import annotations

import os
import re
import secrets
import subprocess
import threading
import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Header, HTTPException
from pydantic import BaseModel, Field

TOKEN = os.environ.get("X1_SANDBOX_WORKER_TOKEN", "")
HOST_DATA_ROOT = Path(os.environ.get("X1_HOST_DATA_ROOT", "/srv/x1-data")).resolve()
MIRROR_DATA_ROOT = Path(os.environ.get("X1_SANDBOX_MIRROR_DATA_ROOT", "/app/data")).resolve()
RUNTIME_IMAGE = os.environ.get("X1_SANDBOX_RUNTIME_IMAGE", "x1-sandbox:0.39")
MAX_CONCURRENT_EXECUTIONS = max(1, int(os.environ.get("X1_SANDBOX_MAX_CONCURRENT_EXECUTIONS", "1")))
MAX_ACTIVE_PREVIEWS = max(1, int(os.environ.get("X1_SANDBOX_MAX_ACTIVE_PREVIEWS", "1")))
MAX_MEMORY_MB = max(128, int(os.environ.get("X1_SANDBOX_MAX_MEMORY_MB", "2048")))
MAX_CPU = max(0.1, float(os.environ.get("X1_SANDBOX_MAX_CPU", "1.0")))
MAX_PIDS = max(16, int(os.environ.get("X1_SANDBOX_MAX_PIDS", "128")))
PREVIEW_TTL_SECONDS = max(60, int(os.environ.get("X1_SANDBOX_PREVIEW_TTL_SECONDS", "900")))
_CONTAINER_RE = re.compile(r"^x1-preview-[a-f0-9]{8,40}$")
_EXECUTION_GATE = threading.BoundedSemaphore(MAX_CONCURRENT_EXECUTIONS)
_PREVIEW_LOCK = threading.Lock()
_PREVIEW_LABEL = "x1.sandbox.preview=true"
_EXPIRY_LABEL = "x1.sandbox.expires_at"
_EXECUTION_LABEL = "x1.sandbox.execution=true"


class ExecRequest(BaseModel):
    image: str
    workspace_rel: str
    scratch_rel: str
    argv: list[str] = Field(min_length=1, max_length=64)
    timeout_seconds: int = Field(default=300, ge=1, le=1800)
    cpu_limit: float = Field(default=1.0, ge=0.1, le=8.0)
    memory_mb: int = Field(default=1024, ge=128, le=16384)
    process_limit: int = Field(default=64, ge=16, le=512)
    network_policy: str = "deny"
    env: dict[str, str] = Field(default_factory=dict)


class PreviewStartRequest(ExecRequest):
    name: str


class PreviewExecRequest(BaseModel):
    container_ref: str
    argv: list[str] = Field(min_length=1, max_length=64)
    timeout_seconds: int = Field(default=120, ge=1, le=600)


class PreviewStopRequest(BaseModel):
    container_ref: str


def _auth(value: str) -> None:
    if not TOKEN or value != TOKEN:
        raise HTTPException(status_code=403, detail="Sandbox worker authentication failed")


def _run(argv: list[str], *, timeout: int = 10) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        argv,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        timeout=timeout,
        shell=False,
    )


def _validate_argv(argv: list[str]) -> None:
    if not argv or len(argv) > 64 or any("\x00" in item or len(item) > 4000 for item in argv):
        raise HTTPException(status_code=422, detail="Invalid sandbox argv")


def _safe_relative(value: str) -> Path:
    path = Path(value)
    if path.is_absolute() or not value or any(part in {"", ".", ".."} for part in path.parts):
        raise HTTPException(status_code=422, detail="Invalid sandbox data path")
    return path


def _paths(workspace_rel: str, scratch_rel: str) -> tuple[Path, Path]:
    workspace_rel_path = _safe_relative(workspace_rel)
    scratch_rel_path = _safe_relative(scratch_rel)
    if workspace_rel_path.parts[0] != "code_workspaces":
        raise HTTPException(status_code=422, detail="Workspace must be inside code_workspaces")
    if scratch_rel_path.parts[0] != "project_runtimes":
        raise HTTPException(status_code=422, detail="Scratch must be inside project_runtimes")

    mirror_workspace = (MIRROR_DATA_ROOT / workspace_rel_path).resolve()
    mirror_scratch = (MIRROR_DATA_ROOT / scratch_rel_path).resolve()
    try:
        mirror_workspace.relative_to(MIRROR_DATA_ROOT)
        mirror_scratch.relative_to(MIRROR_DATA_ROOT)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail="Sandbox path escaped data root") from exc
    mirror_workspace.mkdir(parents=True, exist_ok=True)
    mirror_scratch.mkdir(parents=True, exist_ok=True)

    host_workspace = (HOST_DATA_ROOT / workspace_rel_path).resolve()
    host_scratch = (HOST_DATA_ROOT / scratch_rel_path).resolve()
    try:
        host_workspace.relative_to(HOST_DATA_ROOT)
        host_scratch.relative_to(HOST_DATA_ROOT)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail="Host sandbox path escaped data root") from exc
    return host_workspace, host_scratch


def _docker_ready() -> bool:
    try:
        return _run(["docker", "version"], timeout=5).returncode == 0
    except Exception:
        return False


def _image_ready() -> bool:
    if not _docker_ready():
        return False
    try:
        return _run(["docker", "image", "inspect", RUNTIME_IMAGE], timeout=8).returncode == 0
    except Exception:
        return False


def _validate_limits(payload: ExecRequest) -> None:
    violations: list[str] = []
    if float(payload.cpu_limit) > MAX_CPU:
        violations.append(f"cpu_limit>{MAX_CPU}")
    if int(payload.memory_mb) > MAX_MEMORY_MB:
        violations.append(f"memory_mb>{MAX_MEMORY_MB}")
    if int(payload.process_limit) > MAX_PIDS:
        violations.append(f"process_limit>{MAX_PIDS}")
    if violations:
        raise HTTPException(
            status_code=422,
            detail={"code": "sandbox_host_limit_exceeded", "violations": violations},
        )


def _base_command(payload: ExecRequest, *, read_only_workspace: bool = False) -> list[str]:
    if payload.image != RUNTIME_IMAGE:
        raise HTTPException(status_code=422, detail="Only the configured sandbox image is allowed")
    if payload.network_policy not in {"deny", "restricted"}:
        raise HTTPException(status_code=422, detail="Unsupported sandbox network policy")
    _validate_argv(payload.argv)
    if len(payload.env) > 32:
        raise HTTPException(status_code=422, detail="Too many sandbox environment variables")
    for key, value in payload.env.items():
        if not key.replace("_", "").isalnum() or key.upper() != key or len(value) > 4000 or "\x00" in value:
            raise HTTPException(status_code=422, detail="Invalid sandbox environment")
    _validate_limits(payload)

    workspace, scratch = _paths(payload.workspace_rel, payload.scratch_rel)
    mount_mode = "ro" if read_only_workspace else "rw"
    command = [
        "docker", "run", "--rm", "--pull=never",
        "--workdir", "/workspace",
        "--read-only",
        "--cap-drop=ALL",
        "--security-opt", "no-new-privileges",
        "--pids-limit", str(payload.process_limit),
        "--memory", f"{payload.memory_mb}m",
        "--cpus", str(payload.cpu_limit),
        "--network", "none",
        "--user", "10001:10001",
        "--mount", f"type=bind,src={workspace},dst=/workspace,{mount_mode}",
        "--mount", f"type=bind,src={scratch},dst=/x1-runtime,rw",
        "--tmpfs", "/tmp:rw,noexec,nosuid,nodev,size=256m",
    ]
    for key, value in sorted(payload.env.items()):
        command += ["--env", f"{key}={value}"]
    command.append(RUNTIME_IMAGE)
    return command


def _preview_ids() -> list[str]:
    if not _docker_ready():
        return []
    try:
        result = _run(["docker", "ps", "-q", "--filter", "label=x1.sandbox.preview=true"], timeout=8)
        if result.returncode != 0:
            return []
        return [item.strip() for item in result.stdout.splitlines() if item.strip()]
    except Exception:
        return []


def _preview_metadata(container_ref: str) -> tuple[bool, int]:
    """Return X1 ownership and expiry for a running/known preview container."""
    try:
        result = _run(
            [
                "docker", "inspect", "-f",
                '{{ index .Config.Labels "x1.sandbox.preview" }}|{{ index .Config.Labels "x1.sandbox.expires_at" }}|{{ .Config.Image }}',
                container_ref,
            ],
            timeout=5,
        )
    except Exception:
        return False, 0
    if result.returncode != 0:
        return False, 0
    parts = result.stdout.strip().split("|", 2)
    if len(parts) != 3:
        return False, 0
    label, raw_expiry, image = parts
    try:
        expiry = int(raw_expiry or 0)
    except ValueError:
        expiry = 0
    return label == "true" and image == RUNTIME_IMAGE, expiry


def _require_owned_preview(container_ref: str, *, stop_if_expired: bool = True) -> int:
    if not _CONTAINER_RE.fullmatch(container_ref):
        raise HTTPException(status_code=422, detail="Invalid preview container reference")
    owned, expiry = _preview_metadata(container_ref)
    if not owned:
        raise HTTPException(status_code=404, detail="X1 preview container not found")
    if expiry and expiry <= int(time.time()):
        if stop_if_expired:
            try:
                _run(["docker", "rm", "-f", container_ref], timeout=8)
            except Exception:
                pass
        raise HTTPException(status_code=410, detail="X1 preview container expired")
    return expiry


def _container_expiry(container_id: str) -> int:
    try:
        result = _run([
            "docker", "inspect", "-f", '{{ index .Config.Labels "x1.sandbox.expires_at" }}', container_id,
        ], timeout=5)
        return int(result.stdout.strip() or 0) if result.returncode == 0 else 0
    except Exception:
        return 0


def reap_expired_previews() -> int:
    now = int(time.time())
    reaped = 0
    for container_id in _preview_ids():
        expiry = _container_expiry(container_id)
        if expiry and expiry <= now:
            try:
                _run(["docker", "rm", "-f", container_id], timeout=8)
                reaped += 1
            except Exception:
                pass
    return reaped


def _reaper_loop(stop: threading.Event) -> None:
    while not stop.wait(30.0):
        reap_expired_previews()


@asynccontextmanager
async def lifespan(_app: FastAPI):
    stop = threading.Event()
    thread = threading.Thread(target=_reaper_loop, args=(stop,), daemon=True, name="x1-preview-reaper")
    thread.start()
    try:
        yield
    finally:
        stop.set()
        thread.join(timeout=2)


app = FastAPI(title="X1 Sandbox Worker", docs_url=None, redoc_url=None, openapi_url=None, lifespan=lifespan)


@app.get("/health")
def health() -> dict[str, Any]:
    docker = _docker_ready()
    image = _image_ready() if docker else False
    previews = len(_preview_ids()) if docker else 0
    return {
        "status": "stable" if docker and image else "degraded",
        "docker": docker,
        "image": image,
        "runtime_image": RUNTIME_IMAGE,
        "active_previews": previews,
        "max_active_previews": MAX_ACTIVE_PREVIEWS,
        "max_concurrent_executions": MAX_CONCURRENT_EXECUTIONS,
    }


@app.get("/capabilities")
def capabilities(x_x1_sandbox_token: str = Header(default="", alias="X-X1-Sandbox-Token")) -> dict[str, Any]:
    _auth(x_x1_sandbox_token)
    docker = _docker_ready()
    image = _image_ready() if docker else False
    return {
        "backend": "remote-docker",
        "available": bool(docker and image),
        "runtime_available": docker,
        "image": RUNTIME_IMAGE,
        "image_present": image,
        "network_isolation": bool(docker and image),
        "restricted_network_mode": "deny_until_egress_allowlist_is_configured",
        "filesystem_isolation": bool(docker and image),
        "resource_limits": {
            "max_concurrent_executions": MAX_CONCURRENT_EXECUTIONS,
            "max_active_previews": MAX_ACTIVE_PREVIEWS,
            "max_memory_mb": MAX_MEMORY_MB,
            "max_cpu": MAX_CPU,
            "max_pids": MAX_PIDS,
            "preview_ttl_seconds": PREVIEW_TTL_SECONDS,
        },
        "reason": "" if docker and image else "Docker runtime or sandbox image unavailable",
    }


@app.post("/execute")
def execute(payload: ExecRequest, x_x1_sandbox_token: str = Header(default="", alias="X-X1-Sandbox-Token")) -> dict[str, Any]:
    _auth(x_x1_sandbox_token)
    command = _base_command(payload)
    execution_name = "x1-exec-" + secrets.token_hex(10)
    command[3:3] = ["--name", execution_name, "--label", _EXECUTION_LABEL]
    command += payload.argv
    if not _EXECUTION_GATE.acquire(timeout=2.0):
        raise HTTPException(status_code=429, detail="Sandbox execution capacity is busy")
    try:
        try:
            completed = subprocess.run(
                command,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                timeout=payload.timeout_seconds,
                shell=False,
            )
            return {
                "argv": payload.argv,
                "exit_code": completed.returncode,
                "stdout": completed.stdout[-30_000:],
                "stderr": completed.stderr[-30_000:],
                "timed_out": False,
                "sandbox_level": "remote-docker",
                "network_policy": payload.network_policy,
                "effective_network_policy": "deny",
            }
        except subprocess.TimeoutExpired as exc:
            # Killing only the Docker CLI is insufficient: the container may
            # continue running in the daemon. Force-remove the named execution.
            try:
                _run(["docker", "rm", "-f", execution_name], timeout=8)
            except Exception:
                pass
            return {
                "argv": payload.argv,
                "exit_code": None,
                "stdout": (exc.stdout or "")[-30_000:] if isinstance(exc.stdout, str) else "",
                "stderr": (exc.stderr or "")[-30_000:] if isinstance(exc.stderr, str) else "",
                "timed_out": True,
                "sandbox_level": "remote-docker",
                "network_policy": payload.network_policy,
                "effective_network_policy": "deny",
            }
        except (OSError, ValueError) as exc:
            try:
                _run(["docker", "rm", "-f", execution_name], timeout=8)
            except Exception:
                pass
            raise HTTPException(status_code=503, detail="Sandbox execution failed to start") from exc
    finally:
        _EXECUTION_GATE.release()


@app.post("/preview/start")
def preview_start(payload: PreviewStartRequest, x_x1_sandbox_token: str = Header(default="", alias="X-X1-Sandbox-Token")) -> dict[str, Any]:
    _auth(x_x1_sandbox_token)
    if not _CONTAINER_RE.fullmatch(payload.name):
        raise HTTPException(status_code=422, detail="Invalid preview container name")
    command = _base_command(payload)
    with _PREVIEW_LOCK:
        reap_expired_previews()
        if len(_preview_ids()) >= MAX_ACTIVE_PREVIEWS:
            raise HTTPException(status_code=429, detail="Sandbox preview capacity is busy")
        expiry = int(time.time()) + PREVIEW_TTL_SECONDS
        command[2:2] = [
            "-d", "--name", payload.name,
            "--label", _PREVIEW_LABEL,
            "--label", f"{_EXPIRY_LABEL}={expiry}",
        ]
        command += payload.argv
        try:
            completed = subprocess.run(
                command,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                timeout=20,
                shell=False,
            )
        except (OSError, subprocess.TimeoutExpired, ValueError) as exc:
            try:
                _run(["docker", "rm", "-f", payload.name], timeout=8)
            except Exception:
                pass
            raise HTTPException(status_code=503, detail="Failed to start preview container") from exc
        if completed.returncode != 0:
            raise HTTPException(status_code=503, detail=(completed.stderr or "Failed to start preview")[-4000:])
    return {
        "container_ref": payload.name,
        "backend": "remote-docker",
        "effective_network_policy": "deny",
        "expires_at_epoch": expiry,
    }


@app.post("/preview/exec")
def preview_exec(payload: PreviewExecRequest, x_x1_sandbox_token: str = Header(default="", alias="X-X1-Sandbox-Token")) -> dict[str, Any]:
    _auth(x_x1_sandbox_token)
    _validate_argv(payload.argv)
    expiry = _require_owned_preview(payload.container_ref)
    try:
        completed = subprocess.run(
            ["docker", "exec", payload.container_ref, *payload.argv],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=payload.timeout_seconds,
            shell=False,
        )
    except subprocess.TimeoutExpired as exc:
        return {
            "argv": payload.argv,
            "exit_code": None,
            "stdout": (exc.stdout or "")[-10_000:] if isinstance(exc.stdout, str) else "",
            "stderr": (exc.stderr or "")[-10_000:] if isinstance(exc.stderr, str) else "",
            "timed_out": True,
            "expires_at_epoch": expiry,
        }
    except (OSError, ValueError) as exc:
        raise HTTPException(status_code=503, detail="Preview command execution failed") from exc
    return {
        "argv": payload.argv,
        "exit_code": completed.returncode,
        "stdout": completed.stdout[-10_000:],
        "stderr": completed.stderr[-10_000:],
        "timed_out": False,
        "expires_at_epoch": expiry,
    }


@app.post("/preview/stop")
def preview_stop(payload: PreviewStopRequest, x_x1_sandbox_token: str = Header(default="", alias="X-X1-Sandbox-Token")) -> dict[str, Any]:
    _auth(x_x1_sandbox_token)
    _require_owned_preview(payload.container_ref, stop_if_expired=False)
    try:
        result = _run(["docker", "rm", "-f", payload.container_ref], timeout=8)
    except Exception as exc:
        raise HTTPException(status_code=503, detail="Failed to stop preview container") from exc
    if result.returncode != 0:
        raise HTTPException(status_code=503, detail="Failed to stop preview container")
    return {"status": "stopped"}
