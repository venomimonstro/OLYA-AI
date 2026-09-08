#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import subprocess
import time
from pathlib import Path
from urllib.error import URLError
from urllib.request import urlopen

ROOT = Path(__file__).resolve().parents[1]


def run(argv: list[str], timeout: int = 120) -> subprocess.CompletedProcess[str]:
    return subprocess.run(argv, cwd=ROOT, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=timeout, shell=False)


def wait_http(url: str, timeout: int = 120) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            with urlopen(url, timeout=3) as response:
                if 200 <= int(response.status) < 500:
                    return True
        except (URLError, OSError):
            pass
        time.sleep(2)
    return False


def restart_and_recover(service: str, health_url: str = "http://127.0.0.1:8000/health") -> dict:
    started = time.monotonic()
    restarted = run(["docker", "compose", "restart", service], 120)
    if restarted.returncode != 0:
        return {"service": service, "status": "failed", "phase": "restart", "stderr": restarted.stderr[-2000:]}
    healthy = wait_http(health_url, 180)
    return {"service": service, "status": "passed" if healthy else "failed", "phase": "recovery", "duration_seconds": round(time.monotonic() - started, 2)}


def disk_full_isolated_probe() -> dict:
    command = [
        "docker", "run", "--rm", "--network", "none", "--read-only", "--cap-drop=ALL",
        "--security-opt", "no-new-privileges", "--tmpfs", "/probe:rw,noexec,nosuid,nodev,size=4m",
        "alpine:3.22", "sh", "-c", "dd if=/dev/zero of=/probe/fill bs=1M count=16 >/dev/null 2>&1; test $? -ne 0",
    ]
    result = run(command, 60)
    return {"name": "isolated_disk_full", "status": "passed" if result.returncode == 0 else "failed", "exit_code": result.returncode, "stderr": result.stderr[-1000:]}


def main() -> int:
    parser = argparse.ArgumentParser(description="X1 Sprint 58 host-level chaos/recovery probe")
    parser.add_argument("--report", default="backups/rc-chaos-runtime-latest.json")
    parser.add_argument("--skip-llama", action="store_true")
    args = parser.parse_args()

    services = ["db", "searxng", "docker-runtime-proxy", "sandbox-worker", "document-worker"]
    if not args.skip_llama:
        services.append("llama")
    checks = [restart_and_recover(service) for service in services]
    checks.append(disk_full_isolated_probe())
    failed = [item for item in checks if item.get("status") != "passed"]
    payload = {"format": "x1-rc-chaos-v1", "status": "failed" if failed else "passed", "checks": checks}
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
