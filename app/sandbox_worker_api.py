from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Header, HTTPException
from pydantic import BaseModel, Field

app = FastAPI(title="X1 Sandbox Worker", docs_url=None, redoc_url=None, openapi_url=None)

TOKEN = os.environ.get("X1_SANDBOX_WORKER_TOKEN", "")
HOST_DATA_ROOT = Path(os.environ.get("X1_HOST_DATA_ROOT", "/srv/x1-data")).resolve()
MIRROR_DATA_ROOT = Path(os.environ.get("X1_SANDBOX_MIRROR_DATA_ROOT", "/app/data")).resolve()
RUNTIME_IMAGE = os.environ.get("X1_SANDBOX_RUNTIME_IMAGE", "x1-sandbox:0.39")
_CONTAINER_RE = re.compile(r"^x1-preview-[a-f0-9]{8,40}$")


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
        return subprocess.run(
            ["docker", "version"],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=5,
            shell=False,
        ).returncode == 0
    except Exception:
        return False


def _image_ready() -> bool:
    if not _docker_ready():
        return False
    try:
        return subprocess.run(
            ["docker", "image", "inspect", RUNTIME_IMAGE],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=8,
            shell=False,
        ).returncode == 0
    except Exception:
        return False


def _base_command(payload: ExecRequest, *, read_only_workspace: bool = False) -> list[str]:
    if payload.image != RUNTIME_IMAGE:
        raise HTTPException(status_code=422, detail="Only the configured sandbox image is allowed")
    if payload.network_policy not in {"deny", "restricted"}:
        raise HTTPException(status_code=422, detail="Unsupported sandbox network policy")
    if any("\x00" in item or len(item) > 4000 for item in payload.argv):
        raise HTTPException(status_code=422, detail="Invalid sandbox argv")
    if len(payload.env) > 32:
        raise HTTPException(status_code=422, detail="Too many sandbox environment variables")
    for key, value in payload.env.items():
        if not key.replace("_", "").isalnum() or key.upper() != key or len(value) > 4000 or "\x00" in value:
            raise HTTPException(status_code=422, detail="Invalid sandbox environment")

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


@app.get("/health")
def health() -> dict[str, Any]:
    docker = _docker_ready()
    image = _image_ready() if docker else False
    return {"status": "stable" if docker and image else "degraded", "docker": docker, "image": image, "runtime_image": RUNTIME_IMAGE}


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
        "reason": "" if docker and image else "Docker runtime or sandbox image unavailable",
    }


@app.post("/execute")
def execute(payload: ExecRequest, x_x1_sandbox_token: str = Header(default="", alias="X-X1-Sandbox-Token")) -> dict[str, Any]:
    _auth(x_x1_sandbox_token)
    command = _base_command(payload) + payload.argv
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


@app.post("/preview/start")
def preview_start(payload: PreviewStartRequest, x_x1_sandbox_token: str = Header(default="", alias="X-X1-Sandbox-Token")) -> dict[str, Any]:
    _auth(x_x1_sandbox_token)
    if not _CONTAINER_RE.fullmatch(payload.name):
        raise HTTPException(status_code=422, detail="Invalid preview container name")
    command = _base_command(payload)
    command[2:2] = ["-d", "--name", payload.name]
    command += payload.argv
    completed = subprocess.run(command, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=20, shell=False)
    if completed.returncode != 0:
        raise HTTPException(status_code=503, detail=(completed.stderr or "Failed to start preview")[-4000:])
    return {"container_ref": payload.name, "backend": "remote-docker", "effective_network_policy": "deny"}


@app.post("/preview/exec")
def preview_exec(payload: PreviewExecRequest, x_x1_sandbox_token: str = Header(default="", alias="X-X1-Sandbox-Token")) -> dict[str, Any]:
    _auth(x_x1_sandbox_token)
    if not _CONTAINER_RE.fullmatch(payload.container_ref):
        raise HTTPException(status_code=422, detail="Invalid preview container reference")
    completed = subprocess.run(
        ["docker", "exec", payload.container_ref, *payload.argv],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        timeout=payload.timeout_seconds,
        shell=False,
    )
    return {"argv": payload.argv, "exit_code": completed.returncode, "stdout": completed.stdout[-10_000:], "stderr": completed.stderr[-10_000:]}


@app.post("/preview/stop")
def preview_stop(payload: PreviewStopRequest, x_x1_sandbox_token: str = Header(default="", alias="X-X1-Sandbox-Token")) -> dict[str, Any]:
    _auth(x_x1_sandbox_token)
    if not _CONTAINER_RE.fullmatch(payload.container_ref):
        raise HTTPException(status_code=422, detail="Invalid preview container reference")
    subprocess.run(["docker", "stop", "--time", "2", payload.container_ref], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=8, shell=False)
    return {"status": "stopped"}
