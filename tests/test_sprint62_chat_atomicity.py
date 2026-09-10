from __future__ import annotations

from sqlalchemy import select

from app.models import ChatRun, Conversation, Message, UsageEvent
from app.schemas.chat import ChatMessage, ChatRequest, ChatResponse
from app.services.chat_runtime import request_fingerprint


def _run(db_session, *, user_id: str, conversation_id: str, request_id: str = "ui_atomic_123456") -> ChatRun:
    payload = ChatRequest(
        messages=[ChatMessage(role="user", content="atomic answer")],
        client_request_id=request_id,
    )
    row = ChatRun(
        user_id=user_id,
        conversation_id=conversation_id,
        client_request_id=request_id,
        input_hash=request_fingerprint(user_id, payload),
        status="running",
        runtime_id="runtime-before-crash",
        attempt=1,
        result_json={},
        error_code="",
        error_detail="",
    )
    db_session.add(row)
    db_session.commit()
    return row


def _usage(*, user_id: str, conversation_id: str, request_id: str, success: bool = True) -> UsageEvent:
    return UsageEvent(
        user_id=user_id,
        project_id=None,
        conversation_id=conversation_id,
        mode="work",
        raw_chars=100,
        compiled_chars=80,
        output_chars=16,
        duration_ms=2000,
        inference_ms=1500,
        queue_ms=100,
        success=success,
        request_id=request_id,
    )


def test_successful_usage_and_assistant_make_chat_run_terminal_in_same_commit(register_user, db_session):
    data, _ = register_user("sprint62-atomic@example.com")
    conversation = Conversation(owner_id=data["user_id"], title="Atomic")
    db_session.add(conversation)
    db_session.commit()

    run = _run(db_session, user_id=data["user_id"], conversation_id=conversation.id)
    db_session.add(Message(conversation_id=conversation.id, role="assistant", content="canonical result"))
    db_session.add(_usage(user_id=data["user_id"], conversation_id=conversation.id, request_id=run.id))
    db_session.commit()
    db_session.expire_all()

    persisted = db_session.get(ChatRun, run.id)
    assert persisted.status == "succeeded"
    assert persisted.completed_at is not None
    assert persisted.result_json["text"] == "canonical result"
    assert persisted.result_json["conversation_id"] == conversation.id
    assert persisted.result_json["run_id"] == run.id
    assert persisted.result_json["client_request_id"] == run.client_request_id
    assert persisted.result_json["recovered_from_canonical_commit"] is True

    # The compact crash-recovery payload remains schema-valid for the normal
    # replay path. A healthy manager subsequently replaces it with full metrics.
    ChatResponse.model_validate(persisted.result_json)


def test_atomicity_survives_intermediate_verification_flush(register_user, db_session):
    data, _ = register_user("sprint62-atomic-flush@example.com")
    conversation = Conversation(owner_id=data["user_id"], title="Verified")
    db_session.add(conversation)
    db_session.commit()
    run = _run(
        db_session,
        user_id=data["user_id"],
        conversation_id=conversation.id,
        request_id="ui_verified_123456",
    )

    # Mirrors chat.py: assistant/quality state may be flushed before UsageEvent.
    db_session.add(Message(conversation_id=conversation.id, role="assistant", content="verified result"))
    db_session.flush()
    db_session.add(_usage(user_id=data["user_id"], conversation_id=conversation.id, request_id=run.id))
    db_session.commit()
    db_session.expire_all()

    persisted = db_session.get(ChatRun, run.id)
    assert persisted.status == "succeeded"
    assert persisted.result_json["text"] == "verified result"
    ChatResponse.model_validate(persisted.result_json)


def test_failed_usage_does_not_fake_success(register_user, db_session):
    data, _ = register_user("sprint62-atomic-failed@example.com")
    conversation = Conversation(owner_id=data["user_id"], title="Failed")
    db_session.add(conversation)
    db_session.commit()
    run = _run(db_session, user_id=data["user_id"], conversation_id=conversation.id, request_id="ui_failed_123456")

    db_session.add(Message(conversation_id=conversation.id, role="assistant", content="partial not canonical"))
    db_session.add(_usage(user_id=data["user_id"], conversation_id=conversation.id, request_id=run.id, success=False))
    db_session.commit()
    db_session.expire_all()
    assert db_session.get(ChatRun, run.id).status == "running"


def test_successful_chat_run_is_sticky_against_late_cancel_or_failure(register_user, db_session):
    data, _ = register_user("sprint62-sticky@example.com")
    conversation = Conversation(owner_id=data["user_id"], title="Sticky")
    db_session.add(conversation)
    db_session.commit()
    run = _run(db_session, user_id=data["user_id"], conversation_id=conversation.id, request_id="ui_sticky_123456")

    db_session.add(Message(conversation_id=conversation.id, role="assistant", content="already committed"))
    db_session.add(_usage(user_id=data["user_id"], conversation_id=conversation.id, request_id=run.id))
    db_session.commit()
    original = dict(run.result_json)
    completed_at = run.completed_at

    # Simulate ChatExecutionManager receiving a very late cancellation after the
    # canonical success transaction became durable.
    run.status = "cancelled"
    run.result_json = {}
    run.error_code = "cancelled_by_user"
    run.error_detail = "too late"
    db_session.commit()
    db_session.expire_all()

    persisted = db_session.get(ChatRun, run.id)
    assert persisted.status == "succeeded"
    assert persisted.result_json == original
    assert persisted.error_code == ""
    assert persisted.error_detail == ""
    assert persisted.completed_at == completed_at


def test_atomicity_guard_is_registered_from_canonical_model_registry():
    import app.models as models
    import app.services.chat_run_atomicity as guard

    assert hasattr(models, "ChatRun")
    assert callable(guard._compact_result)
    source = (guard.__file__ and __import__("pathlib").Path(guard.__file__).read_text("utf-8")) or ""
    assert "Verification can flush AnswerAudit before UsageEvent" in source
