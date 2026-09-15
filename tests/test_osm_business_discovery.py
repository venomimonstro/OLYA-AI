from __future__ import annotations

import unittest

from app.services.osm_business_discovery import build_overpass_query, parse_overpass_payload


class OSMBusinessDiscoveryTests(unittest.TestCase):
    def test_car_service_query_uses_car_repair_tags(self):
        query = build_overpass_query("лучшие автосервисы в москве")
        self.assertIn('area["boundary"="administrative"]["name"="Москва"]', query)
        self.assertIn('["shop"="car_repair"]', query)
        self.assertIn('["service:vehicle:car_repair"]', query)

    def test_parse_overpass_business_card(self):
        payload = {
            "elements": [
                {
                    "type": "node",
                    "id": 123456789,
                    "tags": {
                        "name": "Тест Автосервис",
                        "shop": "car_repair",
                        "addr:street": "Тестовая улица",
                        "addr:housenumber": "10",
                        "addr:city": "Москва",
                        "contact:phone": "+7 495 123-45-67",
                        "contact:website": "https://example.test",
                        "opening_hours": "Mo-Su 09:00-21:00",
                    },
                }
            ]
        }
        rows = parse_overpass_payload(payload, "лучшие автосервисы в москве")
        self.assertEqual(len(rows), 1)
        row = rows[0]
        self.assertEqual(row.provider, "osm")
        self.assertEqual(row.name, "Тест Автосервис")
        self.assertEqual(row.address, "Тестовая улица, 10, Москва")
        self.assertEqual(row.phone, "+7 495 123-45-67")
        self.assertEqual(row.website, "https://example.test")
        self.assertEqual(row.card_url, "https://www.openstreetmap.org/node/123456789")

    def test_unknown_category_falls_back_to_name_search(self):
        query = build_overpass_query("лучший сервис москвы")
        self.assertIn('["name"~"сервис",i]', query)


if __name__ == "__main__":
    unittest.main()
