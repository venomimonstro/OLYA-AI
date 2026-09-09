#!/usr/bin/env python3
from __future__ import annotations

import ast
import base64
import hashlib
import json
import zlib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MAX_DECOMPRESSED_BYTES = 8 * 1024 * 1024
WRAPPERS = (
    "app/services/engineering_execution.py",
    "app/services/image_runtime.py",
)
MODEL_PAYLOAD = "app/_models_impl.py.gz"


class CanonicalSourceError(RuntimeError):
    pass


def _decompress_limited(data: bytes, *, wbits: int, label: str) -> bytes:
    decoder = zlib.decompressobj(wbits)
    try:
        output = decoder.decompress(data, MAX_DECOMPRESSED_BYTES + 1)
        if len(output) > MAX_DECOMPRESSED_BYTES or decoder.unconsumed_tail:
            raise CanonicalSourceError(f"{label} expands beyond the canonical source byte limit")
        remaining = MAX_DECOMPRESSED_BYTES + 1 - len(output)
        if remaining > 0:
            output += decoder.flush(remaining)
    except zlib.error as exc:
        raise CanonicalSourceError(f"{label} compressed payload is invalid") from exc
    if len(output) > MAX_DECOMPRESSED_BYTES:
        raise CanonicalSourceError(f"{label} expands beyond the canonical source byte limit")
    if not decoder.eof:
        raise CanonicalSourceError(f"{label} compressed payload is truncated")
    return output


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


def audit_model_payload() -> dict:
    path = ROOT / MODEL_PAYLOAD
    if not path.is_file():
        raise CanonicalSourceError(f"Missing canonical ORM payload: {MODEL_PAYLOAD}")
    compressed = path.read_bytes()
    source = _decompress_limited(compressed, wbits=zlib.MAX_WBITS | 16, label=MODEL_PAYLOAD)
    return {"path": MODEL_PAYLOAD, **_validate_python(source, label=MODEL_PAYLOAD + "::python")}


def _wrapper_payload(path: Path) -> bytes:
    try:
        tree = ast.parse(path.read_text("utf-8"), filename=path.as_posix())
    except (OSError, UnicodeError, SyntaxError) as exc:
        raise CanonicalSourceError(f"Cannot parse wrapper {path.relative_to(ROOT)}") from exc
    payloads: list[str | bytes] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute) or node.func.attr != "b85decode":
            continue
        if len(node.args) != 1 or not isinstance(node.args[0], ast.Constant) or not isinstance(node.args[0].value, (str, bytes)):
            raise CanonicalSourceError(f"Wrapper {path.relative_to(ROOT)} has a non-literal base85 payload")
        payloads.append(node.args[0].value)
    if len(payloads) != 1:
        raise CanonicalSourceError(f"Wrapper {path.relative_to(ROOT)} must contain exactly one base85 payload")
    raw = payloads[0]
    try:
        return base64.b85decode(raw.encode("ascii") if isinstance(raw, str) else raw)
    except (ValueError, UnicodeEncodeError) as exc:
        raise CanonicalSourceError(f"Wrapper {path.relative_to(ROOT)} has invalid base85 data") from exc


def audit_wrapper(relative: str) -> dict:
    path = ROOT / relative
    if not path.is_file():
        raise CanonicalSourceError(f"Missing canonical wrapper: {relative}")
    compressed = _wrapper_payload(path)
    source = _decompress_limited(compressed, wbits=zlib.MAX_WBITS, label=relative)
    return {"path": relative, **_validate_python(source, label=relative + "::canonical")}


def audit_all() -> dict:
    checks: list[dict] = []
    errors: list[dict] = []
    for label, operation in (
        (MODEL_PAYLOAD, audit_model_payload),
        *((relative, (lambda rel=relative: audit_wrapper(rel))) for relative in WRAPPERS),
    ):
        try:
            checks.append({"status": "passed", **operation()})
        except CanonicalSourceError as exc:
            errors.append({"path": label, "error": str(exc)})
    return {
        "format": "x1-canonical-source-audit-v1",
        "status": "passed" if not errors else "failed",
        "max_decompressed_bytes": MAX_DECOMPRESSED_BYTES,
        "checks": checks,
        "errors": errors,
    }


def main() -> int:
    result = audit_all()
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["status"] == "passed" else 2


if __name__ == "__main__":
    raise SystemExit(main())
