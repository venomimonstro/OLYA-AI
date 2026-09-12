#!/usr/bin/env python3
from __future__ import annotations
import json
from pathlib import Path
from app.schemas.chat import ChatRequest
from scripts.load_acceptance import _chat_payload
ROOT=Path(__file__).resolve().parents[1]
def audit()->dict:
    src=(ROOT/'scripts/load_acceptance.py').read_text('utf-8');reg=(ROOT/'scripts/run_full_regression.py').read_text('utf-8');prov=(ROOT/'scripts/build_provenance.py').read_text('utf-8');errors=[]
    for token in ('_validate_target_transport(base_url)','Load acceptance requires HTTPS for non-loopback targets before sending Bearer tokens','len(tokens) < 10','len(set(tokens)) != len(tokens)','/v1/auth/me','unique_user_ids = set(identities)','len(unique_user_ids) != len(tokens)','"unique_authenticated_users"','asyncio.gather','/v1/chat','X-X1-Deadline-Ms','_chat_payload(user_index, round_index)','"messages"','"p95"','"error_rate"','"throughput_requests_per_second"','"max_waiting_observed"','"git_head": current_git_head()','"x1-real-load-acceptance-v2"','/version','BUILD_PROVENANCE_FORMAT','source_fingerprint(ROOT)','"source_fingerprint": candidate_fingerprint','"target_build_fingerprint": runtime_fingerprint','"candidate_matches_runtime_build"','backups/load-acceptance-latest.json','write_report(path, result)','return 0 if result.get("passed") else 2'):
        if token not in src: errors.append({'code':'load_acceptance_contract_missing','token':token})
    if '"message":' in src or '"web":' in src:
        errors.append({'code':'load_acceptance_legacy_chat_payload'})
    try:
        payload=_chat_payload(1,1)
        validated=ChatRequest.model_validate(payload)
        if not validated.messages or validated.messages[0].role!='user' or validated.mode!='fast' or validated.verification!='off':
            errors.append({'code':'load_acceptance_chat_payload_semantics_invalid'})
    except Exception as exc:
        errors.append({'code':'load_acceptance_chat_schema_mismatch','error':type(exc).__name__})
    for token in ('FORMAT = "x1-build-provenance-v1"','_BUILD_INPUTS = ("Dockerfile", "docker-compose.yml")','root / "build-inputs" / name','manifest[f"build-inputs/{name}"]','source_fingerprint'):
        if token not in prov: errors.append({'code':'load_acceptance_build_provenance_missing','token':token})
    if '("scripts.load_acceptance_audit",[])' not in reg and '("scripts.load_acceptance_audit", [])' not in reg: errors.append({'code':'regression_missing_load_acceptance_audit'})
    return {'format':'x1-load-acceptance-audit-v6','status':'passed' if not errors else 'failed','errors':errors,'requires_external_target_run':True,'requires_distinct_authenticated_users':True,'validates_canonical_chat_schema':True,'requires_matching_runtime_build':True,'requires_https_for_remote_tokens':True,'evidence_path':'backups/load-acceptance-latest.json'}
def main()->int:
    r=audit();print(json.dumps(r,ensure_ascii=False,indent=2));return 0 if r['status']=='passed' else 2
if __name__=='__main__':raise SystemExit(main())
