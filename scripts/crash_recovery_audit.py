#!/usr/bin/env python3
from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def audit() -> dict:
    errors: list[str] = []
    checks: dict[str, bool] = {}

    health = (ROOT / "app/api/routes/health.py").read_text("utf-8")
    reliability = (ROOT / "app/workspace_reliability_v5.py").read_text("utf-8")
    crash = (ROOT / "app/workspace_crash_recovery_v1.py").read_text("utf-8")
    ui = (ROOT / "app/task_solver_user_ui.py").read_text("utf-8")

    checks["health_instance_id"] = "_INSTANCE_ID" in health and '"instance_id"' in health
    checks["startup_uses_server_identity"] = "waitForServerIdentity" in reliability
    checks["restart_clears_stale_runs"] = "clearCrashRunState" in reliability and "premiumRunStoreKey" in reliability
    checks["pending_saved_as_draft"] = "preservePendingDraft" in reliability
    checks["startup_nonblocking_recovery"] = "safeRecoverPendingStartup" in reliability
    checks["workspace_force_unlock"] = "forceUnlockWorkspace" in reliability
    checks["background_health_probe"] = "Переподключаюсь автоматически" in crash and "setInterval" in crash
    checks["premium_autorestore_disabled"] = "void restorePremiumRuns();syncRunControls();resizeComposer()" in crash
    checks["crash_layer_installed"] = "enhance_crash_recovery" in ui

    for key, ok in checks.items():
        if not ok:
            errors.append(key)

    return {
        "format": "olya-crash-recovery-audit-v1",
        "status": "passed" if not errors else "failed",
        "errors": errors,
        "checks": checks,
    }


def main() -> int:
    result = audit()
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["status"] == "passed" else 2


if __name__ == "__main__":
    raise SystemExit(main())
