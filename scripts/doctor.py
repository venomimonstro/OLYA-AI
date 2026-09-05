#!/usr/bin/env python3
from __future__ import annotations
import json, shutil, subprocess, sys
from pathlib import Path
from urllib.request import urlopen
ROOT=Path(__file__).resolve().parents[1]
def result(name,status,detail): return {"check":name,"status":status,"detail":detail}
def cmd(args,timeout=20):
    try:return subprocess.run(args,cwd=ROOT,text=True,capture_output=True,timeout=timeout)
    except Exception:return None
def main():
    checks=[result("python","stable" if sys.version_info>=(3,12) else "failed",sys.version.split()[0]),result("docker","stable" if shutil.which("docker") else "failed","installed" if shutil.which("docker") else "missing")]
    env=ROOT/'.env'
    if not env.exists():checks.append(result("env","failed",".env missing"))
    else:
        text=env.read_text(errors='replace');bad=[x for x in ("X1_ADMIN_BOOTSTRAP_TOKEN=change-me","X1_PROJECT_RUNTIME_SECRET_KEY=change-me-runtime-secret","POSTGRES_PASSWORD=x1-dev-only") if x in text]
        checks.append(result("secrets","failed" if bad else "stable","unsafe defaults: "+','.join(bad) if bad else "non-default"))
    if shutil.which('docker'):
        p=cmd(['docker','compose','config','--quiet']);checks.append(result("compose","stable" if p and p.returncode==0 else "failed",(p.stderr.strip() if p else 'cannot run') or 'valid'))
        p=cmd(['docker','compose','exec','-T','app','alembic','current']);checks.append(result("migrations","stable" if p and p.returncode==0 else "degraded",(p.stdout+p.stderr).strip()[-500:] if p else 'app not running'))
    try:
        with urlopen('http://127.0.0.1:8000/ready',timeout=4) as r:
            body=json.loads(r.read().decode());state='stable' if body.get('status') in {'ready','stable'} else 'degraded';checks.append(result("ready",state,json.dumps(body,ensure_ascii=False)))
    except Exception as e:checks.append(result("ready","degraded",type(e).__name__))
    overall='failed' if any(x['status']=='failed' for x in checks) else 'degraded' if any(x['status']=='degraded' for x in checks) else 'stable'
    print(json.dumps({"status":overall,"checks":checks},ensure_ascii=False,indent=2));return 2 if overall=='failed' else 1 if overall=='degraded' else 0
if __name__=='__main__':raise SystemExit(main())
