from __future__ import annotations

import argparse
import json
import os
import shutil
import sqlite3
import subprocess
import tempfile
from pathlib import Path
from typing import Iterable
from urllib.request import Request, urlopen

from app.services.local_search_store import get_local_search_store


OSM_FILTERS = (
    "nwr/shop",
    "nwr/amenity",
    "nwr/office",
    "nwr/craft",
    "nwr/healthcare",
    "nwr/tourism",
    "nwr/leisure",
)


def _category(tags: dict[str, object]) -> tuple[str, str]:
    checks = (
        ("shop", "car_repair", "car_repair"), ("amenity", "car_repair", "car_repair"),
        ("shop", "tyres", "tyres"), ("amenity", "car_wash", "car_wash"),
        ("shop", "hearing_aids", "hearing_aids"), ("healthcare", "audiologist", "hearing_aids"),
        ("amenity", "dentist", "dentist"), ("healthcare", "dentist", "dentist"),
        ("amenity", "clinic", "clinic"), ("healthcare", "clinic", "clinic"),
        ("amenity", "pharmacy", "pharmacy"), ("amenity", "veterinary", "veterinary"),
        ("office", "lawyer", "lawyer"), ("office", "notary", "notary"),
        ("office", "estate_agent", "estate_agent"), ("office", "insurance", "insurance"),
        ("amenity", "bank", "bank"), ("amenity", "restaurant", "restaurant"),
        ("amenity", "cafe", "cafe"), ("amenity", "bar", "bar"), ("amenity", "pub", "bar"),
        ("leisure", "fitness_centre", "fitness_centre"), ("shop", "beauty", "beauty"),
        ("shop", "hairdresser", "hairdresser"), ("shop", "massage", "massage"),
        ("shop", "dry_cleaning", "dry_cleaning"), ("shop", "laundry", "laundry"),
        ("craft", "tailor", "tailor"), ("shop", "copyshop", "printing"), ("craft", "printer", "printing"),
        ("shop", "florist", "florist"), ("shop", "furniture", "furniture"), ("shop", "bakery", "bakery"),
        ("tourism", "hotel", "hotel"), ("tourism", "hostel", "hotel"),
        ("amenity", "driving_school", "driving_school"), ("amenity", "language_school", "language_school"),
        ("office", "construction_company", "construction"), ("craft", "builder", "construction"),
        ("craft", "electrician", "electrician"), ("craft", "plumber", "plumber"),
    )
    for key, value, category in checks:
        if str(tags.get(key) or "").casefold() == value:
            return category, value
    cuisine = str(tags.get("cuisine") or "").casefold()
    if "pizza" in cuisine:
        return "pizza", cuisine[:120]
    if "sushi" in cuisine:
        return "sushi", cuisine[:120]
    if str(tags.get("sport") or "").casefold() == "yoga":
        return "yoga", "yoga"
    if tags.get("service:vehicle:car_repair"):
        return "car_repair", "car_repair"
    for key in ("shop", "amenity", "office", "craft", "healthcare", "tourism", "leisure"):
        value = str(tags.get(key) or "").strip()
        if value:
            return value[:120], value[:120]
    return "", ""


def _name(tags: dict[str, object]) -> str:
    for key in ("name", "name:ru", "brand", "operator"):
        value = " ".join(str(tags.get(key) or "").split()).strip()
        if len(value) >= 2:
            return value[:240]
    return ""


def _address(tags: dict[str, object], *, default_city: str = "") -> tuple[str, str, str, str, str, str, str]:
    country = str(tags.get("addr:country") or "Россия").strip()
    region = str(tags.get("addr:region") or tags.get("addr:state") or "").strip()
    city = str(tags.get("addr:city") or tags.get("addr:town") or tags.get("addr:village") or default_city or "").strip()
    district = str(tags.get("addr:district") or tags.get("addr:suburb") or "").strip()
    street = str(tags.get("addr:street") or tags.get("addr:place") or "").strip()
    house = str(tags.get("addr:housenumber") or "").strip()
    full = str(tags.get("addr:full") or "").strip()
    if not full:
        left = street + (f", {house}" if house else "")
        full = ", ".join(part for part in (left, city, region) if part)
    return country[:120], region[:180], city[:180], district[:180], street[:180], house[:80], full[:500]


def _flatten_coordinates(value) -> Iterable[tuple[float, float]]:
    if isinstance(value, (list, tuple)):
        if len(value) >= 2 and all(isinstance(item, (int, float)) for item in value[:2]):
            yield float(value[0]), float(value[1]); return
        for item in value:
            yield from _flatten_coordinates(item)


def _centroid(feature: dict) -> tuple[float | None, float | None]:
    geometry = feature.get("geometry")
    if not isinstance(geometry, dict):
        return None, None
    coords = list(_flatten_coordinates(geometry.get("coordinates")))
    if not coords:
        return None, None
    if len(coords) > 500:
        coords = coords[::max(1, len(coords) // 500)]
    lon = sum(item[0] for item in coords) / len(coords)
    lat = sum(item[1] for item in coords) / len(coords)
    return (lat, lon) if -90 <= lat <= 90 and -180 <= lon <= 180 else (None, None)


def _osm_identity(feature: dict, properties: dict[str, object]) -> tuple[str, str]:
    raw_id = str(properties.get("@id") or feature.get("id") or properties.get("id") or "").strip()
    raw_type = str(properties.get("@type") or properties.get("type") or "").casefold().strip()
    if "/" in raw_id:
        prefix, value = raw_id.split("/", 1)
        if prefix in {"node", "way", "relation"}:
            raw_type, raw_id = prefix, value
    if raw_type in {"node", "way", "relation"} and raw_id.isdigit():
        return raw_type, raw_id
    match = __import__("re").search(r"(?:node|way|relation)[/:](\d+)", raw_id, __import__("re").I)
    if match:
        prefix = raw_id[:match.start(1)].rstrip("/:").split("/")[-1].casefold()
        if prefix in {"node", "way", "relation"}:
            return prefix, match.group(1)
    return "", ""


def _download(url: str, destination: Path, *, force: bool = False) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.is_file() and destination.stat().st_size > 1_000_000 and not force:
        return
    tmp = destination.with_suffix(destination.suffix + ".part")
    tmp.unlink(missing_ok=True)
    request = Request(url, headers={"User-Agent": "OLYA-AI OSM bootstrap/1.0"})
    with urlopen(request, timeout=120) as response, tmp.open("wb") as handle:
        total = int(response.headers.get("Content-Length") or 0); copied = 0
        while True:
            block = response.read(1024 * 1024)
            if not block: break
            handle.write(block); copied += len(block)
            if total and copied % (100 * 1024 * 1024) < len(block):
                print(f"downloaded {copied / 1024 / 1024:.0f}/{total / 1024 / 1024:.0f} MiB", flush=True)
    if tmp.stat().st_size < 1_000_000:
        tmp.unlink(missing_ok=True); raise RuntimeError("downloaded PBF is unexpectedly small")
    tmp.replace(destination)


def _osmium_stream(source: Path, workdir: Path):
    filtered = workdir / "businesses.filtered.osm.pbf"
    node_index = workdir / "nodes.idx"
    subprocess.run(["osmium", "tags-filter", str(source), *OSM_FILTERS, "-o", str(filtered), "--overwrite"], check=True)
    process = subprocess.Popen(
        ["osmium", "export", str(filtered), "-f", "geojsonseq", "-a", "type,id", "-i", f"sparse_file_array,{node_index}", "-o", "-"],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, bufsize=1,
    )
    assert process.stdout is not None
    try:
        for line in process.stdout:
            line = line.strip().lstrip("\x1e")
            if not line: continue
            try: value = json.loads(line)
            except json.JSONDecodeError: continue
            if isinstance(value, dict): yield value
    finally:
        process.stdout.close()
        stderr = process.stderr.read() if process.stderr else ""
        code = process.wait()
        if code != 0: raise RuntimeError(f"osmium export failed ({code}): {stderr[-1500:]}")


def import_pbf(source: Path, *, default_city: str = "", keep_filtered: bool = False) -> tuple[int, int]:
    if shutil.which("osmium") is None: raise RuntimeError("osmium-tool is not installed")
    store = get_local_search_store(); seen = 0; written = 0
    work_root = Path(os.getenv("X1_DATA_ROOT") or "/app/data") / "search-import"; work_root.mkdir(parents=True, exist_ok=True)
    context = _PersistentWorkdir(work_root) if keep_filtered else tempfile.TemporaryDirectory(prefix="osm-import-", dir=str(work_root))
    with context as raw_workdir:
        for feature in _osmium_stream(source, Path(raw_workdir)):
            seen += 1
            properties = feature.get("properties")
            if not isinstance(properties, dict): continue
            name = _name(properties)
            if not name: continue
            category, subcategory = _category(properties)
            if not category: continue
            lat, lon = _centroid(feature)
            country, region, city, district, street, house, address = _address(properties, default_city=default_city)
            osm_type, osm_id = _osm_identity(feature, properties)
            source_id = f"{osm_type}/{osm_id}" if osm_type and osm_id else ""
            source_url = f"https://www.openstreetmap.org/{osm_type}/{osm_id}" if source_id else ""
            try:
                store.upsert_business({
                    "name": name,
                    "aliases": [properties.get("name:ru", ""), properties.get("brand", ""), properties.get("operator", "")],
                    "category": category, "subcategory": subcategory, "country": country or "Россия", "region": region,
                    "city": city or default_city, "district": district, "street": street, "house": house, "address": address,
                    "lat": lat, "lon": lon,
                    "phone": str(properties.get("contact:phone") or properties.get("phone") or "")[:180],
                    "website": str(properties.get("contact:website") or properties.get("website") or "")[:500],
                    "opening_hours": str(properties.get("opening_hours") or "")[:500],
                    "source": "osm", "source_url": source_url, "source_id": source_id, "confidence": 0.78,
                }); written += 1
            except (ValueError, OSError, sqlite3.Error):
                continue
            if written and written % 5000 == 0: print(f"indexed {written:,} businesses (seen {seen:,})", flush=True)
    return seen, written


class _PersistentWorkdir:
    def __init__(self, path: Path) -> None: self.path = path
    def __enter__(self) -> str: return str(self.path)
    def __exit__(self, exc_type, exc, tb) -> None: return None


def main() -> int:
    parser = argparse.ArgumentParser(description="Build OLYA local business index from an OpenStreetMap PBF extract")
    parser.add_argument("pbf", nargs="?", help="Path to .osm.pbf")
    parser.add_argument("--download-url", help="Download PBF before import")
    parser.add_argument("--download-to", default="/app/data/osm/bootstrap.osm.pbf")
    parser.add_argument("--city", default="", help="Force city for a city extract, e.g. Москва")
    parser.add_argument("--force-download", action="store_true")
    parser.add_argument("--keep-filtered", action="store_true")
    args = parser.parse_args()
    source = Path(args.pbf) if args.pbf else Path(args.download_to)
    if args.download_url:
        print(f"downloading {args.download_url}", flush=True); _download(args.download_url, source, force=args.force_download)
    if not source.exists(): parser.error(f"PBF not found: {source}")
    seen, written = import_pbf(source, default_city=args.city.strip(), keep_filtered=args.keep_filtered)
    print(json.dumps({"ok": True, "source": str(source), "city": args.city, "seen": seen, "indexed": written, "stats": get_local_search_store().stats()}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__": raise SystemExit(main())
