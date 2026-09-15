from __future__ import annotations

import unittest

from scripts.import_2gis_json import _record


class Import2GISJSONTests(unittest.TestCase):
    def test_autoservice_card_is_normalized(self) -> None:
        item = {
            "id": "70000001012345678_deadbeef",
            "name": "Тест Моторс, автосервис",
            "name_ex": {"primary": "Тест Моторс", "extension": "автосервис"},
            "address_name": "Ленинградское шоссе, 1",
            "adm_div": [
                {"type": "country", "name": "Россия"},
                {"type": "region", "name": "Москва"},
                {"type": "city", "name": "Москва"},
            ],
            "point": {"lat": 55.8, "lon": 37.5},
            "rubrics": [{"name": "Автосервисы"}, {"name": "Ремонт автомобилей"}],
            "contact_groups": [{"contacts": [
                {"type": "phone", "value": "+74950000000"},
                {"type": "website", "url": "https://example.ru/", "value": "https://example.ru/"},
            ]}],
            "schedule": {"Mon": {"working_hours": [{"from": "09:00", "to": "18:00"}]}},
        }
        row = _record(item, city="Москва", category_override="")
        self.assertIsNotNone(row)
        assert row is not None
        self.assertEqual(row["source_key"], "2gis:70000001012345678")
        self.assertEqual(row["name"], "Тест Моторс")
        self.assertEqual(row["category"], "car_repair")
        self.assertEqual(row["city"], "Москва")
        self.assertEqual(row["phone"], "+74950000000")
        self.assertEqual(row["website"], "https://example.ru/")
        self.assertIn("Ленинградское шоссе", row["address"])


if __name__ == "__main__":
    unittest.main()
