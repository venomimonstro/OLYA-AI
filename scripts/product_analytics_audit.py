#!/usr/bin/env python3
from __future__ import annotations
import json
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
def audit()->dict:
    errors=[]
    service=(ROOT/'app/services/product_analytics.py').read_text('utf-8')
    route=(ROOT/'app/api/routes/product_analytics.py').read_text('utf-8')
    admin=(ROOT/'app/admin_ui.py').read_text('utf-8')
    onboarding=(ROOT/'app/services/onboarding.py').read_text('utf-8')
    regression=(ROOT/'scripts/run_full_regression.py').read_text('utf-8')
    required={
      'activation':(service,'"activation"'),'retention':(service,'"d7"'),'task_success':(service,'"task_success"'),
      'frustration':(service,'"frustration"'),'conversion':(service,'"conversion"'),'economics':(service,'operations_summary'),
      'privacy_messages':(service,'"stores_message_content": False'),'privacy_prompts':(service,'"stores_prompt_content": False'),
      'server_first_value':(service,'UserOnboarding'),'durable_usage':(service,'UsageEvent.success.is_(True)'),
      'event_dedupe':(onboarding,'dedupe_key=key'),'api':(route,'@router.get("/v1/admin/product-analytics")'),
      'admin_only':(route,'Depends(require_admin)'),'ui':(route,'@router.get("/admin/analytics"'),
      'registered':(admin,'router.include_router(product_analytics_router)'),'nav':(admin,'href="/admin/analytics"')}
    for code,(src,tok) in required.items():
        if tok not in src: errors.append({'code':code,'token':tok})
    if 'Message' in service or 'Conversation' in service: errors.append({'code':'raw_content_model_in_analytics'})
    if '("scripts.product_analytics_audit", [])' not in regression: errors.append({'code':'regression_missing_product_analytics'})
    return {'format':'x1-product-analytics-audit-v1','status':'passed' if not errors else 'failed','errors':errors}
def main()->int:
    r=audit();print(json.dumps(r,ensure_ascii=False,indent=2));return 0 if r['status']=='passed' else 2
if __name__=='__main__': raise SystemExit(main())
