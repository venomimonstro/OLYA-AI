#!/usr/bin/env python3
from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
import tarfile
from pathlib import Path, PurePosixPath

ROOT = Path(__file__).resolve().parents[1]
ARCHIVE = ROOT / "tests" / "legacy_sprint0_26.tar.gz"
TARGET = ROOT / "tests" / "_legacy_sprint0_26"

EXPECTED = {
    "test_account_lifecycle.py", "test_admin.py", "test_auth.py", "test_benchmark_helpers.py",
    "test_business_intelligence.py", "test_chat_file_context.py", "test_chat_persistence.py",
    "test_chat_quality.py", "test_chat_research_context.py", "test_code_workspace.py", "test_context.py",
    "test_conversations.py", "test_development_chat.py", "test_development_orchestrator.py",
    "test_diagnostics.py", "test_documents.py", "test_engineering_execution.py", "test_engineering_team.py",
    "test_file_context.py", "test_files.py", "test_git_collaboration.py", "test_health.py",
    "test_image_learning.py", "test_images.py", "test_jobs.py", "test_media_admin.py", "test_memory.py",
    "test_migrations.py", "test_performance.py", "test_project_context.py", "test_project_runtime.py",
    "test_projects.py", "test_quality.py", "test_quota.py", "test_research.py", "test_research_planner.py",
    "test_research_runs.py", "test_resource_governor.py", "test_router.py", "test_safety_admin.py",
    "test_sandbox.py", "test_search_routing.py", "test_task_concurrency.py", "test_task_context.py", "test_tasks.py",
}


def restore_legacy_suite() -> None:
    if not ARCHIVE.is_file():
        raise RuntimeError(f"Historical regression archive is missing: {ARCHIVE}")
    shutil.rmtree(TARGET, ignore_errors=True)
    TARGET.mkdir(parents=True, exist_ok=True)
    found: set[str] = set()
    with tarfile.open(ARCHIVE, "r:gz") as archive:
        for member in archive.getmembers():
            raw = member.name.replace("\\", "/")
            path = PurePosixPath(raw)
            if member.isdir():
                continue
            if member.issym() or member.islnk() or member.isdev() or member.isfifo():
                raise RuntimeError(f"Unsafe historical test archive member: {raw}")
            if path.is_absolute() or ".." in path.parts:
                raise RuntimeError(f"Unsafe historical test archive path: {raw}")
            if len(path.parts) != 2 or path.parts[0] != "tests" or not path.name.startswith("test_") or path.suffix != ".py":
                raise RuntimeError(f"Unexpected historical test archive member: {raw}")
            if path.name in found:
                raise RuntimeError(f"Duplicate historical regression module: {path.name}")
            extracted = archive.extractfile(member)
            if extracted is None:
                raise RuntimeError(f"Cannot read historical test module: {raw}")
            data = extracted.read()
            if len(data) > 1_000_000:
                raise RuntimeError(f"Historical test module is unexpectedly large: {raw}")
            (TARGET / path.name).write_bytes(data)
            found.add(path.name)
    if found != EXPECTED:
        missing = sorted(EXPECTED - found)
        extra = sorted(found - EXPECTED)
        raise RuntimeError(f"Historical regression suite mismatch: missing={missing}, extra={extra}")


def main() -> int:
    parser = argparse.ArgumentParser(description="Run current and immutable Sprint 0-26 X1 regression suites")
    parser.add_argument("pytest_args", nargs="*", default=[])
    args = parser.parse_args()
    try:
        restore_legacy_suite()
        for module, extra in (
            ("scripts.canonical_source_audit", []),
            ("scripts.runtime_config_audit", []),
            ("scripts.local_image_contract_audit", []),
            ("scripts.product_surface_audit", []),
            ("scripts.api_contract_audit", []),
            ("scripts.api_console_audit", []),
            ("scripts.billing_contract_audit", []),
            ("scripts.account_plan_ux_audit", []),
            ("scripts.admin_control_center_audit", []),
            ("scripts.model_regression_lab", ["--validate-only"]),
            ("scripts.rc_security_audit", []),
        ):
            gate = subprocess.run(
                [sys.executable, "-m", module, *extra],
                cwd=ROOT,
                stdin=subprocess.DEVNULL,
                shell=False,
            )
            if gate.returncode != 0:
                return gate.returncode
        command = [sys.executable, "-m", "pytest", "-q", "tests", *args.pytest_args]
        return subprocess.run(command, cwd=ROOT, stdin=subprocess.DEVNULL, shell=False).returncode
    finally:
        shutil.rmtree(TARGET, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
