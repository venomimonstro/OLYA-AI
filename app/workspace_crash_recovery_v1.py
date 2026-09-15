from __future__ import annotations

import re

_MARKER = "OLYA_CRASH_RECOVERY_V1"


def _nonce(document: str) -> str:
    match = re.search(r'''nonce=["']([^"']+)["']''', document, flags=re.I)
    return match.group(1) if match else ""


def enhance_crash_recovery(document: str) -> str:
    """Make browser recovery fail-open after app/container restarts.

    The premium layer used to auto-restore up to eight persisted background runs
    sequentially. After an app restart those in-memory runs no longer exist, so
    every page reload could spend tens of seconds probing stale ids. Recovery is
    now owned by workspace_reliability_v5, which uses /health instance_id and a
    single bounded pending-run check. The premium list remains useful within a
    live page but is not replayed automatically across page loads.
    """
    if _MARKER in document:
        return document

    old = "setTimeout(()=>{void restorePremiumRuns();syncRunControls();resizeComposer()},450);"
    if old in document:
        document = document.replace(
            old,
            "setTimeout(()=>{syncRunControls();resizeComposer()},450);",
            1,
        )

    nonce = _nonce(document)
    nonce_attr = f' nonce="{nonce}"' if nonce else ""
    script = r'''
/* OLYA_CRASH_RECOVERY_V1 */
(function(){
  const premiumKey='olya_parallel_runs_v1';
  // Do not allow stale browser-only background run records to create a recovery
  // storm after reload. The server/database remains the source of truth for
  // completed chat history, and the active pending request is handled by the
  // crash-aware reliability layer.
  try{localStorage.removeItem(premiumKey)}catch(_e){}

  let offline=false,timer=0;
  async function probe(){
    if(document.hidden)return;
    try{
      const controller=new AbortController(),t=setTimeout(()=>controller.abort(),2500);
      const response=await fetch('/health',{cache:'no-store',credentials:'same-origin',signal:controller.signal});
      clearTimeout(t);
      if(!response.ok)throw new Error('health');
      if(offline){
        offline=false;
        const state=document.getElementById('state');if(state)state.textContent='Сервер восстановлен. Обновляю данные…';
        try{if(typeof loadConversations==='function')await loadConversations()}catch(_e){}
        try{if(typeof loadProjects==='function')await loadProjects()}catch(_e){}
        try{if(typeof refreshBudget==='function')await refreshBudget()}catch(_e){}
        try{if(window.olyaRefreshChatLibrary)await window.olyaRefreshChatLibrary()}catch(_e){}
        if(state)state.textContent='Сервер восстановлен.';
      }
    }catch(_e){
      offline=true;
      const state=document.getElementById('state');if(state&&!/переподключ/i.test(state.textContent||''))state.textContent='Связь с сервером потеряна. Переподключаюсь автоматически…';
    }
  }
  function schedule(){if(timer)return;timer=setInterval(()=>{void probe()},5000)}
  window.addEventListener('online',()=>{void probe()});
  document.addEventListener('visibilitychange',()=>{if(!document.hidden)void probe()});
  schedule();
})();
'''
    return document.replace("</body>", f'<script{nonce_attr}>{script}</script></body>', 1)
