from app.services.response_completeness import contract_for, needs_expansion


def test_complex_technical_question_rejects_one_line():
    question = 'Как лучше реализовать свою поисковую систему и геобазу компаний РФ на дешёвом сервере?'
    contract = contract_for(question)
    assert contract.min_chars >= 700
    assert needs_expansion(question, 'Используйте SQLite и OpenStreetMap.') is True


def test_decision_question_requires_practical_conclusion():
    question = 'Что лучше выбрать для локального поиска: SQLite FTS5 или Elasticsearch и почему?'
    answer = ('SQLite FTS5 экономнее по памяти. Elasticsearch мощнее при большом масштабе. '
              'Для небольшого сервера важны RAM, диск и простота эксплуатации. ' * 12)
    assert needs_expansion(question, answer) is True


def test_explicit_short_request_is_respected():
    question = 'Кратко, в одной строке: что такое FTS5?'
    assert needs_expansion(question, 'FTS5 — полнотекстовый поисковый модуль SQLite.') is False


def test_atomic_answer_may_be_short():
    assert needs_expansion('Сколько будет 2+2?', '4') is False
