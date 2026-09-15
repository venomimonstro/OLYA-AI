from __future__ import annotations

import unittest

from app.services.yandex_medicine_discovery import (
    _matches_requested_city,
    parse_yandex_medicine_page,
)


class YandexMedicineDiscoveryTests(unittest.TestCase):
    def _card(self, *, title: str, address: str, phone_fragment: str = ""):
        body = f"""
        <html><head><title>{title}</title></head><body>
        <h1>Тестовый центр Информация об организации подтверждена владельцем.</h1>
        <script>
        {{"oid":"1234567890","rating":{{"value":4.9}},"reviewsCount":30,
          "Address":{{"text":"{address}"}} {phone_fragment}}}
        </script>
        </body></html>
        """
        card = parse_yandex_medicine_page(
            "https://yandex.ru/medicine/clinic/test_1234567890",
            body,
        )
        self.assertIsNotNone(card)
        return card

    def test_rejects_other_city(self):
        card = self._card(title="Тестовый центр — Уфа", address="ул. Достоевского, 99, Уфа")
        self.assertFalse(_matches_requested_city("лучшие центры в москве", card))

    def test_accepts_moscow_from_page_title_when_address_omits_city(self):
        card = self._card(title="Тестовый центр — Москва, метро Митино", address="Митинская ул., 31")
        self.assertTrue(_matches_requested_city("лучшие центры в москве", card))

    def test_owner_confirmation_suffix_is_removed(self):
        card = self._card(title="Тестовый центр — Москва", address="Профсоюзная ул., 78")
        self.assertEqual(card.place.name, "Тестовый центр")

    def test_rejects_garbage_long_phone(self):
        card = self._card(
            title="Тестовый центр — Москва",
            address="Профсоюзная ул., 78",
            phone_fragment=',"phone":"89463115992894-3765"',
        )
        self.assertEqual(card.place.phone, "")

    def test_normalizes_real_russian_phone(self):
        card = self._card(
            title="Тестовый центр — Москва",
            address="Профсоюзная ул., 78",
            phone_fragment=',"telephone":"+7 (495) 150-12-34"',
        )
        self.assertEqual(card.place.phone, "+7 (495) 150-12-34")


if __name__ == "__main__":
    unittest.main()
