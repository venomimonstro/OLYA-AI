#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MODEL_REPORT = ROOT / "backups" / "model-regression-latest.json"
MODEL_BASELINE = ROOT / "backups" / "model-regression-baseline.json"
_MODEL_FINGERPRINT_FILES = (
    "model-manifest.json",
    "app/inference/client.py",
    "app/inference/router.py",
    "app/services/context.py",
    "app/services/quality.py",
    "app/services/scope_lock.py",
    "app/services/conditional_verification.py",
    "app/services/tool_reliability.py",
)


def run(name: str, argv: list[str], timeout: int) -> dict:
    try:
        result = subprocess.run(argv, cwd=ROOT, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=timeout, shell=False)
        return {"name": name, "status": "passed" if result.returncode == 0 else "failed", "exit_code": result.returncode, "stdout": result.stdout[-12000:], "stderr": result.stderr[-12000:]}
    except subprocess.TimeoutExpired as exc:
        return {"name": name, "status": "failed", "exit_code": None, "error": "timeout", "stdout": (exc.stdout or "")[-12000:] if isinstance(exc.stdout, str) else "", "stderr": (exc.stderr or "")[-12000:] if isinstance(exc.stderr, str) else ""}


def host_ram_gib() -> float:
    for line in Path("/proc/meminfo").read_text("utf-8").splitlines():
        if line.startswith("MemTotal:"):
            return int(line.split()[1]) / 1024.0 / 1024.0
    return 0.0


def current_git_head() -> str:
    try:
        result = subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True, timeout=5, shell=False)
    except (OSError, subprocess.SubprocessError):
        return ""
    value = result.stdout.strip().lower()
    return value if result.returncode == 0 and len(value) == 40 and all(char in "0123456789abcdef" for char in value) else ""


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def current_model_source_identity() -> dict:
    files: dict[str, str] = {}
    for relative in _MODEL_FINGERPRINT_FILES:
        path = ROOT / relative
        files[relative] = _sha256_file(path) if path.is_file() else "missing"
    aggregate = hashlib.sha256(json.dumps(files, sort_keys=True).encode("utf-8")).hexdigest()
    corpus = ROOT / "regression" / "golden_corpus.json"
    manifest = ROOT / "model-manifest.json"
    return {
        "template_runtime_files": files,
        "template_runtime_sha256": aggregate,
        "corpus_sha256": _sha256_file(corpus) if corpus.is_file() else "missing",
        "model_manifest_sha256": _sha256_file(manifest) if manifest.is_file() else "missing",
    }


def _report_passed(data: dict) -> tuple[bool, str]:
    if data.get("format") == "x1-model-regression-report-v1":
        comparison = data.get("comparison") or {}
        critical = (data.get("aggregate") or {}).get("critical_failed") or []
        return bool(comparison.get("passed")) and not critical, "comparison.passed + critical_failed=[]"
    return data.get("status") == "passed", "status=passed"


def require_report(path: Path, expected_format: str | None = None) -> dict:
    if not path.is_file():
        return {"status": "failed", "reason": "missing", "path": str(path)}
    try:
        data = json.loads(path.read_text("utf-8"))
    except Exception as exc:
        return {"status": "failed", "reason": type(exc).__name__, "path": str(path)}
    if expected_format and data.get("format") != expected_format:
        return {"status": "failed", "reason": "format_mismatch", "path": str(path), "format": data.get("format")}
    passed, rule = _report_passed(data)
    item = {"status": "passed" if passed else "failed", "path": str(path), "format": data.get("format"), "success_rule": rule}
    if data.get("format") == "x1-model-regression-report-v1":
        item["critical_failed"] = (data.get("aggregate") or {}).get("critical_failed") or []
        item["comparison_passed"] = bool((data.get("comparison") or {}).get("passed"))
    else:
        item["payload_status"] = data.get("status")
    return item


def verify_release_head() -> dict:
    expected = current_git_head()
    path = ROOT / "backups" / "release-gate-latest.json"
    if not expected or not path.is_file():
        return {"name": "release_evidence_current_head", "status": "failed", "expected_head": expected, "reason": "missing_head_or_report"}
    try:
        report = json.loads(path.read_text("utf-8"))
    except Exception as exc:
        return {"name": "release_evidence_current_head", "status": "failed", "expected_head": expected, "reason": type(exc).__name__}
    actual = str(report.get("git_head") or "").lower()
    return {"name": "release_evidence_current_head", "status": "passed" if actual == expected else "failed", "expected_head": expected, "report_head": actual}


def verify_model_report_current_source() -> dict:
    expected = current_model_source_identity()
    if not MODEL_REPORT.is_file():
        return {"name": "model_evidence_current_source", "status": "failed", "reason": "missing_report"}
    try:
        report = json.loads(MODEL_REPORT.read_text("utf-8"))
    except Exception as exc:
        return {"name": "model_evidence_current_source", "status": "failed", "reason": type(exc).__name__}
    snapshot = report.get("snapshot") or {}
    mismatches = [
        key for key in ("template_runtime_sha256", "corpus_sha256", "model_manifest_sha256")
        if snapshot.get(key) != expected.get(key)
    ]
    if snapshot.get("template_runtime_files") != expected.get("template_runtime_files"):
        mismatches.append("template_runtime_files")
    return {
        "name": "model_evidence_current_source",
        "status": "passed" if not mismatches else "failed",
        "mismatches": mismatches,
        "candidate_template_runtime_sha256": snapshot.get("template_runtime_sha256"),
        "current_template_runtime_sha256": expected.get("template_runtime_sha256"),
    }


def promote_model_baseline() -> dict:
    if not MODEL_REPORT.is_file():
        return {"status": "failed", "reason": "candidate_report_missing"}
    try:
        data = json.loads(MODEL_REPORT.read_text("utf-8"))
    except Exception as exc:
        return {"status": "failed", "reason": type(exc).__name__}
    passed, _ = _report_passed(data)
    if not passed:
        return {"status": "failed", "reason": "candidate_not_accepted"}
    data["baseline"] = True
    data["accepted_by"] = "x1-release-candidate-v1"
    data["accepted_at"] = datetime.now(timezone.utc).isoformat()
    MODEL_BASELINE.parent.mkdir(parents=True, exist_ok=True)
    tmp = MODEL_BASELINE.with_suffix(MODEL_BASELINE.suffix + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", "utf-8")
    os.replace(tmp, MODEL_BASELINE)
    return {"status": "passed", "path": str(MODEL_BASELINE), "accepted_at": data["accepted_at"]}


def main() -> int:
    parser = argparse.ArgumentParser(description="X1 Sprint 58 final 32-GiB release candidate gate")
    parser.add_argument("--allow-nonreference-host", action="store_true")
    parser.add_argument("--report", default="backups/rc-release-candidate-latest.json")
    parser.add_argument("--timeout", type=int, default=7200)
    args = parser.parse_args()

    checks: list[dict] = []
    ram = host_ram_gib()
    reference = 31.0 <= ram < 40.0
    checks.append({"name": "reference_32gib_host", "status": "passed" if reference or args.allow_nonreference_host else "failed", "ram_gib": round(ram, 3), "override": bool(args.allow_nonreference_host)})

    checks.append(run("static_360_security_audit", ["python3", "-m", "scripts.rc_security_audit"], 300))
    checks.append(run(
        "full_runtime_release_gate",
        ["python3", "scripts/release_gate.py", "--runtime", "--live-inference", "--user-journey", "--chaos"],
        max(1800, args.timeout),
    ))
    checks.append(run("host_runtime_chaos", ["python3", "-m", "scripts.rc_runtime_chaos"], 1800))

    evidence = [
        ("release_gate_evidence", ROOT / "backups" / "release-gate-latest.json", "x1-release-gate-v4"),
        ("model_regression_evidence", MODEL_REPORT, "x1-model-regression-report-v1"),
        ("restore_drill_evidence", ROOT / "backups" / "restore-drill-latest.json", None),
        ("runtime_chaos_evidence", ROOT / "backups" / "rc-chaos-runtime-latest.json", "x1-rc-chaos-v1"),
    ]
    for name, path, fmt in evidence:
        item = require_report(path, fmt)
        item["name"] = name
        checks.append(item)

    # Evidence must be from exactly the code/image inputs being accepted. This
    # prevents a stale green JSON from a previous checkout being promoted.
    checks.append(verify_release_head())
    checks.append(verify_model_report_current_source())

    release_path = ROOT / "backups" / "release-gate-latest.json"
    if release_path.is_file():
        try:
            release = json.loads(release_path.read_text("utf-8"))
            required_flags = {
                "component_acceptance_requested": True,
                "multi_user_load_requested": True,
                "live_inference_requested": True,
                "user_journey_requested": True,
                "chaos_requested": True,
                "capacity_calibration_requested": True,
            }
            flags_ok = all(release.get(key) is value for key, value in required_flags.items())
            checks.append({"name": "all_runtime_modes_requested", "status": "passed" if flags_ok else "failed", "flags": {key: release.get(key) for key in required_flags}})
        except Exception as exc:
            checks.append({"name": "all_runtime_modes_requested", "status": "failed", "error": type(exc).__name__})

    failed = [item.get("name") for item in checks if item.get("status") != "passed"]
    baseline_promotion = {"status": "not_run", "reason": "RC checks failed"}
    if not failed:
        baseline_promotion = promote_model_baseline()
        if baseline_promotion.get("status") != "passed":
            failed.append("model_baseline_promotion")

    payload = {
        "format": "x1-release-candidate-v2",
        "status": "passed" if not failed else "failed",
        "reference_host_ram_gib": round(ram, 3),
        "git_head": current_git_head(),
        "critical_regression_cases_required": 0,
        "model_baseline_promotion": baseline_promotion,
        "failed_checks": failed,
        "checks": checks,
    }
    path = Path(args.report)
    if not path.is_absolute():
        path = ROOT / path
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", "utf-8")
    tmp.replace(path)
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0 if payload["status"] == "passed" else 2


if __name__ == "__main__":
    raise SystemExit(main())
