from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from fastapi import HTTPException
from sqlalchemy.orm import sessionmaker

from app.models import ChatRun, Conversation
from app.schemas.chat import ChatMessage, ChatRequest, ChatResponse, ChatUsage
from app.services.chat_runtime import ActiveChatJob, ChatExecutionManager, ChatRunConflict, ChatRunSnapshot, request_fingerprint


ROOT = Path(__file__).resolve().parents[1]


def _payload(text: str, request_id: str, conversation_id: str | None = None) -> ChatRequest:
    return ChatRequest(
        messages=[ChatMessage(role="user", content=text)],
        verification="off",
        client_request_id=request_id,
        conversation_id=conversation_id,
    )


def _response(job: ActiveChatJob, text: str = "ok") -> ChatResponse:
    return ChatResponse(
        text=text,
        model="local-test",
        usage=ChatUsage(raw_message_chars=10, compiled_message_chars=10, mode="fast", verification="off"),
        conversation_id=job.conversation_id,
        run_id=job.run_id,
        client_request_id=job.client_request_id,
    )


def _factory(db_session):
    return sessionmaker(bind=db_session.get_bind(), autoflush=False, expire_on_commit=False)


def test_restart_resume_reuses_durably_bound_conversation(register_user, db_session, monkeypatch):
    import app.services.chat_runtime as runtime

    data, _ = register_user("s62-bound-conversation@example.com")
    conversation = Conversation(owner_id=data["user_id"], title="Existing accepted conversation")
    db_session.add(conversation)
    db_session.commit()

    payload = _payload("resume me", "ui_bound_resume_123456")
    run = ChatRun(
        user_id=data["user_id"],
        conversation_id=conversation.id,
        client_request_id=payload.client_request_id,
        input_hash=request_fingerprint(data["user_id"], payload),
        status="interrupted",
        runtime_id="old-runtime",
        attempt=1,
        result_json={},
        error_code="runtime_shutdown",
        error_detail="restart",
    )
    db_session.add(run)
    db_session.commit()

    monkeypatch.setattr(runtime, "SessionLocal", _factory(db_session))
    manager = ChatExecutionManager()

    async def runner(job: ActiveChatJob) -> ChatResponse:
        assert payload.conversation_id == conversation.id
        assert job.conversation_id == conversation.id
        return _response(job, "resumed without duplicate conversation")

    async def scenario():
        active = await manager.start_or_attach(user_id=data["user_id"], payload=payload, runner=runner)
        assert isinstance(active, ActiveChatJob)
        await active.task
        terminal = await manager.status(user_id=data["user_id"], client_request_id=payload.client_request_id)
        assert terminal.status == "succeeded"
        assert terminal.conversation_id == conversation.id

    asyncio.run(scenario())


def test_parallel_turns_in_same_conversation_are_rejected(register_user, db_session, monkeypatch):
    import app.services.chat_runtime as runtime

    data, _ = register_user("s62-conversation-serial@example.com")
    conversation = Conversation(owner_id=data["user_id"], title="Serial")
    db_session.add(conversation)
    db_session.commit()
    monkeypatch.setattr(runtime, "SessionLocal", _factory(db_session))
    manager = ChatExecutionManager()
    entered = asyncio.Event()

    async def slow_runner(job: ActiveChatJob) -> ChatResponse:
        entered.set()
        await asyncio.Event().wait()
        return _response(job)

    async def scenario():
        first = await manager.start_or_attach(
            user_id=data["user_id"],
            payload=_payload("first", "ui_serial_first_123", conversation.id),
            runner=slow_runner,
        )
        assert isinstance(first, ActiveChatJob)
        await entered.wait()
        with pytest.raises(ChatRunConflict, match="active chat request"):
            await manager.start_or_attach(
                user_id=data["user_id"],
                payload=_payload("second", "ui_serial_second_123", conversation.id),
                runner=slow_runner,
            )
        await manager.cancel(user_id=data["user_id"], client_request_id=first.client_request_id)

    asyncio.run(scenario())


def test_resume_attempts_are_bounded(register_user, db_session, monkeypatch):
    import app.services.chat_runtime as runtime

    data, _ = register_user("s62-resume-budget@example.com")
    payload = _payload("bounded", "ui_resume_budget_123")
    run = ChatRun(
        user_id=data["user_id"],
        client_request_id=payload.client_request_id,
        input_hash=request_fingerprint(data["user_id"], payload),
        status="interrupted",
        runtime_id="old-runtime",
        attempt=3,
        result_json={},
        error_code="runtime_shutdown",
        error_detail="again",
    )
    db_session.add(run)
    db_session.commit()
    monkeypatch.setattr(runtime, "SessionLocal", _factory(db_session))
    manager = ChatExecutionManager()
    called = False

    async def runner(job: ActiveChatJob) -> ChatResponse:
        nonlocal called
        called = True
        return _response(job)

    async def scenario():
        result = await manager.start_or_attach(user_id=data["user_id"], payload=payload, runner=runner)
        assert isinstance(result, ChatRunSnapshot)
        assert result.status == "failed"
        assert result.error_code == "resume_attempts_exhausted"
        assert result.retryable is False

    asyncio.run(scenario())
    assert called is False


def test_shutdown_is_interrupted_not_user_cancel_and_startup_reopens(register_user, db_session, monkeypatch):
    import app.services.chat_runtime as runtime

    data, _ = register_user("s62-shutdown@example.com")
    monkeypatch.setattr(runtime, "SessionLocal", _factory(db_session))
    manager = ChatExecutionManager()
    entered = asyncio.Event()
    payload = _payload("survive restart", "ui_shutdown_resume_123")

    async def slow_runner(job: ActiveChatJob) -> ChatResponse:
        entered.set()
        await asyncio.Event().wait()
        return _response(job)

    async def quick_runner(job: ActiveChatJob) -> ChatResponse:
        return _response(job, "after restart")

    async def scenario():
        active = await manager.start_or_attach(user_id=data["user_id"], payload=payload, runner=slow_runner)
        assert isinstance(active, ActiveChatJob)
        await entered.wait()
        await manager.shutdown()
        interrupted = await manager.status(user_id=data["user_id"], client_request_id=payload.client_request_id)
        assert interrupted.status == "interrupted"
        assert interrupted.error_code == "runtime_shutdown"
        assert interrupted.retryable is True

        with pytest.raises(HTTPException) as exc:
            await manager.start_or_attach(user_id=data["user_id"], payload=payload, runner=quick_runner)
        assert exc.value.status_code == 503

        manager.startup()
        resumed = await manager.start_or_attach(user_id=data["user_id"], payload=payload, runner=quick_runner)
        assert isinstance(resumed, ActiveChatJob)
        await resumed.task
        done = await manager.status(user_id=data["user_id"], client_request_id=payload.client_request_id)
        assert done.status == "succeeded"
        assert done.result["text"] == "after restart"

    asyncio.run(scenario())


def test_late_terminal_write_adopts_sticky_database_success(register_user, db_session, monkeypatch):
    import app.services.chat_runtime as runtime

    data, _ = register_user("s62-late-cancel@example.com")
    conversation = Conversation(owner_id=data["user_id"], title="Sticky")
    db_session.add(conversation)
    db_session.commit()
    payload = _payload("done", "ui_sticky_transport_123", conversation.id)
    result = {
        "text": "already committed",
        "model": "local",
        "usage": {"raw_message_chars": 4, "compiled_message_chars": 4, "mode": "fast", "verification": "off"},
        "conversation_id": conversation.id,
        "run_id": "placeholder",
        "client_request_id": payload.client_request_id,
    }
    run = ChatRun(
        user_id=data["user_id"],
        conversation_id=conversation.id,
        client_request_id=payload.client_request_id,
        input_hash=request_fingerprint(data["user_id"], payload),
        status="succeeded",
        runtime_id="runtime",
        attempt=1,
        result_json=result,
        error_code="",
        error_detail="",
    )
    db_session.add(run)
    db_session.commit()
    result["run_id"] = run.id
    run.result_json = result
    db_session.commit()

    monkeypatch.setattr(runtime, "SessionLocal", _factory(db_session))
    manager = ChatExecutionManager()
    job = ActiveChatJob(
        run_id=run.id,
        user_id=data["user_id"],
        client_request_id=payload.client_request_id,
        input_hash=run.input_hash,
        conversation_id=conversation.id,
        cancel_reason="user",
    )
    snapshot = manager._persist_terminal(
        job,
        status="cancelled",
        error_code="cancelled_by_user",
        error_detail="too late",
    )
    assert snapshot.status == "succeeded"
    assert snapshot.result["text"] == "already committed"


def test_chat_run_lifecycle_controls_are_not_reblocked_after_admission():
    from types import SimpleNamespace
    from app.services.auth import _expensive_public_request

    status_request = SimpleNamespace(method="GET", url=SimpleNamespace(path="/v1/chat/runs/ui_123456789012"))
    cancel_request = SimpleNamespace(method="POST", url=SimpleNamespace(path="/v1/chat/runs/ui_123456789012/cancel"))
    chat_request = SimpleNamespace(method="POST", url=SimpleNamespace(path="/v1/chat"))
    assert _expensive_public_request(status_request) is False
    assert _expensive_public_request(cancel_request) is False
    assert _expensive_public_request(chat_request) is True


def test_application_lifecycle_opens_and_interrupts_chat_runtime():
    source = (ROOT / "app/main.py").read_text("utf-8")
    runtime = (ROOT / "app/services/chat_runtime.py").read_text("utf-8")
    assert "chat_execution_manager.startup()" in source
    assert "await chat_execution_manager.shutdown()" in source
    assert 'job.cancel_reason = "shutdown"' in runtime
    assert 'code = "runtime_shutdown"' in runtime
    assert "_MAX_RUN_ATTEMPTS = 3" in runtime
    assert "_MAX_TRANSIENT_RUNS_PER_USER = 256" in runtime
    assert ".with_for_update()" in runtime
    assert 'row.status == "succeeded" and status != "succeeded"' in runtime


def test_browser_transport_failure_keeps_same_logical_request_for_recovery():
    source = (ROOT / "app/user_ui.py").read_text("utf-8")
    assert "const pendingKey='x1_pending_chat_run'" in source
    assert "savePending(body)" in source
    assert "snapshot=await api('/v1/chat/runs/'" in source
    assert "'Восстановить'" in source
    assert "e.transport=true" in source
    assert "Повторная проверка использует тот же request-id" in source
    assert "Не отправляйте запрос повторно" in source


def test_stop_does_not_drop_pending_state_when_cancel_cannot_be_confirmed():
    source = (ROOT / "app/user_ui.py").read_text("utf-8")
    stop = source[source.index("async function stopActive") : source.index("function switchView")]
    assert "clearPending(id)" in stop
    assert "finally{clearPending(id)}" not in stop
    assert "Сохраняю запрос для проверки состояния" in stop
