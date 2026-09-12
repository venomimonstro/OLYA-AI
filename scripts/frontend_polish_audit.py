#!/usr/bin/env python3
from __future__ import annotations
import json
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
def audit()->dict:
    errors=[]
    base=(ROOT/'app/user_workspace_base.py').read_text('utf-8')
    wrapper=(ROOT/'app/user_ui.py').read_text('utf-8')
    studio=(ROOT/'app/image_studio_ui.py').read_text('utf-8')
    admin=(ROOT/'app/admin_ui.py').read_text('utf-8')
    analytics=(ROOT/'app/api/routes/product_analytics.py').read_text('utf-8')
    regression=(ROOT/'scripts/run_full_regression.py').read_text('utf-8')
    required={
      'app_mobile':(base,'@media(max-width:760px)'),
      'mobile_menu':(base,'id="menu"'),
      'app_retry':(base,'class="retry"'),
      'app_state':(base,'id="state"'),
      'app_stop':(base,"send.stop"),
      'app_views':(base,'id="view-account"'),
      'account_server_prices':(wrapper,"Цены и лимиты загружаются с сервера"),
      'studio_mobile':(studio,'@media(max-width:760px)'),
      'studio_disabled':(studio,"$('run').disabled"),
      'studio_recovery':(studio,'Capability contract недоступен'),
      'admin_mobile':(admin,'@media(max-width:680px)'),
      'admin_nav_users':(admin,'href="/admin/users"'),
      'admin_nav_caps':(admin,'href="/admin/capabilities"'),
      'admin_nav_analytics':(admin,'href="/admin/analytics"'),
      'analytics_mobile':(analytics,'@media(max-width:600px)'),
      'session_tokens':(base,"sessionStorage"),
    }
    for code,(src,tok) in required.items():
        if tok not in src: errors.append({'code':code,'token':tok})
    for name,src in [('app',base),('studio',studio),('admin',admin),('analytics',analytics)]:
        if 'localStorage' in src: errors.append({'code':'persistent_browser_token_storage','surface':name})
    if '("scripts.frontend_polish_audit", [])' not in regression: errors.append({'code':'regression_missing_frontend_polish'})
    return {'format':'x1-frontend-polish-audit-v1','status':'passed' if not errors else 'failed','errors':errors,'composition_layer_deferred_to_sprint83':True}
def main()->int:
    r=audit();print(json.dumps(r,ensure_ascii=False,indent=2));return 0 if r['status']=='passed' else 2
if __name__=='__main__':raise SystemExit(main())
