from app.utility_chat import utility_reply


def test_moscow_time_is_local_utility():
    reply = utility_reply("Какое сейчас время в Москве?")
    assert reply is not None
    assert reply.kind == "local_time"
    assert reply.text.startswith("Сейчас в Москве ")
    assert ":" in reply.text


def test_creator_identity_is_utility():
    reply = utility_reply("Кто тебя создал?")
    assert reply is not None
    assert reply.kind == "identity"
    assert reply.text == "Меня создал Лысенко Артём."


def test_greeting_is_utility():
    reply = utility_reply("привет")
    assert reply is not None
    assert reply.kind == "greeting"
    assert reply.text == "Привет! Чем могу помочь?"


def test_normal_question_is_not_utility():
    assert utility_reply("Объясни, что такое SEO") is None
