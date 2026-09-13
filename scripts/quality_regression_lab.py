#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from scripts.model_regression_lab import aggregate_results, run_text_case


ROOT = Path(__file__).resolve().parents[1]
CORPUS_PATH = ROOT / "regression" / "quality_corpus.json"
DEFAULT_REPORT = ROOT / "backups" / "quality-regression-latest.json"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def load_quality_corpus() -> dict[str, Any]:
    payload = json.loads(CORPUS_PATH.read_text("utf-8"))
    if payload.get("format") != "x1-quality-regression-corpus-v1":
        raise ValueError("Unsupported quality regression corpus format")
    cases = payload.get("cases")
    if not isinstance(cases, list) or len(cases) < 8:
        raise ValueError("Quality corpus must contain at least 8 cases")
    ids: set[str] = set()
    categories: set[str] = set()
    for case in cases:
        if not isinstance(case, dict):
            raise ValueError("Quality case must be an object")
        case_id = str(case.get("id") or "")
        category = str(case.get("category") or "")
        messages = case.get("messages")
        checks = case.get("checks")
        if not case_id or case_id in ids:
            raise ValueError(f"Duplicate/invalid quality case id: {case_id!r}")
        if not category or not isinstance(messages, list) or not messages or not isinstance(checks, list) or not checks:
            raise ValueError(f"Invalid quality case: {case_id}")
        ids.add(case_id)
        categories.add(category)
    required = {"writing", "rag", "factual", "russian_language", "scope"}
    missing = required - categories
    if missing:
        raise ValueError(f"Quality corpus missing categories: {sorted(missing)}")
    return payload


def _write_report(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", "utf-8")
    os.replace(tmp, path)


def main() -> int:
    parser = argparse.ArgumentParser(description="X1 user-visible answer quality regression lab")
    parser.add_argument("--live-url", default="http://127.0.0.1:8080")
    parser.add_argument("--timeout", type=float, default=180.0)
    parser.add_argument("--report", default=str(DEFAULT_REPORT))
    parser.add_argument("--validate-only", action="store_true")
    args = parser.parse_args()

    corpus = load_quality_corpus()
    if args.validate_only:
        print(json.dumps({"status": "passed", "cases": len(corpus["cases"]), "version": corpus.get("version")}, ensure_ascii=False))
        return 0

    results: list[dict[str, Any]] = []
    for case in corpus["cases"]:
        result = run_text_case(args.live_url, case, args.timeout)
        row = {"id": case["id"], "category": case["category"], "critical": bool(case.get("critical")), **result}
        results.append(row)
        print(json.dumps({"case": case["id"], "passed": result.get("passed"), "score": result.get("score")}, ensure_ascii=False), flush=True)

    aggregate = aggregate_results(results)
    passed = not aggregate["critical_failed"] and aggregate["pass_rate"] >= 0.875 and aggregate["mean_score"] >= 0.90
    report = {
        "format": "x1-quality-regression-report-v1",
        "created_at": _now(),
        "corpus_version": corpus.get("version"),
        "aggregate": aggregate,
        "passed": passed,
        "results": results,
    }
    _write_report(Path(args.report), report)
    print(json.dumps({"status": "passed" if passed else "failed", "aggregate": aggregate}, ensure_ascii=False))
    return 0 if passed else 2


if __name__ == "__main__":
    raise SystemExit(main())
