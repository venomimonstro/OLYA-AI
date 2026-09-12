#!/usr/bin/env python3
from __future__ import annotations
import importlib,json
from collections import Counter
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
def audit()->dict:
    errors=[]
    ownership=(ROOT/'app/domain_ownership.py').read_text('utf-8')
    main=(ROOT/'app/main.py').read_text('utf-8')
    user_ui=(ROOT/'app/user_ui.py').read_text('utf-8')
    base=(ROOT/'app/user_workspace_base.py').read_text('utf-8')
    regression=(ROOT/'scripts/run_full_regression.py').read_text('utf-8')
    required_domains=('authentication','billing','quota_entitlement','plan_policy','admin_user_override','capability_availability','inference_admission','request_deadline','agent_completion','image_beta_contract','product_analytics','background_jobs','recovery_integrity')
    for name in required_domains:
        if f'"{name}"' not in ownership:errors.append({'code':'domain_owner_missing','domain':name})
    module=importlib.import_module('app.domain_ownership')
    owners=list(module.DOMAIN_OWNERS.values())
    for owner in sorted(set(owners)):
        try: importlib.import_module(owner)
        except Exception as exc: errors.append({'code':'domain_owner_import_failed','owner':owner,'error':type(exc).__name__})
    app=importlib.import_module('app.main').app
    keys=[]
    for route in app.routes:
        path=getattr(route,'path',None);methods=getattr(route,'methods',None) or set()
        if path:
            for method in methods:keys.append((method,path))
    for key,count in Counter(keys).items():
        if count>1:errors.append({'code':'duplicate_runtime_route','method':key[0],'path':key[1],'count':count})
    if 'from app.user_ui import router as user_ui_router' not in main:errors.append({'code':'workspace_owner_not_user_ui'})
    if 'user_workspace_base_router' in main or 'from app.user_workspace_base import router' in main:errors.append({'code':'workspace_base_registered_directly'})
    if 'from app.user_workspace_base import workspace as _base_workspace' not in user_ui:errors.append({'code':'workspace_template_dependency_missing'})
    if '@router.get("/app"' not in base:errors.append({'code':'legacy_template_route_marker_missing','note':'base remains template-compatible but is not registered'})
    if '("scripts.architecture_cleanup_audit", [])' not in regression:errors.append({'code':'regression_missing_architecture_cleanup'})
    return {'format':'x1-architecture-cleanup-audit-v1','status':'passed' if not errors else 'failed','errors':errors,'registered_route_keys':len(keys),'domain_owners':module.DOMAIN_OWNERS}
def main()->int:
    r=audit();print(json.dumps(r,ensure_ascii=False,indent=2));return 0 if r['status']=='passed' else 2
if __name__=='__main__':raise SystemExit(main())
