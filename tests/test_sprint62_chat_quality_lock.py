from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace

import pytest
from pydantic import ValidationError
from sqlalchemy import func, select
from sqlalchemy.orm import sessionmaker

from app.inference.client import LlamaGeneration, LlamaUnavailable
from app.models import ChatRun, Message, User
from app.schemas.chat import ChatMessage, ChatRequest, ChatResponse, ChatUsage
from app.services.chat_runtime import ActiveChatJob, ChatExecutionManager, ChatRunConflict, ChatRunSnapshot, request_fingerprint
from app.services.context import ContextCompiler


ROOT = Path(__file__).resolve().parents[1]


def _payload(text: str = "hello", request_id: str = "ui_123456789012") -> ChatRequest:
    return ChatRequest(
        messages=[ChatMessage(role="user", content=text)],
        mode="auto",
        verification="auto",
        client_request_id=request_id,
    )


def _response(job: ActiveChatJob, text: str = "done") -> ChatResponse:
    return ChatResponse(
        text=text,
        model="test-local-qwen",
        usage=ChatUsage(raw_message_chars=5, compiled_message_chars=5, mode="fast"),
        conversation_id=job.conversation_id,
        run_id=job.run_id,
        client_request_id=job.client_request_id,
    )


def test_client_request_id_contract_is_strict():
    assert _payload().client_request_id == "ui_123456789012"
    with pytest.raises(ValidationError):
        _payload(request_id="short")
    with pytest.raises(ValidationError):
        _payload(request_id="ui_invalid/request_id")


def test_request_fingerprint_is_stable_but_payload_sensitive():
    left = _payload("same", "ui_aaaaaaaaaaaa")
    right = _payload("same", "ui_bbbbbbbbbbbb")
    changed = _payload("different", "ui_aaaaaaaaaaaa")
    assert request_fingerprint("user-1", left) == request_fingerprint("user-1", right)
    assert request_fingerprint("user-1", left) != request_fingerprint("user-1", changed)
    assert request_fingerprint("user-1", left) != request_fingerprint("user-2", left)


def test_slow_subscriber_is_resynchronised_with_complete_partial_text():
    async def scenario():
        job = ActiveChatJob(
            run_id="run-1",
            user_id="user-1",
            client_request_id="ui_123456789012",
            input_hash="a" * 64,
        )
        queue: asyncio.Queue = asyncio.Queue(maxsize=1)
        job.subscribers.add(queue)
        await job.token("alpha")
        await job.token(" beta")
        event, data = queue.get_nowait()
        assert event == "replace"
        assert data["text"] == "alpha beta"
        assert data["reason"] == "subscriber_resync"

    asyncio.run(scenario())


def test_manager_replays_terminal_result_and_blocks_request_id_reuse(register_user, db_session, monkeypatch):
    import app.services.chat_runtime as runtime

    data, _ = register_user("sprint62-manager@example.com")
    factory = sessionmaker(bind=db_session.get_bind(), autoflush=False, expire_on_commit=False)
    monkeypatch.setattr(runtime, "SessionLocal", factory)
    manager = ChatExecutionManager()
    payload = _payload("one logical request")

    async def runner(job: ActiveChatJob) -> ChatResponse:
        return _response(job, "stable result")

    async def scenario():
        first = await manager.start_or_attach(user_id=data["user_id"], payload=payload, runner=runner)
        assert isinstance(first, ActiveChatJob)
        await first.task
        replay = await manager.start_or_attach(user_id=data["user_id"], payload=payload, runner=runner)
        assert isinstance(replay, ChatRunSnapshot)
        assert replay.status == "succeeded"
        assert replay.result["text"] == "stable result"
        with pytest.raises(ChatRunConflict):
            await manager.start_or_attach(
                user_id=data["user_id"],
                payload=_payload("different request", payload.client_request_id),
                runner=runner,
            )

    asyncio.run(scenario())
    assert db_session.scalar(select(func.count()).select_from(ChatRun).where(ChatRun.user_id == data["user_id"])) == 1


def test_manager_explicit_cancel_is_terminal(register_user, db_session, monkeypatch):
    import app.services.chat_runtime as runtime

    data, _ = register_user("sprint62-cancel@example.com")
    factory = sessionmaker(bind=db_session.get_bind(), autoflush=False, expire_on_commit=False)
    monkeypatch.setattr(runtime, "SessionLocal", factory)
    manager = ChatExecutionManager()
    entered = asyncio.Event()

    async def runner(job: ActiveChatJob) -> ChatResponse:
        entered.set()
        await asyncio.Event().wait()
        return _response(job)

    async def scenario():
        active = await manager.start_or_attach(user_id=data["user_id"], payload=_payload(), runner=runner)
        assert isinstance(active, ActiveChatJob)
        await entered.wait()
        result = await manager.cancel(user_id=data["user_id"], client_request_id=active.client_request_id)
        assert result.status == "cancelled"
        assert result.error_code == "cancelled_by_user"

    asyncio.run(scenario())


def test_previous_runtime_running_row_resumes_same_run(register_user, db_session, monkeypatch):
    import app.services.chat_runtime as runtime

    data, _ = register_user("sprint62-restart@example.com")
    factory = sessionmaker(bind=db_session.get_bind(), autoflush=False, expire_on_commit=False)
    monkeypatch.setattr(runtime, "SessionLocal", factory)
    payload = _payload("resume after process restart", "ui_restart_123456")
    row = ChatRun(
        user_id=data["user_id"],
        client_request_id=payload.client_request_id,
        input_hash=request_fingerprint(data["user_id"], payload),
        status="running",
        runtime_id="old-runtime",
        attempt=1,
        result_json={},
        error_code="",
        error_detail="",
    )
    db_session.add(row)
    db_session.commit()
    run_id = row.id
    manager = ChatExecutionManager()

    async def runner(job: ActiveChatJob) -> ChatResponse:
        return _response(job, "recovered")

    async def scenario():
        active = await manager.start_or_attach(user_id=data["user_id"], payload=payload, runner=runner)
        assert isinstance(active, ActiveChatJob)
        assert active.run_id == run_id
        await active.task
        status = await manager.status(user_id=data["user_id"], client_request_id=payload.client_request_id)
        assert status.status == "succeeded"
        assert status.result["text"] == "recovered"

    asyncio.run(scenario())
    db_session.expire_all()
    persisted = db_session.get(ChatRun, run_id)
    assert persisted.attempt == 2


def test_accepted_user_turn_survives_failure_without_duplicate_retry(register_user, client, db_session):
    from app.api.routes.chat import _persist_accepted_user_turn

    data, headers = register_user("sprint62-turn@example.com")
    created = client.post("/v1/conversations", headers=headers, json={"title": "Durable turn"})
    assert created.status_code == 201, created.text
    conversation_id = created.json()["id"]
    conversation = db_session.get(__import__("app.models", fromlist=["Conversation"]).Conversation, conversation_id)
    turn = ChatMessage(role="user", content="Please keep this accepted question")

    first = _persist_accepted_user_turn(db_session, conversation, turn)
    db_session.commit()
    second = _persist_accepted_user_turn(db_session, conversation, turn)
    db_session.commit()
    assert first.id == second.id
    assert db_session.scalar(
        select(func.count()).select_from(Message).where(Message.conversation_id == conversation_id, Message.role == "user")
    ) == 1

    db_session.add(Message(conversation_id=conversation_id, role="assistant", content="answer"))
    db_session.commit()
    _persist_accepted_user_turn(db_session, conversation, turn)
    db_session.commit()
    assert db_session.scalar(
        select(func.count()).select_from(Message).where(Message.conversation_id == conversation_id, Message.role == "user")
    ) == 2


def test_llama_restart_retries_once_only_before_visible_output():
    from app.api.routes.chat import _primary_generation

    class BeforeTokenFailure:
        def __init__(self):
            self.calls = 0

        async def generate(self, messages, *, max_tokens, reasoning, on_token=None):
            self.calls += 1
            if self.calls == 1:
                raise LlamaUnavailable("restarting")
            if on_token:
                await on_token("ok")
            return LlamaGeneration(text="ok", ttft_ms=1, output_tokens=1, tokens_per_second=1.0, generation_ms=1)

    llama = BeforeTokenFailure()
    request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(llama=llama)))
    seen = []
    result = asyncio.run(_primary_generation(request, [ChatMessage(role="user", content="x")], max_tokens=32, reasoning=False, on_token=lambda value: _append_async(seen, value)))
    assert result.text == "ok"
    assert llama.calls == 2
    assert seen == ["ok"]


def _append_async(target: list[str], value: str):
    async def append():
        target.append(value)
    return append()


def test_llama_failure_after_first_token_is_not_replayed():
    from app.api.routes.chat import _primary_generation

    class AfterTokenFailure:
        def __init__(self):
            self.calls = 0

        async def generate(self, messages, *, max_tokens, reasoning, on_token=None):
            self.calls += 1
            if on_token:
                await on_token("partial")
            raise LlamaUnavailable("died after ttft")

    llama = AfterTokenFailure()
    request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(llama=llama)))
    with pytest.raises(LlamaUnavailable):
        asyncio.run(_primary_generation(request, [ChatMessage(role="user", content="x")], max_tokens=32, reasoning=False, on_token=lambda value: _append_async([], value)))
    assert llama.calls == 1


def test_300_message_context_remains_bounded_and_keeps_latest_request():
    messages = [ChatMessage(role="user" if i % 2 == 0 else "assistant", content=f"turn-{i} " + ("x" * 180)) for i in range(300)]
    messages.append(ChatMessage(role="user", content="LATEST_REQUEST_MUST_SURVIVE"))
    compiled = ContextCompiler(max_chars=12_000).compile(messages, max_chars=12_000)
    assert any(m.content == "LATEST_REQUEST_MUST_SURVIVE" for m in compiled)
    assert len(compiled) < 80
    assert sum(len(m.content) for m in compiled) <= 12_000


def test_stream_contract_separates_disconnect_from_user_stop_and_preserves_ui_reconnect():
    chat = (ROOT / "app/api/routes/chat.py").read_text("utf-8")
    ui = (ROOT / "app/user_ui.py").read_text("utf-8")
    auth = (ROOT / "app/services/auth.py").read_text("utf-8")
    context = (ROOT / "app/services/project_context.py").read_text("utf-8")

    assert "legacy_disconnect_cancels = payload.client_request_id is None" in chat
    assert '"/chat/runs/{client_request_id}/cancel"' in chat
    assert "asyncio.shield(execution.task)" in chat
    assert "_persist_accepted_user_turn" in chat
    assert "if emitted:" in chat and "await asyncio.sleep(0.75)" in chat
    assert "range(max_length, 0, -1)" in context
    assert "db.commit()\n    request.state.auth_session_id" in auth

    assert "client_request_id:requestId" in ui
    assert "/v1/chat/runs/" in ui and "/cancel" in ui
    assert "Связь прервалась. Восстанавливаю тот же запрос" in ui
    assert "x1_pending_chat_run" in ui
    assert "recoverPending" in ui
    assert "for(let attempt=0;attempt<4;attempt++)" in ui
    assert "item.event==='cancelled'" in ui


def test_sprint62_migration_is_linear_and_chat_run_is_registered():
    migration = (ROOT / "alembic/versions/f62c1b7e3d20_add_recoverable_chat_runs.py").read_text("utf-8")
    registry = (ROOT / "app/models.py").read_text("utf-8")
    assert 'revision = "f62c1b7e3d20"' in migration
    assert 'down_revision = "f61b0a4d2c90"' in migration
    assert '"chat_runs"' in migration
    assert "from app.models_sprint62 import *" in registry


def test_chat_run_export_does_not_duplicate_transient_result_text():
    account = (ROOT / "app/api/routes/account.py").read_text("utf-8")
    assert '"chat_runs"' in account
    chat_runs_block = account.split('"chat_runs": [', 1)[1].split('"owned_projects":', 1)[0]
    assert "result_json" not in chat_runs_block
