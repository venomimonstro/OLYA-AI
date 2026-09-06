#!/usr/bin/env python3
from __future__ import annotations

import json
import tempfile
from pathlib import Path

from app.services.documents import build_docx, render_docx_to_pdf, render_qa, structural_qa


def main() -> int:
    report: dict = {"status": "failed"}
    try:
        with tempfile.TemporaryDirectory(prefix="x1-document-probe-") as tmp:
            root = Path(tmp)
            docx = root / "probe.docx"
            spec = {
                "title": "X1 document render probe",
                "blocks": [
                    {"type": "heading", "level": 1, "text": "Проверка"},
                    {"type": "paragraph", "text": "Русский текст: документ должен корректно пройти DOCX, PDF и raster QA."},
                    {"type": "table", "rows": [["Параметр", "Значение"], ["status", "stable"]]},
                ],
            }
            built = build_docx(spec, docx)
            structural = structural_qa(docx, spec)
            if structural.get("status") != "passed":
                raise RuntimeError(f"structural QA failed: {structural}")
            pdf = render_docx_to_pdf(docx, root / "render", timeout_seconds=60)
            rendered = render_qa(pdf, root / "pages", max_pages=10)
            if rendered.get("status") != "passed":
                raise RuntimeError(f"render QA failed: {rendered}")
            if rendered.get("raster_status") != "passed":
                raise RuntimeError("pdftoppm raster QA did not execute")
            report = {
                "status": "passed",
                "docx_bytes": built.get("size_bytes"),
                "pdf_bytes": pdf.stat().st_size,
                "pages": rendered.get("page_count"),
                "raster_status": rendered.get("raster_status"),
            }
    except Exception as exc:
        report = {"status": "failed", "error": f"{type(exc).__name__}: {exc}"}
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["status"] == "passed" else 2


if __name__ == "__main__":
    raise SystemExit(main())
