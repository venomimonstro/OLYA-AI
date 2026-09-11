#!/usr/bin/env python3
from __future__ import annotations

import json
from collections import Counter


REQUIRED_OPERATIONS = {
    ("POST", "/v1/api/chat"),
    ("GET", "/v1/api/contexts"),
    ("POST", "/v1/api/contexts"),
    ("GET", "/v1/api/contexts/{context_id}"),
    ("DELETE", "/v1/api/contexts/{context_id}"),
    ("GET", "/v1/api/telemetry"),
    ("GET", "/v1/commerce/api-keys"),
    ("POST", "/v1/commerce/api-keys"),
    ("DELETE", "/v1/commerce/api-keys/{api_key_id}"),
    ("POST", "/v1/commerce/api-keys/{api_key_id}/rotate"),
}


def audit() -> dict:
    from app.core.config import get_settings
    from app.main import app
    from app.schemas.commerce import ApiChatRequest, ApiKeyCreated, ApiKeyRead
    from app.services.api_access import API_TOKEN_RE

    errors: list[dict] = []
    operations: list[tuple[str, str]] = []
    for route in app.routes:
        path = str(getattr(route, "path", ""))
        for method in set(getattr(route, "methods", set()) or set()):
            if path.startswith(("/v1/api", "/v1/commerce/api-keys")) and method not in {"HEAD", "OPTIONS"}:
                operations.append((method, path))
    available = set(operations)
    for method, path in sorted(REQUIRED_OPERATIONS - available):
        errors.append({"code": "api_operation_missing", "method": method, "path": path})
    for (method, path), count in sorted(Counter(operations).items()):
        if count > 1:
            errors.append({"code": "api_operation_duplicate", "method": method, "path": path, "count": count})

    settings = get_settings()
    if not 1 <= settings.api_default_rate_limit_per_minute <= settings.api_max_rate_limit_per_minute:
        errors.append({"code": "api_rate_limit_defaults_invalid"})
    if not 1 <= settings.api_max_active_keys_per_user <= 100:
        errors.append({"code": "api_active_key_limit_invalid"})
    if not 1 <= settings.api_max_contexts_per_owner <= 10_000:
        errors.append({"code": "api_context_limit_invalid"})
    if "token" in ApiKeyRead.model_fields or "secret_hash" in ApiKeyRead.model_fields:
        errors.append({"code": "api_key_list_exposes_secret"})
    if "token" not in ApiKeyCreated.model_fields:
        errors.append({"code": "api_key_creation_missing_one_time_secret"})
    if "client_request_id" not in ApiChatRequest.model_fields:
        errors.append({"code": "api_chat_idempotency_missing"})
    if API_TOKEN_RE.fullmatch("x1k_0123abcd_" + "A" * 54) is None:
        errors.append({"code": "api_token_format_invalid"})

    return {
        "format": "x1-api-contract-audit-v1",
        "status": "passed" if not errors else "failed",
        "operations": [{"method": method, "path": path} for method, path in sorted(available)],
        "errors": errors,
    }


def main() -> int:
    result = audit()
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["status"] == "passed" else 2


if __name__ == "__main__":
    raise SystemExit(main())
