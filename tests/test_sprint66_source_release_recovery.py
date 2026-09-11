from __future__ import annotations

import ast
import os
import sqlite3
import subprocess
import sys
from pathlib import Path

from app.main import app


ROOT = Path(__file__).resolve().parents[1]
RECOVERED_SERVICES = (
    ROOT / "app/services/engineering_execution.py",
    ROOT / "app/services/image_runtime.py",
    ROOT / "app/services/project_runtime.py",
)


def _run(*args: str, env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, *args],
        cwd=ROOT,
        env=env,
        text=True,
        capture_output=True,
        check=False,
        timeout=120,
    )


def test_recovered_services_are_plain_readable_python() -> None:
    for path in RECOVERED_SERVICES:
        source = path.read_text("utf-8")
        tree = ast.parse(source, filename=str(path))
        assert len(source.splitlines()) >= 80
        assert not any(
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "exec"
            for node in ast.walk(tree)
        )
        assert "b85decode" not in source
        assert "zlib.decompress" not in source


def test_canonical_and_static_source_gates_pass() -> None:
    for script in ("scripts/canonical_source_audit.py", "scripts/static_contract_audit.py"):
        result = _run(script)
        assert result.returncode == 0, result.stdout + result.stderr


def test_route_registry_is_concrete_and_contains_recovered_surfaces() -> None:
    assert all(hasattr(route, "path") for route in app.routes)
    paths = {str(route.path) for route in app.routes}
    assert "/welcome" in paths
    assert "/v1/images/references" in paths
    assert "/v1/images/edits" in paths
    assert "/v1/feedback/complaints" in paths


def test_fresh_alembic_upgrade_has_one_head_and_no_schema_drift(tmp_path: Path) -> None:
    database = tmp_path / "sprint66.sqlite3"
    env = os.environ.copy()
    env["X1_DATABASE_URL"] = f"sqlite+pysqlite:///{database}"

    heads = _run("-m", "alembic", "heads", env=env)
    assert heads.returncode == 0, heads.stdout + heads.stderr
    assert len([line for line in heads.stdout.splitlines() if "(head)" in line]) == 1

    upgrade = _run("-m", "alembic", "upgrade", "head", env=env)
    assert upgrade.returncode == 0, upgrade.stdout + upgrade.stderr
    with sqlite3.connect(database) as connection:
        application_tables = connection.execute(
            "SELECT count(*) FROM sqlite_master "
            "WHERE type = 'table' AND name NOT LIKE 'sqlite_%' AND name != 'alembic_version'"
        ).fetchone()[0]
    assert application_tables == 94

    drift = _run("-m", "alembic", "check", env=env)
    assert drift.returncode == 0, drift.stdout + drift.stderr
    assert "No new upgrade operations detected" in drift.stdout
