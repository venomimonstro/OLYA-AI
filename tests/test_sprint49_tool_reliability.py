import asyncio
from pathlib import Path

import pytest

from app.services.coding_tools import build_coding_tool_registry
from app.services.tool_reliability import (
    StrictToolArgs,
    ToolCall,
    ToolLoopError,
    ToolRegistry,
    ToolSession,
    ToolSpec,
    ToolValidationError,
    UnknownToolError,
)


class ValueArgs(StrictToolArgs):
    value: int


def run(coro):
    return asyncio.run(coro)


def test_unknown_tool_fails_closed():
    session = ToolSession(ToolRegistry())
    with pytest.raises(UnknownToolError):
        run(session.execute(ToolCall(name="ghost.tool", arguments={}, call_id="call_1")))


def test_extra_arguments_are_rejected_by_schema():
    registry = ToolRegistry()
    registry.register(ToolSpec(name="read.value", description="read", args_model=ValueArgs, handler=lambda a: a.value))
    session = ToolSession(registry)
    with pytest.raises(ToolValidationError):
        run(session.execute(ToolCall(name="read.value", arguments={"value": 1, "surprise": True}, call_id="call_2")))


def test_write_tools_cannot_register_automatic_retries():
    with pytest.raises(ValueError):
        ToolSpec(
            name="write.value",
            description="write",
            args_model=ValueArgs,
            handler=lambda a: a.value,
            effect="write",
            max_retries=1,
        )


def test_successful_write_is_idempotent_across_new_call_ids():
    counter = {"writes": 0}

    def handler(args):
        counter["writes"] += 1
        return {"saved": args.value}

    registry = ToolRegistry()
    registry.register(ToolSpec(
        name="write.value",
        description="write",
        args_model=ValueArgs,
        handler=handler,
        effect="write",
        max_retries=0,
    ))
    session = ToolSession(registry, max_same_call=2)
    first = run(session.execute(ToolCall(name="write.value", arguments={"value": 7}, call_id="call_a")))
    second = run(session.execute(ToolCall(name="write.value", arguments={"value": 7}, call_id="call_b")))
    assert first.status == "ok"
    assert second.status == "cached"
    assert counter["writes"] == 1


def test_third_identical_request_is_detected_as_loop_even_when_cached():
    registry = ToolRegistry()
    registry.register(ToolSpec(name="read.value", description="read", args_model=ValueArgs, handler=lambda a: a.value))
    session = ToolSession(registry, max_same_call=2)
    run(session.execute(ToolCall(name="read.value", arguments={"value": 3}, call_id="call_a")))
    run(session.execute(ToolCall(name="read.value", arguments={"value": 3}, call_id="call_b")))
    with pytest.raises(ToolLoopError):
        run(session.execute(ToolCall(name="read.value", arguments={"value": 3}, call_id="call_c")))


def test_call_id_cannot_be_reused_for_different_arguments():
    registry = ToolRegistry()
    registry.register(ToolSpec(name="read.value", description="read", args_model=ValueArgs, handler=lambda a: a.value))
    session = ToolSession(registry)
    run(session.execute(ToolCall(name="read.value", arguments={"value": 1}, call_id="same")))
    with pytest.raises(ToolValidationError):
        run(session.execute(ToolCall(name="read.value", arguments={"value": 2}, call_id="same")))


def test_openai_schema_forbids_unknown_properties():
    registry = ToolRegistry()
    registry.register(ToolSpec(name="read.value", description="read", args_model=ValueArgs, handler=lambda a: a.value))
    schema = registry.schemas()[0]
    assert schema["type"] == "function"
    assert schema["function"]["parameters"]["additionalProperties"] is False


def test_coding_registry_enforces_approved_scope_and_optimistic_write(tmp_path: Path):
    root = tmp_path / "workspace"
    root.mkdir()
    (root / "allowed").mkdir()
    (root / "allowed" / "a.py").write_text("print('old')\n", encoding="utf-8")
    (root / "secret.txt").write_text("hidden", encoding="utf-8")
    registry = build_coding_tool_registry(root, allowed_paths=["allowed"])
    session = ToolSession(registry)

    mapped = run(session.execute(ToolCall(name="workspace.map", arguments={"max_files": 50}, call_id="map1")))
    paths = [item["path"] for item in mapped.output["files"]]
    assert "allowed/a.py" in paths
    assert "secret.txt" not in paths

    read = run(session.execute(ToolCall(name="workspace.read", arguments={"path": "allowed/a.py"}, call_id="read1")))
    old_sha = read.output["sha256"]
    written = run(session.execute(ToolCall(
        name="workspace.write",
        arguments={"path": "allowed/a.py", "content": "print('new')\n", "expected_sha256": old_sha},
        call_id="write1",
    )))
    assert written.output["before_sha256"] == old_sha
    assert (root / "allowed" / "a.py").read_text(encoding="utf-8") == "print('new')\n"

    fresh_session = ToolSession(registry)
    with pytest.raises(Exception):
        run(fresh_session.execute(ToolCall(name="workspace.read", arguments={"path": "secret.txt"}, call_id="read2")))


def test_coding_registry_exposes_no_shell_network_commit_or_push():
    registry = build_coding_tool_registry(Path("."))
    assert set(registry.names) == {"git.status", "workspace.map", "workspace.read", "workspace.write"}
    assert all("push" not in name and "commit" not in name and "shell" not in name for name in registry.names)


def test_llama_tool_protocol_is_serial_and_validated():
    source = Path("app/inference/client.py").read_text(encoding="utf-8")
    assert '"parallel_tool_calls": False' in source
    assert '"tool_choice": "auto"' in source
    assert "_validate_tool_schemas" in source
    assert "_parse_tool_calls" in source
    assert "len(raw_calls) > 8" in source
    assert "local tool arguments must be a JSON object" in source


def test_agent_loop_has_hard_step_and_error_boundaries():
    source = Path("app/services/tool_agent.py").read_text(encoding="utf-8")
    assert "max_steps = max(1, min(int(max_steps), 12))" in source
    assert "tool_error_budget_exhausted" in source
    assert "max_steps_exhausted" in source
    assert "ToolLoopError, ToolBudgetError" in source
    assert "session.registry.schemas()" in source
