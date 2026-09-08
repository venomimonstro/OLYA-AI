#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import time
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[1]
MANIFEST_PATH = ROOT / "model-manifest.json"


def load_manifest() -> dict:
    try:
        payload = json.loads(MANIFEST_PATH.read_text("utf-8"))
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"Model manifest is unreadable: {MANIFEST_PATH}") from exc
    if payload.get("format") != "x1-llm-model-manifest-v1":
        raise RuntimeError("Unsupported model manifest format")
    return payload


MANIFEST = load_manifest()
PRIMARY = MANIFEST["primary"]
VISION_PROJECTOR = MANIFEST.get("vision_projector") or {}
HOST_POLICY = MANIFEST["host_policy"]
DEFAULT_MODEL_NAME = str(PRIMARY["model_name"])
DEFAULT_REPO = str(PRIMARY["repository"])
DEFAULT_REVISION = str(PRIMARY["revision"])
DEFAULT_FILE = str(PRIMARY["filename"])
DEFAULT_SHA256 = str(PRIMARY["sha256"])
DEFAULT_SIZE = int(PRIMARY["size_bytes"])
DEFAULT_URL = f"https://huggingface.co/{DEFAULT_REPO}/resolve/{DEFAULT_REVISION}/{DEFAULT_FILE}?download=true"
DEFAULT_MMPROJ_FILE = str(VISION_PROJECTOR.get("filename") or "")
MIN_DETECTED_RAM_GIB = int(HOST_POLICY["minimum_detected_ram_gib"])
NON_LLAMA_RESERVE_GIB = int(HOST_POLICY["non_llama_reserve_gib"])
LLAMA_MEMORY_CAP_GIB = int(HOST_POLICY["llama_memory_cap_gib"])
LLAMA_MIN_MEMORY_GIB = int(HOST_POLICY["llama_min_memory_gib"])
_PROFILE_NAMES = tuple(name for name in ("primary", "vision_projector", "legacy_rollback") if name in MANIFEST)


def model_profile(name: str = "primary") -> dict:
    if name not in _PROFILE_NAMES:
        raise ValueError(f"Unknown model profile: {name}")
    return dict(MANIFEST[name])


def model_url(profile: dict) -> str:
    return (
        f"https://huggingface.co/{profile['repository']}/resolve/"
        f"{profile['revision']}/{profile['filename']}?download=true"
    )


def safe_context_for_ram_gib(ram_gib: float) -> int:
    for tier in HOST_POLICY["context_tiers"]:
        lower = float(tier["min_ram_gib"])
        upper = tier.get("max_ram_gib_exclusive")
        if ram_gib >= lower and (upper is None or ram_gib < float(upper)):
            return int(tier["tokens"])
    raise ValueError(
        f"Detected RAM {ram_gib:.2f} GiB is below the supported production minimum "
        f"of {MIN_DETECTED_RAM_GIB} GiB"
    )


def sha256_file(path: Path, chunk: int = 16 * 1024 * 1024) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while True:
            block = handle.read(chunk)
            if not block:
                return digest.hexdigest()
            digest.update(block)


def _download(url: str, partial: Path, *, retries: int, timeout: float, label: str) -> None:
    partial.parent.mkdir(parents=True, exist_ok=True)
    for attempt in range(1, retries + 1):
        offset = partial.stat().st_size if partial.exists() else 0
        headers = {"User-Agent": "OLYA-AI-Model-Installer/3", "Accept": "application/octet-stream"}
        if offset:
            headers["Range"] = f"bytes={offset}-"
        request = Request(url, headers=headers)
        try:
            with urlopen(request, timeout=timeout) as response:
                status = int(getattr(response, "status", 200))
                if offset and status != 206:
                    partial.unlink(missing_ok=True)
                    offset = 0
                mode = "ab" if offset and status == 206 else "wb"
                remote = response.headers.get("Content-Range") or response.headers.get("Content-Length") or ""
                with partial.open(mode) as handle:
                    last_report = time.monotonic()
                    written = offset
                    while True:
                        block = response.read(8 * 1024 * 1024)
                        if not block:
                            break
                        handle.write(block)
                        written += len(block)
                        now = time.monotonic()
                        if now - last_report >= 10:
                            print(json.dumps({"state": "downloading", "profile": label, "bytes": written, "remote": remote}), flush=True)
                            last_report = now
                    handle.flush()
                    os.fsync(handle.fileno())
                return
        except (HTTPError, URLError, TimeoutError, OSError) as exc:
            if attempt >= retries:
                raise RuntimeError(
                    f"Model download failed for {label} after {retries} attempts: {type(exc).__name__}: {exc}"
                ) from exc
            delay = min(30, 2 ** attempt)
            print(f"[X1] {label} download attempt {attempt} failed; retrying in {delay}s", file=sys.stderr, flush=True)
            time.sleep(delay)


def _verify(path: Path, *, expected_size: int, expected_sha: str) -> tuple[bool, str]:
    if not path.is_file() or path.stat().st_size != expected_size:
        return False, ""
    actual = sha256_file(path)
    return actual == expected_sha, actual


def _target_path(profile: dict, output: str = "") -> Path:
    raw = output or f"models/{profile['filename']}"
    target = Path(raw).expanduser()
    if not target.is_absolute():
        target = ROOT / target
    return target.resolve()


def ensure_profile(
    name: str,
    *,
    url_override: str = "",
    output_override: str = "",
    sha_override: str = "",
    size_override: int = 0,
    retries: int,
    timeout: float,
    verify_only: bool,
) -> bool:
    profile = model_profile(name)
    expected = (sha_override or str(profile["sha256"])).strip().lower()
    expected_size = int(size_override or profile["size_bytes"])
    url = url_override or model_url(profile)
    target = _target_path(profile, output_override)

    valid, actual = _verify(target, expected_size=expected_size, expected_sha=expected)
    if valid:
        print(json.dumps({
            "status": "ready", "profile": name, "path": str(target),
            "bytes": target.stat().st_size, "sha256": actual,
            "revision": profile["revision"], "model_name": profile["model_name"],
        }))
        return True
    if verify_only:
        state = "invalid" if target.exists() else "missing"
        print(json.dumps({
            "status": state, "profile": name, "path": str(target),
            "bytes": target.stat().st_size if target.exists() else 0,
            "sha256": actual, "expected_sha256": expected, "expected_bytes": expected_size,
        }), file=sys.stderr)
        return False
    if target.exists():
        bad = target.with_suffix(target.suffix + f".invalid-{int(time.time())}")
        target.replace(bad)
        print(f"[X1] Existing {name} artifact did not match pinned manifest; moved to {bad}", file=sys.stderr)

    partial = target.with_suffix(target.suffix + ".partial")
    if partial.exists() and partial.stat().st_size > expected_size:
        partial.unlink()
    _download(url, partial, retries=max(1, retries), timeout=max(10.0, timeout), label=name)
    if partial.stat().st_size != expected_size:
        raise RuntimeError(
            f"Downloaded {name} size mismatch: got {partial.stat().st_size}, expected {expected_size}"
        )
    actual = sha256_file(partial)
    if actual != expected:
        raise RuntimeError(f"Downloaded {name} checksum mismatch: got {actual}, expected {expected}")
    os.replace(partial, target)
    print(json.dumps({
        "status": "downloaded", "profile": name, "path": str(target),
        "bytes": target.stat().st_size, "sha256": actual,
        "revision": profile["revision"], "model_name": profile["model_name"],
    }))
    return True


def main() -> int:
    parser = argparse.ArgumentParser(description="Download and verify pinned production Qwen GGUF artifacts")
    parser.add_argument("--profile", choices=_PROFILE_NAMES, default="primary")
    parser.add_argument("--url", default="")
    parser.add_argument("--output", default="")
    parser.add_argument("--sha256", default="")
    parser.add_argument("--expected-bytes", type=int, default=0)
    parser.add_argument("--retries", type=int, default=5)
    parser.add_argument("--timeout", type=float, default=120.0)
    parser.add_argument("--verify-only", action="store_true")
    parser.add_argument("--print-manifest", action="store_true")
    args = parser.parse_args()

    profile = model_profile(args.profile)
    if args.print_manifest:
        payload = {"profile": args.profile, **profile, "host_policy": HOST_POLICY}
        if args.profile == "primary" and VISION_PROJECTOR:
            payload["required_auxiliary"] = {"vision_projector": VISION_PROJECTOR}
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 0

    custom_primary = any((args.url, args.output, args.sha256, args.expected_bytes))
    targets = [args.profile]
    # Normal production installation of the primary multimodal Qwen always pins,
    # downloads and verifies its matching vision projector. Custom artifact
    # overrides remain single-file operations so operators never receive an
    # unexpected second download.
    if args.profile == "primary" and VISION_PROJECTOR and not custom_primary:
        targets.append("vision_projector")

    all_valid = True
    for index, name in enumerate(targets):
        use_overrides = index == 0
        valid = ensure_profile(
            name,
            url_override=args.url if use_overrides else "",
            output_override=args.output if use_overrides else "",
            sha_override=args.sha256 if use_overrides else "",
            size_override=args.expected_bytes if use_overrides else 0,
            retries=args.retries,
            timeout=args.timeout,
            verify_only=args.verify_only,
        )
        all_valid = all_valid and valid
    return 0 if all_valid else 2


if __name__ == "__main__":
    raise SystemExit(main())
