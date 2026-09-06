#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import secrets
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

import httpx
from sqlalchemy import select

from app.db import SessionLocal
from app.models import BetaParticipant, User, UserQuota


@dataclass
class Check:
    name: str
    status: str
    detail: dict[str, Any]
    duration_ms: int


class Acceptance:
    def __init__(self, base_url: str, timeout: float) -> None:
        self.client = httpx.Client(base_url=base_url.rstrip("/"), timeout=timeout, trust_env=False)
        self.token = ""
        self.user_id = ""
        self.email = ""
        self.password = ""
        self.checks: list[Check] = []

    def headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.token}"} if self.token else {}

    def call(
        self,
        name: str,
        method: str,
        path: str,
        *,
        json_body: dict | None = None,
        content: bytes | None = None,
        extra_headers: dict[str, str] | None = None,
        expect: tuple[int, ...] = (200,),
    ) -> httpx.Response:
        started = time.monotonic()
        headers = self.headers()
        headers.update(extra_headers or {})
        response = self.client.request(method, path, json=json_body, content=content, headers=headers)
        elapsed = int((time.monotonic() - started) * 1000)
        detail: dict[str, Any] = {"path": path, "http_status": response.status_code}
        if response.status_code not in expect:
            detail["body"] = response.text[-1500:]
            self.checks.append(Check(name, "failed", detail, elapsed))
            raise RuntimeError(f"{name}: HTTP {response.status_code}: {response.text[-500:]}")
        self.checks.append(Check(name, "passed", detail, elapsed))
        return response

    def assert_check(self, name: str, condition: bool, detail: dict[str, Any]) -> None:
        self.checks.append(Check(name, "passed" if condition else "failed", detail, 0))
        if not condition:
            raise RuntimeError(f"{name}: {detail}")


def _grant_probe_access(user_id: str) -> None:
    with SessionLocal() as db:
        user = db.get(User, user_id)
        if user is None:
            raise RuntimeError("Acceptance user missing")
        quota = db.get(UserQuota, user.id)
        if quota is None:
            quota = UserQuota(user_id=user.id)
            db.add(quota)
        quota.plan = "max"
        quota.monthly_compute_seconds_limit = 28_800
        quota.max_concurrent_inference = 1
        quota.max_concurrent_jobs = 8
        participant = db.scalar(
            select(BetaParticipant).where(
                BetaParticipant.user_id == user.id,
                BetaParticipant.cohort == "system-component-acceptance",
            )
        )
        if participant is None:
            db.add(
                BetaParticipant(
                    user_id=user.id,
                    cohort="system-component-acceptance",
                    state="active",
                    source="release_gate",
                    metadata_json={"synthetic": True},
                )
            )
        db.commit()


def _deactivate(user_id: str) -> None:
    if not user_id:
        return
    with SessionLocal() as db:
        user = db.get(User, user_id)
        if user is not None:
            user.is_active = False
            db.commit()


def main() -> int:
    parser = argparse.ArgumentParser(description="X1 production component acceptance")
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument("--timeout", type=float, default=120.0)
    parser.add_argument("--report", default="/app/backups/component-acceptance-latest.json")
    args = parser.parse_args()

    run = Acceptance(args.base_url, args.timeout)
    status = "failed"
    error = ""
    project_id = ""
    document_id = ""
    api_key_id = ""
    try:
        health = run.call("health", "GET", "/health").json()
        run.assert_check("health_shape", health.get("status") in {"stable", "degraded", "ok"}, {"status": health.get("status")})

        # Public commercial plan catalog must exist before authentication.
        plans = run.call("commerce_plans", "GET", "/v1/commerce/plans").json()
        run.assert_check("commerce_plan_catalog", isinstance(plans, list) and len(plans) >= 1, {"plans": len(plans) if isinstance(plans, list) else None})

        run.email = f"x1-component-{secrets.token_hex(8)}@example.invalid"
        run.password = "X1!" + secrets.token_urlsafe(24)
        auth = run.call(
            "register",
            "POST",
            "/v1/auth/register",
            json_body={"email": run.email, "password": run.password, "display_name": "X1 Component Acceptance"},
            expect=(201,),
        ).json()
        run.token = str(auth["access_token"])
        run.user_id = str(auth["user_id"])
        _grant_probe_access(run.user_id)

        me = run.call("profile", "GET", "/v1/auth/me").json()
        run.assert_check("profile_matches", me.get("email") == run.email, {"email": me.get("email")})

        # Session lifecycle: revoked tokens must stop working, then password login
        # must issue a fresh usable session.
        old_token = run.token
        run.call("logout", "POST", "/v1/auth/logout", expect=(204,))
        run.token = old_token
        revoked = run.client.get("/v1/auth/me", headers=run.headers())
        run.assert_check("revoked_session_rejected", revoked.status_code == 401, {"http_status": revoked.status_code})
        login = run.call("login", "POST", "/v1/auth/login", json_body={"email": run.email, "password": run.password}).json()
        run.token = str(login["access_token"])
        run.call("profile_after_login", "GET", "/v1/auth/me")

        project = run.call(
            "project_create",
            "POST",
            "/v1/projects",
            json_body={"name": "X1 Component Acceptance", "description": "Synthetic release check", "instructions": "Keep synthetic test data isolated."},
            expect=(201,),
        ).json()
        project_id = str(project["id"])

        memory = run.call(
            "memory_write",
            "PUT",
            f"/v1/projects/{project_id}/memory",
            json_body={"key": "acceptance_marker", "value": "component-memory-ok"},
        ).json()
        memories = run.call("memory_read", "GET", f"/v1/projects/{project_id}/memory").json()
        run.assert_check("memory_roundtrip", any(item.get("id") == memory.get("id") and item.get("value") == "component-memory-ok" for item in memories), {"items": len(memories)})

        file_bytes = "X1 acceptance marker: ultramarine-orchid-739. Файл должен индексироваться и скачиваться без изменений.".encode("utf-8")
        uploaded = run.call(
            "file_upload",
            "POST",
            f"/v1/projects/{project_id}/files?filename=acceptance.txt",
            content=file_bytes,
            extra_headers={"Content-Type": "text/plain; charset=utf-8"},
            expect=(201,),
        ).json()
        run.assert_check("file_parse_ready", uploaded.get("status") == "ready", {"status": uploaded.get("status"), "error": uploaded.get("error_message")})
        file_id = str(uploaded["id"])
        search = run.call("file_search", "GET", f"/v1/projects/{project_id}/file-search?q=ultramarine-orchid-739").json()
        run.assert_check("file_search_hit", bool(search) and any("ultramarine-orchid-739" in str(item.get("content") or "") for item in search), {"hits": len(search)})
        downloaded = run.call("file_download", "GET", f"/v1/projects/{project_id}/files/{file_id}/content").content
        run.assert_check("file_integrity", downloaded == file_bytes, {"expected_bytes": len(file_bytes), "actual_bytes": len(downloaded)})

        document = run.call(
            "document_create",
            "POST",
            "/v1/documents",
            json_body={
                "title": "X1 Acceptance Document",
                "logical_name": "acceptance.docx",
                "project_id": project_id,
                "blocks": [
                    {"type": "heading", "text": "Проверка документа", "level": 1},
                    {"type": "paragraph", "text": "Документ создан, отрендерен и проверен автоматически."},
                    {"type": "table", "rows": [["Контур", "Статус"], ["DOCX", "OK"], ["PDF QA", "OK"]]},
                ],
            },
            expect=(201,),
        ).json()
        document_id = str(document["id"])
        qa = None
        for attempt in range(5):
            response = run.client.post(f"/v1/documents/{document_id}/qa", headers=run.headers())
            if response.status_code == 503:
                time.sleep(min(1 + attempt, 3))
                continue
            run.checks.append(Check("document_qa", "passed" if response.status_code == 200 else "failed", {"http_status": response.status_code, "body": response.text[-1000:] if response.status_code != 200 else ""}, 0))
            if response.status_code != 200:
                raise RuntimeError(f"document_qa: HTTP {response.status_code}")
            qa = response.json()
            break
        run.assert_check("document_qa_passed", bool(qa) and qa.get("qa_status") == "passed" and int(qa.get("page_count") or 0) >= 1, {"qa": qa})
        released = run.call("document_release", "POST", f"/v1/documents/{document_id}/release").json()
        run.assert_check("document_released", released.get("status") == "released", {"status": released.get("status")})
        docx = run.call("document_download", "GET", f"/v1/documents/{document_id}/download").content
        run.assert_check("document_download_integrity", len(docx) > 1000 and docx[:2] == b"PK", {"bytes": len(docx), "magic": docx[:2].hex()})

        image_status = run.call("image_capability", "GET", "/v1/images/status").json()
        image_ok = bool(image_status.get("available")) or image_status.get("reason") == "local_image_backend_not_configured"
        run.assert_check("image_capability_explicit", image_ok, image_status)

        sandbox = run.call("sandbox_capability", "GET", "/v1/project-sandboxes/capabilities").json()
        run.assert_check("sandbox_isolated_available", bool(sandbox.get("available")) and bool(sandbox.get("network_isolation")), sandbox)

        api_key = run.call(
            "api_key_create",
            "POST",
            "/v1/commerce/api-keys",
            json_body={"name": "acceptance", "scopes": ["contexts:write", "contexts:read"], "rate_limit_per_minute": 20},
            expect=(201,),
        ).json()
        api_key_id = str(api_key["id"])
        external_headers = {"X-API-Key": str(api_key["token"])}
        context = run.client.post("/v1/api/contexts", json={"project_id": project_id, "label": "acceptance"}, headers=external_headers)
        run.assert_check("api_context_create", context.status_code == 201, {"http_status": context.status_code, "body": context.text[-600:]})
        context_id = str(context.json()["id"])
        context_read = run.client.get(f"/v1/api/contexts/{context_id}", headers=external_headers)
        run.assert_check("api_context_read", context_read.status_code == 200 and context_read.json().get("id") == context_id, {"http_status": context_read.status_code})
        run.call("api_key_revoke", "DELETE", f"/v1/commerce/api-keys/{api_key_id}", expect=(204,))
        rejected = run.client.get(f"/v1/api/contexts/{context_id}", headers=external_headers)
        run.assert_check("revoked_api_key_rejected", rejected.status_code == 401, {"http_status": rejected.status_code})

        usage = run.call("usage_summary", "GET", "/v1/usage/summary").json()
        run.assert_check("usage_contract", all(key in usage for key in ("events", "monthly_compute_seconds_used", "monthly_compute_seconds_limit", "plan")), usage)

        exported = run.call("account_export", "GET", "/v1/account/export").json()
        run.assert_check("account_export_contains_project", any(item.get("id") == project_id for item in exported.get("owned_projects") or []), {"projects": len(exported.get("owned_projects") or [])})

        status = "passed"
    except Exception as exc:
        error = f"{type(exc).__name__}: {exc}"
    finally:
        try:
            _deactivate(run.user_id)
        except Exception as exc:
            if status == "passed":
                status = "failed"
                error = f"cleanup failed: {type(exc).__name__}: {exc}"
        run.client.close()

    report = {
        "format": "x1-component-acceptance-v1",
        "status": status,
        "error": error,
        "user_id": run.user_id,
        "project_id": project_id,
        "document_id": document_id,
        "api_key_id": api_key_id,
        "checks": [asdict(item) for item in run.checks],
    }
    path = Path(args.report)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(report, ensure_ascii=False, indent=2, default=str) + "\n", "utf-8")
    tmp.replace(path)
    print(json.dumps(report, ensure_ascii=False, indent=2, default=str))
    return 0 if status == "passed" else 2


if __name__ == "__main__":
    raise SystemExit(main())
