from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from app.services.local_search_store import LocalSearchStore


class LocalSearchStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.store = LocalSearchStore(str(Path(self.tmp.name) / 'search.sqlite3'))
        self.store.ensure_schema()

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_category_and_city_business_search(self) -> None:
        self.store.upsert_business({
            'name': 'Тест Авто', 'category': 'car_repair', 'subcategory': 'car_repair',
            'city': 'Москва', 'address': 'Тестовая улица, 1', 'phone': '+7 495 000-00-00',
            'source': 'osm', 'source_id': 'node/123', 'source_url': 'https://www.openstreetmap.org/node/123',
            'lat': 55.75, 'lon': 37.61, 'confidence': 0.8,
        })
        rows = self.store.search_businesses('', category='car_repair', limit=10)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].name, 'Тест Авто')
        self.assertEqual(rows[0].city, 'Москва')

    def test_business_fts_by_name(self) -> None:
        self.store.upsert_business({
            'name': 'Север Моторс', 'category': 'car_repair', 'city': 'Москва',
            'source': 'osm', 'source_id': 'node/124',
        })
        rows = self.store.search_businesses('Север', limit=10)
        self.assertEqual([row.name for row in rows], ['Север Моторс'])

    def test_web_fts(self) -> None:
        self.store.upsert_page(
            url='https://example.org/article',
            title='Как выбрать автосервис',
            description='Практическое руководство',
            content='Проверяйте документы, специализацию и условия гарантии на ремонт автомобиля.',
        )
        rows = self.store.search_pages('гарантия ремонт', limit=5)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].url, 'https://example.org/article')

    def test_geo_nearby(self) -> None:
        self.store.upsert_business({
            'name': 'Рядом', 'category': 'cafe', 'city': 'Москва', 'source': 'osm', 'source_id': 'node/200',
            'lat': 55.7501, 'lon': 37.6101,
        })
        self.store.upsert_business({
            'name': 'Далеко', 'category': 'cafe', 'city': 'Москва', 'source': 'osm', 'source_id': 'node/201',
            'lat': 56.5, 'lon': 38.5,
        })
        rows = self.store.nearby(lat=55.75, lon=37.61, radius_km=5, category='cafe', limit=10)
        self.assertTrue(rows)
        self.assertEqual(rows[0].name, 'Рядом')
        self.assertNotIn('Далеко', [row.name for row in rows])


if __name__ == '__main__':
    unittest.main()
