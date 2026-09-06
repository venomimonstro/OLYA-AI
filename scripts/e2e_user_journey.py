#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import secrets
import sys
import time
from dataclasses import dataclass, asdict
from typing import Any

import httpx
from sqlalchemy import select

from app.db import SessionLocal
from app.models import BetaParticipant, User, UserQuota


@dataclass
class Step:
    name: str
    status: str
    detail: dict[str, Any]
    duration_ms: int


def _now_ms() -> int:
    return int(time.monotonic() * 1000)


class Journey:
    def __init__(self, base_url: str, timeout: float) -> None:
        self.base_url = base_url.rstrip("/")
        self.client = httpx.Client(base_url=self.base_url, timeout=timeout, trust_env=False)
        self.token = ""
        self.user_id = ""
        self.steps: list[Step] = []

    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.token}"} if self.token else {}

    def call(self, name: str, method: str, path: str, *, payload: dict | None = None, expect: tuple[int, ...] = (200,)) -> Any:
        started = _now_ms()
        response = self.client.request(method, path, json=payload, headers=self._headers())
        elapsed = _now_ms() - started
        detail: dict[str, Any] = {"http_status": response.status_code, "path": path}
        try:
            body = response.json() if response.content else None
        except ValueError:
            body = response.text[-2000:]
        if response.status_code not in expect:
            detail["body"] = body
            self.steps.append(Step(name, "failed", detail, elapsed))
            raise RuntimeError(f"{name} failed: HTTP {response.status_code}: {body}")
        if isinstance(body, dict):
            detail.update({key: body.get(key) for key in ("status", "id", "action", "model") if key in body})
        self.steps.append(Step(name, "passed", detail, elapsed))
        return body

    def record(self, name: str, passed: bool, detail: dict[str, Any], started_ms: int) -> None:
        self.steps.append(Step(name, "passed" if passed else "failed", detail, _now_ms() - started_ms))
        if not passed:
            raise RuntimeError(f"{name} failed: {detail}")


def _grant_probe_access(user_id: str) -> None:
    """Allow the synthetic user through rollout gates without giving it admin rights."""
    with SessionLocal() as db:
        user = db.get(User, user_id)
        if user is None:
            raise RuntimeError("Synthetic journey user disappeared")
        quota = db.get(UserQuota, user.id)
        if quota is None:
            quota = UserQuota(user_id=user.id)
            db.add(quota)
        quota.plan = "max"
        quota.monthly_compute_seconds_limit = 28_800
        quota.max_concurrent_inference = 1
        quota.max_concurrent_jobs = 8
        participant = db.scalar(select(BetaParticipant).where(BetaParticipant.user_id == user.id, BetaParticipant.cohort == "system-probe"))
        if participant is None:
            db.add(BetaParticipant(user_id=user.id, cohort="system-probe", state="active", source="release_gate", metadata_json={"synthetic": True}))
        db.commit()


def _disable_probe_user(user_id: str) -> None:
    if not user_id:
        return
    with SessionLocal() as db:
        user = db.get(User, user_id)
        if user is not None:
            user.is_active = False
            db.commit()


def main() -> int:
    parser = argparse.ArgumentParser(description="X1 production end-to-end user journey")
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument("--timeout", type=float, default=300.0)
    parser.add_argument("--development-steps", type=int, default=24)
    parser.add_argument("--report", default="/app/backups/e2e-user-journey-latest.json")
    args = parser.parse_args()

    journey = Journey(args.base_url, args.timeout)
    password = "X1!" + secrets.token_urlsafe(22)
    email = f"x1-s39-{secrets.token_hex(8)}@example.invalid"
    overall = "failed"
    error = ""
    project_id = workspace_id = runtime_id = plan_id = ""
    development_completed = False

    try:
        health = journey.call("health", "GET", "/health")
        if isinstance(health, dict) and health.get("status") not in {"stable", "degraded", "ok"}:
            raise RuntimeError(f"Unexpected health state: {health}")

        auth = journey.call("register", "POST", "/v1/auth/register", payload={"email": email, "password": password, "display_name": "X1 System Journey"}, expect=(201,))
        journey.token = str(auth["access_token"]); journey.user_id = str(auth["user_id"])
        _grant_probe_access(journey.user_id)
        me = journey.call("authenticated_profile", "GET", "/v1/auth/me")
        if me.get("email") != email:
            raise RuntimeError("Authenticated profile does not match registered user")

        # Deterministic correctness case: simple math plus an explicit acceptance
        # criterion must survive generation + quality verification.
        answer = journey.call("verified_question", "POST", "/v1/chat", payload={
            "messages": [{"role": "user", "content": "Вычисли 17 * 19. Ответь кратко и обязательно укажи число 323."}],
            "mode": "fast", "verification": "strict",
            "requirements": [{"kind": "contains", "value": "323", "label": "correct arithmetic result"}],
            "max_output_tokens": 256,
        })
        started = _now_ms(); quality = (answer.get("quality") or {}).get("status")
        journey.record("answer_correctness_gate", "323" in str(answer.get("text") or "") and quality != "failed", {"quality": quality, "text_tail": str(answer.get("text") or "")[-300:]}, started)

        # A changing fact without fresh evidence must never be represented as a
        # fully supported answer. This catches a common hallucination UX failure.
        changing = journey.call("freshness_without_sources", "POST", "/v1/chat", payload={
            "messages": [{"role": "user", "content": "Какая прямо сейчас текущая цена биткоина в долларах? Укажи точное актуальное значение."}],
            "mode": "fast", "verification": "auto", "max_output_tokens": 320,
        })
        started = _now_ms(); changing_status = (changing.get("quality") or {}).get("status")
        journey.record("freshness_fail_closed", changing_status != "supported", {"quality": changing_status}, started)

        research = journey.call("research_plan", "POST", "/v1/research/runs", payload={
            "question": "официальная документация Python asyncio create_task",
            "intent": "general", "max_results": 10,
        }, expect=(201,))
        run_id = research["id"]
        discovered = journey.call("internet_search", "POST", f"/v1/research/runs/{run_id}/discover")
        hits = list(discovered.get("hits") or [])
        started = _now_ms()
        journey.record("internet_search_results", bool(hits), {"hits": len(hits), "providers": sorted({str(x.get('provider')) for x in hits})}, started)
        collected = journey.call("research_collect", "POST", f"/v1/research/runs/{run_id}/collect", payload={"max_sources": 2})
        sources = list(collected.get("sources") or [])
        started = _now_ms(); journey.record("research_sources_available", bool(sources), {"sources": len(sources), "failed": collected.get("failed_urls") or []}, started)
        source_ids = [x["id"] for x in sources[:2]]
        grounded = journey.call("grounded_answer", "POST", "/v1/chat", payload={
            "messages": [{"role": "user", "content": "По найденным источникам кратко объясни назначение asyncio.create_task. Не добавляй неподтверждённых URL."}],
            "mode": "work", "verification": "strict", "research_source_ids": source_ids, "max_output_tokens": 700,
        })
        started = _now_ms(); grounded_quality = (grounded.get("quality") or {}).get("status")
        journey.record("grounded_quality", grounded_quality not in {"failed", None}, {"quality": grounded_quality}, started)

        project = journey.call("project_create", "POST", "/v1/projects", payload={
            "name": "X1 Release-Gate Internet Shop",
            "description": "Synthetic closed-contour project used only by the production release gate.",
            "instructions": "No external services. Keep implementation small, testable and offline inside the sandbox.",
        }, expect=(201,))
        project_id = project["id"]
        workspace = journey.call("workspace_create", "POST", "/v1/code/workspaces", payload={"name": "internet-shop", "project_id": project_id}, expect=(201,))
        workspace_id = workspace["id"]
        runtime = journey.call("runtime_create", "POST", "/v1/project-runtimes", payload={
            "workspace_id": workspace_id, "cpu_limit": 1.0, "memory_limit_mb": 768,
            "disk_limit_mb": 1536, "process_limit": 64, "network_policy": "deny",
        }, expect=(201,))
        runtime_id = runtime["id"]
        caps = journey.call("sandbox_capabilities", "GET", "/v1/project-sandboxes/capabilities")
        started = _now_ms(); journey.record("closed_sandbox_available", bool(caps.get("available")) and bool(caps.get("network_isolation")), caps, started)

        architect = journey.call("shop_architect", "POST", "/v1/development-plans/architect-draft", payload={
            "project_id": project_id,
            "runtime_id": runtime_id,
            "product_brief": (
                "Создай минимальный, но рабочий интернет-магазин для проверки X1. FastAPI + Jinja2 + SQLite. "
                "Нужны каталог товаров, карточка товара, корзина в сессии, оформление тестового заказа без реального платежа, "
                "health endpoint и pytest. Проект обязан работать полностью офлайн в закрытом sandbox, не использовать CDN/API, "
                "не требовать npm install/pip install во время выполнения. Один короткий спринт, один-два work item, чёткие acceptance tests."
            ),
            "constraints": [
                "offline sandbox; network deny", "use only packages preinstalled in x1-sandbox", "no secrets", "tests must pass",
            ],
            "target_sprints": 1,
        }, expect=(201,))
        plan = architect.get("plan") or {}; plan_id = plan.get("id") or ""
        if not plan_id:
            raise RuntimeError("Architect did not create a development plan")

        conversation_id = None
        terminal_failure = None
        for index in range(max(1, args.development_steps)):
            state_response = journey.call(f"development_step_{index+1}", "POST", "/v1/development-chat", payload={
                "project_id": project_id,
                "conversation_id": conversation_id,
                "message": "следующий спринт",
                "command": "continue",
            })
            state = state_response.get("state") or {}
            conversation_id = state.get("conversation_id") or conversation_id
            if state.get("plan_status") == "completed":
                development_completed = True
                break
            execution = state.get("execution") or {}
            if execution.get("status") in {"failed", "error"}:
                terminal_failure = execution.get("failure_reason") or execution.get("status")
                break
            if state.get("last_action") == "blocked" or state_response.get("action") == "blocked":
                terminal_failure = state.get("last_summary") or "development blocked"
                break
        started = _now_ms()
        journey.record("closed_shop_development", development_completed and not terminal_failure, {"completed": development_completed, "failure": terminal_failure, "plan_id": plan_id, "steps_allowed": args.development_steps}, started)

        file_map = journey.call("shop_workspace_map", "GET", f"/v1/code/workspaces/{workspace_id}/map")
        started = _now_ms(); journey.record("shop_files_created", int(file_map.get("file_count") or 0) > 0, {"file_count": file_map.get("file_count"), "total_bytes": file_map.get("total_bytes")}, started)

        snapshot = journey.call("shop_runtime_snapshot", "POST", f"/v1/project-runtimes/{runtime_id}/snapshots", expect=(201,))
        started = _now_ms(); journey.record("shop_checkpoint", bool(snapshot.get("manifest_sha256")), {"snapshot_id": snapshot.get("id"), "files": snapshot.get("file_count")}, started)
        overall = "passed"
    except Exception as exc:
        error = f"{type(exc).__name__}: {exc}"
    finally:
        try:
            _disable_probe_user(journey.user_id)
        except Exception as exc:
            if not error:
                error = f"probe cleanup failed: {exc}"
                overall = "failed"
        journey.client.close()

    report = {
        "format": "x1-e2e-user-journey-v1",
        "status": overall,
        "error": error,
        "user_id": journey.user_id,
        "project_id": project_id,
        "workspace_id": workspace_id,
        "runtime_id": runtime_id,
        "plan_id": plan_id,
        "development_completed": development_completed,
        "steps": [asdict(step) for step in journey.steps],
    }
    from pathlib import Path
    report_path = Path(args.report)
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2, default=str) + "\n", "utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2, default=str))
    return 0 if overall == "passed" else 2


if __name__ == "__main__":
    raise SystemExit(main())
