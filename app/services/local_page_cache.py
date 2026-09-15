from __future__ import annotations

from dataclasses import dataclass

from app.services.local_search_store import get_local_search_store


@dataclass(frozen=True)
class LocalPageSnapshot:
    url: str
    title: str
    content: str
    fetched_at: str


def get_local_page(url: str) -> LocalPageSnapshot | None:
    canonical = str(url or '').strip()
    if not canonical:
        return None
    store = get_local_search_store()
    # LocalSearchStore owns this connection helper; using it here preserves the
    # same WAL/busy-timeout settings without duplicating SQLite configuration.
    with store._connect() as connection:  # noqa: SLF001 - internal search-core adapter
        row = connection.execute(
            'SELECT url,title,content,fetched_at FROM pages WHERE url=? LIMIT 1',
            (canonical,),
        ).fetchone()
    if row is None or not str(row['content'] or '').strip():
        return None
    return LocalPageSnapshot(
        url=str(row['url']),
        title=str(row['title'] or ''),
        content=str(row['content'] or ''),
        fetched_at=str(row['fetched_at'] or ''),
    )
