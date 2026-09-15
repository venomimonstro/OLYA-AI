from __future__ import annotations

import unittest

from app.services.twogis_ssr_discovery import _from_anchor_matches, _from_embedded_state


class TwoGisSsrDiscoveryTests(unittest.TestCase):
    def test_extracts_direct_firm_anchor(self) -> None:
        body = '''
        <div>
          <a href="/moscow/firm/70000001083408189">Автопилот</a>
          <span>4.8</span><span>51 оценка</span>
          <span>2-я улица Машиностроения, 27 ст2, Москва</span>
        </div>
        '''
        rows = _from_anchor_matches(body, "moscow", "https://2gis.ru/moscow/search/test")
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].name, "Автопилот")
        self.assertEqual(rows[0].card_url, "https://2gis.ru/moscow/firm/70000001083408189")
        self.assertEqual(rows[0].rating, 4.8)
        self.assertEqual(rows[0].reviews, 51)

    def test_extracts_firm_from_escaped_ssr_state(self) -> None:
        body = r'''
        <script>
        window.__STATE__={"items":[{"name":"ТРС-Моторс","full_address_name":"Автозаводская улица, 20 ст9, Москва","rating":5,"review_count":128,"url":"https:\/\/2gis.ru\/moscow\/firm\/70000001046059103"}]};
        </script>
        '''
        rows = _from_embedded_state(body, "moscow", "https://2gis.ru/moscow/search/test")
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].name, "ТРС-Моторс")
        self.assertEqual(rows[0].card_url, "https://2gis.ru/moscow/firm/70000001046059103")
        self.assertEqual(rows[0].address, "Автозаводская улица, 20 ст9, Москва")
        self.assertEqual(rows[0].rating, 5.0)
        self.assertEqual(rows[0].reviews, 128)

    def test_rejects_wrong_city_namespace(self) -> None:
        body = r'''{"name":"Чужой сервис","url":"https:\/\/2gis.ru\/spb\/firm\/70000001046059103"}'''
        rows = _from_embedded_state(body, "moscow", "https://2gis.ru/moscow/search/test")
        self.assertEqual(rows, [])


if __name__ == "__main__":
    unittest.main()
