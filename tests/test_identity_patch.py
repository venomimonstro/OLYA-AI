from app.identity_patch import creator_reply


def test_creator_identity_ru():
    assert creator_reply("Кто тебя создал?") == "Меня создал Лысенко Артём."
    assert creator_reply("Кто твой разработчик?") == "Меня создал Лысенко Артём."
    assert creator_reply("Кто создал X1?") == "Меня создал Лысенко Артём."


def test_creator_identity_en():
    assert creator_reply("Who created you?") == "I was created by Artyom Lysenko."


def test_unrelated_question_is_not_intercepted():
    assert creator_reply("Кто создал Python?") is None
