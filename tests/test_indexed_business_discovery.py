from __future__ import annotations

import unittest

from app.services.discovery import SearchHit
from app.services.indexed_business_discovery import _clean_name, _provider_for


class IndexedBusinessDiscoveryTests(unittest.TestCase):
    def test_twogis_moscow_card_is_accepted(self):
        self.assertEqual(
            _provider_for("https://2gis.ru/moscow/firm/70000001012345678", "moscow", "msk"),
            "2gis",
        )

    def test_wrong_twogis_city_is_rejected(self):
        self.assertEqual(
            _provider_for("https://2gis.ru/ufa/firm/70000001012345678", "moscow", "msk"),
            "",
        )

    def test_yell_moscow_card_is_accepted(self):
        self.assertEqual(
            _provider_for("https://www.yell.ru/moscow/com/test-service_1234567/", "moscow", "msk"),
            "yell",
        )

    def test_zoon_category_page_is_rejected(self):
        self.assertEqual(_provider_for("https://zoon.ru/msk/autoservice/", "moscow", "msk"), "")

    def test_zoon_business_card_is_accepted(self):
        self.assertEqual(
            _provider_for("https://zoon.ru/msk/autoservice/avtoservis_test/", "moscow", "msk"),
            "zoon",
        )

    def test_directory_suffix_is_removed_from_title(self):
        hit = SearchHit(
            query="q",
            title="Автопилот — 2ГИС",
            url="https://2gis.ru/moscow/firm/70000001012345678",
            snippet="",
            rank=1,
            provider="test",
        )
        self.assertEqual(_clean_name(hit), "Автопилот")


if __name__ == "__main__":
    unittest.main()
