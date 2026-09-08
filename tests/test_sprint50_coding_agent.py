import asyncio
from pathlib import Path

import pytest

from app.services.coding_agent import VerificationEvidence, _Checkpoint, _diff_line_count, _diff_was_inspected_after_last_write, _latest_passed, _patch_digest
from app.services.coding_tools import ToolScopeError, build_coding_tool_registry
from app.services.tool_reliability import StrictToolArgs, ToolCall, ToolRegistry, ToolSession, ToolSpec


class EmptyArgs(StrictToolArgs):
    pass


def run(coro):
    return asyncio.run(coro)


def test_non_cacheable_verification_tool_reexecutes():
    counter={"calls":0}
    def handler(_args): counter["calls"]+=1; return {"call":counter["calls"]}
    registry=ToolRegistry(); registry.register(ToolSpec(name="verification.test",description="verify",args_model=EmptyArgs,handler=handler,max_retries=0,cacheable=False)); session=ToolSession(registry,max_same_call=4)
    first=run(session.execute(ToolCall(name="verification.test",arguments={},call_id="v1"))); second=run(session.execute(ToolCall(name="verification.test",arguments={},call_id="v2")))
    assert first.status=="ok" and second.status=="ok" and counter["calls"]==2


def test_scope_rejection_is_validation_error_not_uncertain_write(tmp_path: Path):
    root=tmp_path/"repo"; root.mkdir(); (root/"allowed").mkdir()
    registry=build_coding_tool_registry(root,allowed_paths=["allowed"]); session=ToolSession(registry)
    with pytest.raises(ToolScopeError):
        run(session.execute(ToolCall(name="workspace.write",arguments={"path":"outside.py","content":"x=1\n"},call_id="bad")))
    result=run(session.execute(ToolCall(name="workspace.write",arguments={"path":"allowed/a.py","content":"x=1\n"},call_id="good")))
    assert result.status=="ok"


def test_checkpoint_restores_existing_and_removes_new_file(tmp_path: Path):
    root=tmp_path/"repo"; root.mkdir(); old=root/"a.py"; old.write_text("old\n",encoding="utf-8"); new=root/"b.py"; cp=_Checkpoint(); cp.capture("a.py",old); cp.mark_touched({"path":"a.py"}); cp.capture("b.py",new); cp.mark_touched({"path":"b.py"}); old.write_text("new\n",encoding="utf-8"); new.write_text("created\n",encoding="utf-8"); cp.rollback(root)
    assert old.read_text(encoding="utf-8")=="old\n" and not new.exists()


def test_patch_digest_changes_after_post_test_edit(tmp_path: Path):
    root=tmp_path/"repo"; root.mkdir(); target=root/"a.py"; target.write_text("a=1\n",encoding="utf-8"); cp=_Checkpoint(); cp.capture("a.py",target); cp.mark_touched({"path":"a.py"}); first=_patch_digest(root,cp); evidence=[VerificationEvidence(check="unit",argv=("pytest",),passed=True,exit_code=0,timed_out=False,sandbox_level="test",patch_digest=first,stdout_tail="",stderr_tail="")]
    assert _latest_passed(evidence,"unit",first) is True
    target.write_text("a=2\n",encoding="utf-8"); second=_patch_digest(root,cp)
    assert second!=first and _latest_passed(evidence,"unit",second) is False


def test_diff_budget_counts_changed_lines(tmp_path: Path):
    root=tmp_path/"repo"; root.mkdir(); target=root/"a.py"; target.write_text("a\nb\nc\n",encoding="utf-8"); cp=_Checkpoint(); cp.capture("a.py",target); cp.mark_touched({"path":"a.py"}); target.write_text("a\nB\nc\nd\n",encoding="utf-8")
    assert _diff_line_count(root,cp)==3


def test_final_diff_must_be_after_last_write():
    bad=({"tool":"proof.inspect_diff","status":"ok"},{"tool":"workspace.write","status":"ok"}); good=({"tool":"workspace.write","status":"ok"},{"tool":"verification.run","status":"ok"},{"tool":"proof.inspect_diff","status":"ok"})
    assert _diff_was_inspected_after_last_write(bad) is False
    assert _diff_was_inspected_after_last_write(good) is True


def test_sandbox_remote_contract_carries_read_only_workspace():
    client=Path("app/services/sandbox.py").read_text(encoding="utf-8"); worker=Path("app/sandbox_worker_api.py").read_text(encoding="utf-8")
    assert '"read_only_workspace":bool(read_only_workspace)' in client
    assert "read_only_workspace: bool = False" in worker
    assert "_base_command(payload,read_only_workspace=payload.read_only_workspace)" in worker


def test_coding_agent_requires_server_owned_proof_gates():
    source=Path("app/services/coding_agent.py").read_text(encoding="utf-8")
    for marker in ("required_verification_missing_failed_or_stale","final_diff_not_inspected","diff_budget_exceeded","checkpoint.rollback(workspace)","cacheable=False","read_only_workspace=True","proof.inspect_diff","patch_digest"):
        assert marker in source


def test_coding_agent_does_not_expose_commit_push_or_arbitrary_shell():
    source=Path("app/services/coding_agent.py").read_text(encoding="utf-8")
    assert "git_commit(" not in source and "push(" not in source and "subprocess.run(" not in source
    assert "verification.run" in source
