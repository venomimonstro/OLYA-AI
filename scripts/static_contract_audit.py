#!/usr/bin/env python3
from __future__ import annotations

import ast
import json
import re
import sys
import tomllib
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]

REQUIRED_FILES = (
    "app/main.py",
    "app/user_ui.py",
    "app/sandbox_worker_api.py",
    "app/services/research.py",
    "app/services/resource_governor.py",
    "app/services/git_collaboration.py",
    "scripts/bootstrap.sh",
    "scripts/install.sh",
    "scripts/download_model.py",
    "scripts/doctor.py",
    "scripts/release_gate.py",
    "scripts/backup.sh",
    "scripts/restore_drill.sh",
    "model-manifest.json",
    "docker-compose.yml",
    "Dockerfile",
    "Dockerfile.sandbox-worker",
    "Dockerfile.sandbox-runtime",
    "searxng/settings.yml",
)

ALLOWED_SETTINGS_ATTRIBUTES = {"model_dump", "model_copy", "model_fields"}


def issue(code: str, path: str, line: int = 0, detail: str = "") -> dict[str, Any]:
    return {"code": code, "path": path, "line": int(line or 0), "detail": detail[:500]}


def _settings_fields() -> set[str]:
    tree = ast.parse((ROOT / "app/core/config.py").read_text("utf-8"), filename="app/core/config.py")
    for node in tree.body:
        if isinstance(node, ast.ClassDef) and node.name == "Settings":
            return {
                child.target.id
                for child in node.body
                if isinstance(child, ast.AnnAssign) and isinstance(child.target, ast.Name)
            }
    return set()


def _call_name(node: ast.AST) -> str:
    parts: list[str] = []
    current = node
    while isinstance(current, ast.Attribute):
        parts.append(current.attr)
        current = current.value
    if isinstance(current, ast.Name):
        parts.append(current.id)
    return ".".join(reversed(parts))


def _is_true(node: ast.AST | None) -> bool:
    return isinstance(node, ast.Constant) and node.value is True


def _settings_attr(node: ast.Attribute) -> str | None:
    if isinstance(node.value, ast.Name) and node.value.id in {"settings", "st", "cfg"}:
        return node.attr
    if isinstance(node.value, ast.Attribute) and node.value.attr == "settings":
        return node.attr
    return None


def _audit_python(path: Path, settings_fields: set[str]) -> list[dict[str, Any]]:
    rel = path.relative_to(ROOT).as_posix()
    findings: list[dict[str, Any]] = []
    try:
        tree = ast.parse(path.read_text("utf-8"), filename=rel)
    except (OSError, UnicodeError, SyntaxError) as exc:
        return [issue("python_parse_failed", rel, getattr(exc, "lineno", 0) or 0, str(exc))]

    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute):
            attr = _settings_attr(node)
            if attr and attr not in settings_fields and attr not in ALLOWED_SETTINGS_ATTRIBUTES:
                findings.append(issue("unknown_settings_attribute", rel, node.lineno, attr))
        if not isinstance(node, ast.Call):
            continue
        name = _call_name(node.func)
        if name in {"eval", "builtins.eval", "os.system", "os.popen", "tempfile.mktemp"}:
            findings.append(issue("dangerous_execution_primitive", rel, node.lineno, name))
        if name in {"exec", "builtins.exec"} and rel != "app/models.py":
            findings.append(issue("unexpected_exec", rel, node.lineno, name))
        if name in {"pickle.loads", "pickle.load", "marshal.loads", "marshal.load"}:
            findings.append(issue("unsafe_deserialization", rel, node.lineno, name))
        if name.startswith("subprocess."):
            for keyword in node.keywords:
                if keyword.arg == "shell" and _is_true(keyword.value):
                    findings.append(issue("subprocess_shell_true", rel, node.lineno, name))
        if name.endswith("extractall"):
            has_filter = any(keyword.arg == "filter" for keyword in node.keywords)
            if "zip" in name.lower() or not has_filter:
                findings.append(issue("unfiltered_archive_extractall", rel, node.lineno, name))
        for keyword in node.keywords:
            if keyword.arg == "verify" and isinstance(keyword.value, ast.Constant) and keyword.value.value is False:
                findings.append(issue("tls_verification_disabled", rel, node.lineno, name))
    return findings


def _audit_versions() -> list[dict[str, Any]]:
    findings: list[dict[str, Any]] = []
    package = tomllib.loads((ROOT / "pyproject.toml").read_text("utf-8"))
    expected = str(package["project"]["version"])
    main_text = (ROOT / "app/main.py").read_text("utf-8")
    match = re.search(r'version\s*=\s*"([0-9]+\.[0-9]+\.[0-9]+)"', main_text)
    actual = match.group(1) if match else ""
    if actual != expected:
        findings.append(issue("version_mismatch", "app/main.py", detail=f"app={actual!r} package={expected!r}"))
    return findings


def _service_section(text: str, service: str) -> str:
    marker = f"\n  {service}:\n"
    if marker not in text:
        return ""
    rest = text.split(marker, 1)[1]
    match = re.search(r"\n  [A-Za-z0-9_.-]+:\n", rest)
    return rest[: match.start()] if match else rest


def _audit_compose() -> list[dict[str, Any]]:
    findings: list[dict[str, Any]] = []
    text = (ROOT / "docker-compose.yml").read_text("utf-8")
    app = _service_section(text, "app")
    worker = _service_section(text, "sandbox-worker")
    socket = "/var/run/docker.sock"
    if socket in app:
        findings.append(issue("docker_socket_exposed_to_web_app", "docker-compose.yml"))
    if socket not in worker:
        findings.append(issue("sandbox_worker_missing_docker_socket", "docker-compose.yml"))
    if text.count(socket) != 2:
        findings.append(issue("unexpected_docker_socket_reference_count", "docker-compose.yml", detail=str(text.count(socket))))
    for service in ("db", "searxng", "llama"):
        section = _service_section(text, service)
        match = re.search(r"^\s*image:\s*(\S+)", section, flags=re.MULTILINE)
        image = match.group(1) if match else ""
        if "@sha256:" not in image:
            findings.append(issue("unpinned_runtime_image", "docker-compose.yml", detail=f"{service}: {image}"))
    if "mem_limit:" not in app or "mem_limit:" not in worker:
        findings.append(issue("core_container_memory_unbounded", "docker-compose.yml"))
    return findings


def _audit_model_contract() -> list[dict[str, Any]]:
    findings: list[dict[str, Any]] = []
    try:
        manifest = json.loads((ROOT / "model-manifest.json").read_text("utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        return [issue("model_manifest_unreadable", "model-manifest.json", detail=str(exc))]
    if manifest.get("format") != "x1-llm-model-manifest-v1":
        findings.append(issue("model_manifest_format", "model-manifest.json"))
        return findings
    primary = manifest.get("primary") or {}
    name = str(primary.get("model_name") or "")
    filename = str(primary.get("filename") or "")
    revision = str(primary.get("revision") or "")
    digest = str(primary.get("sha256") or "")
    size = primary.get("size_bytes")
    if not name or not filename.endswith(".gguf"):
        findings.append(issue("model_identity_invalid", "model-manifest.json"))
    if not re.fullmatch(r"[0-9a-f]{40}", revision):
        findings.append(issue("model_revision_not_immutable", "model-manifest.json", detail=revision))
    if not re.fullmatch(r"[0-9a-f]{64}", digest):
        findings.append(issue("model_sha256_invalid", "model-manifest.json"))
    if not isinstance(size, int) or size < 10_000_000_000:
        findings.append(issue("model_size_invalid", "model-manifest.json", detail=str(size)))
    low_ram = manifest.get("low_ram_policy") or {}
    if low_ram.get("automatic_downgrade") is not False:
        findings.append(issue("silent_model_downgrade_allowed", "model-manifest.json"))

    env = (ROOT / ".env.example").read_text("utf-8")
    config = (ROOT / "app/core/config.py").read_text("utf-8")
    compose = (ROOT / "docker-compose.yml").read_text("utf-8")
    downloader = (ROOT / "scripts/download_model.py").read_text("utf-8")
    installer = (ROOT / "scripts/install.sh").read_text("utf-8")
    if f"X1_LLAMA_MODEL_NAME={name}" not in env:
        findings.append(issue("env_model_identity_mismatch", ".env.example", detail=name))
    if name not in config:
        findings.append(issue("config_model_identity_mismatch", "app/core/config.py", detail=name))
    if filename not in compose:
        findings.append(issue("compose_model_file_mismatch", "docker-compose.yml", detail=filename))
    for marker, path, text in (
        ("model-manifest.json", "scripts/download_model.py", downloader),
        ("model-manifest.json", "scripts/install.sh", installer),
        ("X1_LLAMA_MODEL_FILE", "scripts/install.sh", installer),
    ):
        if marker not in text:
            findings.append(issue("model_contract_not_consumed", path, detail=marker))
    return findings


def main() -> int:
    findings: list[dict[str, Any]] = []
    for rel in REQUIRED_FILES:
        if not (ROOT / rel).is_file():
            findings.append(issue("required_file_missing", rel))
    settings_fields = _settings_fields()
    if not settings_fields:
        findings.append(issue("settings_contract_unreadable", "app/core/config.py"))
    for base in (ROOT / "app", ROOT / "scripts"):
        for path in sorted(base.rglob("*.py")):
            findings.extend(_audit_python(path, settings_fields))
    findings.extend(_audit_versions())
    findings.extend(_audit_compose())
    findings.extend(_audit_model_contract())

    models = (ROOT / "app/models.py").read_text("utf-8", errors="replace")
    if models.count("exec(") != 1 or "_models_impl.py.gz" not in models:
        findings.append(issue("canonical_model_wrapper_contract_changed", "app/models.py"))

    payload = {
        "format": "x1-static-contract-v2",
        "status": "passed" if not findings else "failed",
        "settings_fields": len(settings_fields),
        "findings": findings,
    }
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0 if not findings else 2


if __name__ == "__main__":
    raise SystemExit(main())
