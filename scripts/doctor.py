#!/usr/bin/env python3
from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import urlopen

ROOT = Path(__file__).resolve().parents[1]
MODEL = ROOT / "models" / "Qwen3-30B-A3B-Q4_K_M.gguf"


def result(name: str, status: str, detail: str) -> dict:
    return {"check": name, "status": status, "detail": detail}


def cmd(args: list[str], timeout: int = 30):
    try:
        return subprocess.run(args, cwd=ROOT, text=True, capture_output=True, timeout=timeout, stdin=subprocess.DEVNULL, shell=False)
    except Exception:
        return None


def main() -> int:
    checks=[result("python","stable" if sys.version_info >= (3,12) else "failed",sys.version.split()[0]),result("docker","stable" if shutil.which("docker") else "failed","installed" if shutil.which("docker") else "missing")]
    env=ROOT/".env"
    if not env.exists(): checks.append(result("env","failed",".env missing")); env_text=""
    else:
        env_text=env.read_text("utf-8",errors="replace")
        unsafe=[marker for marker in ("X1_ADMIN_BOOTSTRAP_TOKEN=change-me","X1_PROJECT_RUNTIME_SECRET_KEY=change-me-runtime-secret","X1_PROJECT_SANDBOX_WORKER_TOKEN=change-me-sandbox-worker","POSTGRES_PASSWORD=change-me-db","POSTGRES_PASSWORD=x1-dev-only","x1:change-me-db@db:5432/x1") if marker in env_text]
        checks.append(result("secrets","failed" if unsafe else "stable","unsafe defaults: "+",".join(unsafe) if unsafe else "non-default"))
        checks.append(result("environment","stable" if "X1_ENV=production" in env_text else "degraded","production" if "X1_ENV=production" in env_text else "X1_ENV is not production"))

    inference_expected="X1_LLAMA_BASE_URL=http://llama:8080" in env_text
    if inference_expected:
        checks.append(result("qwen_model","stable" if MODEL.is_file() and MODEL.stat().st_size>17_000_000_000 else "failed",f"{MODEL} ({MODEL.stat().st_size if MODEL.exists() else 0} bytes)"))

    if shutil.which("docker"):
        compose=cmd(["docker","compose","config"])
        if compose and compose.returncode==0:
            checks.append(result("compose","stable","valid")); rendered=compose.stdout
            persistent="/app/data" in rendered and str((ROOT/"data").resolve()) in rendered
            checks.append(result("app_data_volume","stable" if persistent else "failed","host bind /app/data configured" if persistent else "persistent host /app/data bind missing"))
            socket_scope="sandbox-worker:" in rendered and "/var/run/docker.sock" in rendered
            app_section=rendered.split("\n  app:\n",1)[1].split("\n  ",1)[0] if "\n  app:\n" in rendered else ""
            checks.append(result("sandbox_privilege_boundary","stable" if socket_scope and "/var/run/docker.sock" not in app_section else "failed","Docker socket isolated to sandbox worker" if socket_scope else "sandbox worker socket boundary missing"))
        else: checks.append(result("compose","failed",((compose.stderr if compose else "cannot run") or "invalid")[-800:]))

        migration=cmd(["docker","compose","exec","-T","app","alembic","current"])
        checks.append(result("migrations","stable" if migration and migration.returncode==0 else "degraded",((migration.stdout+migration.stderr) if migration else "app not running")[-800:]))
        data_probe=cmd(["docker","compose","exec","-T","app","python","-c","from pathlib import Path; p=Path('/app/data'); p.mkdir(parents=True,exist_ok=True); assert p.is_dir() and p.exists()"])
        checks.append(result("app_data_runtime","stable" if data_probe and data_probe.returncode==0 else "failed","writable persistent data path" if data_probe and data_probe.returncode==0 else "app data path unavailable"))
        search_probe=cmd(["docker","compose","exec","-T","searxng","sh","-c","wget -q -O /dev/null 'http://127.0.0.1:8080/search?q=x1-doctor&format=json'"])
        checks.append(result("internet_search","stable" if search_probe and search_probe.returncode==0 else "failed","private SearXNG JSON search reachable" if search_probe and search_probe.returncode==0 else "SearXNG unavailable"))
        sandbox_probe=cmd(["docker","compose","exec","-T","app","python","-m","scripts.sandbox_probe"],timeout=90)
        checks.append(result("closed_sandbox","stable" if sandbox_probe and sandbox_probe.returncode==0 else "failed",((sandbox_probe.stdout+sandbox_probe.stderr) if sandbox_probe else "sandbox probe unavailable")[-1200:]))

    try:
        with urlopen("http://127.0.0.1:8000/ready",timeout=8) as response:
            body=json.loads(response.read().decode()); state="stable" if body.get("status")=="stable" else "degraded"; checks.append(result("ready",state,json.dumps(body,ensure_ascii=False)[:3000]))
    except HTTPError as exc:
        try: detail=exc.read().decode("utf-8",errors="replace")
        except Exception: detail=""
        checks.append(result("ready","degraded",f"HTTP {exc.code}: {detail[:2000]}"))
    except Exception as exc: checks.append(result("ready","degraded",type(exc).__name__))

    backups=ROOT/"backups"; snapshots=sorted((path for path in backups.glob("*") if path.is_dir() and ".partial." not in path.name),reverse=True) if backups.exists() else []
    if snapshots:
        latest=snapshots[0]; checks.append(result("backup","stable" if (latest/"SHA256SUMS").is_file() else "failed",str(latest)))
    else: checks.append(result("backup","degraded","no host-visible backup snapshot found"))

    overall="failed" if any(item["status"]=="failed" for item in checks) else "degraded" if any(item["status"]=="degraded" for item in checks) else "stable"
    print(json.dumps({"status":overall,"checks":checks},ensure_ascii=False,indent=2)); return 2 if overall=="failed" else 1 if overall=="degraded" else 0


if __name__=="__main__": raise SystemExit(main())
