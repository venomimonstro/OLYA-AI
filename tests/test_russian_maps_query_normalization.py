from __future__ import annotations

import unittest

from app.services.russian_maps_discovery import _provider_question


class RussianMapsQueryNormalizationTests(unittest.TestCase):
    def test_city_case_is_normalized_once(self):
        cases = (
            ("лучший сервис москвы", "Москва", "лучший сервис Москва"),
            ("лучшие автосервисы Москва", "Москва", "лучшие автосервисы Москва"),
            ("найди юриста в Москве", "Москва", "найди юриста Москва"),
            ("топ стоматологий Казани", "Казань", "топ стоматологий Казань"),
        )
        for query, city, expected in cases:
            with self.subTest(query=query):
                self.assertEqual(_provider_question(query, city), expected)


if __name__ == "__main__":
    unittest.main()
