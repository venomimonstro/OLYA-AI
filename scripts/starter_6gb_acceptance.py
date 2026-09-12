#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import subprocess
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[1]


def command(name: str, argv: list[str], timeout: int = 60) -> dict:
    try:
        result = subprocess.run(argv, cwd=ROOT, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=timeout, shell=False)
        return {"name": name, "status": "passed" if result.returncode == 0 else "failed", "exit_code": result.returncode, "stdout": result.stdout[-3000:], "stderr": result.stderr[-3000:]}
    except (OSError, subprocess.TimeoutExpired) as exc:
        return {"name": name, "status": "failed", "error": type(exc).__name__}


def current_git_head() -> str:
    result = command("git_head", ["git", "rev-parse", "HEAD"], 10)
    value = (result.get("stdout") or "").strip().lower()
    return value if result.get("status") == "passed" and len(value) == 40 else ""


def normalized_target(value: str) -> str:
    parsed = urlsplit(str(value or "").strip())
    if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username or parsed.password:
        return ""
    default_port = 443 if parsed.scheme == "https" else 80
    port = parsed.port or default_port
    authority = parsed.hostname.lower() if port == default_port else f"{parsed.hostname.lower()}:{port}"
    return f"{parsed.scheme.lower()}://{authority}{parsed.path.rstrip('/')}"


def http_json(name: str, url: str, timeout: float = 30.0) -> tuple[dict, dict | None]:
    try:
        request = Request(url, headers={"Accept": "application/json", "User-Agent": "X1-Starter-Acceptance/2"})
        with urlopen(request, timeout=timeout) as response:
            payload = json.loads(response.read(1_000_000).decode("utf-8"))
            code = int(response.status)
        return {"name": name, "status": "passed" if code == 200 else "failed", "http_status": code}, payload
    except HTTPError as exc:
        return {"name": name, "status": "failed", "http_status": exc.code}, None
    except (URLError, OSError, ValueError, json.JSONDecodeError) as exc:
        return {"name": name, "status": "failed", "error": type(exc).__name__}, None


def write_report(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", "utf-8")
    os.replace(tmp, path)


def load_evidence_check(base_url: str, fingerprint: str, head: str) -> dict:
    path = ROOT / "backups" / "load-acceptance-latest.json"
    if not path.is_file():
        return {"name": "ten_user_load_evidence", "status": "failed", "reason": "missing"}
    try:
        payload = json.loads(path.read_text("utf-8"))
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        return {"name": "ten_user_load_evidence", "status": "failed", "reason": type(exc).__name__}
    same_target = normalized_target(str(payload.get("target") or "")) == normalized_target(base_url)
    ok = (
        payload.get("format") == "x1-real-load-acceptance-v2"
        and payload.get("passed") is True
        and int(payload.get("unique_authenticated_users") or 0) >= 10
        and str(payload.get("git_head") or "").lower() == head
        and str(payload.get("source_fingerprint") or "").lower() == fingerprint
        and str(payload.get("target_build_fingerprint") or "").lower() == fingerprint
        and same_target
    )
    return {
        "name": "ten_user_load_evidence",
        "status": "passed" if ok else "failed",
        "users": int(payload.get("unique_authenticated_users") or 0),
        "same_target": same_target,
        "p95_latency_ms": int((payload.get("latency_ms") or {}).get("p95") or 0),
        "error_rate": payload.get("error_rate"),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Accept the low-cost 4 CPU / 6 GiB X1 starter runtime")
    parser.add_argument("--base-url", default=os.environ.get("X1_STARTER_BASE_URL", "http://127.0.0.1:8000"))
    parser.add_argument("--require-billing", action="store_true", help="require checkout URL + payment ingestion")
    parser.add_argument("--require-load", action="store_true", help="require current 10-user Sprint80 load evidence")
    parser.add_argument("--commercial", action="store_true", help="commercial launch: HTTPS + billing + current 10-user load evidence")
    parser.add_argument("--report", default="backups/starter-6gb-acceptance-latest.json")
    args = parser.parse_args()

    require_billing = bool(args.require_billing or args.commercial)
    require_load = bool(args.require_load or args.commercial)
    checks: list[dict] = []
    checks.append(command("starter_static_contract", ["python3", "-m", "scripts.starter_6gb_audit"]))

    head = current_git_head()
    checks.append({"name": "git_head", "status": "passed" if head else "failed", "git_head": head})

    target = normalized_target(args.base_url)
    parsed = urlsplit(target) if target else None
    local = bool(parsed and (parsed.hostname or "") in {"127.0.0.1", "localhost", "::1"})
    transport_ok = bool(target and (parsed.scheme == "https" or (parsed.scheme == "http" and local)))
    if args.commercial:
        transport_ok = bool(target and parsed.scheme == "https")
    checks.append({"name": "transport", "status": "passed" if transport_ok else "failed", "commercial": bool(args.commercial), "target": target})

    mem_kb = 0
    try:
        for line in Path("/proc/meminfo").read_text("utf-8").splitlines():
            if line.startswith("MemTotal:"):
                mem_kb = int(line.split()[1]); break
    except (OSError, ValueError):
        pass
    checks.append({"name": "host_ram", "status": "passed" if mem_kb >= 5*1024*1024 else "failed", "ram_gib": round(mem_kb/1024/1024, 2)})

    disk = command("disk_free", ["df", "-Pk", str(ROOT)])
    if disk.get("status") == "passed":
        try:
            free_kb = int((disk.get("stdout") or "").splitlines()[1].split()[3])
        except (IndexError, ValueError):
            free_kb = 0
        disk["free_gib"] = round(free_kb/1024/1024, 2); disk.pop("stdout", None)
        if free_kb < 10*1024*1024:
            disk["status"] = "failed"; disk["reason"] = "less_than_10_gib_free"
    checks.append(disk)

    services = command("core_services", ["docker", "compose", "--profile", "inference", "ps", "--status", "running", "--services"])
    if services.get("status") == "passed":
        running = {row.strip() for row in (services.get("stdout") or "").splitlines() if row.strip()}
        required = {"db", "searxng", "app", "llama"}; missing = sorted(required-running)
        services["running"] = sorted(running); services["missing"] = missing; services.pop("stdout", None)
        if missing: services["status"] = "failed"
    checks.append(services)

    checks.append(command("postgres_ready", ["docker", "compose", "exec", "-T", "db", "pg_isready", "-U", "x1", "-d", "x1"], 30))
    checks.append(command("llama_health", ["docker", "compose", "exec", "-T", "app", "python", "-c", "import urllib.request; urllib.request.urlopen('http://llama:8080/health',timeout=5).read()"], 30))
    checks.append(command("searxng_health", ["docker", "compose", "exec", "-T", "app", "python", "-c", "import urllib.request; urllib.request.urlopen('http://searxng:8080/search?q=x1&format=json',timeout=6).read()"], 30))
    checks.append(command("model_integrity", ["python3", "scripts/download_model.py", "--profile", "primary", "--verify-only"], 120))

    runtime_code = (
        "from app.core.config import get_settings; s=get_settings(); "
        "assert s.server_optimization_profile=='starter_6gb'; "
        "assert s.llama_model_name=='Qwen3-4B-Q4_K_M'; "
        "assert s.max_context_tokens<=4096 and s.deep_context_tokens<=4096; "
        "assert s.max_concurrent_generations==1 and s.max_queue_size<=16; "
        "assert s.database_pool_size<=3 and s.database_max_overflow<=1; "
        "assert s.project_sandbox_backend=='disabled'; "
        "assert s.document_render_backend=='disabled'; assert s.image_backend=='disabled'"
    )
    checks.append(command("starter_runtime_settings", ["docker", "compose", "exec", "-T", "app", "python", "-c", runtime_code], 30))

    for service in ("app", "llama", "db", "searxng"):
        cid = command(f"{service}_container_id", ["docker", "compose", "--profile", "inference", "ps", "-q", service], 15)
        container_id = (cid.get("stdout") or "").strip()
        if not container_id:
            checks.append({"name": f"{service}_oom_state", "status": "failed", "reason": "container_missing"}); continue
        oom = command(f"{service}_oom_state", ["docker", "inspect", "-f", "{{.State.OOMKilled}}", container_id], 15)
        if oom.get("status") == "passed" and "true" in (oom.get("stdout") or "").lower():
            oom["status"] = "failed"; oom["reason"] = "container_was_oom_killed"
        checks.append(oom)

    version_check, version = http_json("public_version", args.base_url.rstrip("/") + "/version")
    fingerprint = str((version or {}).get("source_fingerprint") or "").lower()
    if version_check["status"] == "passed":
        version_check["runtime_profile"] = (version or {}).get("runtime_profile")
        version_check["model"] = (version or {}).get("model")
        version_check["source_fingerprint"] = fingerprint
        if version_check["runtime_profile"] != "starter_6gb" or version_check["model"] != "Qwen3-4B-Q4_K_M" or len(fingerprint) != 64:
            version_check["status"] = "failed"
    checks.append(version_check)

    health_check, health = http_json("public_health", args.base_url.rstrip("/") + "/health")
    if health_check["status"] == "passed" and (health or {}).get("status") != "ok": health_check["status"] = "failed"
    checks.append(health_check)

    if require_billing:
        billing_code = (
            "from app.core.config import get_settings; from app.services.billing import checkout_url; "
            "s=get_settings(); assert checkout_url(s,'starter-probe') is not None; "
            "assert bool(str(s.payment_ingest_secret or '').strip())"
        )
        checks.append(command("billing_ready", ["docker", "compose", "exec", "-T", "app", "python", "-c", billing_code], 30))
    if require_load:
        checks.append(load_evidence_check(args.base_url, fingerprint, head))

    failed = [row.get("name") for row in checks if row.get("status") != "passed"]
    payload = {
        "format": "x1-starter-6gb-acceptance-v2",
        "status": "passed" if not failed else "failed",
        "accepted_for_starter_launch": not failed,
        "commercial_launch": bool(args.commercial),
        "billing_required": require_billing,
        "ten_user_load_required": require_load,
        "failed_checks": failed,
        "checks": checks,
    }
    path = Path(args.report)
    if not path.is_absolute(): path = ROOT / path
    write_report(path, payload)
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0 if payload["accepted_for_starter_launch"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
