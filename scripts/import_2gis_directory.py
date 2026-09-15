from __future__ import annotations

import argparse
import json
from pathlib import Path

from app.services.business_quality import save_business_quality
from app.services.local_search_store import get_local_search_store
from scripts.import_2gis_json import _load_recoverable_array, _record


def main() -> int:
    parser = argparse.ArgumentParser(description="Import many parser-2gis JSON files into OLYA owned search index")
    parser.add_argument("path", help="Directory containing parser-2gis JSON files")
    parser.add_argument("--city", default="Москва")
    parser.add_argument("--pattern", default="*.json")
    args = parser.parse_args()

    root = Path(args.path)
    if not root.is_dir():
        raise SystemExit(f"Directory not found: {root}")

    files = sorted(path for path in root.rglob(args.pattern) if path.is_file())
    if not files:
        raise SystemExit(f"No JSON files found in {root}")

    store = get_local_search_store()
    files_ok = files_recovered = files_failed = 0
    input_records = written = skipped = ratings_imported = reviews_imported = 0
    category_counts: dict[str, int] = {}

    for path in files:
        try:
            report = _load_recoverable_array(path)
        except BaseException:
            files_failed += 1
            continue
        files_ok += 1
        if report.recovered:
            files_recovered += 1
        input_records += len(report.items)
        for item in report.items:
            record = _record(item, city=args.city, category_override="")
            if not record:
                skipped += 1
                continue
            try:
                business_id = store.upsert_business(record)
                save_business_quality(
                    business_id,
                    rating=record.get("rating"),
                    reviews=record.get("review_count"),
                    source_updated_at=str(record.get("source_updated_at") or ""),
                    store=store,
                )
            except Exception:
                skipped += 1
                continue
            written += 1
            if record.get("rating") is not None:
                ratings_imported += 1
            if record.get("review_count") is not None:
                reviews_imported += 1
            category = str(record.get("category") or "unknown")
            category_counts[category] = category_counts.get(category, 0) + 1

    print(json.dumps({
        "root": str(root),
        "files_total": len(files),
        "files_ok": files_ok,
        "files_recovered": files_recovered,
        "files_failed": files_failed,
        "input_records": input_records,
        "written_or_updated": written,
        "skipped": skipped,
        "ratings_imported": ratings_imported,
        "reviews_imported": reviews_imported,
        "categories": dict(sorted(category_counts.items(), key=lambda item: (-item[1], item[0]))),
        "store": store.stats(),
    }, ensure_ascii=False, indent=2))
    return 0 if written else 2


if __name__ == "__main__":
    raise SystemExit(main())
