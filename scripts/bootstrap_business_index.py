from __future__ import annotations

import argparse
import gzip
import io
import sys
import urllib.request
import xml.etree.ElementTree as ET
from collections import defaultdict

from app.services.business_local_index import index_stats, store_places
from app.services.public_maps_discovery import MapPlace

CITY_EXTRACTS = {
    "москва": "https://download.bbbike.org/osm/bbbike/Moscow/Moscow.osm.gz",
}

CATEGORY_RULES: tuple[tuple[str, tuple[tuple[str, str], ...]], ...] = (
    ("автосервис", (("shop", "car_repair"), ("amenity", "car_repair"))),
    ("шиномонтаж", (("shop", "tyres"),)),
    ("автомойка", (("amenity", "car_wash"),)),
    ("слухопротезирование", (("shop", "hearing_aids"), ("healthcare", "audiologist"))),
    ("стоматология", (("amenity", "dentist"), ("healthcare", "dentist"))),
    ("медицина", (("amenity", "clinic"), ("healthcare", "clinic"))),
    ("аптеки", (("amenity", "pharmacy"),)),
    ("ветеринария", (("amenity", "veterinary"),)),
    ("юристы", (("office", "lawyer"),)),
    ("недвижимость", (("office", "estate_agent"),)),
    ("рестораны", (("amenity", "restaurant"),)),
    ("кафе", (("amenity", "cafe"),)),
    ("фитнес", (("leisure", "fitness_centre"),)),
    ("красота", (("shop", "beauty"),)),
    ("парикмахерские", (("shop", "hairdresser"),)),
    ("химчистка", (("shop", "dry_cleaning"),)),
    ("гостиницы", (("tourism", "hotel"), ("tourism", "hostel"))),
)


def _name(tags: dict[str, str]) -> str:
    return (tags.get("name") or tags.get("brand") or tags.get("operator") or "").strip()[:140]


def _address(tags: dict[str, str]) -> str:
    full = tags.get("addr:full", "").strip()
    if full:
        return full[:220]
    street = (tags.get("addr:street") or tags.get("addr:place") or "").strip()
    number = tags.get("addr:housenumber", "").strip()
    city = tags.get("addr:city", "").strip()
    parts: list[str] = []
    if street:
        parts.append(street + (f", {number}" if number else ""))
    elif number:
        parts.append(number)
    if city:
        parts.append(city)
    return ", ".join(parts)[:220]


def _categories(tags: dict[str, str]) -> list[str]:
    result: list[str] = []
    for category, selectors in CATEGORY_RULES:
        if any(tags.get(key) == value for key, value in selectors):
            result.append(category)
    return result


def _place(element_type: str, element_id: int, tags: dict[str, str]) -> MapPlace | None:
    name = _name(tags)
    if not name:
        return None
    website = (tags.get("contact:website") or tags.get("website") or "").strip()[:500]
    phone = (tags.get("contact:phone") or tags.get("phone") or "").strip()[:80]
    card = f"https://www.openstreetmap.org/{element_type}/{element_id}"
    return MapPlace(provider="osm", name=name, card_url=card, address=_address(tags), phone=phone, website=website, source_url=card)


def _parse_stream(stream, city: str) -> dict[str, list[MapPlace]]:
    grouped: dict[str, list[MapPlace]] = defaultdict(list)
    seen: set[tuple[str, str]] = set()
    for _event, elem in ET.iterparse(stream, events=("end",)):
        tag = elem.tag.rsplit("}", 1)[-1]
        if tag not in {"node", "way", "relation"}:
            continue
        try:
            element_id = int(elem.attrib.get("id", "0"))
        except ValueError:
            elem.clear(); continue
        tags = {child.attrib.get("k", ""): child.attrib.get("v", "") for child in elem if child.tag.rsplit("}", 1)[-1] == "tag"}
        categories = _categories(tags)
        if categories:
            place = _place(tag, element_id, tags)
            if place is not None:
                for category in categories:
                    key = (category, place.card_url)
                    if key not in seen:
                        seen.add(key); grouped[category].append(place)
        elem.clear()
    return grouped


def bootstrap(city: str) -> int:
    url = CITY_EXTRACTS.get(city)
    if not url:
        print(f"unsupported city: {city}")
        return 2
    request = urllib.request.Request(url, headers={"User-Agent": "OLYA-AI/1.0 OSM offline index bootstrap"})
    print("download:", url)
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            with gzip.GzipFile(fileobj=response) as gz:
                grouped = _parse_stream(gz, city)
    except Exception as exc:
        print(f"bootstrap failed: {type(exc).__name__}: {exc}")
        return 3

    total = 0
    for category, places in sorted(grouped.items()):
        store_places(f"{category} в {city}", places)
        total += len(places)
        print(f"{category}: {len(places)}")
    print("imported:", total)
    print("index:", index_stats())
    return 0 if total else 4


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--city", default="москва")
    args = parser.parse_args()
    return bootstrap(args.city.casefold().strip())


if __name__ == "__main__":
    raise SystemExit(main())
