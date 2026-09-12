#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[1]
BUILD_PROVENANCE_FORMAT = "x1-build-provenance-v1"


def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


def current_git_head() -> str:
    try:
        result = subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True, timeout=5, shell=False)
    except (OSError, subprocess.SubprocessError):
        return ""
    value = result.stdout.strip().lower()
    return value if result.returncode == 0 and len(value) == 40 and all(c in "0123456789abcdef" for c in value) else ""


def command(name: str, argv: list[str], timeout: int = 30) -> dict:
    try:
        result = subprocess.run(argv, cwd=ROOT, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=timeout, shell=False)
        return {
            "name": name,
            "status": "passed" if result.returncode == 0 else "failed",
            "exit_code": result.returncode,
            "stdout": result.stdout[-4000:],
            "stderr": result.stderr[-4000:],
        }
    except (OSError, subprocess.TimeoutExpired) as exc:
        return {"name": name, "status": "failed", "error": type(exc).__name__}


def _provenance_from_command(check: dict) -> tuple[dict, dict | None]:
    if check.get("status") != "passed":
        return check, None
    try:
        payload = json.loads(check.get("stdout") or "{}")
    except (TypeError, ValueError, json.JSONDecodeError) as exc:
        check["status"] = "failed"
        check["error"] = type(exc).__name__
        return check, None
    fingerprint = str(payload.get("source_fingerprint") or "").lower()
    valid = (
        payload.get("format") == BUILD_PROVENANCE_FORMAT
        and len(fingerprint) == 64
        and all(char in "0123456789abcdef" for char in fingerprint)
    )
    check["format"] = payload.get("format")
    check["source_fingerprint"] = fingerprint
    check["file_count"] = payload.get("file_count")
    if not valid:
        check["status"] = "failed"
        check["reason"] = "invalid_build_provenance"
        return check, None
    return check, payload


def http_json(name: str, url: str, token: str = "", timeout: float = 30.0) -> tuple[dict, dict | None]:
    headers = {"Accept": "application/json", "User-Agent": "X1-Production-Acceptance/2"}
    if token:
        headers["Authorization"] = "Bearer " + token
    try:
        request = Request(url, headers=headers)
        with urlopen(request, timeout=timeout) as response:
            body = response.read(2_000_000)
            status_code = int(response.status)
        payload = json.loads(body.decode("utf-8"))
        return {"name": name, "status": "passed" if status_code == 200 else "failed", "http_status": status_code}, payload
    except HTTPError as exc:
        return {"name": name, "status": "failed", "http_status": exc.code}, None
    except (URLError, OSError, ValueError, json.JSONDecodeError) as exc:
        return {"name": name, "status": "failed", "error": type(exc).__name__}, None


def evidence(
    name: str,
    relative: str,
    *,
    expected_format: str,
    head: str,
    pass_field: str = "status",
    source_fingerprint: str = "",
) -> dict:
    path = ROOT / relative
    if not path.is_file():
        return {"name": name, "status": "failed", "reason": "missing", "path": relative}
    try:
        payload = json.loads(path.read_text("utf-8"))
    except Exception as exc:
        return {"name": name, "status": "failed", "reason": type(exc).__name__, "path": relative}
    if payload.get("format") != expected_format:
        return {"name": name, "status": "failed", "reason": "format_mismatch", "format": payload.get("format")}
    passed = payload.get(pass_field) is True if pass_field == "passed" else payload.get(pass_field) == "passed"
    report_head = str(payload.get("git_head") or "").lower()
    same_head = bool(head and report_head == head)
    report_fingerprint = str(payload.get("source_fingerprint") or "").lower()
    same_source = True if not source_fingerprint else report_fingerprint == source_fingerprint
    return {
        "name": name,
        "status": "passed" if passed and same_head and same_source else "failed",
        "payload_passed": passed,
        "expected_head": head,
        "report_head": report_head,
        "same_head": same_head,
        "expected_source_fingerprint": source_fingerprint or None,
        "report_source_fingerprint": report_fingerprint or None,
        "same_source": same_source,
        "path": relative,
    }


def generic_evidence(name: str, relative: str) -> dict:
    path = ROOT / relative
    if not path.is_file():
        return {"name": name, "status": "failed", "reason": "missing", "path": relative}
    try:
        payload = json.loads(path.read_text("utf-8"))
    except Exception as exc:
        return {"name": name, "status": "failed", "reason": type(exc).__name__, "path": relative}
    status = payload.get("status")
    if status is None and "passed" in payload:
        status = "passed" if payload.get("passed") is True else "failed"
    return {"name": name, "status": "passed" if status == "passed" else "failed", "payload_status": status, "path": relative}


def internal_http_probe(name: str, url: str) -> dict:
    code = (
        "import urllib.request; "
        f"r=urllib.request.urlopen({url!r},timeout=10); "
        "data=r.read(200000); "
        "assert r.status==200 and data"
    )
    return command(name, ["docker", "compose", "exec", "-T", "app", "python", "-c", code], timeout=30)


def write_report(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", "utf-8")
    os.replace(tmp, path)


def main() -> int:
    parser = argparse.ArgumentParser(description="Final X1 production acceptance for the deployed candidate revision")
    parser.add_argument("--base-url", default=os.environ.get("X1_PRODUCTION_BASE_URL", "http://127.0.0.1:8000"))
    parser.add_argument("--admin-token-env", default="X1_PRODUCTION_ADMIN_TOKEN", help="environment variable containing an admin Bearer token; token is never written to reports")
    parser.add_argument("--require-images", action="store_true", help="also require image-worker and image generation/editing capabilities")
    parser.add_argument("--report", default="backups/production-acceptance-latest.json")
    args = parser.parse_args()

    head = current_git_head()
    token = os.environ.get(args.admin_token_env, "").strip()
    checks: list[dict] = []
    if not head:
        checks.append({"name": "current_git_head", "status": "failed", "reason": "git_head_unavailable"})
    else:
        checks.append({"name": "current_git_head", "status": "passed", "git_head": head})

    worktree = command("git_worktree_clean", ["git", "status", "--porcelain", "--untracked-files=all"], timeout=30)
    dirty_rows = [row for row in (worktree.get("stdout") or "").splitlines() if row.strip()]
    worktree["dirty_entry_count"] = len(dirty_rows)
    worktree.pop("stdout", None)
    if dirty_rows:
        worktree["status"] = "failed"
        worktree["reason"] = "working_tree_not_clean"
    checks.append(worktree)
    checks.append({"name": "admin_token_supplied", "status": "passed" if token else "failed"})

    parsed_target = urlsplit(args.base_url)
    local_hosts = {"127.0.0.1", "localhost", "::1"}
    transport_ok = parsed_target.scheme == "https" or (parsed_target.scheme == "http" and (parsed_target.hostname or "") in local_hosts)
    checks.append({
        "name": "production_transport",
        "status": "passed" if transport_ok else "failed",
        "scheme": parsed_target.scheme,
        "host": parsed_target.hostname,
        "policy": "HTTPS required except loopback acceptance",
    })

    candidate_provenance_check = command(
        "candidate_source_provenance",
        ["python3", "-m", "scripts.build_provenance", "--root", str(ROOT), "--no-manifest"],
        timeout=60,
    )
    candidate_provenance_check, candidate_provenance = _provenance_from_command(candidate_provenance_check)
    checks.append(candidate_provenance_check)
    candidate_fingerprint = str((candidate_provenance or {}).get("source_fingerprint") or "")

    # Candidate/evidence must be generated from exactly this immutable source revision.
    checks.append(evidence("release_candidate", "backups/rc-release-candidate-latest.json", expected_format="x1-release-candidate-v4", head=head, source_fingerprint=candidate_fingerprint))
    checks.append(evidence("target_load", "backups/load-acceptance-latest.json", expected_format="x1-real-load-acceptance-v2", head=head, pass_field="passed", source_fingerprint=candidate_fingerprint))
    checks.append(evidence("release_gate", "backups/release-gate-latest.json", expected_format="x1-release-gate-v4", head=head))
    checks.append(generic_evidence("restore_drill", "backups/restore-drill-latest.json"))
    checks.append(generic_evidence("runtime_chaos", "backups/rc-chaos-runtime-latest.json"))

    running = command("docker_services", ["docker", "compose", "--profile", "inference", "--profile", "images", "ps", "--status", "running", "--services"], timeout=30)
    if running["status"] == "passed":
        services = {line.strip() for line in running.get("stdout", "").splitlines() if line.strip()}
        required = {"db", "searxng", "app", "sandbox-worker", "document-worker", "llama"}
        if args.require_images:
            required.add("image-worker")
        missing = sorted(required - services)
        running["services"] = sorted(services)
        running["required_services"] = sorted(required)
        running["missing_services"] = missing
        if missing:
            running["status"] = "failed"
    checks.append(running)

    runtime_provenance_check = command(
        "runtime_source_provenance",
        ["docker", "compose", "exec", "-T", "app", "python", "-m", "scripts.build_provenance", "--root", "/app", "--no-manifest"],
        timeout=60,
    )
    runtime_provenance_check, runtime_provenance = _provenance_from_command(runtime_provenance_check)
    runtime_fingerprint = str((runtime_provenance or {}).get("source_fingerprint") or "")
    runtime_provenance_check["expected_source_fingerprint"] = candidate_fingerprint
    runtime_provenance_check["same_as_candidate"] = bool(candidate_fingerprint and runtime_fingerprint == candidate_fingerprint)
    if not runtime_provenance_check["same_as_candidate"]:
        runtime_provenance_check["status"] = "failed"
    checks.append(runtime_provenance_check)

    checks.append(command("postgresql_ready", ["docker", "compose", "exec", "-T", "db", "pg_isready", "-U", "x1", "-d", "x1"], timeout=30))
    checks.append(internal_http_probe("qwen_llama_health", "http://llama:8080/health"))
    checks.append(internal_http_probe("searxng_health", "http://searxng:8080/search?q=x1-production-acceptance&format=json"))
    checks.append(internal_http_probe("sandbox_worker_health", "http://sandbox-worker:8090/health"))
    checks.append(internal_http_probe("document_worker_health", "http://document-worker:8091/health"))

    billing_code = (
        "from app.core.config import get_settings; "
        "from app.services.billing import checkout_url; "
        "s=get_settings(); "
        "assert checkout_url(s,'production-acceptance-probe') is not None; "
        "assert bool(str(s.payment_ingest_secret or '').strip()); "
        "assert any(int(getattr(s,f'billing_price_{p}_minor',0))>0 for p in ('x1','pro','max','business'))"
    )
    checks.append(command("billing_runtime_config", ["docker", "compose", "exec", "-T", "app", "python", "-c", billing_code], timeout=30))

    ready_check, ready = http_json("public_ready", args.base_url.rstrip("/") + "/ready", timeout=30)
    if ready_check["status"] == "passed" and str((ready or {}).get("status") or "") != "stable":
        ready_check["status"] = "failed"
        ready_check["ready_status"] = (ready or {}).get("status")
    checks.append(ready_check)

    public_provenance_check, public_provenance = http_json("public_build_provenance", args.base_url.rstrip("/") + "/version", timeout=30)
    public_fingerprint = str((public_provenance or {}).get("source_fingerprint") or "").lower()
    public_provenance_check["format"] = (public_provenance or {}).get("format")
    public_provenance_check["source_fingerprint"] = public_fingerprint
    public_provenance_check["expected_source_fingerprint"] = candidate_fingerprint
    public_provenance_check["same_as_candidate"] = bool(candidate_fingerprint and public_fingerprint == candidate_fingerprint)
    if (
        public_provenance_check["status"] != "passed"
        or (public_provenance or {}).get("format") != BUILD_PROVENANCE_FORMAT
        or not public_provenance_check["same_as_candidate"]
    ):
        public_provenance_check["status"] = "failed"
    checks.append(public_provenance_check)

    if token:
        release_check, release = http_json("admin_release_readiness", args.base_url.rstrip("/") + "/v1/admin/reliability/release-readiness?refresh=true", token=token, timeout=180)
        if release_check["status"] == "passed":
            release_check["ready_for_public_release"] = bool((release or {}).get("ready_for_public_release"))
            release_check["blocker_count"] = len((release or {}).get("blockers") or [])
            if not release_check["ready_for_public_release"]:
                release_check["status"] = "failed"
        checks.append(release_check)

        contract_check, contract = http_json("business_logic_contract", args.base_url.rstrip("/") + "/v1/admin/reliability/business-contract", token=token, timeout=60)
        if contract_check["status"] == "passed" and (contract or {}).get("status") != "passed":
            contract_check["status"] = "failed"
            contract_check["blocker_count"] = len((contract or {}).get("blockers") or [])
        checks.append(contract_check)

        caps_check, caps = http_json("live_capability_registry", args.base_url.rstrip("/") + "/v1/admin/capabilities?live=true", token=token, timeout=60)
        required_caps = {"chat", "files", "documents", "research.search", "sandbox.execute", "development", "api", "billing"}
        if args.require_images:
            required_caps |= {"images.generate", "images.edit"}
        if caps_check["status"] == "passed":
            by_id = {row.get("id"): row for row in (caps or {}).get("capabilities") or []}
            unavailable = sorted(cap for cap in required_caps if not bool((by_id.get(cap) or {}).get("available")))
            caps_check["required_capabilities"] = sorted(required_caps)
            caps_check["unavailable_required"] = unavailable
            if unavailable:
                caps_check["status"] = "failed"
        checks.append(caps_check)

    failed = [row["name"] for row in checks if row.get("status") != "passed"]
    payload = {
        "format": "x1-production-acceptance-v2",
        "status": "passed" if not failed else "failed",
        "accepted_for_launch": not failed,
        "git_head": head,
        "source_fingerprint": candidate_fingerprint,
        "target": args.base_url,
        "require_images": bool(args.require_images),
        "checked_at": utcnow(),
        "failed_checks": failed,
        "checks": checks,
    }
    path = Path(args.report)
    if not path.is_absolute():
        path = ROOT / path
    write_report(path, payload)
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0 if payload["accepted_for_launch"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
