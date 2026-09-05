from app.schemas.chat import ChatMessage
from app.services.context import ContextCompiler


def test_context_compiler_hard_caps_long_chats_and_keeps_latest_turn():
    compiler = ContextCompiler(max_chars=5000, max_message_chars=1800)
    messages = [ChatMessage(role="system", content="trusted")]
    for i in range(200):
        messages.append(ChatMessage(role="user" if i % 2 == 0 else "assistant", content=(f"turn-{i}-" + "x" * 500)))
    messages.append(ChatMessage(role="user", content="LATEST:" + "z" * 4000))
    out = compiler.compile(messages)
    assert sum(len(x.content) for x in out) <= 5000
    assert out[-1].role == "user"
    assert "z" * 100 in out[-1].content
    assert len(out) < len(messages)


def test_context_compiler_deduplicates_without_losing_newest():
    compiler = ContextCompiler(max_chars=8000)
    duplicate = ChatMessage(role="assistant", content="same")
    out = compiler.compile([duplicate, duplicate, ChatMessage(role="user", content="new")])
    assert [x.content for x in out] == ["same", "new"]
