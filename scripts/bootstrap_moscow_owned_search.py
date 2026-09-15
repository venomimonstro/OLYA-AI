from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from urllib.request import Request, urlopen

from app.services.local_search_store import get_local_search_store
from scripts.import_osm_search_index import import_pbf

PBF_URL = "https://download.bbbike.org/osm/bbbike/Moscow/Moscow.osm.pbf"


def _download(url: str, target: Path, *, force: bool = False) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.is_file() and target.stat().st_size > 5_000_000 and not force:
        return
    partial = target.with_suffix(target.suffix + ".part")
    partial.unlink(missing_ok=True)
    request = Request(url, headers={"User-Agent": "OLYA-AI owned-search bootstrap/1.0"})
    with urlopen(request, timeout=120) as response, partial.open("wb") as handle:
        total = int(response.headers.get("Content-Length") or 0)
        copied = 0
        while True:
            chunk = response.read(2 * 1024 * 1024)
            if not chunk:
                break
            handle.write(chunk)
            copied += len(chunk)
            if total and copied % (32 * 1024 * 1024) < len(chunk):
                print(f"download: {copied * 100 // total}%", file=sys.stderr, flush=True)
    if partial.stat().st_size < 5_000_000:
        partial.unlink(missing_ok=True)
        raise RuntimeError("downloaded Moscow PBF is unexpectedly small")
    partial.replace(target)


def main() -> int:
    parser = argparse.ArgumentParser(description="Fast low-RAM bootstrap of OLYA Moscow business index.")
    parser.add_argument("--workdir", default="/app/data/osm-moscow")
    parser.add_argument("--keep-pbf", action="store_true")
    parser.add_argument("--force-download", action="store_true")
    parser.add_argument("--limit", type=int, default=0, help="Testing limit; 0 indexes all businesses")
    parser.add_argument("--seed-websites", action="store_true", help="After geo import, discover sitemaps of indexed official websites")
    args = parser.parse_args()

    workdir = Path(args.workdir)
    target = workdir / "Moscow.osm.pbf"
    try:
        print(f"download {PBF_URL}", file=sys.stderr, flush=True)
        _download(PBF_URL, target, force=args.force_download)
        print(f"import {target}", file=sys.stderr, flush=True)
        indexed, rejected = import_pbf(target, limit=max(0, args.limit))
    except Exception as exc:
        print(json.dumps({"ok": False, "error": f"{type(exc).__name__}: {exc}"}, ensure_ascii=False, indent=2))
        return 2
    finally:
        if not args.keep_pbf:
            target.unlink(missing_ok=True)

    store = get_local_search_store()
    result = {"ok": indexed > 0, "indexed": indexed, "rejected": rejected, "stats": store.stats()}
    print(json.dumps(result, ensure_ascii=False, indent=2))

    if args.seed_websites and indexed > 0:
        import subprocess
        code = subprocess.run([
            sys.executable, "-m", "scripts.seed_business_websites",
            "--city", "Москва", "--limit", "100", "--max-urls-per-domain", "250",
        ], check=False).returncode
        if code != 0:
            return 3
    return 0 if indexed > 0 else 4


if __name__ == "__main__":
    raise SystemExit(main())
