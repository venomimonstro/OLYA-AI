#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import resource
from pathlib import Path

from app.services.files import parse_content


def _limits(memory_mb: int, cpu_seconds: int) -> None:
    memory = max(256, int(memory_mb)) * 1024 * 1024
    cpu = max(5, int(cpu_seconds))
    resource.setrlimit(resource.RLIMIT_AS, (memory, memory))
    resource.setrlimit(resource.RLIMIT_CPU, (cpu, cpu + 2))
    resource.setrlimit(resource.RLIMIT_NOFILE, (64, 64))
    # The worker only reads the already-stored upload and writes JSON to stdout.
    resource.setrlimit(resource.RLIMIT_FSIZE, (16 * 1024 * 1024, 16 * 1024 * 1024))


def main() -> int:
    parser = argparse.ArgumentParser(description="Isolated X1 PDF/DOCX/text parser")
    parser.add_argument("--path", required=True)
    parser.add_argument("--filename", required=True)
    parser.add_argument("--max-pdf-pages", type=int, default=500)
    parser.add_argument("--max-docx-unpacked-bytes", type=int, default=100 * 1024 * 1024)
    parser.add_argument("--max-extracted-chars", type=int, default=2_000_000)
    parser.add_argument("--memory-mb", type=int, default=768)
    parser.add_argument("--cpu-seconds", type=int, default=30)
    args = parser.parse_args()
    _limits(args.memory_mb, args.cpu_seconds)

    path = Path(args.path).resolve()
    if not path.is_file() or path.stat().st_size > 32 * 1024 * 1024:
        raise SystemExit("invalid parser input file")
    content = path.read_bytes()
    segments = parse_content(
        args.filename,
        content,
        max_pdf_pages=max(1, args.max_pdf_pages),
        max_docx_unpacked_bytes=max(1_000_000, args.max_docx_unpacked_bytes),
    )
    total_chars = sum(len(item.text) for item in segments)
    if total_chars > max(10_000, int(args.max_extracted_chars)):
        raise SystemExit(f"extracted text exceeds limit: {total_chars}")
    print(json.dumps({
        "segments": [{"text": item.text, "page_number": item.page_number} for item in segments],
        "total_chars": total_chars,
    }, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
