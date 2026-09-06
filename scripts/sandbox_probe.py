#!/usr/bin/env python3
from __future__ import annotations

import json
import shutil
import uuid
from pathlib import Path

from app.core.config import get_settings
from app.services.sandbox import SandboxError, run_in_container, sandbox_capabilities


def main() -> int:
    settings = get_settings()
    data_root = Path("/app/data").resolve()
    probe_id = "_s39_probe_" + uuid.uuid4().hex[:12]
    workspace = data_root / "code_workspaces" / probe_id
    scratch = data_root / "project_runtimes" / probe_id / "sandbox"
    workspace.mkdir(parents=True, exist_ok=False)
    scratch.mkdir(parents=True, exist_ok=True)
    try:
        (workspace / "probe.py").write_text("print('X1_SANDBOX_OK')\n", "utf-8")
        caps = sandbox_capabilities(settings.project_sandbox_backend, settings.project_sandbox_image)
        if not caps.get("available"):
            raise RuntimeError(f"Sandbox unavailable: {caps}")
        result = run_in_container(
            preferred_backend=settings.project_sandbox_backend,
            image=settings.project_sandbox_image,
            workspace=workspace,
            scratch=scratch,
            argv=["python", "probe.py"],
            timeout_seconds=30,
            cpu_limit=0.5,
            memory_mb=256,
            process_limit=32,
            network_policy="deny",
        )
        if result.get("exit_code") != 0 or "X1_SANDBOX_OK" not in str(result.get("stdout") or ""):
            raise RuntimeError(f"Sandbox execution failed: {result}")

        # Generated code must not have outbound network access in closed mode.
        denied = run_in_container(
            preferred_backend=settings.project_sandbox_backend,
            image=settings.project_sandbox_image,
            workspace=workspace,
            scratch=scratch,
            argv=["python", "-c", "import socket; socket.create_connection(('1.1.1.1',53),1)"],
            timeout_seconds=10,
            cpu_limit=0.5,
            memory_mb=256,
            process_limit=32,
            network_policy="restricted",
        )
        if denied.get("exit_code") == 0:
            raise RuntimeError("Sandbox restricted network unexpectedly allowed outbound connection")

        path_escape_blocked = False
        try:
            run_in_container(
                preferred_backend=settings.project_sandbox_backend,
                image=settings.project_sandbox_image,
                workspace=Path("/tmp/x1-escape-probe"),
                scratch=scratch,
                argv=["python", "-V"],
                timeout_seconds=5,
                cpu_limit=0.5,
                memory_mb=256,
                process_limit=32,
                network_policy="deny",
            )
        except SandboxError:
            path_escape_blocked = True
        if not path_escape_blocked:
            raise RuntimeError("Sandbox path escape was not rejected")

        print(json.dumps({"status":"passed","capabilities":caps,"execution":result,"network_denied":True,"path_escape_blocked":True}, ensure_ascii=False))
        return 0
    finally:
        shutil.rmtree(workspace, ignore_errors=True)
        shutil.rmtree(data_root / "project_runtimes" / probe_id, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
