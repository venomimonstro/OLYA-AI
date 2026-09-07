from __future__ import annotations

from pathlib import Path

import pytest

from app.services.git_collaboration import GitError, commit, ensure_local_repo, git_status, normalize_github_url, scan_secrets

ROOT = Path(__file__).resolve().parents[1]


def test_git_collaboration_is_plain_auditable_source_without_exec_or_shell():
    source = (ROOT / "app" / "services" / "git_collaboration.py").read_text("utf-8")
    assert "exec(" not in source
    assert "b85decode" not in source and "zlib" not in source
    assert "shell=False" in source
    assert "core.hooksPath=/dev/null" in source
    assert "protocol.file.allow=never" in source
    assert "protocol.ext.allow=never" in source
    assert 'errors="replace"' in source


def test_github_url_is_canonicalized_and_credentials_or_lookalike_hosts_are_rejected():
    canonical, owner, repo = normalize_github_url("https://github.com/openai/example.git")
    assert canonical == "https://github.com/openai/example.git"
    assert owner == "openai" and repo == "example"
    with pytest.raises(GitError):
        normalize_github_url("https://github.com.evil.invalid/openai/example.git")
    with pytest.raises(GitError):
        normalize_github_url("https://token@github.com/openai/example.git")
    with pytest.raises(GitError):
        normalize_github_url("https://github.com/openai/example.git?token=secret")


def test_local_git_commit_contract_works_and_secret_scan_blocks_changed_secret(tmp_path: Path):
    state = ensure_local_repo(tmp_path, "main")
    assert state["branch"] == "main"
    (tmp_path / "README.md").write_text("hello\n", "utf-8")
    first = commit(tmp_path, "initial test commit", ["README.md"])
    assert first["head_after"] and first["head_after"] != first["head_before"]
    assert git_status(tmp_path)["dirty"] is False

    (tmp_path / "config.env").write_text("API_KEY=abcdefghijklmnopqrstuvwxyz123456\n", "utf-8")
    findings = scan_secrets(tmp_path, ["config.env"])
    assert findings
    assert any(item["kind"] == "generic_secret" for item in findings)
