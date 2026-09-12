import pytest

from scripts.load_acceptance import _chat_payload, _validate_target_transport
from app.schemas.chat import ChatRequest


def test_load_acceptance_rejects_remote_plain_http_before_tokens():
    with pytest.raises(RuntimeError, match="requires HTTPS"):
        _validate_target_transport("http://example.com")


def test_load_acceptance_allows_loopback_http_and_remote_https():
    _validate_target_transport("http://127.0.0.1:8000")
    _validate_target_transport("http://localhost:8000")
    _validate_target_transport("https://example.com")


def test_load_acceptance_payload_matches_current_chat_schema():
    payload = _chat_payload(1, 1)
    request = ChatRequest.model_validate(payload)
    assert request.mode == "fast"
    assert request.verification == "off"
    assert request.messages[0].role == "user"
