from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from scripts.import_osm_businesses import _download, import_pbf
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


def main() -> int:
    parser = argparse.ArgumentParser(description='Sequential low-RAM bootstrap of OLYA geo index for Russia.')
    parser.add_argument('--district', action='append', choices=sorted(DISTRICTS), default=[])
    parser.add_argument('--all-russia', action='store_true')
    parser.add_argument('--workdir', default='/app/data/osm-russia')
    parser.add_argument('--keep-pbf', action='store_true')
    parser.add_argument('--force-download', action='store_true')
    args = parser.parse_args()

    selected = list(DISTRICTS) if args.all_russia else list(dict.fromkeys(args.district))
    if not selected:
        # Moscow is in Central FD; this gives a useful first production index
        # without forcing a multi-GB all-Russia bootstrap.
        selected = ['central']

    workdir = Path(args.workdir); workdir.mkdir(parents=True, exist_ok=True)
    report = []
    total_written = 0
    for district in selected:
        filename = DISTRICTS[district]
        target = workdir / filename
        url = f'{BASE}/{filename}'
        try:
            print(f'[{district}] download {url}', file=sys.stderr, flush=True)
            _download(url, target, force=args.force_download)
            print(f'[{district}] import {target}', file=sys.stderr, flush=True)
            seen, written = import_pbf(target)
            total_written += written
            report.append({'district':district,'ok':True,'seen':seen,'indexed':written})
        except Exception as exc:
            report.append({'district':district,'ok':False,'error':f'{type(exc).__name__}: {exc}'})
            print(f'[{district}] failed: {type(exc).__name__}: {exc}', file=sys.stderr, flush=True)
        finally:
            if not args.keep_pbf:
                try: target.unlink()
                except FileNotFoundError: pass

    stats = get_local_search_store().stats()
    ok = total_written > 0 and all(item.get('ok') for item in report)
    print(json.dumps({'ok':ok,'indexed':total_written,'districts':report,'stats':stats}, ensure_ascii=False, indent=2))
    return 0 if ok else 2


if __name__ == '__main__':
    raise SystemExit(main())
