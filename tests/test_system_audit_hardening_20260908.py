from __future__ import annotations

from pathlib import Path

from starlette.requests import Request

from app.services.auth import _expensive_public_request
from app.services.git_collaboration import commit, ensure_local_repo, scan_secrets

ROOT = Path(__file__).resolve().parents[1]


def _request(method: str, path: str) -> Request:
    return Request({"type": "http", "method": method, "path": path, "query_string": b"", "headers": [], "scheme": "http", "server": ("test", 80), "client": ("127.0.0.1", 12345)})


def test_rollout_gate_covers_resource_heavy_surfaces_but_not_project_crud():
    for path in (
        "/v1/chat",
        "/v1/code/workspaces",
        "/v1/project-runtimes",
        "/v1/git/bindings",
        "/v1/documents",
        "/v1/research/runs",
    ):
        assert _expensive_public_request(_request("POST", path)) is True
    assert _expensive_public_request(_request("POST", "/v1/projects/p1/files")) is True
    assert _expensive_public_request(_request("GET", "/v1/projects/p1")) is False
    assert _expensive_public_request(_request("POST", "/v1/projects")) is False


def test_sandbox_worker_is_non_root_and_has_no_docker_socket():
    dockerfile = (ROOT / "Dockerfile.sandbox-worker").read_text("utf-8")
    compose = (ROOT / "docker-compose.yml").read_text("utf-8")
    worker = compose.split("  sandbox-worker:", 1)[1].split("  document-worker:", 1)[0]
    assert "USER x1" in dockerfile or "USER 10001" in dockerfile
    assert "docker.sock" not in worker


def test_safety_service_is_plain_reviewable_python():
    source = (ROOT / "app" / "services" / "safety.py").read_text("utf-8")
    assert "b85decode" not in source
    assert "exec(compile" not in source
    assert "def require_capability" in source
    assert "def create_risk_event" in source


def test_legacy_code_agent_cannot_complete_on_missing_or_stale_proof():
    source = (ROOT / "app" / "api" / "routes" / "code.py").read_text("utf-8")
    assert '"verification_valid": False' in source
    assert "Changed code requires a server-recorded verification command" in source
    assert "Verification is missing or stale after the latest code change" in source
    assert 'run.status = "running" if passed else "blocked"' in source


def test_durable_job_enqueue_contains_race_safe_idempotency_boundary():
    source = (ROOT / "app" / "services" / "jobs.py").read_text("utf-8")
    assert "with db.begin_nested()" in source
    assert "except IntegrityError" in source
    assert "idempotency_key == idempotency_key" in source


def test_secret_scan_catches_secret_removed_from_head_but_present_in_pending_history(tmp_path: Path):
    ensure_local_repo(tmp_path, "main")
    secret_file = tmp_path / "temporary-secret.txt"
    secret_file.write_text("api_key=ghp_ABCDEFGHIJKLMNOPQRSTUVWXYZ1234567890\n", "utf-8")
    commit(tmp_path, "add temporary secret", ["temporary-secret.txt"])
    secret_file.unlink()
    commit(tmp_path, "remove temporary secret", ["temporary-secret.txt"])
    findings = scan_secrets(tmp_path, [])
    assert any(item.get("kind") == "github_token" and str(item.get("origin", "")).startswith("commit:") for item in findings)


def test_static_contract_audit_requires_socket_only_on_runtime_proxy():
    source = (ROOT / "scripts" / "static_contract_audit.py").read_text("utf-8")
    assert "docker_socket_exposed_to_sandbox_worker" in source
    assert "docker_runtime_proxy_missing_socket" in source
    assert "text.count(socket) != 1" in source
    assert "sandbox_worker_missing_docker_socket" not in source


def test_real_public_user_journey_never_grants_itself_db_privileges():
    source = (ROOT / "scripts" / "e2e_public_user_journey.py").read_text("utf-8")
    assert "SessionLocal" not in source
    assert "BetaParticipant" not in source
    assert "UserQuota" not in source
    for marker in (
        '"register"',
        '"create_project"',
        '"create_conversation"',
        '"rollout_eligibility"',
        '"logout"',
        '"login_again"',
        '"account_export"',
        '"deactivate"',
    ):
        assert marker in source
