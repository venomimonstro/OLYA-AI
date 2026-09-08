from __future__ import annotations

import os
import re
import secrets
import subprocess
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Header, HTTPException
from pydantic import BaseModel, Field

TOKEN = os.environ.get("X1_DOCKER_RUNTIME_PROXY_TOKEN", "")
RUNTIME_IMAGE = os.environ.get("X1_SANDBOX_RUNTIME_IMAGE", "x1-sandbox:0.39")
HOST_DATA_ROOT = Path(os.environ.get("X1_HOST_DATA_ROOT", "/srv/x1-data")).resolve()
MIRROR_DATA_ROOT = Path(os.environ.get("X1_DOCKER_PROXY_DATA_MIRROR_ROOT", "/x1-host-data")).resolve()
MAX_TIMEOUT = max(30, min(1900, int(os.environ.get("X1_DOCKER_PROXY_MAX_TIMEOUT_SECONDS", "1830"))))
MAX_MEMORY_MB = max(128, min(16384, int(os.environ.get("X1_DOCKER_PROXY_MAX_MEMORY_MB", "2048"))))
MAX_CPU = max(0.1, min(8.0, float(os.environ.get("X1_DOCKER_PROXY_MAX_CPU", "1.0"))))
MAX_PIDS = max(16, min(512, int(os.environ.get("X1_DOCKER_PROXY_MAX_PIDS", "128"))))
_MANAGED_NAME = re.compile(r"^x1-(?:preview|exec)-[a-f0-9]{8,40}$")
_HEX_ID = re.compile(r"^[a-f0-9]{12,64}$")
_EXPIRY_LABEL_RE = re.compile(r"^x1\.sandbox\.expires_at=(\d{10,})$")
_ENV_KEY_RE = re.compile(r"^[A-Z][A-Z0-9_]{0,127}$")
_MANAGED_LABELS = {"x1.sandbox.preview=true", "x1.sandbox.execution=true"}
_RUN_STANDALONE = {"--rm", "-d", "--read-only", "--cap-drop=ALL", "--pull=never"}
_RUN_VALUE_FLAGS = {
    "--name", "--label", "--workdir", "--security-opt", "--pids-limit", "--memory", "--cpus",
    "--network", "--user", "--mount", "--tmpfs", "--env",
}
_ALLOWED_INSPECT_FORMATS = {
    '{{ index .Config.Labels "x1.sandbox.preview" }}|{{ index .Config.Labels "x1.sandbox.expires_at" }}|{{ .Config.Image }}',
    '{{ index .Config.Labels "x1.sandbox.expires_at" }}',
}


class CommandRequest(BaseModel):
    argv: list[str] = Field(min_length=2, max_length=160)
    timeout_seconds: int = Field(default=10, ge=1, le=1900)


def _auth(value: str) -> None:
    if not TOKEN or not secrets.compare_digest(value, TOKEN):
        raise HTTPException(status_code=403, detail="Docker runtime proxy authentication failed")


def _raw(argv: list[str], timeout: int) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        argv,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        timeout=timeout,
        shell=False,
    )


def _managed(ref: str) -> bool:
    if not (_MANAGED_NAME.fullmatch(ref) or _HEX_ID.fullmatch(ref)):
        return False
    try:
        result = _raw(
            [
                "docker", "inspect", "-f",
                '{{ index .Config.Labels "x1.sandbox.preview" }}|{{ index .Config.Labels "x1.sandbox.execution" }}|{{ .Config.Image }}',
                ref,
            ],
            5,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    if result.returncode != 0:
        return False
    parts = result.stdout.strip().split("|", 2)
    return len(parts) == 3 and parts[2] == RUNTIME_IMAGE and (parts[0] == "true" or parts[1] == "true")


def _mount_fields(spec: str) -> tuple[dict[str, str], set[str]]:
    values: dict[str, str] = {}
    flags: set[str] = set()
    for raw_part in spec.split(","):
        part = raw_part.strip()
        if not part:
            raise HTTPException(status_code=422, detail="Empty Docker mount option")
        if "=" in part:
            key, value = part.split("=", 1)
            if key in values or not value:
                raise HTTPException(status_code=422, detail="Duplicate or empty Docker mount option")
            values[key] = value
        else:
            if part in flags:
                raise HTTPException(status_code=422, detail="Duplicate Docker mount flag")
            flags.add(part)
    return values, flags


def _safe_mount(spec: str) -> bool:
    try:
        values, flags = _mount_fields(spec)
    except HTTPException:
        return False
    if set(values) - {"type", "src", "source", "dst", "target"}:
        return False
    if flags - {"ro", "rw"}:
        return False
    if values.get("type") != "bind":
        return False
    if ("src" in values) == ("source" in values):
        return False
    if ("dst" in values) == ("target" in values):
        return False
    source = values.get("src") or values.get("source") or ""
    target = values.get("dst") or values.get("target") or ""
    if target not in {"/workspace", "/x1-runtime"}:
        return False
    if len({"ro", "rw"} & flags) != 1:
        return False
    if target == "/x1-runtime" and "rw" not in flags:
        return False

    try:
        source_path = Path(source)
        if not source_path.is_absolute():
            return False
        source_path = source_path.resolve(strict=False)
        relative = source_path.relative_to(HOST_DATA_ROOT)
    except (OSError, ValueError):
        return False

    expected_namespace = "code_workspaces" if target == "/workspace" else "project_runtimes"
    if len(relative.parts) < 2 or relative.parts[0] != expected_namespace:
        return False

    # The proxy has a read-only mirror of the host data directory. This is
    # deliberate: a lexical host path check cannot detect a host-side symlink
    # created inside a workspace. Require the requested source to already exist
    # in the mirror and resolve inside the exact namespace before Docker sees it.
    mirror_namespace = (MIRROR_DATA_ROOT / expected_namespace).resolve(strict=False)
    mirror_source = MIRROR_DATA_ROOT.joinpath(*relative.parts)
    try:
        if not mirror_source.exists() or not mirror_source.is_dir():
            return False
        resolved_mirror = mirror_source.resolve(strict=True)
        resolved_mirror.relative_to(mirror_namespace)
    except (OSError, ValueError):
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


def _single_value(argv: list[str], flag: str, image_index: int) -> str:
    positions = [index for index, value in enumerate(argv[:image_index]) if value == flag]
    if len(positions) != 1 or positions[0] + 1 >= image_index:
        raise HTTPException(status_code=422, detail=f"Sandbox must define exactly one {flag}")
    return argv[positions[0] + 1]


def _validate_resource_limits(argv: list[str], image_index: int) -> None:
    pids_raw = _single_value(argv, "--pids-limit", image_index)
    memory_raw = _single_value(argv, "--memory", image_index)
    cpu_raw = _single_value(argv, "--cpus", image_index)
    try:
        pids = int(pids_raw)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail="Invalid sandbox pids limit") from exc
    match = re.fullmatch(r"(\d+)m", memory_raw.lower())
    if not match:
        raise HTTPException(status_code=422, detail="Sandbox memory limit must use MiB")
    memory_mb = int(match.group(1))
    try:
        cpus = float(cpu_raw)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail="Invalid sandbox CPU limit") from exc
    if not 16 <= pids <= MAX_PIDS:
        raise HTTPException(status_code=422, detail="Sandbox pids limit exceeds proxy envelope")
    if not 128 <= memory_mb <= MAX_MEMORY_MB:
        raise HTTPException(status_code=422, detail="Sandbox memory limit exceeds proxy envelope")
    if not 0.1 <= cpus <= MAX_CPU:
        raise HTTPException(status_code=422, detail="Sandbox CPU limit exceeds proxy envelope")


def _validate_env(argv: list[str], image_index: int) -> None:
    values = [argv[index + 1] for index, token in enumerate(argv[:image_index - 1]) if token == "--env"]
    if len(values) > 32:
        raise HTTPException(status_code=422, detail="Too many sandbox environment variables")
    seen: set[str] = set()
    for item in values:
        if "=" not in item:
            raise HTTPException(status_code=422, detail="Sandbox environment entry must be KEY=value")
        key, value = item.split("=", 1)
        if not _ENV_KEY_RE.fullmatch(key) or key in seen or len(value) > 4000 or "\x00" in value:
            raise HTTPException(status_code=422, detail="Invalid sandbox environment variable")
        seen.add(key)


def _validate_run(argv: list[str]) -> None:
    image_positions = [index for index, item in enumerate(argv) if item == RUNTIME_IMAGE]
    if len(image_positions) != 1:
        raise HTTPException(status_code=422, detail="Only pinned sandbox runtime image is allowed")
    image_index = image_positions[0]
    if image_index < 3 or image_index == len(argv) - 1:
        raise HTTPException(status_code=422, detail="Sandbox runtime command is incomplete")
    _validate_run_option_grammar(argv, image_index)

    if argv[:image_index].count("--pull=never") != 1:
        raise HTTPException(status_code=422, detail="Sandbox image pulls must be disabled")
    if argv[:image_index].count("--read-only") != 1 or argv[:image_index].count("--cap-drop=ALL") != 1:
        raise HTTPException(status_code=422, detail="Sandbox rootfs/capability hardening flags missing")

    network = _single_value(argv, "--network", image_index)
    if network != "none":
        raise HTTPException(status_code=422, detail="Sandbox Docker network must be none")
    security_opt = _single_value(argv, "--security-opt", image_index)
    if security_opt != "no-new-privileges":
        raise HTTPException(status_code=422, detail="Sandbox no-new-privileges required")
    user = _single_value(argv, "--user", image_index)
    if user != "10001:10001":
        raise HTTPException(status_code=422, detail="Sandbox container user mismatch")
    workdir = _single_value(argv, "--workdir", image_index)
    if workdir != "/workspace":
        raise HTTPException(status_code=422, detail="Sandbox workdir must be /workspace")
    tmpfs = _single_value(argv, "--tmpfs", image_index)
    if tmpfs != "/tmp:rw,noexec,nosuid,nodev,size=256m":
        raise HTTPException(status_code=422, detail="Sandbox tmpfs hardening mismatch")
    name = _single_value(argv, "--name", image_index)
    if not _MANAGED_NAME.fullmatch(name):
        raise HTTPException(status_code=422, detail="Managed sandbox container name required")

    labels = [argv[index + 1] for index, value in enumerate(argv[:image_index - 1]) if value == "--label"]
    primary_labels = [label for label in labels if label in _MANAGED_LABELS]
    expiry_labels = [label for label in labels if _EXPIRY_LABEL_RE.fullmatch(label)]
    if len(labels) != 2 or len(primary_labels) != 1 or len(expiry_labels) != 1:
        raise HTTPException(status_code=422, detail="Sandbox labels must contain exactly one managed label and one expiry label")
    primary = primary_labels[0]
    if primary == "x1.sandbox.execution=true":
        if not name.startswith("x1-exec-") or argv[:image_index].count("--rm") != 1 or "-d" in argv[:image_index]:
            raise HTTPException(status_code=422, detail="Execution container mode/name mismatch")
    else:
        if not name.startswith("x1-preview-") or argv[:image_index].count("-d") != 1 or "--rm" in argv[:image_index]:
            raise HTTPException(status_code=422, detail="Preview container mode/name mismatch")

    mounts = [argv[index + 1] for index, value in enumerate(argv[:image_index - 1]) if value == "--mount"]
    if len(mounts) != 2 or not all(_safe_mount(item) for item in mounts):
        raise HTTPException(status_code=422, detail="Sandbox mounts are outside approved host data namespaces")
    targets = set()
    for spec in mounts:
        values, _ = _mount_fields(spec)
        targets.add(values.get("dst") or values.get("target"))
    if targets != {"/workspace", "/x1-runtime"}:
        raise HTTPException(status_code=422, detail="Sandbox requires one workspace and one runtime mount")
    if any("docker.sock" in item for item in argv):
        raise HTTPException(status_code=422, detail="Docker socket may not be mounted into sandbox")

    _validate_resource_limits(argv, image_index)
    _validate_env(argv, image_index)


def _validate(argv: list[str]) -> None:
    if not argv or argv[0] != "docker" or any("\x00" in item or len(item) > 4096 for item in argv):
        raise HTTPException(status_code=422, detail="Invalid Docker proxy command")
    if len(argv) < 2:
        raise HTTPException(status_code=422, detail="Incomplete Docker proxy command")
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
        if len(argv) != 5 or argv[:4] != ["docker", "ps", "-q", "--filter"] or argv[4] not in {
            "label=x1.sandbox.preview=true", "label=x1.sandbox.execution=true"
        }:
            raise HTTPException(status_code=422, detail="Only managed sandbox listing is allowed")
        return
    if command == "run":
        _validate_run(argv)
        return
    if command in {"inspect", "rm", "exec"}:
        refs = [item for item in argv[2:] if _MANAGED_NAME.fullmatch(item) or _HEX_ID.fullmatch(item)]
        if len(refs) != 1 or not _managed(refs[0]):
            raise HTTPException(status_code=404, detail="Managed sandbox container not found")
        ref = refs[0]
        if command == "rm" and argv != ["docker", "rm", "-f", ref]:
            raise HTTPException(status_code=422, detail="Only forced removal of managed sandbox containers is allowed")
        if command == "exec" and (len(argv) < 4 or argv[2] != ref or argv[3].startswith("--")):
            raise HTTPException(status_code=422, detail="Invalid managed sandbox exec")
        if command == "inspect":
            if len(argv) != 5 or argv[2] != "-f" or argv[3] not in _ALLOWED_INSPECT_FORMATS or argv[4] != ref:
                raise HTTPException(status_code=422, detail="Only approved inspection of managed sandbox containers is allowed")
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
def command(
    payload: CommandRequest,
    x_x1_docker_proxy_token: str = Header(default="", alias="X-X1-Docker-Proxy-Token"),
) -> dict[str, Any]:
    _auth(x_x1_docker_proxy_token)
    _validate(payload.argv)
    timeout = min(MAX_TIMEOUT, int(payload.timeout_seconds))
    try:
        completed = _raw(payload.argv, timeout)
        return {
            "exit_code": completed.returncode,
            "stdout": completed.stdout[-30000:],
            "stderr": completed.stderr[-30000:],
            "timed_out": False,
        }
    except subprocess.TimeoutExpired as exc:
        return {
            "exit_code": None,
            "stdout": (exc.stdout or "")[-30000:] if isinstance(exc.stdout, str) else "",
            "stderr": (exc.stderr or "")[-30000:] if isinstance(exc.stderr, str) else "",
            "timed_out": True,
        }
    except OSError as exc:
        raise HTTPException(status_code=503, detail="Docker runtime unavailable") from exc
