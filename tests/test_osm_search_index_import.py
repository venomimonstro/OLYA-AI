from scripts.import_osm_search_index import canonical_category, feature_record, infer_city


def test_car_repair_category_is_canonical():
    category, subcategory = canonical_category({'shop': 'car_repair'})
    assert category == 'car_repair'
    assert subcategory == 'car_repair'


def test_moscow_city_fallback_from_coordinates():
    assert infer_city({}, 55.75, 37.62) == 'Москва'


def test_osm_feature_becomes_search_record():
    feature = {
        'type': 'Feature',
        'geometry': {'type': 'Point', 'coordinates': [37.62, 55.75]},
        'properties': {
            '@id': 'node/123456789',
            'name': 'Тест Авто',
            'shop': 'car_repair',
            'addr:street': 'Тестовая улица',
            'addr:housenumber': '1',
            'phone': '+7 495 000-00-00',
            'website': 'https://example.test',
        },
    }
    record = feature_record(feature)
    assert record is not None
    assert record['name'] == 'Тест Авто'
    assert record['category'] == 'car_repair'
    assert record['city'] == 'Москва'
    assert record['source_key'] == 'osm:node:123456789'
    assert record['source_url'].endswith('/node/123456789')
