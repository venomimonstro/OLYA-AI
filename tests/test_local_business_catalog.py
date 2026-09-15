from __future__ import annotations

import sqlite3
from pathlib import Path

from app.services.local_business_catalog import search_local_catalog


def _make_db(path: Path) -> None:
    connection = sqlite3.connect(path)
    connection.executescript(
        '''
        CREATE TABLE metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL);
        CREATE TABLE businesses (
            city TEXT NOT NULL,
            osm_type TEXT NOT NULL,
            osm_id INTEGER NOT NULL,
            name TEXT NOT NULL,
            name_norm TEXT NOT NULL,
            category TEXT NOT NULL,
            address TEXT NOT NULL DEFAULT '',
            phone TEXT NOT NULL DEFAULT '',
            website TEXT NOT NULL DEFAULT '',
            search_text TEXT NOT NULL DEFAULT '',
            PRIMARY KEY (city, osm_type, osm_id)
        );
        '''
    )
    connection.execute(
        'INSERT INTO businesses VALUES (?,?,?,?,?,?,?,?,?,?)',
        ('Москва', 'node', 123, 'Тест Авто', 'тест авто', 'car_repair', 'ул. Тестовая, 1', '+7 495 000-00-00', 'https://example.test', 'тест авто car_repair'),
    )
    connection.commit()
    connection.close()


def test_offline_catalog_finds_moscow_car_repair(tmp_path: Path) -> None:
    path = tmp_path / 'businesses.sqlite3'
    _make_db(path)
    rows = search_local_catalog('лучшие автосервисы в москве', path=path, limit=5)
    assert len(rows) == 1
    assert rows[0].name == 'Тест Авто'
    assert rows[0].provider == 'osm'
    assert rows[0].card_url == 'https://www.openstreetmap.org/node/123'
    assert rows[0].phone == '+7 495 000-00-00'


def test_offline_catalog_does_not_leak_city(tmp_path: Path) -> None:
    path = tmp_path / 'businesses.sqlite3'
    _make_db(path)
    rows = search_local_catalog('лучшие автосервисы в санкт-петербурге', path=path, limit=5)
    assert rows == []
