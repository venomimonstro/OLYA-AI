from __future__ import annotations

import argparse
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from app.services.local_search_store import get_local_search_store


_CATEGORY_RULES: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"автосервис|авторемонт|ремонт\s+автомоб|сто\b", re.I), "car_repair"),
    (re.compile(r"шиномонтаж|шины|покрыш", re.I), "tyres"),
    (re.compile(r"автомойк", re.I), "car_wash"),
    (re.compile(r"слухопротез|слухов.*аппарат|сурдолог|аудиолог", re.I), "hearing_aids"),
    (re.compile(r"стоматолог", re.I), "dentist"),
    (re.compile(r"клиник|медицин.*центр|диагност.*центр", re.I), "clinic"),
    (re.compile(r"аптек", re.I), "pharmacy"),
    (re.compile(r"ветеринар|ветклиник", re.I), "veterinary"),
    (re.compile(r"юрист|адвокат|юридичес", re.I), "lawyer"),
    (re.compile(r"нотариус", re.I), "notary"),
    (re.compile(r"ресторан", re.I), "restaurant"),
    (re.compile(r"кафе|кофейн", re.I), "cafe"),
    (re.compile(r"бар\b|паб\b", re.I), "bar"),
    (re.compile(r"фитнес|спортзал|тренаж", re.I), "fitness_centre"),
    (re.compile(r"салон.*красот|косметолог|маникюр|педикюр", re.I), "beauty"),
    (re.compile(r"парикмах|барбершоп", re.I), "hairdresser"),
    (re.compile(r"химчист", re.I), "dry_cleaning"),
    (re.compile(r"прачеч", re.I), "laundry"),
    (re.compile(r"отел|гостиниц", re.I), "hotel"),
    (re.compile(r"риелтор|риэлтор|недвижим", re.I), "estate_agent"),
    (re.compile(r"страхов", re.I), "insurance"),
    (re.compile(r"банк\b", re.I), "bank"),
)


@dataclass(frozen=True)
class LoadReport:
    items: list[dict[str, Any]]
    recovered: bool
    error_offset: int | None
    trailing_chars: int


def _clean(value: Any) -> str:
    return " ".join(str(value or "").split()).strip()


def _first(*values: Any) -> str:
    for value in values:
        text = _clean(value)
        if text:
            return text
    return ""


def _load_recoverable_array(path: Path) -> LoadReport:
    """Load parser-2gis JSON, salvaging complete records from a truncated tail.

    parser-2gis writes a JSON array incrementally. If Chrome/parser crashes while
    writing the last card, the whole file becomes invalid even though all earlier
    objects are complete. We first try strict JSON and, on failure, decode array
    elements one-by-one and stop only at the first incomplete/corrupt tail.
    """
    text = path.read_text(encoding="utf-8-sig")
    try:
        payload = json.loads(text)
    except json.JSONDecodeError as strict_error:
        decoder = json.JSONDecoder()
        length = len(text)
        index = 0
        while index < length and text[index].isspace():
            index += 1
        if index >= length or text[index] != "[":
            raise SystemExit(f"2GIS JSON must start with '['; parse error at char {strict_error.pos}") from strict_error
        index += 1
        items: list[dict[str, Any]] = []
        error_offset: int | None = None

        while index < length:
            while index < length and (text[index].isspace() or text[index] == ","):
                index += 1
            if index >= length or text[index] == "]":
                break
            try:
                value, end = decoder.raw_decode(text, index)
            except json.JSONDecodeError as exc:
                error_offset = exc.pos
                break
            if isinstance(value, dict):
                items.append(value)
            index = end

        if not items:
            raise SystemExit(
                f"2GIS JSON is corrupted and no complete records could be recovered; "
                f"parse error at char {strict_error.pos}"
            ) from strict_error
        stop = error_offset if error_offset is not None else index
        return LoadReport(
            items=items,
            recovered=True,
            error_offset=error_offset if error_offset is not None else strict_error.pos,
            trailing_chars=max(0, length - stop),
        )

    if not isinstance(payload, list):
        raise SystemExit("2GIS JSON must contain a list")
    return LoadReport(
        items=[item for item in payload if isinstance(item, dict)],
        recovered=False,
        error_offset=None,
        trailing_chars=0,
    )


def _contacts(item: dict[str, Any]) -> tuple[str, str, str]:
    phones: list[str] = []
    websites: list[str] = []
    emails: list[str] = []
    for group in item.get("contact_groups") or []:
        if not isinstance(group, dict):
            continue
        for contact in group.get("contacts") or []:
            if not isinstance(contact, dict):
                continue
            kind = _clean(contact.get("type")).casefold()
            value = _first(contact.get("value"), contact.get("url"), contact.get("text"))
            if not value:
                continue
            if kind == "phone":
                if value not in phones:
                    phones.append(value)
            elif kind == "website":
                direct = _first(contact.get("url"), contact.get("text"), contact.get("value"))
                if direct and "link.2gis.ru" not in direct and direct not in websites:
                    websites.append(direct if "://" in direct else "https://" + direct)
                elif value and "link.2gis.ru" not in value and value not in websites:
                    websites.append(value if "://" in value else "https://" + value)
            elif kind in {"email", "e-mail"} and value not in emails:
                emails.append(value)
    return "; ".join(phones[:5]), websites[0] if websites else "", "; ".join(emails[:3])


def _rubrics(item: dict[str, Any]) -> tuple[list[str], str]:
    names: list[str] = []
    for rubric in item.get("rubrics") or []:
        if not isinstance(rubric, dict):
            continue
        name = _clean(rubric.get("name"))
        if name and name not in names:
            names.append(name)
    haystack = " | ".join(names)
    for pattern, category in _CATEGORY_RULES:
        if pattern.search(haystack):
            return names, category
    return names, ""


def _schedule(item: dict[str, Any]) -> str:
    schedule = item.get("schedule")
    if not isinstance(schedule, dict):
        return ""
    chunks: list[str] = []
    for day, payload in schedule.items():
        if not isinstance(payload, dict):
            continue
        hours: list[str] = []
        for row in payload.get("working_hours") or []:
            if isinstance(row, dict):
                start = _clean(row.get("from"))
                end = _clean(row.get("to"))
                if start or end:
                    hours.append(f"{start}-{end}".strip("-"))
        if hours:
            chunks.append(f"{day}:{','.join(hours)}")
    return "; ".join(chunks)


def _city(item: dict[str, Any], fallback: str) -> str:
    for row in item.get("adm_div") or []:
        if isinstance(row, dict) and _clean(row.get("type")).casefold() == "city":
            value = _clean(row.get("name"))
            if value:
                return value
    return fallback


def _record(item: dict[str, Any], *, city: str, category_override: str) -> dict[str, Any] | None:
    branch_id = _clean(item.get("id"))
    if "_" in branch_id:
        branch_id = branch_id.split("_", 1)[0]
    if not branch_id:
        branch_id = _clean((item.get("org") or {}).get("id")) if isinstance(item.get("org"), dict) else ""
    name_ex = item.get("name_ex") if isinstance(item.get("name_ex"), dict) else {}
    name = _first(name_ex.get("primary"), item.get("name"), (item.get("org") or {}).get("name") if isinstance(item.get("org"), dict) else "")
    if len(name) < 2:
        return None

    rubrics, detected_category = _rubrics(item)
    point = item.get("point") if isinstance(item.get("point"), dict) else {}
    phone, website, email = _contacts(item)
    resolved_city = _city(item, city)
    address = _first(item.get("address_name"), (item.get("address") or {}).get("name") if isinstance(item.get("address"), dict) else "")
    if address and resolved_city and resolved_city.casefold() not in address.casefold():
        address = f"{resolved_city}, {address}"

    aliases = list(rubrics)
    extension = _clean(name_ex.get("extension"))
    if extension:
        aliases.append(extension)
    if email:
        aliases.append(email)

    return {
        "source_key": f"2gis:{branch_id}" if branch_id else "",
        "source": "2gis",
        "source_id": branch_id,
        "source_url": f"https://2gis.ru/moscow/firm/{branch_id}" if branch_id else "",
        "name": name,
        "aliases": aliases,
        "category": category_override or detected_category,
        "subcategory": rubrics[0] if rubrics else "",
        "country": "Россия",
        "region": next((_clean(row.get("name")) for row in item.get("adm_div") or [] if isinstance(row, dict) and _clean(row.get("type")).casefold() == "region"), ""),
        "city": resolved_city,
        "address": address,
        "lat": point.get("lat"),
        "lon": point.get("lon"),
        "phone": phone,
        "website": website,
        "opening_hours": _schedule(item),
        "confidence": 0.93 if address and (phone or website) else 0.86,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Import parser-2gis JSON into OLYA owned search index")
    parser.add_argument("path")
    parser.add_argument("--city", default="Москва")
    parser.add_argument("--category", default="")
    args = parser.parse_args()

    path = Path(args.path)
    report = _load_recoverable_array(path)

    store = get_local_search_store()
    written = skipped = 0
    category_counts: dict[str, int] = {}
    for item in report.items:
        record = _record(item, city=args.city, category_override=args.category)
        if not record:
            skipped += 1
            continue
        try:
            store.upsert_business(record)
        except Exception:
            skipped += 1
            continue
        written += 1
        category = str(record.get("category") or "unknown")
        category_counts[category] = category_counts.get(category, 0) + 1

    print(json.dumps({
        "source": str(path),
        "input_records": len(report.items),
        "recovered_truncated_json": report.recovered,
        "json_error_offset": report.error_offset,
        "trailing_chars_ignored": report.trailing_chars,
        "written": written,
        "skipped": skipped,
        "categories": category_counts,
        "store": store.stats(),
    }, ensure_ascii=False, indent=2))
    return 0 if written else 2


if __name__ == "__main__":
    raise SystemExit(main())
