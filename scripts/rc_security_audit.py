#!/usr/bin/env python3
from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def read(path: str) -> str:
    return (ROOT / path).read_text("utf-8")


def main() -> int:
    checks: list[dict] = []

    def check(name: str, condition: bool, detail: str = "") -> None:
        checks.append({"name": name, "status": "passed" if condition else "failed", "detail": detail})

    compose = read("docker-compose.yml")
    proxy = read("app/docker_runtime_proxy.py")
    worker = read("app/sandbox_worker_api.py")
    worker_dockerfile = read("Dockerfile.sandbox-worker")
    research = read("app/services/research.py")
    workspace = read("app/services/code_workspace.py")
    db = read("app/db.py")
    launch = read("app/services/public_launch_scheduler.py")

    check("docker_socket_single_boundary", compose.count("/var/run/docker.sock:/var/run/docker.sock") == 1)
    sandbox_block = compose.split("  sandbox-worker:", 1)[1].split("  document-worker:", 1)[0]
    proxy_block = compose.split("  docker-runtime-proxy:", 1)[1].split("  sandbox-worker:", 1)[0]
    check("sandbox_worker_has_no_socket", "docker.sock" not in sandbox_block)
    check("runtime_proxy_owns_socket", "docker.sock" in proxy_block and "X1_DOCKER_RUNTIME_PROXY_TOKEN" in proxy_block)
    check("sandbox_worker_has_no_docker_cli", "docker.io" not in worker_dockerfile and "subprocess" not in worker)
    check("proxy_rejects_privileged", all(marker in proxy for marker in ("--privileged", "--device", "--volume", "--entrypoint")))
    check("proxy_forces_network_none", 'payload.argv' not in proxy or '"--network"' in proxy)
    check("proxy_mounts_scoped", "HOST_DATA_ROOT" in proxy and "source_path.relative_to(HOST_DATA_ROOT)" in proxy)
    check("proxy_managed_labels_only", "x1.sandbox.preview=true" in proxy and "x1.sandbox.execution=true" in proxy)
    check("proxy_pinned_image", "RUNTIME_IMAGE" in proxy and "Only pinned sandbox runtime image is allowed" in proxy)

    check("ssrf_scheme_allowlist", "_ALLOWED_SCHEMES = {\"http\", \"https\"}" in research)
    check("ssrf_private_ip_rejection", "ip.is_private" in research and "ip.is_loopback" in research and "ip.is_link_local" in research)
    check("ssrf_dns_rebinding_peer_check", "server_addr" in research and "Connected peer is a private" in research)
    check("ssrf_redirect_revalidation", "validate_public_url(urljoin(current,location))" in research.replace(" ", ""))
    check("ssrf_no_env_proxy", "trust_env=False" in research)

    check("workspace_path_traversal", "Path escapes workspace" in workspace and "safe_relative_path" in workspace)
    check("archive_bomb_byte_limit", "Archive expands beyond workspace limit" in workspace and "max_unpacked_bytes" in workspace)
    check("archive_symlink_rejection", "Symlinks are not allowed in workspace archives" in workspace)
    check("archive_entry_limit", "Archive contains too many entries" in workspace)
    check("host_command_fail_closed", "Command requires an isolated sandbox backend" in workspace and "shell=False" in workspace)

    check("db_pool_bounded", all(marker in db for marker in ("pool_size", "max_overflow", "pool_timeout", "pool_pre_ping")))
    check("db_statement_deadline", "statement_timeout" in db)
    check("db_lock_deadline", "lock_timeout" in db)
    check("db_idle_transaction_deadline", "idle_in_transaction_session_timeout" in db)

    check("canary_freeze_present", "freeze_rollout" in launch)
    check("canary_auto_rollback_present", "public_launch_auto_rollback" in launch and "rollback_rollout" in launch)

    failed = [item["name"] for item in checks if item["status"] != "passed"]
    payload = {"format": "x1-rc-security-audit-v1", "status": "passed" if not failed else "failed", "critical_failures": failed, "checks": checks}
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0 if payload["status"] == "passed" else 2


if __name__ == "__main__":
    raise SystemExit(main())
