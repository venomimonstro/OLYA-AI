#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from time import perf_counter
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[1]
CORPUS_PATH = ROOT / "regression" / "golden_corpus.json"
DEFAULT_BASELINE = ROOT / "backups" / "model-regression-baseline.json"
DEFAULT_REPORT = ROOT / "backups" / "model-regression-latest.json"
FINGERPRINT_FILES = (
    "model-manifest.json",
    "app/inference/client.py",
    "app/inference/router.py",
    "app/services/context.py",
    "app/services/quality.py",
    "app/services/scope_lock.py",
    "app/services/conditional_verification.py",
    "app/services/tool_reliability.py",
)


def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: Path) -> str:
    return sha256_bytes(path.read_bytes())


def git_head() -> str:
    if not (ROOT / ".git").exists():
        return ""
    try:
        result = subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True, capture_output=True, timeout=5, shell=False)
        return result.stdout.strip() if result.returncode == 0 else ""
    except OSError:
        return ""


def load_corpus(path: Path = CORPUS_PATH) -> dict[str, Any]:
    payload = json.loads(path.read_text("utf-8"))
    if payload.get("format") != "x1-model-regression-corpus-v1":
        raise ValueError("Unsupported regression corpus format")
    cases = payload.get("cases")
    if not isinstance(cases, list) or len(cases) < 8:
        raise ValueError("Regression corpus must contain at least 8 cases")
    ids: set[str] = set()
    categories: set[str] = set()
    for case in cases:
        if not isinstance(case, dict):
            raise ValueError("Corpus case must be an object")
        case_id = str(case.get("id") or "")
        category = str(case.get("category") or "")
        messages = case.get("messages")
        if not case_id or case_id in ids or not category or not isinstance(messages, list) or not messages:
            raise ValueError(f"Invalid regression case: {case_id!r}")
        ids.add(case_id); categories.add(category)
        if case.get("tools") is None and not isinstance(case.get("checks"), list):
            raise ValueError(f"Text case {case_id} has no checks")
    required = {"factual", "writing", "code", "rag", "long_chat", "tools", "scope", "russian_language"}
    missing = required - categories
    if missing:
        raise ValueError(f"Regression corpus missing categories: {sorted(missing)}")
    return payload


def runtime_snapshot(corpus: dict[str, Any]) -> dict[str, Any]:
    files: dict[str, str] = {}
    for relative in FINGERPRINT_FILES:
        path = ROOT / relative
        files[relative] = sha256_file(path) if path.is_file() else "missing"
    manifest = json.loads((ROOT / "model-manifest.json").read_text("utf-8"))
    active_profile = ROOT / "data" / "server-profile-active.json"
    profile = None
    if active_profile.is_file():
        try:
            profile = json.loads(active_profile.read_text("utf-8"))
        except (OSError, ValueError, json.JSONDecodeError):
            profile = {"status": "invalid"}
    aggregate = sha256_bytes(json.dumps(files, sort_keys=True).encode("utf-8"))
    return {
        "git_head": git_head(),
        "model": manifest.get("primary") or {},
        "model_manifest_sha256": sha256_file(ROOT / "model-manifest.json"),
        "corpus_version": corpus.get("version"),
        "corpus_sha256": sha256_file(CORPUS_PATH),
        "template_runtime_files": files,
        "template_runtime_sha256": aggregate,
        "server_profile": profile,
    }


def _sampling(reasoning: bool) -> dict[str, Any]:
    if reasoning:
        return {"temperature": 1.0, "top_p": 0.95, "top_k": 20, "min_p": 0.0, "presence_penalty": 1.5, "repeat_penalty": 1.0}
    return {"temperature": 0.7, "top_p": 0.8, "top_k": 20, "min_p": 0.0, "presence_penalty": 1.5, "repeat_penalty": 1.0}


def _base_payload(case: dict[str, Any]) -> dict[str, Any]:
    reasoning = bool(case.get("reasoning"))
    payload: dict[str, Any] = {
        "model": "local",
        "messages": case["messages"],
        "max_tokens": max(32, min(1000, int(case.get("max_tokens") or 256))),
        **_sampling(reasoning),
        "chat_template_kwargs": {"enable_thinking": reasoning},
        "reasoning_format": "deepseek" if reasoning else "none",
    }
    if not reasoning:
        payload["reasoning_effort"] = "none"
    return payload


def _content_text(value: Any) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        return "".join(str(item.get("text") or "") for item in value if isinstance(item, dict))
    return ""


def run_text_case(base_url: str, case: dict[str, Any], timeout: float) -> dict[str, Any]:
    payload = _base_payload(case); payload["stream"] = True
    request = Request(base_url.rstrip("/") + "/v1/chat/completions", data=json.dumps(payload).encode("utf-8"), headers={"Content-Type": "application/json", "Accept": "text/event-stream", "User-Agent": "X1-Regression-Lab/1"}, method="POST")
    started = perf_counter(); first_at: float | None = None; pieces: list[str] = []; output_tokens = 0
    try:
        with urlopen(request, timeout=timeout) as response:
            while True:
                raw = response.readline()
                if not raw:
                    break
                line = raw.decode("utf-8", errors="replace").strip()
                if not line or line.startswith(":"):
                    continue
                body = line[5:].strip() if line.startswith("data:") else line if line.startswith("{") else ""
                if not body or body == "[DONE]":
                    if body == "[DONE]": break
                    continue
                try: data = json.loads(body)
                except json.JSONDecodeError: continue
                usage = data.get("usage") if isinstance(data, dict) else None
                if isinstance(usage, dict) and isinstance(usage.get("completion_tokens"), int): output_tokens = max(output_tokens, int(usage["completion_tokens"]))
                choices = data.get("choices") if isinstance(data, dict) else None
                if not isinstance(choices, list) or not choices or not isinstance(choices[0], dict): continue
                delta = choices[0].get("delta") or choices[0].get("message") or {}
                text = _content_text(delta.get("content")) if isinstance(delta, dict) else ""
                if text:
                    if first_at is None: first_at = perf_counter()
                    pieces.append(text)
    except (HTTPError, URLError, OSError, TimeoutError) as exc:
        return {"error": f"{type(exc).__name__}: {exc}", "passed": False, "score": 0.0, "latency_ms": int((perf_counter()-started)*1000), "ttft_ms": None, "output_tokens": 0, "text": ""}
    finished = perf_counter(); text = "".join(pieces).strip()
    checks = evaluate_checks(text, case.get("checks") or [])
    max_chars = int(case.get("max_output_chars") or 0)
    length_ok = not max_chars or len(text) <= max_chars
    passed = bool(text) and all(item["passed"] for item in checks) and length_ok
    score_items = [1.0 if item["passed"] else 0.0 for item in checks] + ([1.0 if length_ok else 0.0] if max_chars else [])
    return {"passed": passed, "score": round(sum(score_items)/max(1, len(score_items)), 4), "latency_ms": max(0, int((finished-started)*1000)), "ttft_ms": None if first_at is None else max(0, int((first_at-started)*1000)), "output_tokens": output_tokens or max(1, len(text.split())), "text": text[:4000], "checks": checks, "length_ok": length_ok}


def run_tool_case(base_url: str, case: dict[str, Any], timeout: float) -> dict[str, Any]:
    payload = _base_payload(case); payload.update({"stream": False, "tools": case["tools"], "tool_choice": "auto", "parallel_tool_calls": False})
    request = Request(base_url.rstrip("/") + "/v1/chat/completions", data=json.dumps(payload).encode("utf-8"), headers={"Content-Type": "application/json", "Accept": "application/json", "User-Agent": "X1-Regression-Lab/1"}, method="POST")
    started = perf_counter()
    try:
        with urlopen(request, timeout=timeout) as response: data = json.loads(response.read().decode("utf-8"))
    except (HTTPError, URLError, OSError, TimeoutError, ValueError, json.JSONDecodeError) as exc:
        return {"error": f"{type(exc).__name__}: {exc}", "passed": False, "score": 0.0, "latency_ms": int((perf_counter()-started)*1000), "ttft_ms": None, "output_tokens": 0, "tool_success": False}
    latency = int((perf_counter()-started)*1000); choices = data.get("choices") or []
    message = choices[0].get("message") if choices and isinstance(choices[0], dict) else {}
    calls = message.get("tool_calls") if isinstance(message, dict) else []
    expected_name = case.get("expected_tool"); expected_args = case.get("expected_arguments") or {}; matched = False; seen: list[dict[str, Any]] = []
    if isinstance(calls, list):
        for raw in calls:
            function = raw.get("function") if isinstance(raw, dict) else None
            if not isinstance(function, dict): continue
            name = function.get("name"); arguments = function.get("arguments")
            try: parsed = json.loads(arguments) if isinstance(arguments, str) else arguments
            except json.JSONDecodeError: parsed = None
            seen.append({"name": name, "arguments": parsed})
            if name == expected_name and parsed == expected_args: matched = True
    usage = data.get("usage") if isinstance(data, dict) else {}
    tokens = int(usage.get("completion_tokens") or 0) if isinstance(usage, dict) else 0
    return {"passed": matched, "score": 1.0 if matched else 0.0, "latency_ms": max(0, latency), "ttft_ms": None, "output_tokens": tokens, "tool_success": matched, "tool_calls": seen}


def evaluate_checks(text: str, checks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    for check in checks:
        kind = str(check.get("type") or "")
        passed = False
        if kind == "contains_all": passed = all(str(value).lower() in text.lower() for value in check.get("values") or [])
        elif kind == "not_contains": passed = all(str(value).lower() not in text.lower() for value in check.get("values") or [])
        elif kind == "regex": passed = re.search(str(check.get("pattern") or ""), text.strip()) is not None
        elif kind == "json_object":
            try: obj = json.loads(text); passed = isinstance(obj, dict) and all(key in obj for key in check.get("required_keys") or [])
            except (ValueError, json.JSONDecodeError): passed = False
        elif kind == "cyrillic_ratio":
            letters = [char for char in text if char.isalpha()]
            cyr = sum(1 for char in letters if "А" <= char <= "я" or char in "Ёё")
            ratio = cyr / max(1, len(letters)); passed = ratio >= float(check.get("minimum") or 0.0)
        results.append({"type": kind, "passed": bool(passed)})
    return results


def percentile(values: list[int], q: float) -> int:
    if not values: return 0
    ordered = sorted(values); index = max(0, min(len(ordered)-1, math.ceil(q*len(ordered))-1)); return int(ordered[index])


def aggregate_results(results: list[dict[str, Any]]) -> dict[str, Any]:
    total = len(results); passed = sum(1 for row in results if row.get("passed")); critical_failed = [row["id"] for row in results if row.get("critical") and not row.get("passed")]
    ttft = [int(row["ttft_ms"]) for row in results if row.get("ttft_ms") is not None]
    latency = [int(row.get("latency_ms") or 0) for row in results]; tokens = [int(row.get("output_tokens") or 0) for row in results]
    tool_rows = [row for row in results if row.get("category") == "tools"]
    by_category: dict[str, dict[str, Any]] = {}
    for row in results:
        bucket = by_category.setdefault(row["category"], {"cases": 0, "passed": 0, "score_sum": 0.0})
        bucket["cases"] += 1; bucket["passed"] += int(bool(row.get("passed"))); bucket["score_sum"] += float(row.get("score") or 0)
    for bucket in by_category.values():
        bucket["pass_rate"] = round(bucket["passed"] / max(1, bucket["cases"]), 4); bucket["mean_score"] = round(bucket.pop("score_sum") / max(1, bucket["cases"]), 4)
    return {"cases": total, "passed": passed, "pass_rate": round(passed/max(1,total),4), "mean_score": round(sum(float(row.get("score") or 0) for row in results)/max(1,total),4), "critical_failed": critical_failed, "p95_ttft_ms": percentile(ttft,0.95), "p95_latency_ms": percentile(latency,0.95), "mean_output_tokens": round(sum(tokens)/max(1,len(tokens)),2), "tool_success_rate": round(sum(1 for row in tool_rows if row.get("tool_success"))/max(1,len(tool_rows)),4), "by_category": by_category}


def compare(candidate: dict[str, Any], baseline: dict[str, Any]) -> dict[str, Any]:
    c = candidate["aggregate"]; b = baseline.get("aggregate") or {}
    failures: list[dict[str, Any]] = []
    def fail(metric: str, actual: Any, limit: Any) -> None: failures.append({"metric": metric, "candidate": actual, "limit": limit})
    if c["critical_failed"]: fail("critical_cases", c["critical_failed"], [])
    pass_floor = max(0.90, float(b.get("pass_rate") or 0.0) - 0.03)
    if c["pass_rate"] < pass_floor: fail("pass_rate", c["pass_rate"], pass_floor)
    score_floor = max(0.90, float(b.get("mean_score") or 0.0) - 0.03)
    if c["mean_score"] < score_floor: fail("mean_score", c["mean_score"], score_floor)
    tool_floor = float(b.get("tool_success_rate") or 0.0)
    if c["tool_success_rate"] < tool_floor: fail("tool_success_rate", c["tool_success_rate"], tool_floor)
    b_ttft = int(b.get("p95_ttft_ms") or 0)
    if b_ttft:
        limit = max(b_ttft + 750, int(b_ttft * 1.35))
        if c["p95_ttft_ms"] > limit: fail("p95_ttft_ms", c["p95_ttft_ms"], limit)
    b_latency = int(b.get("p95_latency_ms") or 0)
    if b_latency:
        limit = max(b_latency + 2000, int(b_latency * 1.35))
        if c["p95_latency_ms"] > limit: fail("p95_latency_ms", c["p95_latency_ms"], limit)
    b_tokens = float(b.get("mean_output_tokens") or 0.0)
    if b_tokens:
        limit = round(b_tokens * 1.25 + 20, 2)
        if c["mean_output_tokens"] > limit: fail("mean_output_tokens", c["mean_output_tokens"], limit)
    for category, current in (c.get("by_category") or {}).items():
        old = (b.get("by_category") or {}).get(category)
        if old and float(current.get("pass_rate") or 0) + 0.20 < float(old.get("pass_rate") or 0):
            fail(f"category:{category}:pass_rate", current.get("pass_rate"), round(float(old.get("pass_rate"))-0.20,4))
    return {"passed": not failures, "failures": failures}


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True); tmp = path.with_suffix(path.suffix + ".tmp"); tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", "utf-8"); os.replace(tmp, path)


def main() -> int:
    parser = argparse.ArgumentParser(description="X1 model/prompt regression lab")
    parser.add_argument("--live-url", default="http://127.0.0.1:8080")
    parser.add_argument("--baseline", default=str(DEFAULT_BASELINE))
    parser.add_argument("--report", default=str(DEFAULT_REPORT))
    parser.add_argument("--timeout", type=float, default=180.0)
    parser.add_argument("--validate-only", action="store_true")
    parser.add_argument("--record-baseline", action="store_true")
    parser.add_argument("--force-baseline", action="store_true")
    args = parser.parse_args()
    corpus = load_corpus(); snapshot = runtime_snapshot(corpus)
    if args.validate_only:
        print(json.dumps({"status": "passed", "cases": len(corpus["cases"]), "snapshot": snapshot}, ensure_ascii=False)); return 0
    results: list[dict[str, Any]] = []
    for case in corpus["cases"]:
        result = run_tool_case(args.live_url, case, args.timeout) if case.get("tools") else run_text_case(args.live_url, case, args.timeout)
        results.append({"id": case["id"], "category": case["category"], "critical": bool(case.get("critical")), **result})
        print(json.dumps({"case": case["id"], "passed": result.get("passed"), "latency_ms": result.get("latency_ms")}, ensure_ascii=False), flush=True)
    report = {"format": "x1-model-regression-report-v1", "created_at": utcnow(), "snapshot": snapshot, "aggregate": aggregate_results(results), "results": results}
    baseline_path = Path(args.baseline)
    if args.record_baseline:
        if baseline_path.exists() and not args.force_baseline:
            print("Baseline already exists; use --force-baseline only after an explicitly accepted release", file=sys.stderr); return 4
        if report["aggregate"]["critical_failed"] or report["aggregate"]["pass_rate"] < 0.90:
            print("Candidate is not healthy enough to become baseline", file=sys.stderr); write_json(Path(args.report), report); return 5
        report["baseline"] = True; write_json(baseline_path, report); write_json(Path(args.report), report); print(json.dumps({"status": "baseline_recorded", "path": str(baseline_path), "aggregate": report["aggregate"]}, ensure_ascii=False)); return 0
    if not baseline_path.is_file():
        report["comparison"] = {"passed": False, "failures": [{"metric": "baseline", "candidate": "missing", "limit": "accepted baseline required"}]}; write_json(Path(args.report), report); print("Accepted regression baseline is missing", file=sys.stderr); return 3
    baseline = json.loads(baseline_path.read_text("utf-8")); report["baseline_snapshot"] = baseline.get("snapshot") or {}; report["comparison"] = compare(report, baseline); write_json(Path(args.report), report)
    print(json.dumps({"status": "passed" if report["comparison"]["passed"] else "failed", "aggregate": report["aggregate"], "comparison": report["comparison"]}, ensure_ascii=False))
    return 0 if report["comparison"]["passed"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
