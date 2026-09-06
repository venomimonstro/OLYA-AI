#!/usr/bin/env python3
from __future__ import annotations

import json
import re
import shutil
import tomllib
from pathlib import Path

from app.main import app

ROOT = Path(__file__).resolve().parents[1]


def main() -> int:
    checks: list[dict] = []

    def record(name: str, passed: bool, detail: object) -> None:
        checks.append({"name": name, "status": "passed" if passed else "failed", "detail": detail})

    pyproject = tomllib.loads((ROOT / "pyproject.toml").read_text("utf-8"))
    package_version = str(pyproject["project"]["version"])
    record("version_match", app.version == package_version, {"app": app.version, "package": package_version})

    direct = list(pyproject["project"].get("dependencies") or [])
    mutable = [item for item in direct if any(op in item for op in (">=", "<=", "<", "~=")) and "==" not in item]
    record("production_dependencies_pinned", not mutable, {"mutable": mutable})

    dockerfile = (ROOT / "Dockerfile").read_text("utf-8")
    record(
        "document_runtime_installed",
        all(marker in dockerfile for marker in ("libreoffice-writer", "poppler-utils", "fonts-dejavu-core")),
        "LibreOffice + poppler + deterministic font package",
    )
    record("libreoffice_binary", bool(shutil.which("libreoffice") or shutil.which("soffice")), shutil.which("libreoffice") or shutil.which("soffice"))
    record("pdftoppm_binary", bool(shutil.which("pdftoppm")), shutil.which("pdftoppm"))

    compose = (ROOT / "docker-compose.yml").read_text("utf-8")
    app_section = compose.split("\n  app:\n", 1)[1].split("\n  gate:\n", 1)[0]
    record("web_app_has_no_docker_socket", "/var/run/docker.sock" not in app_section, "Docker socket belongs only to sandbox-worker")
    record("host_data_is_persistent", "./data:/app/data" in app_section, "host bind /app/data")

    image_lines = [line.strip() for line in compose.splitlines() if line.strip().startswith("image:")]
    critical = [line for line in image_lines if "searxng" in line or "llama.cpp" in line]
    floating = [line for line in critical if "@sha256:" not in line]
    record("critical_runtime_images_pinned", bool(critical) and not floating, {"critical": critical, "floating": floating})

    worker = (ROOT / "app" / "sandbox_worker_api.py").read_text("utf-8")
    for marker in (
        "MAX_CONCURRENT_EXECUTIONS",
        "MAX_ACTIVE_PREVIEWS",
        "MAX_MEMORY_MB",
        "MAX_CPU",
        "MAX_PIDS",
        "PREVIEW_TTL_SECONDS",
        "reap_expired_previews",
    ):
        record(f"sandbox_{marker.lower()}", marker in worker, marker)

    update = (ROOT / "scripts" / "update.sh").read_text("utf-8") if (ROOT / "scripts" / "update.sh").exists() else ""
    backup_pos = update.find("Creating consistent pre-update backup")
    merge_pos = update.find("git merge --ff-only")
    restore_pos = update.find("bash \"$RESTORE_COPY\"")
    record("transactional_update_backup_before_merge", backup_pos >= 0 and merge_pos > backup_pos, {"backup_pos": backup_pos, "merge_pos": merge_pos})
    record("transactional_update_has_restore", restore_pos >= 0 and "git reset --hard \"$OLD_HEAD\"" in update, "code + DB/data rollback")

    paths = {str(getattr(route, "path", "")) for route in app.routes}
    required_prefixes = (
        "/v1/auth", "/v1/chat", "/v1/research", "/v1/documents", "/v1/code",
        "/v1/project-runtimes", "/v1/project-sandboxes", "/v1/commerce", "/v1/api",
        "/v1/admin/reliability", "/v1/admin/beta", "/v1/admin/launch",
    )
    missing = [prefix for prefix in required_prefixes if not any(path.startswith(prefix) for path in paths)]
    record("core_routes_registered", not missing, {"missing": missing, "route_count": len(paths)})

    # The old silent-optional implementation is a known regression signature.
    main_source = (ROOT / "app" / "main.py").read_text("utf-8")
    record("production_router_import_fail_fast", "if _is_production:\n            raise" in main_source, "core product router import failures abort production startup")

    failed = [item["name"] for item in checks if item["status"] != "passed"]
    payload = {"format": "x1-stability-probe-v1", "status": "passed" if not failed else "failed", "failed": failed, "checks": checks}
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0 if not failed else 2


if __name__ == "__main__":
    raise SystemExit(main())
