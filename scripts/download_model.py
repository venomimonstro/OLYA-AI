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

DEFAULT_REPO = "Qwen/Qwen3-30B-A3B-GGUF"
# Immutable upstream revision whose Q4_K_M file has the verified SHA below.
DEFAULT_REVISION = "dae61f032d7880e6effe712505db4de5d06d6549"
DEFAULT_FILE = "Qwen3-30B-A3B-Q4_K_M.gguf"
DEFAULT_SHA256 = "0d003f6662faee786ed5da3e31b29c978de5ae5d275c8794c606a7f3c01aa8f5"
DEFAULT_SIZE = 18_556_685_824
DEFAULT_URL = f"https://huggingface.co/{DEFAULT_REPO}/resolve/{DEFAULT_REVISION}/{DEFAULT_FILE}?download=true"


def sha256_file(path: Path, chunk: int = 16 * 1024 * 1024) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while True:
            block = handle.read(chunk)
            if not block:
                return digest.hexdigest()
            digest.update(block)


def _download(url: str, partial: Path, *, retries: int, timeout: float) -> None:
    partial.parent.mkdir(parents=True, exist_ok=True)
    for attempt in range(1, retries + 1):
        offset = partial.stat().st_size if partial.exists() else 0
        headers = {"User-Agent": "X1-Model-Installer/1", "Accept": "application/octet-stream"}
        if offset:
            headers["Range"] = f"bytes={offset}-"
        request = Request(url, headers=headers)
        try:
            with urlopen(request, timeout=timeout) as response:
                status = int(getattr(response, "status", 200))
                if offset and status != 206:
                    # The origin ignored Range; restart instead of corrupting the file.
                    partial.unlink(missing_ok=True)
                    offset = 0
                mode = "ab" if offset and status == 206 else "wb"
                total_header = response.headers.get("Content-Range") or response.headers.get("Content-Length") or ""
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
                            print(json.dumps({"state": "downloading", "bytes": written, "remote": total_header}), flush=True)
                            last_report = now
                    handle.flush()
                    os.fsync(handle.fileno())
                return
        except (HTTPError, URLError, TimeoutError, OSError) as exc:
            if attempt >= retries:
                raise RuntimeError(f"Model download failed after {retries} attempts: {type(exc).__name__}: {exc}") from exc
            delay = min(30, 2 ** attempt)
            print(f"[X1] model download attempt {attempt} failed; retrying in {delay}s", file=sys.stderr, flush=True)
            time.sleep(delay)


def main() -> int:
    parser = argparse.ArgumentParser(description="Download and verify the production Qwen GGUF model")
    parser.add_argument("--url", default=DEFAULT_URL)
    parser.add_argument("--output", default=f"models/{DEFAULT_FILE}")
    parser.add_argument("--sha256", default=DEFAULT_SHA256)
    parser.add_argument("--min-bytes", type=int, default=DEFAULT_SIZE)
    parser.add_argument("--retries", type=int, default=5)
    parser.add_argument("--timeout", type=float, default=120.0)
    parser.add_argument("--verify-only", action="store_true")
    args = parser.parse_args()

    target = Path(args.output).expanduser().resolve()
    expected = args.sha256.strip().lower()
    if target.is_file() and target.stat().st_size >= args.min_bytes:
        actual = sha256_file(target)
        if actual == expected:
            print(json.dumps({"status": "ready", "path": str(target), "bytes": target.stat().st_size, "sha256": actual, "revision": DEFAULT_REVISION}))
            return 0
        if args.verify_only:
            print(json.dumps({"status": "invalid", "path": str(target), "sha256": actual, "expected": expected}), file=sys.stderr)
            return 2
        bad = target.with_suffix(target.suffix + f".invalid-{int(time.time())}")
        target.replace(bad)
        print(f"[X1] Existing GGUF checksum mismatch; moved to {bad}", file=sys.stderr)
    elif args.verify_only:
        print(json.dumps({"status": "missing", "path": str(target)}), file=sys.stderr)
        return 2

    partial = target.with_suffix(target.suffix + ".partial")
    _download(args.url, partial, retries=max(1, args.retries), timeout=max(10.0, args.timeout))
    if partial.stat().st_size < args.min_bytes:
        raise RuntimeError(f"Downloaded model is unexpectedly small: {partial.stat().st_size} bytes")
    actual = sha256_file(partial)
    if actual != expected:
        raise RuntimeError(f"Downloaded model checksum mismatch: got {actual}, expected {expected}")
    os.replace(partial, target)
    print(json.dumps({"status": "downloaded", "path": str(target), "bytes": target.stat().st_size, "sha256": actual, "revision": DEFAULT_REVISION}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
