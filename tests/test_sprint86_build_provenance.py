from pathlib import Path

from scripts.build_provenance import FORMAT, source_fingerprint


def _minimal_source_tree(root: Path) -> None:
    for name in ("app", "scripts", "alembic", "regression"):
        (root / name).mkdir(parents=True, exist_ok=True)
    (root / "app" / "main.py").write_text("VALUE = 1\n", "utf-8")
    (root / "scripts" / "probe.py").write_text("print('ok')\n", "utf-8")
    (root / "alembic" / "env.py").write_text("# migration env\n", "utf-8")
    (root / "regression" / "golden_corpus.json").write_text("[]\n", "utf-8")
    (root / "alembic.ini").write_text("[alembic]\n", "utf-8")
    (root / "pyproject.toml").write_text("[project]\nname='x1'\n", "utf-8")
    (root / "model-manifest.json").write_text("{}\n", "utf-8")
    (root / "Dockerfile").write_text("FROM scratch\n", "utf-8")
    (root / "docker-compose.yml").write_text("services: {}\n", "utf-8")


def test_build_provenance_is_deterministic_and_source_sensitive(tmp_path: Path):
    _minimal_source_tree(tmp_path)
    first = source_fingerprint(tmp_path)
    second = source_fingerprint(tmp_path)
    assert first["format"] == FORMAT == "x1-build-provenance-v1"
    assert first["source_fingerprint"] == second["source_fingerprint"]
    assert len(first["source_fingerprint"]) == 64
    assert first["manifest"]["build-inputs/Dockerfile"] != "missing"
    assert first["manifest"]["build-inputs/docker-compose.yml"] != "missing"

    (tmp_path / "app" / "main.py").write_text("VALUE = 2\n", "utf-8")
    changed = source_fingerprint(tmp_path)
    assert changed["source_fingerprint"] != first["source_fingerprint"]


def test_build_provenance_ignores_runtime_cache_files(tmp_path: Path):
    _minimal_source_tree(tmp_path)
    before = source_fingerprint(tmp_path)["source_fingerprint"]
    cache = tmp_path / "app" / "__pycache__"
    cache.mkdir()
    (cache / "main.cpython-312.pyc").write_bytes(b"runtime-cache")
    after = source_fingerprint(tmp_path)["source_fingerprint"]
    assert after == before
