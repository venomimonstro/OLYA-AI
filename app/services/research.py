from __future__ import annotations

import asyncio
import hashlib
import html
import ipaddress
import re
import socket
from dataclasses import dataclass
from html.parser import HTMLParser
from urllib.parse import urljoin, urlsplit, urlunsplit

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import ResearchSource, SourceEvidence


_WORD_RE = re.compile(r"[\w\-]{2,}", re.UNICODE)
_SPACE_RE = re.compile(r"\s+")
_BLOCKED_HOSTS = {"localhost", "localhost.localdomain"}
_ALLOWED_SCHEMES = {"http", "https"}
_ALLOWED_PORTS = {80, 443, None}
_ALLOWED_MEDIA_TYPES = {"text/html", "application/xhtml+xml", "text/plain"}


class ResearchFetchError(RuntimeError): pass
class UnsafeURL(ResearchFetchError): pass


class _TextExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True); self.title=""; self._in_title=False; self._skip=0; self.parts:list[str]=[]
    def handle_starttag(self, tag: str, attrs) -> None:
        tag=tag.lower()
        if tag in {"script","style","noscript","svg","canvas","template"}: self._skip += 1
        elif tag == "title": self._in_title=True
        elif tag in {"p","div","article","section","main","li","br","h1","h2","h3","h4","td","th"}: self.parts.append("\n")
    def handle_endtag(self, tag: str) -> None:
        tag=tag.lower()
        if tag in {"script","style","noscript","svg","canvas","template"} and self._skip: self._skip -= 1
        elif tag == "title": self._in_title=False
        elif tag in {"p","div","article","section","main","li","h1","h2","h3","h4","tr"}: self.parts.append("\n")
    def handle_data(self, data: str) -> None:
        if self._skip: return
        value=_SPACE_RE.sub(" ",data).strip()
        if not value: return
        if self._in_title and not self.title: self.title=value[:500]
        self.parts.append(value)
    def text(self) -> str:
        raw=" ".join(self.parts); raw=re.sub(r"[ \t]+\n","\n",raw); raw=re.sub(r"\n[ \t]+","\n",raw); raw=re.sub(r"\n{3,}","\n\n",raw)
        return html.unescape(raw).strip()


def normalize_url(url: str) -> str:
    parsed=urlsplit(str(url).strip())
    if parsed.scheme.lower() not in _ALLOWED_SCHEMES: raise UnsafeURL("Only http/https URLs are allowed")
    if parsed.username or parsed.password: raise UnsafeURL("Credentials in URLs are not allowed")
    if not parsed.hostname: raise UnsafeURL("URL host is required")
    try: port=parsed.port
    except ValueError as exc: raise UnsafeURL("Invalid URL port") from exc
    if port not in _ALLOWED_PORTS: raise UnsafeURL("Only standard HTTP/HTTPS ports are allowed")
    host=parsed.hostname.rstrip(".").lower()
    if host in _BLOCKED_HOSTS or host.endswith(".local"): raise UnsafeURL("Local network hosts are not allowed")
    netloc=host + (f":{parsed.port}" if parsed.port else "")
    return urlunsplit((parsed.scheme.lower(),netloc,parsed.path or "/",parsed.query,""))


def _ip_is_public(value: str) -> bool:
    ip=ipaddress.ip_address(value)
    return not (ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_multicast or ip.is_reserved or ip.is_unspecified)


async def validate_public_url(url: str) -> str:
    normalized=normalize_url(url); host=urlsplit(normalized).hostname; assert host is not None
    try: literal=ipaddress.ip_address(host)
    except ValueError: literal=None
    if literal is not None:
        if not _ip_is_public(str(literal)): raise UnsafeURL("Private or special-use IP addresses are not allowed")
        return normalized
    def resolve() -> set[str]:
        infos=socket.getaddrinfo(host,None,type=socket.SOCK_STREAM); return {item[4][0] for item in infos}
    try: addresses=await asyncio.to_thread(resolve)
    except socket.gaierror as exc: raise ResearchFetchError("DNS resolution failed") from exc
    if not addresses: raise ResearchFetchError("DNS returned no addresses")
    if any(not _ip_is_public(address) for address in addresses): raise UnsafeURL("Host resolves to a private or special-use address")
    return normalized


def extract_text(body: str, media_type: str) -> tuple[str,str]:
    if media_type == "text/plain": return "", _SPACE_RE.sub(" ",body).strip()
    parser=_TextExtractor(); parser.feed(body); return parser.title, parser.text()


def _tokens(text: str) -> set[str]: return {item.casefold() for item in _WORD_RE.findall(text)}


def lexical_excerpts(content: str, query: str, *, limit: int=8, window: int=900) -> list[tuple[str,float]]:
    query_tokens=_tokens(query)
    if not query_tokens or not content.strip(): return []
    paragraphs=[part.strip() for part in re.split(r"\n{1,2}",content) if part.strip()]; ranked=[]
    for paragraph in paragraphs:
        tokens=_tokens(paragraph); overlap=len(query_tokens & tokens)
        if overlap: ranked.append((overlap/max(len(query_tokens),1),paragraph[:window]))
    ranked.sort(key=lambda item:(item[0],len(item[1])),reverse=True); return [(text,score) for score,text in ranked[:limit]]


@dataclass(frozen=True)
class FetchedPage:
    requested_url:str; final_url:str; title:str; content:str; http_status:int; media_type:str


class ResearchFetcher:
    def __init__(self, *, timeout_seconds:float=15.0,max_bytes:int=2_000_000,max_chars:int=500_000,max_redirects:int=3)->None:
        self.timeout_seconds=timeout_seconds; self.max_bytes=max_bytes; self.max_chars=max_chars; self.max_redirects=max_redirects
    async def fetch(self,url:str)->FetchedPage:
        current=await validate_public_url(url); requested=current
        headers={"User-Agent":"X1-Research/0.1 (+local research fetcher)","Accept":"text/html,application/xhtml+xml,text/plain;q=0.9,*/*;q=0.1"}
        timeout=httpx.Timeout(self.timeout_seconds)
        async with httpx.AsyncClient(timeout=timeout,follow_redirects=False,headers=headers,trust_env=False) as client:
            for redirect_index in range(self.max_redirects+1):
                try:
                    async with client.stream("GET",current) as response:
                        if response.status_code in {301,302,303,307,308}:
                            location=response.headers.get("location")
                            if not location: raise ResearchFetchError("Redirect without Location")
                            if redirect_index>=self.max_redirects: raise ResearchFetchError("Too many redirects")
                            current=await validate_public_url(urljoin(current,location)); continue
                        response.raise_for_status(); network_stream=response.extensions.get("network_stream")
                        if network_stream is not None:
                            try: peer=network_stream.get_extra_info("server_addr")
                            except Exception: peer=None
                            if peer:
                                peer_ip=peer[0] if isinstance(peer,tuple) else str(peer)
                                if not _ip_is_public(str(peer_ip)): raise UnsafeURL("Connected peer is a private or special-use address")
                        media_type=response.headers.get("content-type","").split(";",1)[0].strip().lower()
                        if media_type not in _ALLOWED_MEDIA_TYPES: raise ResearchFetchError(f"Unsupported content type: {media_type or 'unknown'}")
                        declared=response.headers.get("content-length")
                        if declared and declared.isdigit() and int(declared)>self.max_bytes: raise ResearchFetchError("Source exceeds byte limit")
                        chunks=[]; size=0
                        async for chunk in response.aiter_bytes():
                            size += len(chunk)
                            if size>self.max_bytes: raise ResearchFetchError("Source exceeds byte limit")
                            chunks.append(chunk)
                        encoding=response.encoding or "utf-8"; body=b"".join(chunks).decode(encoding,errors="replace"); title,content=extract_text(body,media_type); content=content[:self.max_chars].strip()
                        if not content: raise ResearchFetchError("Source contains no usable text")
                        return FetchedPage(requested,current,title or urlsplit(current).hostname or current,content,response.status_code,media_type)
                except httpx.HTTPError as exc: raise ResearchFetchError(f"HTTP fetch failed: {exc.__class__.__name__}") from exc
        raise ResearchFetchError("Unable to fetch source")


def source_sha256(content:str)->str: return hashlib.sha256(content.encode("utf-8")).hexdigest()


def exact_evidence(db:Session,*,source:ResearchSource,claim:str,excerpt:str,created_by:str)->SourceEvidence:
    normalized_excerpt=_SPACE_RE.sub(" ",excerpt).strip(); normalized_content=_SPACE_RE.sub(" ",source.content).strip()
    if normalized_excerpt not in normalized_content: raise ValueError("Evidence excerpt is not present verbatim in the stored source snapshot")
    evidence=SourceEvidence(source_id=source.id,created_by=created_by,claim=claim.strip(),excerpt=normalized_excerpt,state="verified_excerpt")
    db.add(evidence); db.flush(); return evidence


def get_sources(db:Session,ids:list[str])->list[ResearchSource]:
    if not ids: return []
    rows=list(db.scalars(select(ResearchSource).where(ResearchSource.id.in_(ids))).all()); by_id={item.id:item for item in rows}; return [by_id[item_id] for item_id in ids if item_id in by_id]
