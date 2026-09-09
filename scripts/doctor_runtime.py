#!/usr/bin/env python3
from __future__ import annotations

import json
import re
import shutil
import subprocess
import sys
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import urlopen

from scripts.download_model import (
    DEFAULT_FILE,
    DEFAULT_MMPROJ_FILE,
    DEFAULT_MODEL_NAME,
    DEFAULT_SHA256,
    DEFAULT_SIZE,
    LLAMA_MEMORY_CAP_GIB,
    LLAMA_MIN_MEMORY_GIB,
    MIN_DETECTED_RAM_GIB,
    NON_LLAMA_RESERVE_GIB,
    VISION_PROJECTOR,
    safe_context_for_ram_gib,
)

ROOT = Path(__file__).resolve().parents[1]
MODEL = ROOT / "models" / DEFAULT_FILE
MMPROJ = ROOT / "models" / DEFAULT_MMPROJ_FILE if DEFAULT_MMPROJ_FILE else None
_GIB = 1024**3


def result(name: str, status: str, detail: str) -> dict:
    return {"check": name, "status": status, "detail": detail}


def cmd(args: list[str], timeout: int = 30):
    try:
        return subprocess.run(args, cwd=ROOT, text=True, capture_output=True, timeout=timeout, stdin=subprocess.DEVNULL, shell=False)
    except Exception:
        return None


def _revision_tokens(text: str) -> set[str]:
    return set(re.findall(r"\b[0-9a-f]{8,32}\b", text or "", flags=re.IGNORECASE))


def _service_section(rendered: str, service: str) -> str:
    marker = f"\n  {service}:\n"
    if marker not in rendered:
        if rendered.startswith(f"services:\n  {service}:\n"):
            rest = rendered.split(f"services:\n  {service}:\n", 1)[1]
        else:
            return ""
    else:
        rest = rendered.split(marker, 1)[1]
    match = re.search(r"\n  [A-Za-z0-9_.-]+:\n", rest)
    return rest[: match.start()] if match else rest


def _env_value(text: str, key: str) -> str:
    prefix = key + "="
    value = ""
    for line in text.splitlines():
        if line.startswith(prefix):
            value = line.split("=", 1)[1].strip()
    return value


def _memory_gib(value: str) -> float | None:
    match = re.fullmatch(r"\s*(\d+(?:\.\d+)?)\s*([gGmM])(?:[bB])?\s*", value or "")
    if not match:
        return None
    number = float(match.group(1))
    return number if match.group(2).lower() == "g" else number / 1024.0


def _host_memory_gib() -> float:
    try:
        text = Path("/proc/meminfo").read_text("utf-8")
        match = re.search(r"^MemTotal:\s+(\d+)\s+kB", text, flags=re.MULTILINE)
        return int(match.group(1)) / 1024.0 / 1024.0 if match else 0.0
    except (OSError, ValueError):
        return 0.0


def _container_running(service: str) -> bool:
    ps = cmd(["docker", "compose", "ps", "-q", service], timeout=15)
    if ps is None or ps.returncode != 0 or not ps.stdout.strip():
        return False
    container_id = ps.stdout.splitlines()[0].strip()
    inspect = cmd(["docker", "inspect", "--format", "{{.State.Running}}", container_id], timeout=10)
    return bool(inspect and inspect.returncode == 0 and inspect.stdout.strip().lower() == "true")


def _active_container_memory_budget(env_text: str, host_gib: float) -> tuple[bool, dict]:
    ps = cmd(["docker", "compose", "ps", "-q"], timeout=20)
    if ps is None or ps.returncode != 0:
        return False, {"error": "cannot_list_active_compose_containers"}
    ids = [line.strip() for line in ps.stdout.splitlines() if line.strip()]
    total_bytes = 0
    unbounded: list[str] = []
    containers: list[dict] = []
    for container_id in ids:
        inspected = cmd(["docker", "inspect", "--format", "{{.Name}} {{.HostConfig.Memory}}", container_id], timeout=10)
        if inspected is None or inspected.returncode != 0:
            unbounded.append(container_id[:12]); continue
        parts = inspected.stdout.strip().split()
        if len(parts) != 2:
            unbounded.append(container_id[:12]); continue
        name = parts[0].lstrip("/")
        try:
            memory = int(parts[1])
        except ValueError:
            memory = 0
        containers.append({"name": name, "limit_gib": round(memory / _GIB, 3) if memory else 0})
        if memory <= 0:
            unbounded.append(name)
        else:
            total_bytes += memory
    try:
        sandbox_gib = max(0.0, int(_env_value(env_text, "X1_SANDBOX_MAX_MEMORY_MB") or "2048") / 1024.0)
    except ValueError:
        sandbox_gib = 2.0
    if (_env_value(env_text, "X1_PROJECT_SANDBOX_BACKEND") or "remote").lower() != "remote":
        sandbox_gib = 0.0
    os_reserve_gib = 1.0
    configured_gib = total_bytes / _GIB
    required_gib = configured_gib + sandbox_gib + os_reserve_gib
    ok = not unbounded and host_gib > 0 and required_gib <= host_gib
    return ok, {
        "host_gib": round(host_gib, 2), "active_compose_limit_gib": round(configured_gib, 2),
        "sandbox_child_reserve_gib": round(sandbox_gib, 2), "host_os_reserve_gib": os_reserve_gib,
        "required_worst_case_gib": round(required_gib, 2), "headroom_gib": round(host_gib - required_gib, 2),
        "unbounded_or_unreadable": unbounded, "containers": containers,
    }


def _image_worker_check(env_text: str) -> tuple[str, str]:
    generation_enabled = (_env_value(env_text, "X1_IMAGE_BACKEND") or "disabled").lower() != "disabled"
    editing_enabled = (_env_value(env_text, "X1_IMAGE_EDIT_BACKEND") or "disabled").lower() != "disabled"
    if not (generation_enabled or editing_enabled):
        return "stable", "image capabilities disabled by configuration"
    if not _container_running("image-worker"):
        return "failed", "image capability is enabled but image-worker is not running"
    probe = cmd([
        "docker", "compose", "exec", "-T", "image-worker", "python", "-c",
        "from app.core.config import get_settings; from app.services.image_capabilities import validate_image_edit_files; s=get_settings(); failures=validate_image_edit_files(s); assert not failures, failures",
    ], timeout=30)
    if probe is None or probe.returncode != 0:
        detail = ((probe.stdout + probe.stderr) if probe else "image worker model preflight unavailable")[-1200:]
        return "failed", detail
    heartbeat = cmd([
        "docker", "compose", "exec", "-T", "app", "python", "-c",
        "from app.db import SessionLocal; from app.services.image_worker_state import image_worker_snapshot; db=SessionLocal(); s=image_worker_snapshot(db,stale_seconds=90); db.close(); print(s); assert s.get('alive'), s",
    ], timeout=20)
    if heartbeat is None or heartbeat.returncode != 0:
        detail = ((heartbeat.stdout + heartbeat.stderr) if heartbeat else "image worker heartbeat unavailable")[-1200:]
        return "failed", detail
    return "stable", "image worker running with current model paths and fresh heartbeat"


def main() -> int:
    checks = [
        result("python", "stable" if sys.version_info >= (3, 12) else "failed", sys.version.split()[0]),
        result("docker", "stable" if shutil.which("docker") else "failed", "installed" if shutil.which("docker") else "missing"),
    ]
    env = ROOT / ".env"
    if not env.exists():
        checks.append(result("env", "failed", ".env missing")); env_text = ""
    else:
        env_text = env.read_text("utf-8", errors="replace")
        unsafe = [marker for marker in (
            "X1_ADMIN_BOOTSTRAP_TOKEN=change-me", "X1_PROJECT_RUNTIME_SECRET_KEY=change-me-runtime-secret",
            "X1_PROJECT_SANDBOX_WORKER_TOKEN=change-me-sandbox-worker", "X1_DOCUMENT_RENDER_WORKER_TOKEN=change-me-document-worker",
            "POSTGRES_PASSWORD=change-me-db", "POSTGRES_PASSWORD=x1-dev-only", "x1:change-me-db@db:5432/x1",
        ) if marker in env_text]
        checks.append(result("secrets", "failed" if unsafe else "stable", "unsafe defaults: " + ",".join(unsafe) if unsafe else "non-default"))
        checks.append(result("environment", "stable" if "X1_ENV=production" in env_text else "degraded", "production" if "X1_ENV=production" in env_text else "X1_ENV is not production"))

    host_gib = _host_memory_gib()
    inference_expected = "X1_LLAMA_BASE_URL=http://llama:8080" in env_text
    if inference_expected:
        size = MODEL.stat().st_size if MODEL.exists() else 0
        model_ok = MODEL.is_file() and size == DEFAULT_SIZE
        checks.append(result("qwen_model", "stable" if model_ok else "failed", json.dumps({"path": str(MODEL), "bytes": size, "expected_bytes": DEFAULT_SIZE, "expected_sha256": DEFAULT_SHA256, "hash_verification": "installer_and_release_gate"})))
        if VISION_PROJECTOR and MMPROJ is not None:
            mm_size = MMPROJ.stat().st_size if MMPROJ.exists() else 0
            expected_mm_size = int(VISION_PROJECTOR.get("size_bytes") or 0)
            mm_ok = MMPROJ.is_file() and mm_size == expected_mm_size
            checks.append(result("qwen_vision_projector", "stable" if mm_ok else "failed", json.dumps({"path": str(MMPROJ), "bytes": mm_size, "expected_bytes": expected_mm_size, "expected_sha256": VISION_PROJECTOR.get("sha256")})))
        env_name = _env_value(env_text, "X1_LLAMA_MODEL_NAME"); env_file = _env_value(env_text, "X1_LLAMA_MODEL_FILE")
        identity_ok = env_name == DEFAULT_MODEL_NAME and env_file == DEFAULT_FILE
        checks.append(result("qwen_model_identity", "stable" if identity_ok else "failed", json.dumps({"configured_name": env_name, "expected_name": DEFAULT_MODEL_NAME, "configured_file": env_file, "expected_file": DEFAULT_FILE})))
        llama_gib = _memory_gib(_env_value(env_text, "X1_LLAMA_MEMORY_LIMIT"))
        max_safe = min(float(LLAMA_MEMORY_CAP_GIB), max(0.0, host_gib - NON_LLAMA_RESERVE_GIB))
        memory_ok = bool(host_gib >= MIN_DETECTED_RAM_GIB and llama_gib is not None and LLAMA_MIN_MEMORY_GIB <= llama_gib <= max_safe + 0.05)
        checks.append(result("host_memory_budget", "stable" if memory_ok else "failed", json.dumps({"host_gib": round(host_gib, 2), "llama_limit_gib": None if llama_gib is None else round(llama_gib, 2), "required_non_llama_reserve_gib": NON_LLAMA_RESERVE_GIB, "required_llama_min_gib": LLAMA_MIN_MEMORY_GIB, "safe_llama_ceiling_gib": round(max_safe, 2)})))
        try:
            safe_context = safe_context_for_ram_gib(host_gib)
        except ValueError:
            safe_context = 0
        try:
            configured_context = int(_env_value(env_text, "X1_DEEP_CONTEXT_TOKENS") or "0")
        except ValueError:
            configured_context = 0
        checks.append(result("qwen_context_budget", "stable" if safe_context > 0 and 1024 <= configured_context <= safe_context else "failed", json.dumps({"configured_tokens": configured_context, "safe_ceiling_tokens": safe_context})))

    if shutil.which("docker"):
        compose = cmd(["docker", "compose", "config"])
        if compose and compose.returncode == 0:
            checks.append(result("compose", "stable", "valid")); rendered = compose.stdout
            app_section = _service_section(rendered, "app")
            sandbox_section = _service_section(rendered, "sandbox-worker")
            proxy_section = _service_section(rendered, "docker-runtime-proxy")
            document_section = _service_section(rendered, "document-worker")
            image_section = _service_section(rendered, "image-worker")
            persistent = "/app/data" in app_section and str((ROOT / "data").resolve()) in app_section
            checks.append(result("app_data_volume", "stable" if persistent else "failed", "host bind /app/data configured" if persistent else "persistent host /app/data bind missing"))
            socket_ok = (
                "/var/run/docker.sock" in proxy_section
                and "/var/run/docker.sock" not in sandbox_section
                and "/var/run/docker.sock" not in app_section
                and "/var/run/docker.sock" not in document_section
                and "/var/run/docker.sock" not in image_section
            )
            checks.append(result("sandbox_privilege_boundary", "stable" if socket_ok else "failed", "Docker socket isolated to authenticated runtime proxy" if socket_ok else "Docker socket boundary is incorrect"))
            document_isolated = bool(document_section and "/app/data" in document_section and "8091" in document_section)
            checks.append(result("document_render_isolation", "stable" if document_isolated else "failed", "dedicated document worker with shared data volume" if document_isolated else "document worker service missing or misconfigured"))
            llama_section = _service_section(rendered, "llama")
            model_path_ok = f"/models/{DEFAULT_FILE}" in llama_section
            mmproj_ok = not DEFAULT_MMPROJ_FILE or ("--mmproj" in llama_section and f"/models/{DEFAULT_MMPROJ_FILE}" in llama_section)
            checks.append(result("compose_qwen_identity", "stable" if model_path_ok and mmproj_ok else "failed", json.dumps({"model": model_path_ok, "vision_projector": mmproj_ok})))
        else:
            checks.append(result("compose", "failed", ((compose.stderr if compose else "cannot run") or "invalid")[-800:]))

        memory_ok, memory_detail = _active_container_memory_budget(env_text, host_gib)
        checks.append(result("active_container_memory_budget", "stable" if memory_ok else "failed", json.dumps(memory_detail, ensure_ascii=False)))
        current = cmd(["docker", "compose", "exec", "-T", "app", "alembic", "current"]); heads = cmd(["docker", "compose", "exec", "-T", "app", "alembic", "heads"])
        current_tokens = _revision_tokens((current.stdout + current.stderr) if current else ""); head_tokens = _revision_tokens((heads.stdout + heads.stderr) if heads else "")
        migrations_ok = bool(current and heads and current.returncode == 0 and heads.returncode == 0 and len(head_tokens) == 1 and current_tokens == head_tokens)
        checks.append(result("migrations", "stable" if migrations_ok else "failed", json.dumps({"current": sorted(current_tokens), "heads": sorted(head_tokens)}, ensure_ascii=False)))
        data_probe = cmd(["docker", "compose", "exec", "-T", "app", "python", "-c", "from pathlib import Path; p=Path('/app/data'); p.mkdir(parents=True,exist_ok=True); assert p.is_dir() and p.exists()"])
        checks.append(result("app_data_runtime", "stable" if data_probe and data_probe.returncode == 0 else "failed", "writable persistent data path" if data_probe and data_probe.returncode == 0 else "app data path unavailable"))
        search_probe = cmd(["docker", "compose", "exec", "-T", "searxng", "python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8080/search?q=x1-doctor&format=json',timeout=5).read()"])
        checks.append(result("internet_search", "stable" if search_probe and search_probe.returncode == 0 else "failed", "private SearXNG JSON search reachable" if search_probe and search_probe.returncode == 0 else "SearXNG unavailable"))
        document_probe = cmd(["docker", "compose", "exec", "-T", "document-worker", "python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8091/health',timeout=5).read()"])
        checks.append(result("document_renderer", "stable" if document_probe and document_probe.returncode == 0 else "failed", "isolated LibreOffice/Poppler worker reachable" if document_probe and document_probe.returncode == 0 else "document render worker unavailable"))
        sandbox_probe = cmd(["docker", "compose", "exec", "-T", "app", "python", "-m", "scripts.sandbox_probe"], timeout=90)
        checks.append(result("closed_sandbox", "stable" if sandbox_probe and sandbox_probe.returncode == 0 else "failed", ((sandbox_probe.stdout + sandbox_probe.stderr) if sandbox_probe else "sandbox probe unavailable")[-1200:]))
        image_state, image_detail = _image_worker_check(env_text)
        checks.append(result("image_worker", image_state, image_detail))

    try:
        with urlopen("http://127.0.0.1:8000/ready", timeout=8) as response:
            body = json.loads(response.read().decode()); state = "stable" if body.get("status") == "stable" else "degraded"
            checks.append(result("ready", state, json.dumps(body, ensure_ascii=False)[:3000]))
    except HTTPError as exc:
        try:
            detail = exc.read().decode("utf-8", errors="replace")
        except Exception:
            detail = ""
        checks.append(result("ready", "degraded", f"HTTP {exc.code}: {detail[:2000]}"))
    except Exception as exc:
        checks.append(result("ready", "degraded", type(exc).__name__))

    backups = ROOT / "backups"
    snapshots = sorted((p for p in backups.glob("*") if p.is_dir() and ".partial." not in p.name), reverse=True) if backups.exists() else []
    if snapshots:
        latest = snapshots[0]; checks.append(result("backup", "stable" if (latest / "SHA256SUMS").is_file() else "failed", str(latest)))
    else:
        checks.append(result("backup", "degraded", "no host-visible backup snapshot found"))

    overall = "failed" if any(x["status"] == "failed" for x in checks) else "degraded" if any(x["status"] == "degraded" for x in checks) else "stable"
    print(json.dumps({"status": overall, "checks": checks}, ensure_ascii=False, indent=2))
    return 2 if overall == "failed" else 1 if overall == "degraded" else 0


if __name__ == "__main__":
    raise SystemExit(main())
