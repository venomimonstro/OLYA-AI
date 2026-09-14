from __future__ import annotations

import re


def _nonce(document: str) -> str:
    match = re.search(r'nonce=["\']([^"\']+)["\']', document, flags=re.I)
    return match.group(1) if match else ""


def enhance_parallel_draft_safety(document: str) -> str:
    marker = "OLYA_PARALLEL_DRAFT_SAFETY_V1"
    if marker in document or "OLYA_PREMIUM_WORKSPACE_V6" not in document:
        return document
    nonce = _nonce(document)
    nonce_attr = f' nonce="{nonce}"' if nonce else ""
    script = r'''
/* OLYA_PARALLEL_DRAFT_SAFETY_V1 */
(function(){
  if(typeof renderFinal!=='function'||typeof prompt==='undefined')return;
  const previous=renderFinal;
  renderFinal=function(live,data){
    const nextText=String(prompt.value||'');
    previous(live,data);
    if(nextText.trim()&&typeof saveDraft==='function')saveDraft();
  };
})();
'''
    return document.replace("</body>", f'<script{nonce_attr}>{script}</script></body>', 1)
