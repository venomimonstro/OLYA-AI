from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Header, HTTPException
from pydantic import BaseModel, Field

TOKEN = os.environ.get("X1_DOCKER_RUNTIME_PROXY_TOKEN", "")
RUNTIME_IMAGE = os.environ.get("X1_SANDBOX_RUNTIME_IMAGE", "x1-sandbox:0.39")
HOST_DATA_ROOT = Path(os.environ.get("X1_HOST_DATA_ROOT", "/srv/x1-data")).resolve()
MAX_TIMEOUT = max(30, min(1900, int(os.environ.get("X1_DOCKER_PROXY_MAX_TIMEOUT_SECONDS", "1830"))))
_MANAGED_NAME = re.compile(r"^x1-(?:preview|exec)-[a-f0-9]{8,40}$")
_HEX_ID = re.compile(r"^[a-f0-9]{12,64}$")
_MANAGED_LABELS = {"x1.sandbox.preview=true", "x1.sandbox.execution=true"}
_RUN_STANDALONE = {"--rm", "-d", "--read-only", "--cap-drop=ALL", "--pull=never"}
_RUN_VALUE_FLAGS = {
    "--name", "--label", "--workdir", "--security-opt", "--pids-limit", "--memory", "--cpus",
    "--network", "--user", "--mount", "--tmpfs", "--env",
}


class CommandRequest(BaseModel):
    argv: list[str] = Field(min_length=2, max_length=160)
    timeout_seconds: int = Field(default=10, ge=1, le=1900)


def _auth(value: str) -> None:
    if not TOKEN or value != TOKEN:
        raise HTTPException(status_code=403, detail="Docker runtime proxy authentication failed")


def _raw(argv: list[str], timeout: int) -> subprocess.CompletedProcess[str]:
    return subprocess.run(argv, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=timeout, shell=False)


def _managed(ref: str) -> bool:
    if not (_MANAGED_NAME.fullmatch(ref) or _HEX_ID.fullmatch(ref)):
        return False
    try:
        result = _raw(["docker", "inspect", "-f", '{{ index .Config.Labels "x1.sandbox.preview" }}|{{ index .Config.Labels "x1.sandbox.execution" }}|{{ .Config.Image }}', ref], 5)
    except (OSError, subprocess.SubprocessError):
        return False
    if result.returncode != 0:
        return False
    parts = result.stdout.strip().split("|", 2)
    return len(parts) == 3 and parts[2] == RUNTIME_IMAGE and (parts[0] == "true" or parts[1] == "true")


def _safe_mount(spec: str) -> bool:
    values: dict[str, str] = {}
    flags: set[str] = set()
    for part in spec.split(","):
        if "=" in part:
            key, value = part.split("=", 1)
            if key in values:
                return False
            values[key] = value
        else:
            flags.add(part)
    if values.get("type") != "bind":
        return False
    source = values.get("src") or values.get("source") or ""
    target = values.get("dst") or values.get("target") or ""
    try:
        source_path = Path(source).resolve()
        source_path.relative_to(HOST_DATA_ROOT)
    except (OSError, ValueError):
        return False
    if target not in {"/workspace", "/x1-runtime"}:
        return False
    if target == "/workspace" and len({"ro", "rw"} & flags) != 1:
        return False
    if target == "/x1-runtime" and "rw" not in flags:
        return False
    return True


def _validate_run_option_grammar(argv: list[str], image_index: int) -> None:
    index = 2
    while index < image_index:
        token = argv[index]
        if token in _RUN_STANDALONE:
            index += 1
            continue
        if token in _RUN_VALUE_FLAGS:
            if index + 1 >= image_index:
                raise HTTPException(status_code=422, detail=f"Docker run option {token} is missing a value")
            value = argv[index + 1]
            if value.startswith("--") and token not in {"--env", "--label", "--tmpfs", "--mount"}:
                raise HTTPException(status_code=422, detail=f"Docker run option {token} has an invalid value")
            index += 2
            continue
        raise HTTPException(status_code=422, detail=f"Docker run option is not allowed: {token}")


def _validate_run(argv: list[str]) -> None:
    image_positions = [index for index, item in enumerate(argv) if item == RUNTIME_IMAGE]
    if len(image_positions) != 1:
        raise HTTPException(status_code=422, detail="Only pinned sandbox runtime image is allowed")
    image_index = image_positions[0]
    if image_index < 3 or image_index == len(argv) - 1:
        raise HTTPException(status_code=422, detail="Sandbox runtime command is incomplete")
    _validate_run_option_grammar(argv, image_index)

    if argv.count("--network") != 1:
        raise HTTPException(status_code=422, detail="Sandbox run must define exactly one network policy")
    network_index = argv.index("--network")
    if argv[network_index + 1] != "none":
        raise HTTPException(status_code=422, detail="Sandbox Docker network must be none")

    if argv.count("--security-opt") != 1:
        raise HTTPException(status_code=422, detail="Sandbox must define exactly one security-opt")
    security_index = argv.index("--security-opt")
    if argv[security_index + 1] != "no-new-privileges":
        raise HTTPException(status_code=422, detail="Sandbox no-new-privileges required")

    if argv.count("--read-only") != 1 or argv.count("--cap-drop=ALL") != 1:
        raise HTTPException(status_code=422, detail="Sandbox rootfs/capability hardening flags missing")

    if argv.count("--user") != 1:
        raise HTTPException(status_code=422, detail="Sandbox container must define exactly one user")
    user_index = argv.index("--user")
    if argv[user_index + 1] != "10001:10001":
        raise HTTPException(status_code=422, detail="Sandbox container user mismatch")

    names = [argv[index + 1] for index, value in enumerate(argv[:image_index]) if value == "--name"]
    if len(names) != 1 or not _MANAGED_NAME.fullmatch(names[0]):
        raise HTTPException(status_code=422, detail="Managed sandbox container name required")

    labels = [argv[index + 1] for index, value in enumerate(argv[:image_index]) if value == "--label"]
    if not set(labels).intersection(_MANAGED_LABELS):
        raise HTTPException(status_code=422, detail="Managed sandbox label required")
    if any(label.startswith("x1.sandbox.") and label not in _MANAGED_LABELS and not label.startswith("x1.sandbox.expires_at=") for label in labels):
        raise HTTPException(status_code=422, detail="Unexpected sandbox label")

    mounts = [argv[index + 1] for index, value in enumerate(argv[:image_index]) if value == "--mount"]
    if len(mounts) != 2 or not all(_safe_mount(item) for item in mounts):
        raise HTTPException(status_code=422, detail="Sandbox mounts are outside approved host data root")
    if any("docker.sock" in item for item in argv):
        raise HTTPException(status_code=422, detail="Docker socket may not be mounted into sandbox")

    if argv.count("--pids-limit") != 1 or argv.count("--memory") != 1 or argv.count("--cpus") != 1:
        raise HTTPException(status_code=422, detail="Sandbox resource limits are mandatory")
    if argv.count("--workdir") != 1 or argv[argv.index("--workdir") + 1] != "/workspace":
        raise HTTPException(status_code=422, detail="Sandbox workdir must be /workspace")
    if argv.count("--tmpfs") != 1 or not argv[argv.index("--tmpfs") + 1].startswith("/tmp:rw,noexec,nosuid,nodev,"):
        raise HTTPException(status_code=422, detail="Sandbox tmpfs hardening mismatch")


def _validate(argv: list[str]) -> None:
    if argv[0] != "docker" or any("\x00" in item or len(item) > 4096 for item in argv):
        raise HTTPException(status_code=422, detail="Invalid Docker proxy command")
    command = argv[1]
    if command == "version":
        if argv != ["docker", "version"]:
            raise HTTPException(status_code=422, detail="Invalid Docker version request")
        return
    if command == "image":
        if len(argv) != 4 or argv[2] != "inspect" or argv[3] != RUNTIME_IMAGE:
            raise HTTPException(status_code=422, detail="Only configured image inspection is allowed")
        return
    if command == "ps":
        if len(argv) != 5 or argv[:4] != ["docker", "ps", "-q", "--filter"] or argv[4] not in {"label=x1.sandbox.preview=true", "label=x1.sandbox.execution=true"}:
            raise HTTPException(status_code=422, detail="Only managed sandbox listing is allowed")
        return
    if command == "run":
        _validate_run(argv)
        return
    if command in {"inspect", "rm", "exec"}:
        refs = [item for item in argv[2:] if _MANAGED_NAME.fullmatch(item) or _HEX_ID.fullmatch(item)]
        if len(refs) != 1 or not _managed(refs[0]):
            raise HTTPException(status_code=404, detail="Managed sandbox container not found")
        if command == "rm" and argv != ["docker", "rm", "-f", refs[0]]:
            raise HTTPException(status_code=422, detail="Only forced removal of managed sandbox containers is allowed")
        if command == "exec" and (len(argv) < 4 or argv[2] != refs[0] or argv[3].startswith("--")):
            raise HTTPException(status_code=422, detail="Invalid managed sandbox exec")
        if command == "inspect" and not (len(argv) >= 5 and argv[2] == "-f" and argv[-1] == refs[0]):
            raise HTTPException(status_code=422, detail="Only formatted inspection of managed sandbox containers is allowed")
        return
    raise HTTPException(status_code=422, detail="Docker command is not exposed by runtime proxy")


app = FastAPI(title="X1 Docker Runtime Proxy", docs_url=None, redoc_url=None, openapi_url=None)


@app.get("/health")
def health() -> dict[str, Any]:
    try:
        result = _raw(["docker", "version"], 5)
        ready = result.returncode == 0
    except Exception:
        ready = False
    return {"status": "stable" if ready else "degraded", "docker": ready, "runtime_image": RUNTIME_IMAGE}


@app.post("/command")
def command(payload: CommandRequest, x_x1_docker_proxy_token: str = Header(default="", alias="X-X1-Docker-Proxy-Token")) -> dict[str, Any]:
    _auth(x_x1_docker_proxy_token)
    _validate(payload.argv)
    timeout = min(MAX_TIMEOUT, int(payload.timeout_seconds))
    try:
        completed = _raw(payload.argv, timeout)
        return {"exit_code": completed.returncode, "stdout": completed.stdout[-30000:], "stderr": completed.stderr[-30000:], "timed_out": False}
    except subprocess.TimeoutExpired as exc:
        return {"exit_code": None, "stdout": (exc.stdout or "")[-30000:] if isinstance(exc.stdout, str) else "", "stderr": (exc.stderr or "")[-30000:] if isinstance(exc.stderr, str) else "", "timed_out": True}
    except OSError as exc:
        raise HTTPException(status_code=503, detail="Docker runtime unavailable") from exc
