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


@dataclass
class Step:
    name: str
    status: str
    http_status: int | None
    duration_ms: int
    detail: dict[str, Any]


class JourneyError(RuntimeError):
    pass


class PublicUserJourney:
    """HTTP-only onboarding/product simulation for a real newly registered user.

    Unlike the privileged release-gate journey, this script never edits the DB,
    never grants itself a beta cohort and never changes quota/rollout state.
    """

    def __init__(self, base_url: str, timeout: float) -> None:
        self.client = httpx.Client(base_url=base_url.rstrip("/"), timeout=timeout, trust_env=False)
        self.token = ""
        self.steps: list[Step] = []

    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.token}"} if self.token else {}

    def call(
        self,
        name: str,
        method: str,
        path: str,
        *,
        payload: dict | None = None,
        content: bytes | None = None,
        headers: dict[str, str] | None = None,
        expect: tuple[int, ...] = (200,),
    ) -> Any:
        started = time.monotonic()
        merged = {**self._headers(), **(headers or {})}
        response = self.client.request(method, path, json=payload if content is None else None, content=content, headers=merged)
        elapsed = int((time.monotonic() - started) * 1000)
        try:
            body: Any = response.json() if response.content else None
        except ValueError:
            body = response.text[-2000:]
        detail: dict[str, Any] = {"path": path}
        if isinstance(body, dict):
            for key in ("id", "status", "eligible", "reason", "exposure_percent"):
                if key in body:
                    detail[key] = body[key]
        if response.status_code not in expect:
            detail["body"] = body
            self.steps.append(Step(name, "failed", response.status_code, elapsed, detail))
            raise JourneyError(f"{name}: HTTP {response.status_code}: {body}")
        self.steps.append(Step(name, "passed", response.status_code, elapsed, detail))
        return body

    def close(self) -> None:
        self.client.close()


def main() -> int:
    parser = argparse.ArgumentParser(description="X1 real public-user onboarding/product journey")
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument("--timeout", type=float, default=180.0)
    parser.add_argument("--report", default="./backups/e2e-public-user-journey-latest.json")
    args = parser.parse_args()

    journey = PublicUserJourney(args.base_url, args.timeout)
    password = "X1!" + secrets.token_urlsafe(24)
    email = f"x1-public-{secrets.token_hex(8)}@example.invalid"
    user_id = project_id = conversation_id = ""
    status = "failed"
    error = ""

    try:
        journey.call("health", "GET", "/health")
        auth = journey.call(
            "register",
            "POST",
            "/v1/auth/register",
            payload={"email": email, "password": password, "display_name": "Public Journey User"},
            expect=(201,),
        )
        journey.token = str(auth["access_token"])
        user_id = str(auth["user_id"])

        me = journey.call("profile", "GET", "/v1/auth/me")
        if me.get("email") != email or me.get("id") != user_id:
            raise JourneyError("profile does not match the registered account")

        project = journey.call(
            "create_project",
            "POST",
            "/v1/projects",
            payload={"name": "Public Journey", "description": "HTTP-only onboarding probe", "instructions": "Keep context local."},
            expect=(201,),
        )
        project_id = str(project["id"])
        journey.call("list_projects", "GET", "/v1/projects")

        conversation = journey.call(
            "create_conversation",
            "POST",
            "/v1/conversations",
            payload={"project_id": project_id, "title": "Onboarding conversation"},
            expect=(201,),
        )
        conversation_id = str(conversation["id"])
        journey.call("list_conversations", "GET", f"/v1/conversations?project_id={project_id}")
        journey.call("empty_message_history", "GET", f"/v1/conversations/{conversation_id}/messages")

        eligibility = journey.call("rollout_eligibility", "GET", "/v1/launch/eligibility")
        if bool(eligibility.get("eligible")) or not bool(eligibility.get("enforcement_enabled")):
            # Exercise real file ingestion/RAG only when the rollout policy says
            # this new account is actually exposed. No DB-side privilege bypass.
            uploaded = journey.call(
                "upload_project_file",
                "POST",
                f"/v1/projects/{project_id}/files?filename=probe.md&logical_name=probe.md",
                content=b"# X1 journey\n\nThe verification phrase is ORANGE-TELESCOPE-4729.\n",
                headers={"Content-Type": "text/markdown"},
                expect=(201,),
            )
            if uploaded.get("status") != "ready":
                raise JourneyError(f"uploaded file was not ready: {uploaded}")
            results = journey.call("file_search", "GET", f"/v1/projects/{project_id}/file-search?q=ORANGE-TELESCOPE-4729")
            if not isinstance(results, list) or not results:
                raise JourneyError("RAG file search did not retrieve the uploaded phrase")
        else:
            blocked = journey.call(
                "rollout_blocks_expensive_upload",
                "POST",
                f"/v1/projects/{project_id}/files?filename=blocked.md",
                content=b"must not be accepted before rollout",
                headers={"Content-Type": "text/markdown"},
                expect=(403,),
            )
            detail = blocked.get("detail") if isinstance(blocked, dict) else None
            if not isinstance(detail, dict) or detail.get("code") != "public_rollout_not_exposed":
                raise JourneyError("file upload was blocked for an unexpected reason")

        exported = journey.call("account_export", "GET", "/v1/account/export")
        if exported.get("account", {}).get("id") != user_id:
            raise JourneyError("account export belongs to a different user")

        journey.call("logout", "POST", "/v1/auth/logout", expect=(204,))
        journey.call("revoked_session_rejected", "GET", "/v1/auth/me", expect=(401,))

        auth2 = journey.call("login_again", "POST", "/v1/auth/login", payload={"email": email, "password": password})
        journey.token = str(auth2["access_token"])
        journey.call("profile_after_login", "GET", "/v1/auth/me")

        journey.call("deactivate", "POST", "/v1/account/deactivate", expect=(204,))
        journey.token = ""
        journey.call("deactivated_login_rejected", "POST", "/v1/auth/login", payload={"email": email, "password": password}, expect=(401,))
        status = "passed"
    except Exception as exc:
        error = f"{type(exc).__name__}: {exc}"
    finally:
        journey.close()

    report = {
        "format": "x1-e2e-public-user-journey-v1",
        "status": status,
        "error": error,
        "user_id": user_id,
        "project_id": project_id,
        "conversation_id": conversation_id,
        "steps": [asdict(step) for step in journey.steps],
    }
    target = Path(args.report)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(report, ensure_ascii=False, indent=2, default=str) + "\n", "utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2, default=str))
    return 0 if status == "passed" else 2


if __name__ == "__main__":
    raise SystemExit(main())
