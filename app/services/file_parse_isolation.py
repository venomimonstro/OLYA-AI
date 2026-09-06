from __future__ import annotations

import json
import subprocess
import sys
import threading
from pathlib import Path

from app.services.files import ParsedSegment


class FileParseError(RuntimeError):
    pass


class FileParseBusyError(FileParseError):
    pass


# Single-node X1 intentionally keeps one hostile-document parser process at a
# time. Without this boundary, a burst of 20 MB PDFs could spawn many 768 MB
# parser processes and OOM the same host that serves inference.
_PARSE_SLOT = threading.BoundedSemaphore(1)


def parse_file_isolated(
    path: Path,
    filename: str,
    *,
    max_pdf_pages: int,
    max_docx_unpacked_bytes: int,
    max_extracted_chars: int,
    timeout_seconds: int,
    memory_mb: int,
    queue_timeout_seconds: float = 5.0,
) -> list[ParsedSegment]:
    if not _PARSE_SLOT.acquire(timeout=max(0.1, float(queue_timeout_seconds))):
        raise FileParseBusyError("File parser capacity is busy; retry shortly")
    try:
        argv = [
            sys.executable, "-m", "scripts.file_parse_worker",
            "--path", str(path.resolve()),
            "--filename", filename,
            "--max-pdf-pages", str(max(1, int(max_pdf_pages))),
            "--max-docx-unpacked-bytes", str(max(1_000_000, int(max_docx_unpacked_bytes))),
            "--max-extracted-chars", str(max(10_000, int(max_extracted_chars))),
            "--memory-mb", str(max(256, int(memory_mb))),
            "--cpu-seconds", str(max(5, min(int(timeout_seconds), 120))),
        ]
        try:
            result = subprocess.run(
                argv,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                timeout=max(5, int(timeout_seconds)),
                shell=False,
                cwd=Path(__file__).resolve().parents[2],
                env={"PATH": "/usr/local/bin:/usr/bin:/bin", "PYTHONNOUSERSITE": "1"},
            )
        except subprocess.TimeoutExpired as exc:
            raise FileParseError("File parsing exceeded the safe time limit") from exc
        except OSError as exc:
            raise FileParseError("Isolated file parser could not start") from exc
        if result.returncode != 0:
            detail = (result.stderr or result.stdout or "File parser failed")[-1000:].strip()
            raise FileParseError(detail or "File parser failed")
        try:
            payload = json.loads(result.stdout)
            rows = payload.get("segments") or []
            segments = [ParsedSegment(text=str(row["text"]), page_number=row.get("page_number")) for row in rows]
        except (ValueError, TypeError, KeyError, json.JSONDecodeError) as exc:
            raise FileParseError("Isolated file parser returned invalid data") from exc
        if not segments:
            raise FileParseError("No readable text found")
        return segments
    finally:
        _PARSE_SLOT.release()
