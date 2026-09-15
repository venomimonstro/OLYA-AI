#!/usr/bin/env python3
from __future__ import annotations

import argparse
import asyncio
import json
import os
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

from app.db import SessionLocal
from app.models import User
from app.services.auth import create_session
from scripts import real_user_live_simulation_100 as live
from scripts.real_user_scenarios import SCENARIOS


def _create_user(category: str) -> tuple[str, str]:
    run = uuid4().hex[:12]
    with SessionLocal() as db:
        user = User(
            email=f"olya-bench-{category}-{run}@benchmark.invalid",
            password_hash="benchmark-session-only",
            display_name=f"OLYA Benchmark · {category}",
            is_active=True,
            is_admin=False,
        )
        db.add(user)
        db.flush()
        user_id = str(user.id)
        token, _session = create_session(db, user)
        return user_id, token


def _delete_user(user_id: str) -> str:
    with SessionLocal() as db:
        user = db.get(User, user_id)
        if user is None:
            return ""
        try:
            db.delete(user)
            db.commit()
            return ""
        except Exception as exc:
            db.rollback()
            user = db.get(User, user_id)
            if user is not None:
                user.is_active = False
                db.commit()
            return f"cleanup_failed:{type(exc).__name__}:{exc}"


async def _run(args) -> dict:
    categories: list[str] = []
    for case in SCENARIOS:
        if case.category not in categories:
            categories.append(case.category)
    if args.category:
        wanted = set(args.category)
        categories = [item for item in categories if item in wanted]

    original_token = os.environ.get("OLYA_BENCH_TOKEN")
    original_email = os.environ.get("OLYA_BENCH_EMAIL")
    original_password = os.environ.get("OLYA_BENCH_PASSWORD")
    reports: list[dict] = []
    temp_users: list[str] = []
    cleanup_warnings: list[str] = []
    try:
        for category in categories:
            user_id, token = _create_user(category)
            temp_users.append(user_id)
            os.environ["OLYA_BENCH_TOKEN"] = token
            os.environ.pop("OLYA_BENCH_EMAIL", None)
            os.environ.pop("OLYA_BENCH_PASSWORD", None)
            print(f"[OLYA-100] category={category} temporary_user={user_id}", flush=True)
            report = await live._run(SimpleNamespace(
                category=[category],
                skip_longform=bool(args.skip_longform),
                limit=100,
                keep_chats=False,
            ))
            reports.append(report)
    finally:
        if original_token is None:
            os.environ.pop("OLYA_BENCH_TOKEN", None)
        else:
            os.environ["OLYA_BENCH_TOKEN"] = original_token
        if original_email is None:
            os.environ.pop("OLYA_BENCH_EMAIL", None)
        else:
            os.environ["OLYA_BENCH_EMAIL"] = original_email
        if original_password is None:
            os.environ.pop("OLYA_BENCH_PASSWORD", None)
        else:
            os.environ["OLYA_BENCH_PASSWORD"] = original_password
        for user_id in temp_users:
            warning = _delete_user(user_id)
            if warning:
                cleanup_warnings.append(f"{user_id}:{warning}")

    observations = [row for report in reports for row in report.get("observations", [])]
    surfaces = [report.get("surface", {}) for report in reports]
    issue_counts = Counter(issue.split(":", 1)[0] for row in observations for issue in row.get("issues", []))
    category_summary: dict[str, dict] = {}
    for report in reports:
        category_summary.update(report.get("category_summary", {}))
    passed = sum(1 for row in observations if row.get("ok"))
    users = len(observations)
    pass_rate = passed / max(1, users)
    surface_ok = bool(surfaces) and all(bool(item.get("passed")) for item in surfaces)
    expected_users = len([
        case for case in SCENARIOS
        if case.category in categories and not (args.skip_longform and case.category == "longform")
    ])
    status = "passed" if pass_rate >= 0.95 and surface_ok and users == expected_users else "degraded"
    return {
        "format": "olya-real-user-simulation-100-v2",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "status": status,
        "users": users,
        "expected_users": expected_users,
        "passed": passed,
        "failed": users - passed,
        "pass_rate": round(pass_rate, 3),
        "temporary_accounts": len(temp_users),
        "surface_passed": surface_ok,
        "cleanup_warnings": cleanup_warnings,
        "issue_counts": dict(issue_counts.most_common()),
        "category_summary": category_summary,
        "failures": [row for row in observations if not row.get("ok")],
        "observations": observations,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Run 100 realistic OLYA AI users across isolated temporary accounts")
    parser.add_argument("--category", action="append", default=[])
    parser.add_argument("--skip-longform", action="store_true")
    parser.add_argument("--output", default="")
    args = parser.parse_args()
    report = asyncio.run(_run(args))
    root = Path(os.getenv("X1_DATA_ROOT", "/app/data")) / "audits"
    root.mkdir(parents=True, exist_ok=True)
    output = Path(args.output) if args.output else root / "real_user_simulation_100.json"
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({
        "format": report["format"],
        "status": report["status"],
        "users": report["users"],
        "expected_users": report["expected_users"],
        "passed": report["passed"],
        "failed": report["failed"],
        "pass_rate": report["pass_rate"],
        "temporary_accounts": report["temporary_accounts"],
        "surface_passed": report["surface_passed"],
        "cleanup_warnings": report["cleanup_warnings"],
        "issue_counts": report["issue_counts"],
        "category_summary": report["category_summary"],
        "report_path": str(output),
    }, ensure_ascii=False, indent=2))
    return 0 if report["status"] == "passed" else 2


if __name__ == "__main__":
    raise SystemExit(main())
