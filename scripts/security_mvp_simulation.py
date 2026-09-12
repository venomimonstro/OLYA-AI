#!/usr/bin/env python3
from __future__ import annotations

import json
from pathlib import Path

from app.services.code_workspace import WorkspaceError, safe_relative_path
from app.services.git_collaboration import GitError, normalize_github_url
from app.services.research import UnsafeURL, normalize_url

ROOT = Path(__file__).resolve().parents[1]


def _raises(exc_type, fn, *args) -> bool:
    try:
        fn(*args)
    except exc_type:
        return True
    return False


def audit() -> dict:
    errors: list[dict] = []
    auth = (ROOT / "app/services/auth.py").read_text("utf-8")
    api_access = (ROOT / "app/services/api_access.py").read_text("utf-8")
    files = (ROOT / "app/services/code_workspace.py").read_text("utf-8")
    research = (ROOT / "app/services/research.py").read_text("utf-8")
    sandbox = (ROOT / "app/services/sandbox.py").read_text("utf-8")
    git = (ROOT / "app/services/git_collaboration.py").read_text("utf-8")
    billing = (ROOT / "app/services/billing.py").read_text("utf-8")
    admin = (ROOT / "app/services/admin.py").read_text("utf-8")
    http = (ROOT / "app/services/http_limits.py").read_text("utf-8")
    regression = (ROOT / "scripts/run_full_regression.py").read_text("utf-8")

    guards = {
        "auth_token_hash": (auth, "token_digest(credentials.credentials)"),
        "auth_revocation": (auth, "session.revoked_at is not None"),
        "auth_inactive_user": (auth, "not user.is_active"),
        "api_key_shape": (api_access, "API_TOKEN_RE"),
        "api_rate_limit": (api_access, "request_count < api_key.rate_limit_per_minute"),
        "api_scope": (api_access, "scope not in set(row.scopes or [])"),
        "path_traversal": (files, 'any(part in {"", ".", ".."} for part in path.parts)'),
        "host_shell_disabled": (files, "shell=False"),
        "ssrf_private_ip": (research, "not _ip_is_public"),
        "ssrf_credentials": (research, "Credentials in URLs are not allowed"),
        "sandbox_read_only": (sandbox, '"--read-only"'),
        "sandbox_cap_drop": (sandbox, '"--cap-drop=ALL"'),
        "sandbox_network_none": (sandbox, '"--network","none"'),
        "git_protocol_file_denied": (git, '"protocol.file.allow=never"'),
        "git_protocol_ext_denied": (git, '"protocol.ext.allow=never"'),
        "git_secret_scan": (git, "_SECRET_PATTERNS"),
        "billing_user_match": (billing, "record.user_id!=checkout.user_id"),
        "billing_amount_match": (billing, "int(record.amount_minor)!=int(checkout.amount_minor)"),
        "billing_currency_match": (billing, "str(record.currency).upper()!=str(checkout.currency).upper()"),
        "admin_dependency": (admin, "Depends(get_current_user)"),
        "admin_role": (admin, "if not user.is_admin"),
        "body_limit": (http, "RequestBodyTooLarge"),
    }
    for code, (source, token) in guards.items():
        if token not in source:
            errors.append({"code": "missing_guard", "surface": code, "token": token})

    scenarios = {
        "workspace_parent_traversal": _raises(WorkspaceError, safe_relative_path, "../secrets.txt"),
        "workspace_absolute_path": _raises(WorkspaceError, safe_relative_path, "/etc/passwd"),
        "research_localhost_ssrf": _raises(UnsafeURL, normalize_url, "http://localhost/admin"),
        "research_private_ip_ssrf": _raises(UnsafeURL, normalize_url, "http://127.0.0.1/private"),
        "research_url_credentials": _raises(UnsafeURL, normalize_url, "https://user:pass@example.com/"),
        "git_non_github_remote": _raises(GitError, normalize_github_url, "https://example.com/a/b.git"),
        "git_url_credentials": _raises(GitError, normalize_github_url, "https://user:token@github.com/a/b.git"),
    }
    for name, passed in scenarios.items():
        if not passed:
            errors.append({"code": "security_scenario_failed", "scenario": name})

    if '("scripts.security_mvp_simulation", [])' not in regression:
        errors.append({"code": "regression_missing_security_simulation"})

    return {
        "format": "x1-security-mvp-simulation-v1",
        "status": "passed" if not errors else "failed",
        "scenarios": scenarios,
        "errors": errors,
    }


def main() -> int:
    result = audit()
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["status"] == "passed" else 2


if __name__ == "__main__":
    raise SystemExit(main())
