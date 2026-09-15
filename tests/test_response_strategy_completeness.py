from __future__ import annotations

import unittest

from app.services.response_completeness import needs_expansion
from app.services.response_strategy import is_atomic_knowledge_question


class ResponseStrategyCompletenessTests(unittest.TestCase):
    def test_simple_definition_can_be_atomic(self) -> None:
        self.assertTrue(is_atomic_knowledge_question('Что такое CDN?'))

    def test_definition_plus_usage_is_not_atomic(self) -> None:
        self.assertFalse(is_atomic_knowledge_question('Что такое CDN и как его правильно использовать для интернет-магазина?'))

    def test_definition_plus_tradeoffs_is_not_atomic(self) -> None:
        self.assertFalse(is_atomic_knowledge_question('Что такое RAG, какие у него плюсы и минусы и когда его использовать?'))

    def test_one_line_complex_answer_requires_repair(self) -> None:
        question = 'Сравни SQLite и Elasticsearch для локального поисковика и порекомендуй архитектуру.'
        answer = 'SQLite будет дешевле.'
        self.assertTrue(needs_expansion(question, answer))

    def test_explicit_short_request_is_respected(self) -> None:
        question = 'Кратко, одной строкой: что такое CDN?'
        self.assertFalse(needs_expansion(question, 'CDN — сеть серверов для доставки контента ближе к пользователю.'))


if __name__ == '__main__':
    unittest.main()
