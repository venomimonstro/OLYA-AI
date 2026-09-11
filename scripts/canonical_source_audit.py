#!/usr/bin/env python3
from __future__ import annotations

import ast
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MODEL_SOURCES = (
    "app/models.py",
    "app/models_core.py",
    "app/models_migrations.py",
    "app/services/engineering_execution.py",
    "app/services/image_runtime.py",
    "app/services/project_runtime.py",
)


class CanonicalSourceError(RuntimeError):
    pass


def _validate_python(source: bytes, *, label: str) -> dict:
    try:
        text = source.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise CanonicalSourceError(f"{label} does not decode as UTF-8") from exc
    try:
        tree = ast.parse(text, filename=label)
        compile(tree, label, "exec", dont_inherit=True)
    except (SyntaxError, ValueError, TypeError) as exc:
        raise CanonicalSourceError(f"{label} is not valid Python: {exc}") from exc
    return {
        "bytes": len(source),
        "lines": text.count("\n") + 1,
        "sha256": hashlib.sha256(source).hexdigest(),
        "top_level_nodes": len(tree.body),
    }


def audit_model_source(relative: str) -> dict:
    path = ROOT / relative
    if not path.is_file():
        raise CanonicalSourceError(f"Missing canonical ORM source: {relative}")
    try:
        source = path.read_bytes()
    except OSError as exc:
        raise CanonicalSourceError(f"Cannot read canonical ORM source: {relative}") from exc
    return {"path": relative, **_validate_python(source, label=relative)}


def audit_generated_models() -> dict:
    import importlib.util

    relative = "scripts/generate_orm_models.py"
    path = ROOT / relative
    spec = importlib.util.spec_from_file_location("x1_model_generator_audit", path)
    if spec is None or spec.loader is None:
        raise CanonicalSourceError("Cannot load ORM model generator")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    expected = module.generate()
    actual = (ROOT / "app/models_migrations.py").read_text("utf-8")
    if actual != expected:
        raise CanonicalSourceError("Generated ORM source is stale; run scripts/generate_orm_models.py")
    details = _validate_python(path.read_bytes(), label=relative)
    return {"path": relative, **details, "generated_path": "app/models_migrations.py", "generation": "synchronized"}


def audit_all() -> dict:
    checks: list[dict] = []
    errors: list[dict] = []
    operations = [
        *((relative, (lambda rel=relative: audit_model_source(rel))) for relative in MODEL_SOURCES),
        ("scripts/generate_orm_models.py", audit_generated_models),
    ]
    for label, operation in operations:
        try:
            checks.append({"status": "passed", **operation()})
        except CanonicalSourceError as exc:
            errors.append({"path": label, "error": str(exc)})
    return {
        "format": "x1-canonical-source-audit-v2",
        "status": "passed" if not errors else "failed",
        "checks": checks,
        "errors": errors,
    }


def main() -> int:
    result = audit_all()
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["status"] == "passed" else 2


if __name__ == "__main__":
    raise SystemExit(main())
