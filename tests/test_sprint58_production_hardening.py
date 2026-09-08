from __future__ import annotations

import io
import zipfile
from pathlib import Path

import pytest
from fastapi import HTTPException

from app import docker_runtime_proxy as proxy
from app.services.code_workspace import WorkspaceError, import_zip, safe_relative_path
from app.services.research import _ip_is_public, normalize_url

ROOT = Path(__file__).resolve().parents[1]


def test_docker_socket_exists_only_on_runtime_proxy():
    compose = (ROOT / "docker-compose.yml").read_text("utf-8")
    assert compose.count("/var/run/docker.sock:/var/run/docker.sock") == 1
    worker = compose.split("  sandbox-worker:", 1)[1].split("  document-worker:", 1)[0]
    runtime_proxy = compose.split("  docker-runtime-proxy:", 1)[1].split("  sandbox-worker:", 1)[0]
    assert "docker.sock" not in worker
    assert "docker.sock" in runtime_proxy
    assert "X1_DOCKER_RUNTIME_PROXY_TOKEN" in runtime_proxy
    assert "./data:/x1-host-data:ro" in runtime_proxy
    assert "X1_DOCKER_PROXY_MAX_MEMORY_MB" in runtime_proxy
    assert "X1_DOCKER_PROXY_MAX_CPU" in runtime_proxy
    assert "X1_DOCKER_PROXY_MAX_PIDS" in runtime_proxy


def test_sandbox_worker_has_no_docker_cli_or_subprocess():
    dockerfile = (ROOT / "Dockerfile.sandbox-worker").read_text("utf-8")
    worker = (ROOT / "app" / "sandbox_worker_api.py").read_text("utf-8")
    assert "docker.io" not in dockerfile
    assert "import subprocess" not in worker
    assert "X1_DOCKER_RUNTIME_PROXY_URL" in worker


def _configure_proxy_roots(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(proxy, "HOST_DATA_ROOT", tmp_path.resolve())
    monkeypatch.setattr(proxy, "MIRROR_DATA_ROOT", tmp_path.resolve())
    monkeypatch.setattr(proxy, "MAX_MEMORY_MB", 2048)
    monkeypatch.setattr(proxy, "MAX_CPU", 1.0)
    monkeypatch.setattr(proxy, "MAX_PIDS", 128)


def _safe_run(tmp_path: Path) -> list[str]:
    workspace = tmp_path / "code_workspaces" / "w"
    scratch = tmp_path / "project_runtimes" / "r"
    workspace.mkdir(parents=True, exist_ok=True)
    scratch.mkdir(parents=True, exist_ok=True)
    return [
        "docker", "run", "--rm", "--name", "x1-exec-abcdef1234567890",
        "--label", "x1.sandbox.execution=true", "--label", "x1.sandbox.expires_at=9999999999",
        "--pull=never", "--workdir", "/workspace", "--read-only", "--cap-drop=ALL",
        "--security-opt", "no-new-privileges", "--pids-limit", "64", "--memory", "512m", "--cpus", "1.0",
        "--network", "none", "--user", "10001:10001",
        "--mount", f"type=bind,src={workspace},dst=/workspace,rw",
        "--mount", f"type=bind,src={scratch},dst=/x1-runtime,rw",
        "--tmpfs", "/tmp:rw,noexec,nosuid,nodev,size=256m",
        proxy.RUNTIME_IMAGE, "python", "-m", "py_compile", "main.py",
    ]


def test_runtime_proxy_accepts_only_canonical_sandbox_run(tmp_path: Path, monkeypatch):
    _configure_proxy_roots(tmp_path, monkeypatch)
    argv = _safe_run(tmp_path)
    proxy._validate_run(argv)

    for mutation in (
        [*argv[:-4], "--privileged", *argv[-4:]],
        [*argv[:-4], "--network", "host", *argv[-4:]],
        [*argv[:-4], "--pid", "host", *argv[-4:]],
        [*argv[:-4], "--security-opt", "seccomp=unconfined", *argv[-4:]],
    ):
        with pytest.raises(HTTPException):
            proxy._validate_run(mutation)


def test_runtime_proxy_rejects_mount_outside_or_above_approved_namespaces(tmp_path: Path, monkeypatch):
    _configure_proxy_roots(tmp_path, monkeypatch)
    argv = _safe_run(tmp_path)
    mount_index = argv.index("--mount")
    for unsafe_source in ("/", str(tmp_path)):
        mutated = list(argv)
        mutated[mount_index + 1] = f"type=bind,src={unsafe_source},dst=/workspace,rw"
        with pytest.raises(HTTPException):
            proxy._validate_run(mutated)


def test_runtime_proxy_rejects_host_side_symlink_escape(tmp_path: Path, monkeypatch):
    _configure_proxy_roots(tmp_path, monkeypatch)
    argv = _safe_run(tmp_path)
    outside = tmp_path.parent / (tmp_path.name + "-outside")
    outside.mkdir(exist_ok=True)
    link = tmp_path / "code_workspaces" / "escape"
    link.symlink_to(outside, target_is_directory=True)
    mount_index = argv.index("--mount")
    argv[mount_index + 1] = f"type=bind,src={link},dst=/workspace,rw"
    with pytest.raises(HTTPException):
        proxy._validate_run(argv)


def test_runtime_proxy_enforces_its_own_resource_ceiling(tmp_path: Path, monkeypatch):
    _configure_proxy_roots(tmp_path, monkeypatch)
    argv = _safe_run(tmp_path)
    for flag, unsafe in (("--memory", "99999m"), ("--cpus", "8"), ("--pids-limit", "4096")):
        mutated = list(argv)
        mutated[mutated.index(flag) + 1] = unsafe
        with pytest.raises(HTTPException):
            proxy._validate_run(mutated)


def test_runtime_proxy_requires_no_pull_and_exact_managed_labels(tmp_path: Path, monkeypatch):
    _configure_proxy_roots(tmp_path, monkeypatch)
    argv = _safe_run(tmp_path)
    without_pull = [item for item in argv if item != "--pull=never"]
    with pytest.raises(HTTPException):
        proxy._validate_run(without_pull)

    image_index = argv.index(proxy.RUNTIME_IMAGE)
    with_extra_label = [*argv[:image_index], "--label", "com.example.untrusted=true", *argv[image_index:]]
    with pytest.raises(HTTPException):
        proxy._validate_run(with_extra_label)


def test_runtime_proxy_restricts_inspect_templates(monkeypatch):
    ref = "x1-preview-abcdef1234567890"
    monkeypatch.setattr(proxy, "_managed", lambda value: value == ref)
    with pytest.raises(HTTPException):
        proxy._validate(["docker", "inspect", "-f", "{{json .}}", ref])


def test_runtime_proxy_command_allowlist_rejects_arbitrary_docker():
    with pytest.raises(HTTPException):
        proxy._validate(["docker", "system", "prune", "-af"])


def test_ssrf_rejects_local_and_special_networks():
    assert _ip_is_public("127.0.0.1") is False
    assert _ip_is_public("10.1.2.3") is False
    assert _ip_is_public("169.254.169.254") is False
    with pytest.raises(Exception):
        normalize_url("file:///etc/passwd")
    with pytest.raises(Exception):
        normalize_url("http://localhost/secret")


def test_workspace_path_traversal_and_archive_bomb_are_rejected(tmp_path: Path):
    with pytest.raises(WorkspaceError):
        safe_relative_path("../../etc/passwd")

    data = io.BytesIO()
    with zipfile.ZipFile(data, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("a.txt", "A" * 2048)
        archive.writestr("b.txt", "B" * 2048)
    with pytest.raises(WorkspaceError, match="expands beyond"):
        import_zip(tmp_path / "workspace", data.getvalue(), max_files=10, max_unpacked_bytes=1024)


def test_db_pool_and_transaction_deadlines_are_mandatory():
    text = (ROOT / "app" / "db.py").read_text("utf-8")
    for marker in ("pool_pre_ping", "pool_size", "max_overflow", "pool_timeout", "statement_timeout", "lock_timeout", "idle_in_transaction_session_timeout"):
        assert marker in text


def test_final_rc_gate_requires_full_runtime_evidence():
    text = (ROOT / "scripts" / "rc_release_candidate.py").read_text("utf-8")
    for marker in ("--runtime", "--live-inference", "--user-journey", "--chaos", "model-regression-latest.json", "restore-drill-latest.json", "critical_regression_cases_required"):
        assert marker in text
    release = (ROOT / "scripts" / "release_gate.py").read_text("utf-8")
    assert '"--virtual-users", "100000"' in release
    assert "restore_drill" in release


def test_runtime_chaos_covers_real_service_restarts_and_isolated_enospc():
    text = (ROOT / "scripts" / "rc_runtime_chaos.py").read_text("utf-8")
    for service in ("db", "searxng", "docker-runtime-proxy", "sandbox-worker", "document-worker", "llama"):
        assert f'"{service}"' in text
    assert '["docker", "run"' in text
    assert "size=4m" in text
    assert "dd if=/dev/zero" in text


def test_canary_auto_rollback_is_wired():
    text = (ROOT / "app" / "services" / "public_launch_scheduler.py").read_text("utf-8")
    assert "freeze_rollout" in text
    assert "public_launch_auto_rollback" in text
    assert "rollback_rollout" in text


def test_full_regression_invokes_security_audit():
    text = (ROOT / "scripts" / "run_full_regression.py").read_text("utf-8")
    assert "scripts.rc_security_audit" in text
