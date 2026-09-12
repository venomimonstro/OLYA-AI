#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FORMAT = "x1-build-provenance-v1"
_DIRECTORY_INPUTS = ("app", "scripts", "alembic", "regression")
_FILE_INPUTS = ("alembic.ini", "pyproject.toml", "model-manifest.json")
_BUILD_INPUTS = ("Dockerfile", "docker-compose.yml")
_EXCLUDED_NAMES = {"BUILD_PROVENANCE.json"}
_EXCLUDED_SUFFIXES = {".pyc", ".pyo"}
_EXCLUDED_PARTS = {"__pycache__", ".git", ".pytest_cache", ".mypy_cache", ".ruff_cache"}


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _eligible(path: Path) -> bool:
    return (
        path.is_file()
        and path.name not in _EXCLUDED_NAMES
        and path.suffix not in _EXCLUDED_SUFFIXES
        and not any(part in _EXCLUDED_PARTS for part in path.parts)
    )


def _canonical_build_input(root: Path, name: str) -> Path:
    # Docker copies build-only inputs under /app/build-inputs so the provenance
    # can include Dockerfile/Compose without relying on the daemon's build context
    # after the image has been created. A source checkout keeps them at root.
    copied = root / "build-inputs" / name
    return copied if copied.is_file() else root / name


def source_manifest(root: Path = ROOT) -> dict[str, str]:
    root = root.resolve()
    manifest: dict[str, str] = {}
    for directory in _DIRECTORY_INPUTS:
        base = root / directory
        if not base.is_dir():
            manifest[f"{directory}/<missing>"] = "missing"
            continue
        for path in sorted(base.rglob("*")):
            if not _eligible(path):
                continue
            relative = path.relative_to(root).as_posix()
            manifest[relative] = _sha256(path)
    for name in _FILE_INPUTS:
        path = root / name
        manifest[name] = _sha256(path) if path.is_file() else "missing"
    for name in _BUILD_INPUTS:
        path = _canonical_build_input(root, name)
        manifest[f"build-inputs/{name}"] = _sha256(path) if path.is_file() else "missing"
    return manifest


def source_fingerprint(root: Path = ROOT) -> dict:
    manifest = source_manifest(root)
    canonical = json.dumps(manifest, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return {
        "format": FORMAT,
        "source_fingerprint": hashlib.sha256(canonical).hexdigest(),
        "file_count": len(manifest),
        "manifest": manifest,
    }


def write_report(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", "utf-8")
    os.replace(tmp, path)


def main() -> int:
    parser = argparse.ArgumentParser(description="Compute deterministic X1 source/build provenance")
    parser.add_argument("--root", default=str(ROOT))
    parser.add_argument("--write", default="")
    parser.add_argument("--no-manifest", action="store_true")
    args = parser.parse_args()
    payload = source_fingerprint(Path(args.root))
    if args.no_manifest:
        payload = {key: value for key, value in payload.items() if key != "manifest"}
    if args.write:
        write_report(Path(args.write), payload)
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0 if payload["source_fingerprint"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
