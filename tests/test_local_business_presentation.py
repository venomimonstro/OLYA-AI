from __future__ import annotations

import unittest

from app.services.local_business_search import _render
from app.services.public_maps_discovery import MapPlace


class LocalBusinessPresentationTests(unittest.TestCase):
    def test_rich_answer_prefers_stable_rating_signal(self) -> None:
        places = [
            MapPlace(
                provider="2gis",
                name="Один отзыв",
                card_url="https://2gis.ru/moscow/firm/1",
                address="Москва, улица А, 1",
                phone="+74950000001",
                website="https://one.example/",
                rating=5.0,
                reviews=1,
            ),
            MapPlace(
                provider="2gis",
                name="Проверенный сервис",
                card_url="https://2gis.ru/moscow/firm/2",
                address="Москва, улица Б, 2",
                phone="+74950000002",
                website="https://stable.example/",
                rating=4.8,
                reviews=600,
            ),
        ]
        result = _render(
            "лучшие автосервисы в москве",
            places,
            [place.public_source() for place in places],
            cache_only=True,
        )
        self.assertLess(result.text.find("Проверенный сервис"), result.text.find("Один отзыв"))
        self.assertIn("Почему в подборке", result.text)
        self.assertIn("Что выбрать", result.text)
        self.assertIn("600 отзывов", result.text)
        self.assertNotIn("Использую проверенные данные из локального индекса", result.text)
        self.assertNotIn("поиск на карте", result.text)


if __name__ == "__main__":
    unittest.main()
