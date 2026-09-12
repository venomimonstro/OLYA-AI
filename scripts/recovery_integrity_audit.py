#!/usr/bin/env python3
from __future__ import annotations
import json
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
def audit()->dict:
    errors=[]
    recovery=(ROOT/'app/services/recovery_integrity.py').read_text('utf-8')
    jobs=(ROOT/'app/services/jobs.py').read_text('utf-8')
    maintenance=(ROOT/'app/services/maintenance.py').read_text('utf-8')
    atomic=(ROOT/'app/services/chat_run_atomicity.py').read_text('utf-8')
    runtime=(ROOT/'app/services/chat_runtime.py').read_text('utf-8')
    autonomous=(ROOT/'app/services/autonomous_development.py').read_text('utf-8')
    regression=(ROOT/'scripts/run_full_regression.py').read_text('utf-8')
    required={
      'snapshot':(recovery,'recovery_integrity_snapshot'),'expired_jobs':(recovery,'"expired_live_jobs"'),
      'stale_chat':(recovery,'"stale_chat_runs"'),'agent_slots':(recovery,'"stale_agent_slots"'),
      'reconcile':(recovery,'cleanup_ephemeral_state'),'job_idempotency':(jobs,'idempotency_key'),
      'lease_token':(jobs,'lease_token'),'lease_compare_swap':(jobs,'result.rowcount == 1'),
      'retry_budget':(jobs,'attempt_count < BackgroundJob.max_attempts'),
      'maintenance_order':(maintenance,'counts["reaped_exhausted_jobs"] = reap_exhausted_jobs(db)'),
      'image_reconcile':(maintenance,'_recover_interrupted_image_jobs'),
      'file_recover':(maintenance,'_recover_stale_file_processing'),
      'doc_recover':(maintenance,'_recover_stale_document_qa'),
      'agent_recover':(maintenance,'recover_abandoned_slots'),
      'chat_atomic':(atomic,'same database transaction'),
      'sticky_success':(atomic,'_restore_sticky_success'),
      'stale_interrupted':(runtime,'row.status = "interrupted"'),
      'chat_attempt_budget':(runtime,'_MAX_RUN_ATTEMPTS = 3'),
      'agent_checkpoint':(autonomous,'AutonomousDevelopmentCheckpoint')}
    for code,(src,tok) in required.items():
        if tok not in src:errors.append({'code':code,'token':tok})
    if '("scripts.recovery_integrity_audit", [])' not in regression:errors.append({'code':'regression_missing_recovery_integrity'})
    return {'format':'x1-recovery-integrity-audit-v1','status':'passed' if not errors else 'failed','errors':errors}
def main()->int:
    r=audit();print(json.dumps(r,ensure_ascii=False,indent=2));return 0 if r['status']=='passed' else 2
if __name__=='__main__':raise SystemExit(main())
