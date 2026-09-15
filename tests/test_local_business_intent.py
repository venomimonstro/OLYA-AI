from __future__ import annotations

import unittest

from app.services.local_business_search import is_local_business_question


class LocalBusinessIntentTests(unittest.TestCase):
    def test_routes_city_without_preposition(self):
        cases = (
            "лучший сервис москвы",
            "лучшие автосервисы Москва",
            "топ стоматологий Москвы",
            "рейтинг юридических компаний Москва",
            "порекомендуй автосервис Москвы",
        )
        for query in cases:
            with self.subTest(query=query):
                self.assertTrue(is_local_business_question(query))

    def test_routes_regular_local_phrases(self):
        cases = (
            "найди юриста в Москве",
            "лучшие центры слухопротезирования в москве",
            "посоветуй кафе в Казани",
            "лучшие автосервисы в Екатеринбурге",
        )
        for query in cases:
            with self.subTest(query=query):
                self.assertTrue(is_local_business_question(query))

    def test_routes_compact_commercial_queries(self):
        cases = (
            "автосервисы Москва",
            "стоматологии Москвы отзывы",
            "юристы Москва цены",
            "химчистки Казань",
        )
        for query in cases:
            with self.subTest(query=query):
                self.assertTrue(is_local_business_question(query))

    def test_does_not_route_known_non_business_queries(self):
        cases = (
            "погода в Москве",
            "история Москвы",
            "население Москвы",
            "мэр Москвы",
            "новости Москвы",
        )
        for query in cases:
            with self.subTest(query=query):
                self.assertFalse(is_local_business_question(query))


if __name__ == "__main__":
    unittest.main()
