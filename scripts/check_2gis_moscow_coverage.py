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
    finally:
        connection.close()

    result = dict(summary) if summary else {}
    result["top_subcategories"] = [{"rubric": row["rubric"], "businesses": row["n"]} for row in top]
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if int(result.get("businesses") or 0) > 0 else 2


if __name__ == "__main__":
    raise SystemExit(main())
