from app.services.yell_discovery import _direct_category, _listing_places


def test_direct_autoservice_category():
    assert _direct_category("лучшие автосервисы в москве", "moscow") == (
        "https://www.yell.ru/moscow/top/avtoservisy-i-tyuning/"
    )


def test_parse_server_rendered_yell_listing():
    body = r'''
    <section>
      <a href="/moscow/com/tokio-servis_4890336/"><h2>Автосервис Токио Сервис</h2></a>
      <div>Россия, г Москва, проезд Научный, д 14А стр 5</div>
      <div>4.6 590 отзывов</div>
      <div>8 (926) 651-74-48</div>
    </section>
    <section>
      <a href="/moscow/com/avtopilot_1234567/"><h2>Автосервис Автопилот</h2></a>
      <div>г Москва, ул Маршала Рыбалко, д 2</div>
      <div>4.9 128 отзывов</div>
      <div>8 (966) 295-25-81</div>
    </section>
    '''
    rows = _listing_places(
        "https://www.yell.ru/moscow/top/avtoservisy-i-tyuning/",
        body,
        "moscow",
        "Москва",
        8,
    )
    assert len(rows) == 2
    by_name = {row.name: row for row in rows}
    tokio = by_name["Автосервис Токио Сервис"]
    assert tokio.card_url == "https://www.yell.ru/moscow/com/tokio-servis_4890336/"
    assert tokio.rating == 4.6
    assert tokio.reviews == 590
    assert tokio.phone == "+7 (926) 651-74-48"
    assert "Москва" in tokio.address

    autopilot = by_name["Автосервис Автопилот"]
    assert autopilot.rating == 4.9
    assert autopilot.reviews == 128
