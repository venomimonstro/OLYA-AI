from __future__ import annotations

import json
import sqlite3

from app.services.business_quality import ensure_business_quality_schema
from app.services.local_search_store import get_local_search_store


def main() -> int:
    store = get_local_search_store()
    ensure_business_quality_schema(store)
    connection = sqlite3.connect(str(store.path), timeout=15.0)
    connection.row_factory = sqlite3.Row
    try:
        summary = connection.execute(
            """
            SELECT
                COUNT(*) AS businesses,
                COUNT(DISTINCT NULLIF(subcategory,'')) AS subcategories,
                SUM(CASE WHEN phone<>'' THEN 1 ELSE 0 END) AS with_phone,
                SUM(CASE WHEN website<>'' THEN 1 ELSE 0 END) AS with_website,
                SUM(CASE WHEN address<>'' THEN 1 ELSE 0 END) AS with_address,
                SUM(CASE WHEN lat IS NOT NULL AND lon IS NOT NULL THEN 1 ELSE 0 END) AS with_coordinates,
                SUM(CASE WHEN rating IS NOT NULL THEN 1 ELSE 0 END) AS with_rating,
                SUM(CASE WHEN review_count IS NOT NULL THEN 1 ELSE 0 END) AS with_reviews,
                MAX(source_updated_at) AS source_updated_at
            FROM businesses
            WHERE city='Москва' AND source='2gis'
            """
        ).fetchone()
        top = connection.execute(
            """
            SELECT COALESCE(NULLIF(subcategory,''),'Без рубрики') AS rubric, COUNT(*) AS n
            FROM businesses
            WHERE city='Москва' AND source='2gis'
            GROUP BY COALESCE(NULLIF(subcategory,''),'Без рубрики')
            ORDER BY n DESC, rubric ASC
            LIMIT 25
            """
        ).fetchall()
        bbox = connection.execute(
            """
            SELECT MIN(lat) AS min_lat, MAX(lat) AS max_lat, MIN(lon) AS min_lon, MAX(lon) AS max_lon
            FROM businesses
            WHERE city='Москва' AND source='2gis' AND lat IS NOT NULL AND lon IS NOT NULL
            """
        ).fetchone()
    finally:
        connection.close()

    result = dict(summary) if summary else {}
    businesses = int(result.get("businesses") or 0)
    with_coordinates = int(result.get("with_coordinates") or 0)
    result["coordinate_coverage_pct"] = round(with_coordinates * 100.0 / businesses, 2) if businesses else 0.0
    if bbox and bbox["min_lat"] is not None:
        result["coordinate_bbox"] = {
            "min_lat": bbox["min_lat"],
            "max_lat": bbox["max_lat"],
            "min_lon": bbox["min_lon"],
            "max_lon": bbox["max_lon"],
        }
    result["top_subcategories"] = [{"rubric": row["rubric"], "businesses": row["n"]} for row in top]
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if businesses > 0 else 2


if __name__ == "__main__":
    raise SystemExit(main())
