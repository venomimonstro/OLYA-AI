from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from urllib.request import Request, urlopen

from scripts.import_osm_search_index import import_pbf
from app.services.local_search_store import get_local_search_store


BASE = 'https://download.geofabrik.de/russia'
DISTRICTS = {
    'central': 'central-fed-district-latest.osm.pbf',
    'northwestern': 'northwestern-fed-district-latest.osm.pbf',
    'south': 'south-fed-district-latest.osm.pbf',
    'north-caucasus': 'north-caucasus-fed-district-latest.osm.pbf',
    'volga': 'volga-fed-district-latest.osm.pbf',
    'ural': 'ural-fed-district-latest.osm.pbf',
    'siberian': 'siberian-fed-district-latest.osm.pbf',
    'far-eastern': 'far-eastern-fed-district-latest.osm.pbf',
    'crimean': 'crimean-fed-district-latest.osm.pbf',
    'kaliningrad': 'kaliningrad-latest.osm.pbf',
}


def _download(url: str, destination: Path, *, force: bool = False) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.is_file() and destination.stat().st_size > 1_000_000 and not force:
        return
    tmp = destination.with_suffix(destination.suffix + '.part')
    tmp.unlink(missing_ok=True)
    request = Request(url, headers={'User-Agent':'OLYA-AI owned-search bootstrap/1.0'})
    with urlopen(request, timeout=120) as response, tmp.open('wb') as handle:
        total = int(response.headers.get('Content-Length') or 0); copied = 0
        while True:
            chunk = response.read(4 * 1024 * 1024)
            if not chunk: break
            handle.write(chunk); copied += len(chunk)
            if total and copied % (128 * 1024 * 1024) < len(chunk):
                print(f'download {destination.name}: {copied * 100 // total}%', file=sys.stderr, flush=True)
    if tmp.stat().st_size < 1_000_000:
        tmp.unlink(missing_ok=True); raise RuntimeError('downloaded PBF is unexpectedly small')
    tmp.replace(destination)


def main() -> int:
    parser = argparse.ArgumentParser(description='Sequential low-RAM bootstrap of OLYA geo index for Russia.')
    parser.add_argument('--district', action='append', choices=sorted(DISTRICTS), default=[])
    parser.add_argument('--all-russia', action='store_true')
    parser.add_argument('--workdir', default='/app/data/osm-russia')
    parser.add_argument('--keep-pbf', action='store_true')
    parser.add_argument('--force-download', action='store_true')
    parser.add_argument('--limit', type=int, default=0, help='Testing limit per district; 0 indexes the full extract')
    args = parser.parse_args()

    selected = list(DISTRICTS) if args.all_russia else list(dict.fromkeys(args.district))
    if not selected:
        selected = ['central']

    workdir = Path(args.workdir); workdir.mkdir(parents=True, exist_ok=True)
    report = []; total_written = 0; total_rejected = 0
    for district in selected:
        filename = DISTRICTS[district]; target = workdir / filename; url = f'{BASE}/{filename}'
        try:
            print(f'[{district}] download {url}', file=sys.stderr, flush=True)
            _download(url, target, force=args.force_download)
            print(f'[{district}] import {target}', file=sys.stderr, flush=True)
            written, rejected = import_pbf(target, limit=max(0,args.limit))
            total_written += written; total_rejected += rejected
            report.append({'district':district,'ok':True,'indexed':written,'rejected':rejected})
        except Exception as exc:
            report.append({'district':district,'ok':False,'error':f'{type(exc).__name__}: {exc}'})
            print(f'[{district}] failed: {type(exc).__name__}: {exc}', file=sys.stderr, flush=True)
        finally:
            if not args.keep_pbf:
                try: target.unlink()
                except FileNotFoundError: pass

    stats = get_local_search_store().stats()
    ok = total_written > 0 and all(item.get('ok') for item in report)
    print(json.dumps({'ok':ok,'indexed':total_written,'rejected':total_rejected,'districts':report,'stats':stats}, ensure_ascii=False, indent=2))
    return 0 if ok else 2


if __name__ == '__main__': raise SystemExit(main())
