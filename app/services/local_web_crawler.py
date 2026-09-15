from __future__ import annotations

import asyncio
import gzip
import html
import ipaddress
import re
import socket
import sqlite3
import urllib.robotparser
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urljoin, urlsplit, urlunsplit

import httpx

from app.services.local_search_store import get_local_search_store


_MAX_HTML_BYTES = 2_000_000
_MAX_SITEMAP_BYTES = 8_000_000
_MAX_TEXT_CHARS = 300_000
_BLOCKED_SUFFIXES = (
    '.jpg', '.jpeg', '.png', '.gif', '.webp', '.svg', '.ico', '.css', '.js', '.mjs', '.woff', '.woff2', '.ttf',
    '.mp4', '.webm', '.mp3', '.wav', '.zip', '.rar', '.7z', '.exe', '.dmg', '.apk', '.pdf', '.doc', '.docx', '.xls', '.xlsx',
)
_SKIP_TAGS = {'script', 'style', 'noscript', 'svg', 'canvas', 'template', 'iframe'}
_SPACE = re.compile(r'\s+')


@dataclass(frozen=True)
class ParsedPage:
    title: str
    description: str
    text: str
    canonical: str


class _TextExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.title_parts: list[str] = []
        self.text_parts: list[str] = []
        self.description = ''
        self.canonical = ''
        self._skip_depth = 0
        self._in_title = False

    def handle_starttag(self, tag: str, attrs) -> None:
        low = tag.casefold()
        values = {str(k).casefold(): str(v or '') for k, v in attrs}
        if low in _SKIP_TAGS:
            self._skip_depth += 1
            return
        if low == 'title':
            self._in_title = True
        elif low == 'meta':
            name = values.get('name', '').casefold()
            prop = values.get('property', '').casefold()
            if (name == 'description' or prop == 'og:description') and not self.description:
                self.description = _clean(values.get('content', ''), 1500)
        elif low == 'link' and 'canonical' in values.get('rel', '').casefold():
            self.canonical = values.get('href', '').strip()

    def handle_endtag(self, tag: str) -> None:
        low = tag.casefold()
        if low in _SKIP_TAGS and self._skip_depth:
            self._skip_depth -= 1
            return
        if low == 'title':
            self._in_title = False
        if low in {'p', 'div', 'article', 'section', 'main', 'h1', 'h2', 'h3', 'li', 'br'} and not self._skip_depth:
            self.text_parts.append('\n')

    def handle_data(self, data: str) -> None:
        if self._skip_depth or not data:
            return
        value = data.strip()
        if not value:
            return
        if self._in_title:
            self.title_parts.append(value)
        else:
            self.text_parts.append(value)

    def result(self, base_url: str) -> ParsedPage:
        raw_text = ' '.join(self.text_parts)
        return ParsedPage(
            title=_clean(' '.join(self.title_parts), 500),
            description=self.description,
            text=_clean(raw_text, _MAX_TEXT_CHARS),
            canonical=urljoin(base_url, self.canonical) if self.canonical else base_url,
        )


def _clean(value: str, limit: int) -> str:
    return _SPACE.sub(' ', html.unescape(str(value or ''))).strip()[:limit]


def _canonical_http_url(value: str) -> str:
    raw = str(value or '').strip()
    if not raw:
        return ''
    if raw.startswith('//'):
        raw = 'https:' + raw
    parsed = urlsplit(raw)
    if parsed.scheme.casefold() not in {'http', 'https'} or not parsed.hostname:
        return ''
    host = parsed.hostname.casefold().rstrip('.')
    port = parsed.port
    netloc = host
    if port and not ((parsed.scheme == 'https' and port == 443) or (parsed.scheme == 'http' and port == 80)):
        netloc += f':{port}'
    return urlunsplit((parsed.scheme.casefold(), netloc, parsed.path or '/', parsed.query, ''))


def _allowed_path(url: str) -> bool:
    return not any(urlsplit(url).path.casefold().endswith(suffix) for suffix in _BLOCKED_SUFFIXES)


def _public_ip(address: str) -> bool:
    try:
        ip = ipaddress.ip_address(address)
    except ValueError:
        return False
    return not (ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_multicast or ip.is_reserved or ip.is_unspecified)


async def _host_is_public(host: str) -> bool:
    try:
        infos = await asyncio.get_running_loop().run_in_executor(None, lambda: socket.getaddrinfo(host, None, type=socket.SOCK_STREAM))
    except OSError:
        return False
    addresses = {str(item[4][0]) for item in infos if item and item[4]}
    return bool(addresses) and all(_public_ip(address) for address in addresses)


async def _safe_url(url: str, *, expected_host: str = '') -> str:
    canonical = _canonical_http_url(url)
    if not canonical or not _allowed_path(canonical):
        return ''
    host = (urlsplit(canonical).hostname or '').casefold()
    if expected_host and host != expected_host.casefold():
        return ''
    if not await _host_is_public(host):
        return ''
    return canonical


def _robots_parser(body: str, base_url: str) -> urllib.robotparser.RobotFileParser:
    parser = urllib.robotparser.RobotFileParser()
    parser.set_url(urljoin(base_url, '/robots.txt'))
    parser.parse(body.splitlines())
    return parser


def _sitemap_urls(body: bytes, content_encoding: str = '') -> list[tuple[str, str]]:
    payload = body
    if content_encoding.casefold() == 'gzip' or payload[:2] == b'\x1f\x8b':
        try:
            payload = gzip.decompress(payload)
        except OSError:
            return []
    if len(payload) > _MAX_SITEMAP_BYTES:
        return []
    try:
        root = ET.fromstring(payload)
    except ET.ParseError:
        return []
    rows: list[tuple[str, str]] = []
    for node in root.iter():
        tag = node.tag.rsplit('}', 1)[-1].casefold()
        if tag not in {'url', 'sitemap'}:
            continue
        loc = ''
        lastmod = ''
        for child in node:
            child_tag = child.tag.rsplit('}', 1)[-1].casefold()
            if child_tag == 'loc': loc = (child.text or '').strip()
            elif child_tag == 'lastmod': lastmod = (child.text or '').strip()
        if loc:
            rows.append((loc, lastmod))
    return rows


def _parse_html(body: str, url: str) -> ParsedPage:
    parser = _TextExtractor()
    try:
        parser.feed(body)
    except Exception:
        pass
    return parser.result(url)


def _date(value: str) -> datetime | None:
    raw = str(value or '').strip()
    if not raw:
        return None
    try:
        parsed = datetime.fromisoformat(raw.replace('Z', '+00:00'))
    except ValueError:
        # RFC 1123 Last-Modified headers are handled by the next crawl; they do
        # not need to block sitemap scheduling when parsing is ambiguous.
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _page_is_fresh(db_path: Path, url: str, sitemap_lastmod: str, *, fallback_days: int = 7) -> bool:
    """Skip unchanged sitemap URLs without loading page bodies into memory."""
    if not db_path.is_file():
        return False
    connection = sqlite3.connect(str(db_path), timeout=5.0)
    connection.row_factory = sqlite3.Row
    try:
        row = connection.execute('SELECT fetched_at,modified_at FROM pages WHERE url=? LIMIT 1', (url,)).fetchone()
    except sqlite3.Error:
        row = None
    finally:
        connection.close()
    if row is None:
        return False
    fetched = _date(str(row['fetched_at'] or ''))
    if fetched is None:
        return False
    source_modified = _date(sitemap_lastmod)
    if source_modified is not None:
        return fetched >= source_modified
    return fetched >= datetime.now(timezone.utc) - timedelta(days=max(1, fallback_days))


class LocalWebCrawler:
    user_agent = 'OLYA-AI-Indexer/1.0 (+self-hosted local search; respects robots.txt)'

    def __init__(self, *, timeout_seconds: float = 10.0) -> None:
        self.timeout_seconds = max(3.0, min(float(timeout_seconds), 30.0))
        self.store = get_local_search_store()

    async def _client(self):
        return httpx.AsyncClient(
            timeout=httpx.Timeout(self.timeout_seconds, connect=min(4.0, self.timeout_seconds)),
            follow_redirects=False,
            trust_env=False,
            headers={'User-Agent': self.user_agent, 'Accept-Language': 'ru-RU,ru;q=0.9,en;q=0.5'},
        )

    async def _fetch(self, client: httpx.AsyncClient, url: str, *, expected_host: str, max_bytes: int, accept: str) -> tuple[int, bytes, dict[str, str], str]:
        current = await _safe_url(url, expected_host=expected_host)
        if not current:
            return 0, b'', {}, ''
        for _ in range(4):
            try:
                response = await client.get(current, headers={'Accept': accept})
            except httpx.HTTPError:
                return 0, b'', {}, ''
            if response.status_code in {301, 302, 303, 307, 308}:
                location = response.headers.get('location', '')
                next_url = await _safe_url(urljoin(current, location), expected_host=expected_host)
                if not next_url:
                    return response.status_code, b'', dict(response.headers), current
                current = next_url
                continue
            return response.status_code, response.content[:max_bytes], {k.casefold(): v for k, v in response.headers.items()}, current
        return 0, b'', {}, ''

    async def seed_domain(self, value: str, *, max_sitemaps: int = 20, max_urls: int = 5000) -> dict[str, int | str]:
        base = _canonical_http_url(value if '://' in value else 'https://' + value.strip('/'))
        if not base:
            raise ValueError('valid public http(s) domain/url is required')
        parsed = urlsplit(base); host = (parsed.hostname or '').casefold()
        if not await _host_is_public(host):
            raise ValueError('domain does not resolve exclusively to public IP addresses')

        robots_url = f'{parsed.scheme}://{parsed.netloc}/robots.txt'
        async with await self._client() as client:
            _status, robots_body, _headers, _final = await self._fetch(client, robots_url, expected_host=host, max_bytes=500_000, accept='text/plain,*/*;q=0.1')
            robots_text = robots_body.decode('utf-8', 'replace') if robots_body else ''
            robots = _robots_parser(robots_text, base)
            sitemap_candidates: list[str] = []
            for line in robots_text.splitlines():
                if line.casefold().startswith('sitemap:'):
                    candidate = line.split(':', 1)[1].strip()
                    if candidate: sitemap_candidates.append(candidate)
            sitemap_candidates.extend([f'{parsed.scheme}://{parsed.netloc}/sitemap.xml', f'{parsed.scheme}://{parsed.netloc}/sitemap_index.xml'])

            seen_sitemaps: set[str] = set(); queued_urls = 0; skipped_fresh = 0; sitemap_count = 0; pending = sitemap_candidates[:]
            while pending and sitemap_count < max_sitemaps and queued_urls < max_urls:
                raw = pending.pop(0); sitemap = await _safe_url(raw, expected_host=host)
                if not sitemap or sitemap in seen_sitemaps: continue
                seen_sitemaps.add(sitemap)
                status, body, headers, final_url = await self._fetch(client, sitemap, expected_host=host, max_bytes=_MAX_SITEMAP_BYTES, accept='application/xml,text/xml,application/gzip,*/*;q=0.2')
                if status != 200 or not body: continue
                sitemap_count += 1
                for loc, lastmod in _sitemap_urls(body, headers.get('content-encoding', '')):
                    target = await _safe_url(loc, expected_host=host)
                    if not target: continue
                    if target.casefold().endswith(('.xml', '.xml.gz', '.gz')) and len(pending) < max_sitemaps * 4:
                        pending.append(target); continue
                    if not robots.can_fetch(self.user_agent, target): continue
                    if _page_is_fresh(self.store.path, target, lastmod):
                        skipped_fresh += 1; continue
                    self.store.enqueue(target, discovered_from=final_url or sitemap, priority=50, next_fetch_at='')
                    queued_urls += 1
                    if queued_urls >= max_urls: break

        return {'domain': host, 'sitemaps': sitemap_count, 'queued': queued_urls, 'fresh_skipped': skipped_fresh}

    async def crawl_one(self, row: dict) -> bool:
        url = str(row.get('url') or ''); parsed = urlsplit(url); host = (parsed.hostname or '').casefold()
        if not host:
            self.store.queue_failed(url, 'invalid_host'); return False
        robots_url = f'{parsed.scheme}://{parsed.netloc}/robots.txt'
        async with await self._client() as client:
            _rs, robots_body, _rh, _rf = await self._fetch(client, robots_url, expected_host=host, max_bytes=500_000, accept='text/plain,*/*;q=0.1')
            robots = _robots_parser(robots_body.decode('utf-8', 'replace') if robots_body else '', url)
            if not robots.can_fetch(self.user_agent, url):
                self.store.queue_done(url); return False
            status, body, headers, final_url = await self._fetch(client, url, expected_host=host, max_bytes=_MAX_HTML_BYTES, accept='text/html,application/xhtml+xml;q=0.9,*/*;q=0.1')
        if status != 200 or not body:
            self.store.queue_failed(url, f'http_{status or 0}'); return False
        content_type = headers.get('content-type', '').casefold()
        if 'html' not in content_type and '<html' not in body[:2000].decode('utf-8', 'ignore').casefold():
            self.store.queue_done(url); return False
        encoding = 'utf-8'; match = re.search(r'charset=([A-Za-z0-9._-]+)', content_type)
        if match: encoding = match.group(1)
        try: text = body.decode(encoding, 'replace')
        except LookupError: text = body.decode('utf-8', 'replace')
        parsed_page = _parse_html(text, final_url or url)
        if len(parsed_page.text) < 120 and not parsed_page.title:
            self.store.queue_done(url); return False
        canonical = await _safe_url(parsed_page.canonical, expected_host=host); target = canonical or final_url or url
        self.store.upsert_page(
            url=target, title=parsed_page.title, description=parsed_page.description,
            content=parsed_page.text, modified_at=headers.get('last-modified', ''), status=status,
        )
        self.store.queue_done(url)
        return True

    async def crawl_batch(self, *, limit: int = 20, concurrency: int = 3) -> dict[str, int]:
        rows = self.store.queue_batch(limit=limit)
        if not rows: return {'attempted': 0, 'indexed': 0}
        semaphore = asyncio.Semaphore(max(1, min(int(concurrency), 8)))

        async def run(row: dict) -> bool:
            async with semaphore:
                try: return await self.crawl_one(row)
                except Exception as exc:
                    self.store.queue_failed(str(row.get('url') or ''), f'{type(exc).__name__}:{exc}')
                    return False

        results = await asyncio.gather(*(run(row) for row in rows))
        return {'attempted': len(rows), 'indexed': sum(1 for item in results if item)}
