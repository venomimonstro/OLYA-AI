from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_installer_builds_and_health_checks_runtime_proxy():
    text = (ROOT / "scripts" / "install.sh").read_text("utf-8")
    assert "docker compose build app docker-runtime-proxy sandbox-worker document-worker" in text
    assert "docker compose up -d db searxng docker-runtime-proxy sandbox-worker document-worker" in text
    assert "Docker runtime proxy did not become ready" in text
    assert "docker-runtime-proxy sandbox-worker document-worker" in text


def test_first_install_bootstraps_baseline_only_through_regression_lab():
    text = (ROOT / "scripts" / "install.sh").read_text("utf-8")
    assert "if [ ! -f backups/model-regression-baseline.json ]" in text
    assert "scripts.model_regression_lab --live-url http://llama:8080 --record-baseline" in text
    assert "Initial model regression baseline failed" in text


def test_update_rollback_removes_new_proxy_before_old_commit_restore():
    text = (ROOT / "scripts" / "update.sh").read_text("utf-8")
    assert "quiesce_runtime_proxy" in text
    assert "com.docker.compose.service=docker-runtime-proxy" in text
    rollback = text.split("rollback()", 1)[1].split("trap 'rollback", 1)[0]
    assert rollback.index("quiesce_runtime_proxy") < rollback.index("git reset --hard")


def test_rc_promotes_baseline_only_after_all_checks_pass():
    text = (ROOT / "scripts" / "rc_release_candidate.py").read_text("utf-8")
    assert "if not failed:" in text
    assert "promote_model_baseline()" in text
    assert "accepted_by" in text
    assert "x1-release-candidate-v1" in text
