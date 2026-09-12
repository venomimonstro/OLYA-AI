from __future__ import annotations

import time

import pytest

from app.services.deadline import begin_deadline_from_headers, clamp_timeout_seconds, current_deadline
from scripts.deadline_budget_audit import audit


def test_sprint75_client_deadline_can_only_shorten_server_budget() -> None:
    row = begin_deadline_from_headers(
        {"x-x1-deadline-ms": "500000"},
        default_seconds=3,
        source="test",
        replace=True,
    )
    assert row.budget_seconds == 3


def test_sprint75_inner_layers_reuse_root_deadline() -> None:
    first = begin_deadline_from_headers(
        {"x-x1-deadline-ms": "2000"},
        default_seconds=30,
        source="http",
        replace=True,
    )
    time.sleep(0.01)
    second = begin_deadline_from_headers({}, default_seconds=300, source="auth")
    assert second is first
    assert second.remaining_seconds() < 2


def test_sprint75_local_timeout_is_clamped_to_remaining_budget() -> None:
    begin_deadline_from_headers(
        {"x-x1-deadline-ms": "1500"},
        default_seconds=30,
        source="test",
        replace=True,
    )
    timeout = clamp_timeout_seconds(20, stage="unit-test")
    assert 0 < timeout <= 1.5
    assert current_deadline() is not None


def test_sprint75_invalid_deadline_header_is_rejected() -> None:
    with pytest.raises(ValueError):
        begin_deadline_from_headers(
            {"x-x1-deadline-ms": "999"},
            default_seconds=30,
            source="test",
            replace=True,
        )


def test_sprint75_contract_audit_passes() -> None:
    result = audit()
    assert result["status"] == "passed", result["errors"]
