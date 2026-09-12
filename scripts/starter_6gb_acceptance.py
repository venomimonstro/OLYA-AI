#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import subprocess
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[1]


def command(name: str, argv: list[str], timeout: int = 60) -> dict:
    try:
        result = subprocess.run(argv, cwd=ROOT, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=timeout, shell=False)
        return {"name": name, "status": "passed" if result.returncode == 0 else "failed", "exit_code": result.returncode, "stdout": result.stdout[-3000:], "stderr": result.stderr[-3000:]}
    except (OSError, subprocess.TimeoutExpired) as exc:
        return {"name": name, "status": "failed", "error": type(exc).__name__}


def http_json(name: str, url: str, timeout: float = 30.0) -> tuple[dict, dict | None]:
    try:
        request = Request(url, headers={"Accept": "application/json", "User-Agent": "X1-Starter-Acceptance/1"})
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


def main() -> int:
    parser = argparse.ArgumentParser(description="Accept the low-cost 4 CPU / 6 GiB X1 starter runtime")
    parser.add_argument("--base-url", default=os.environ.get("X1_STARTER_BASE_URL", "http://127.0.0.1:8000"))
    parser.add_argument("--require-billing", action="store_true", help="require checkout URL + payment ingestion for commercial launch")
    parser.add_argument("--report", default="backups/starter-6gb-acceptance-latest.json")
    args = parser.parse_args()

    checks: list[dict] = []
    checks.append(command("starter_static_contract", ["python3", "-m", "scripts.starter_6gb_audit"]))

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
        disk["free_gib"] = round(free_kb/1024/1024, 2)
        disk.pop("stdout", None)
        if free_kb < 10*1024*1024:
            disk["status"] = "failed"; disk["reason"] = "less_than_10_gib_free"
    checks.append(disk)

    services = command("core_services", ["docker", "compose", "--profile", "inference", "ps", "--status", "running", "--services"])
    if services.get("status") == "passed":
        running = {row.strip() for row in (services.get("stdout") or "").splitlines() if row.strip()}
        required = {"db", "searxng", "app", "llama"}
        missing = sorted(required-running)
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
        "assert s.document_render_backend=='disabled'; "
        "assert s.image_backend=='disabled'"
    )
    checks.append(command("starter_runtime_settings", ["docker", "compose", "exec", "-T", "app", "python", "-c", runtime_code], 30))

    oom = command("core_oom_state", ["docker", "inspect", "-f", "{{.Name}}={{.State.OOMKilled}}", "$(docker compose ps -q app)"])
    # docker inspect does not expand command substitutions with shell=False; use
    # a safe Python/docker-compose probe instead of invoking a shell.
    if oom.get("status") != "passed":
        app_id = command("app_container_id", ["docker", "compose", "ps", "-q", "app"], 15)
        container_id = (app_id.get("stdout") or "").strip()
        oom = command("app_oom_state", ["docker", "inspect", "-f", "{{.State.OOMKilled}}", container_id], 15) if container_id else {"name": "app_oom_state", "status": "failed", "reason": "app_container_missing"}
    if oom.get("status") == "passed" and "true" in (oom.get("stdout") or "").lower():
        oom["status"] = "failed"; oom["reason"] = "app_was_oom_killed"
    checks.append(oom)

    version_check, version = http_json("public_version", args.base_url.rstrip("/") + "/version")
    if version_check["status"] == "passed":
        version_check["runtime_profile"] = (version or {}).get("runtime_profile")
        version_check["model"] = (version or {}).get("model")
        if version_check["runtime_profile"] != "starter_6gb" or version_check["model"] != "Qwen3-4B-Q4_K_M":
            version_check["status"] = "failed"
    checks.append(version_check)

    health_check, health = http_json("public_health", args.base_url.rstrip("/") + "/health")
    if health_check["status"] == "passed" and (health or {}).get("status") != "ok": health_check["status"] = "failed"
    checks.append(health_check)

    if args.require_billing:
        billing_code = (
            "from app.core.config import get_settings; from app.services.billing import checkout_url; "
            "s=get_settings(); assert checkout_url(s,'starter-probe') is not None; "
            "assert bool(str(s.payment_ingest_secret or '').strip())"
        )
        checks.append(command("billing_ready", ["docker", "compose", "exec", "-T", "app", "python", "-c", billing_code], 30))

    failed = [row.get("name") for row in checks if row.get("status") != "passed"]
    payload = {
        "format": "x1-starter-6gb-acceptance-v1",
        "status": "passed" if not failed else "failed",
        "accepted_for_starter_launch": not failed,
        "commercial_billing_required": bool(args.require_billing),
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
