from __future__ import annotations

import pytest

from app.services.agent_contract import AgentContractError, require_agent_progress_budget
from app.services.deadline import begin_deadline_from_headers
from scripts.agent_mvp_lock_audit import audit


def test_sprint76_agent_progress_respects_request_deadline() -> None:
    begin_deadline_from_headers({"x-x1-deadline-ms": "1000"}, default_seconds=30, source="test", replace=True)
    remaining = require_agent_progress_budget("agent-test")
    assert remaining is not None and remaining > 0


def test_sprint76_agent_progress_fails_when_deadline_expired() -> None:
    row = begin_deadline_from_headers({"x-x1-deadline-ms": "1000"}, default_seconds=30, source="test", replace=True)
    object.__setattr__(row, "deadline_at", row.started_at - 1)
    with pytest.raises(AgentContractError):
        require_agent_progress_budget("agent-test-expired")


def test_sprint76_contract_audit_passes() -> None:
    result = audit()
    assert result["status"] == "passed", result["errors"]
