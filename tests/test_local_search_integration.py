from __future__ import annotations

import unittest
from unittest.mock import AsyncMock, patch

from app.services.discovery import SearchHit
from app.services.local_business_index import discover_local_businesses
from app.services.local_page_cache import LocalPageSnapshot
from app.services.local_search_discovery import LocalSearchDiscovery
from app.services.local_search_store import BusinessHit, PageHit
from app.services.research import ResearchFetcher


class _FakeStore:
    def search_businesses(self, query: str, *, city: str = '', category: str = '', limit: int = 10):
        _ = city, limit
        if category == 'car_repair' and query == '':
            return [
                BusinessHit(
                    id=1, name='Мотор Тест', category='car_repair', subcategory='car_repair', country='Россия',
                    region='', city='Москва', district='', address='Тестовая, 1', lat=55.75, lon=37.61,
                    phone='+7 495 000-00-00', website='https://motor.test', opening_hours='', source='osm',
                    source_url='https://www.openstreetmap.org/node/1', source_id='node/1', confidence=0.8,
                    updated_at='2026-09-15T00:00:00+00:00', score=0.0,
                )
            ]
        return []

    def search_pages(self, query: str, *, domains=(), limit: int = 8):
        _ = query, domains, limit
        return [
            PageHit(
                id=1, url='https://example.org/page', domain='example.org', title='Локальная статья',
                description='Сохранённое описание', content='Полный сохранённый текст про локальный поиск.',
                modified_at='', fetched_at='2026-09-15T00:00:00+00:00', score=-1.0,
            )
        ]


class LocalSearchIntegrationTests(unittest.IsolatedAsyncioTestCase):
    def test_known_business_category_does_not_depend_on_russian_morphology_in_fts(self) -> None:
        with patch('app.services.local_business_index.get_local_search_store', return_value=_FakeStore()):
            rows = discover_local_businesses('лучшие автосервисы в москве', limit=10)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].name, 'Мотор Тест')
        self.assertEqual(rows[0].provider, 'osm')

    async def test_local_discovery_returns_indexed_pages(self) -> None:
        with patch('app.services.local_search_discovery.get_local_search_store', return_value=_FakeStore()):
            rows = await LocalSearchDiscovery().search('локальный поиск', count=5, language='ru')
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].provider, 'olya_local_index')
        self.assertEqual(rows[0].url, 'https://example.org/page')

    async def test_research_fetcher_uses_local_snapshot_without_network(self) -> None:
        snapshot = LocalPageSnapshot(
            url='https://example.org/page', title='Сохранённая страница',
            content='Текст уже находится в локальном индексе.', fetched_at='2026-09-15T00:00:00+00:00',
        )
        with patch('app.services.research.get_local_page', return_value=snapshot), patch(
            'app.services.research.validate_public_url', new=AsyncMock(side_effect=AssertionError('network path must not run'))
        ):
            page = await ResearchFetcher(timeout_seconds=1).fetch('https://example.org/page')
        self.assertEqual(page.content, snapshot.content)
        self.assertEqual(page.metadata.get('source'), 'olya_local_index')


if __name__ == '__main__':
    unittest.main()
