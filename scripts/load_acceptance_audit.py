#!/usr/bin/env python3
from __future__ import annotations
import json
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
def audit()->dict:
    src=(ROOT/'scripts/load_acceptance.py').read_text('utf-8');reg=(ROOT/'scripts/run_full_regression.py').read_text('utf-8');errors=[]
    for token in ('len(tokens) < 10','len(set(tokens)) != len(tokens)','/v1/auth/me','unique_user_ids = set(identities)','len(unique_user_ids) != len(tokens)','"unique_authenticated_users"','asyncio.gather','/v1/chat','X-X1-Deadline-Ms','"p95"','"error_rate"','"throughput_requests_per_second"','"max_waiting_observed"','"git_head": current_git_head()','backups/load-acceptance-latest.json','write_report(path, result)','return 0 if result.get("passed") else 2'):
        if token not in src: errors.append({'code':'load_acceptance_contract_missing','token':token})
    if '("scripts.load_acceptance_audit",[])' not in reg and '("scripts.load_acceptance_audit", [])' not in reg: errors.append({'code':'regression_missing_load_acceptance_audit'})
    return {'format':'x1-load-acceptance-audit-v3','status':'passed' if not errors else 'failed','errors':errors,'requires_external_target_run':True,'requires_distinct_authenticated_users':True,'evidence_path':'backups/load-acceptance-latest.json'}
def main()->int:
    r=audit();print(json.dumps(r,ensure_ascii=False,indent=2));return 0 if r['status']=='passed' else 2
if __name__=='__main__':raise SystemExit(main())
