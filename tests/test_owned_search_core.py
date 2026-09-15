from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import app.services.local_search_store as store_module
from app.services.local_business_index import discover_local_businesses
from app.services.local_search_discovery import LocalSearchDiscovery
from app.services.local_search_store import LocalSearchStore
from app.services.response_completeness import needs_expansion


class OwnedSearchCoreTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.previous_store = store_module._STORE
        self.store = LocalSearchStore(str(Path(self.temp.name) / "olya_search.db"))
        self.store.ensure_schema()
        store_module._STORE = self.store

    def tearDown(self) -> None:
        store_module._STORE = self.previous_store
        self.temp.cleanup()

    async def test_moscow_car_repair_is_found_without_network(self) -> None:
        self.store.upsert_business({
            "source_key": "osm:node:1001",
            "source": "osm",
            "source_id": "1001",
            "source_url": "https://www.openstreetmap.org/node/1001",
            "name": "Тест Авто",
            "category": "car_repair",
            "subcategory": "car_repair",
            "country": "Россия",
            "city": "Москва",
            "address": "Москва, Тестовая улица, 1",
            "phone": "+7 495 000-00-00",
            "website": "https://example.test/",
            "confidence": 0.9,
        })
        rows = discover_local_businesses("лучшие автосервисы в москве", limit=10)
        self.assertTrue(rows)
        self.assertEqual(rows[0].name, "Тест Авто")
        self.assertEqual(rows[0].provider, "osm")

    async def test_owned_page_index_is_a_search_provider(self) -> None:
        self.store.upsert_page(
            url="https://example.test/service",
            title="Ремонт автомобилей в Москве",
            description="Автосервис и диагностика",
            content="Ремонт автомобилей, диагностика двигателя и техническое обслуживание в Москве.",
        )
        discovery = LocalSearchDiscovery()
        rows = await discovery.search("ремонт автомобилей москва", count=5, language="ru")
        self.assertTrue(rows)
        self.assertEqual(rows[0].provider, "olya_local")
        self.assertEqual(rows[0].url, "https://example.test/service")

    async def test_complex_one_line_answer_fails_completeness_gate(self) -> None:
        question = "Проведи подробный аудит архитектуры проекта, найди слабые места и предложи план улучшений"
        self.assertTrue(needs_expansion(question, "Архитектуру стоит улучшить."))


if __name__ == "__main__":
    unittest.main()
