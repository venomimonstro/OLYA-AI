from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from app.services.git_collaboration import GitError, _repo_root, ensure_local_repo, normalize_github_url
from app.services.http_limits import RequestBodyLimitMiddleware

ROOT = Path(__file__).resolve().parents[1]


async def _asgi_status(headers: list[tuple[bytes, bytes]], *, max_bytes: int = 1024) -> tuple[int, bool]:
    reached = False

    async def downstream(scope, receive, send):
        nonlocal reached
        reached = True
        await send({"type": "http.response.start", "status": 204, "headers": []})
        await send({"type": "http.response.body", "body": b""})

    middleware = RequestBodyLimitMiddleware(downstream, max_bytes=max_bytes)
    messages: list[dict] = []

    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(message):
        messages.append(message)

    await middleware({"type": "http", "method": "POST", "path": "/", "headers": headers}, receive, send)
    status = next(item["status"] for item in messages if item["type"] == "http.response.start")
    return int(status), reached


def test_request_body_limit_rejects_conflicting_content_length_before_app():
    status, reached = asyncio.run(_asgi_status([(b"content-length", b"10"), (b"content-length", b"20")]))
    assert status == 413
    assert reached is False


def test_request_body_limit_rejects_negative_or_comma_framing():
    for value in (b"-1", b"10, 10", b""):
        status, reached = asyncio.run(_asgi_status([(b"content-length", value)]))
        assert status == 413
        assert reached is False


def test_request_body_limit_accepts_identical_duplicate_lengths():
    status, reached = asyncio.run(_asgi_status([(b"content-length", b"10"), (b"content-length", b"10")]))
    assert status == 204
    assert reached is True


def test_malformed_github_port_is_controlled_validation_error():
    with pytest.raises(GitError, match="port"):
        normalize_github_url("https://github.com:not-a-port/owner/repo.git")


def test_git_pointer_file_cannot_redirect_workspace_metadata(tmp_path: Path):
    outside = tmp_path / "outside-git"
    outside.mkdir()
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / ".git").write_text(f"gitdir: {outside}\n", "utf-8")
    with pytest.raises(GitError, match="metadata"):
        _repo_root(workspace)
    with pytest.raises(GitError, match="metadata"):
        ensure_local_repo(workspace, "main")


def test_local_git_directory_is_still_accepted(tmp_path: Path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / ".git").mkdir()
    assert _repo_root(workspace) == workspace.resolve()


def test_runtime_image_contains_model_regression_assets():
    dockerfile = (ROOT / "Dockerfile").read_text("utf-8")
    assert "COPY --chown=x1:x1 model-manifest.json ./model-manifest.json" in dockerfile
    assert "COPY --chown=x1:x1 regression ./regression" in dockerfile


def test_sandbox_overlays_git_metadata_read_only():
    worker = (ROOT / "app" / "sandbox_worker_api.py").read_text("utf-8")
    proxy = (ROOT / "app" / "docker_runtime_proxy.py").read_text("utf-8")
    assert "dst=/workspace/.git,ro" in worker
    assert '"/workspace/.git"' in proxy
    assert "Git metadata mount must belong to the selected workspace" in proxy


def test_overload_resumable_means_existing_server_state_only():
    main = (ROOT / "app" / "main.py").read_text("utf-8")
    helper = main.split("def _overload_request_resumable", 1)[1].split("def _file_upload_request", 1)[0]
    assert 'lane_name == "research"' in helper
    assert 'path.startswith("/v1/research/runs/")' in helper
    assert 'lane_name == "sandbox"' in helper
    assert 'lane_name == "images"' not in helper
    assert 'return False' in helper


def test_final_rc_binds_release_and_model_evidence_to_current_source():
    rc = (ROOT / "scripts" / "rc_release_candidate.py").read_text("utf-8")
    assert "verify_release_head" in rc
    assert "verify_model_report_current_source" in rc
    assert "template_runtime_sha256" in rc
    assert "model_manifest_sha256" in rc
    assert "corpus_sha256" in rc
