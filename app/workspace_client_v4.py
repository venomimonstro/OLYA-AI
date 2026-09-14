from __future__ import annotations

import re

_MARKER = "OLYA_WORKSPACE_CLIENT_V4"


def _nonce(document: str) -> str:
    match = re.search(r'nonce=["\']([^"\']+)["\']', document, flags=re.I)
    return match.group(1) if match else ""


def enhance_workspace_v4(document: str) -> str:
    """Final user-workspace polish applied after all legacy composition layers."""
    if _MARKER in document or 'id="view-chat"' not in document or 'id="messages"' not in document:
        return document

    nonce = _nonce(document)
    nonce_attr = f' nonce="{nonce}"' if nonce else ""
    css = r'''
/* OLYA_WORKSPACE_CLIENT_V4 */
html body .side .conv:hover,
html body .side .conv.active,
html body .side .navbtn:hover,
html body .side .navbtn.active,
html body .side .new:hover,
html body .side .linkbtn:hover{
  background:#e9e9ec!important;
  color:#202124!important;
  box-shadow:none!important;
}
html body .side .conv:hover *,html body .side .conv.active *{color:inherit!important}
html body .side .navbtn:focus-visible,
html body .side .conv:focus-visible,
html body .side .new:focus-visible{outline:2px solid #9b9ba3!important;outline-offset:1px!important}

.olya-waiting-row{max-width:800px;width:100%;margin:0 auto 24px;display:flex;align-items:center;gap:10px;color:#6f7077;font-size:13px;min-height:28px}
.olya-waiting-mark{width:28px;height:20px;display:inline-flex;align-items:center;justify-content:center;gap:3px;flex:0 0 auto}
.olya-waiting-mark i{display:block;width:5px;height:5px;border-radius:50%;background:#777982;animation:olya-dot 1.15s infinite ease-in-out both}
.olya-waiting-mark i:nth-child(1){animation-delay:-.24s}.olya-waiting-mark i:nth-child(2){animation-delay:-.12s}
.olya-waiting-text{white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
@keyframes olya-dot{0%,80%,100%{transform:scale(.55);opacity:.38}40%{transform:scale(1);opacity:1}}

.olya-source-strip{max-width:800px;width:100%;margin:10px auto 4px;display:flex;align-items:center;gap:7px;flex-wrap:wrap}
.olya-source-label{font-size:11px;color:#8a8b91;margin-right:1px}
.olya-source-chip{height:31px;max-width:190px;display:inline-flex;align-items:center;gap:7px;padding:4px 9px 4px 5px;border:1px solid #e1e1e4;border-radius:999px;background:#fff;color:#44454a!important;text-decoration:none!important;font-size:11px;line-height:1;transition:background .13s ease,border-color .13s ease,transform .13s ease}
.olya-source-chip:hover{background:#f3f3f5!important;border-color:#d4d4d9!important;color:#202124!important;transform:translateY(-1px)}
.olya-source-chip:focus-visible{outline:2px solid #a8a8af;outline-offset:1px}
.olya-source-logo{position:relative;width:21px;height:21px;flex:0 0 21px;border-radius:50%;display:grid;place-items:center;background:#f0f0f2;color:#55565c;font-size:10px;font-weight:700;overflow:hidden;text-transform:uppercase}
.olya-source-logo img{position:absolute;inset:2px;width:17px;height:17px;border-radius:4px;object-fit:contain;background:#fff}
.olya-source-domain{overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.olya-source-chip[data-verified="true"]{border-color:#dedee2}
.olya-source-chip[data-verified="false"]{border-style:dashed;opacity:.86}
.assistant>.sources{margin-top:8px!important;border-color:#eeeeef!important;background:#fff!important}
.assistant>.sources summary{font-size:11px!important;color:#77787e!important;padding:6px 8px!important}

@media(max-width:760px){
  html body .side .conv:hover,html body .side .conv.active,html body .side .navbtn:hover,html body .side .navbtn.active{background:#e9e9ec!important;color:#202124!important}
  .olya-waiting-row{padding:0 3px;margin-bottom:18px;font-size:12px}
  .olya-source-strip{gap:5px;margin-top:8px}.olya-source-chip{max-width:145px;height:30px}.olya-source-label{width:100%;margin-bottom:1px}
}
@media(prefers-reduced-motion:reduce){.olya-waiting-mark i,.olya-source-chip{animation:none!important;transition:none!important}}
'''

    js = r'''
(function(){
  const messages=document.getElementById('messages');
  const send=document.getElementById('send');
  const state=document.getElementById('state');
  if(!messages||!send)return;

  let waiting=null;
  function lastTurnHasVisibleAssistant(){
    const rows=[...messages.querySelectorAll('.msg')].filter(row=>!row.classList.contains('olya-waiting-row'));
    let lastUser=-1;
    rows.forEach((row,index)=>{if(row.classList.contains('user'))lastUser=index});
    if(lastUser<0)return false;
    for(let i=lastUser+1;i<rows.length;i++){
      if(!rows[i].classList.contains('assistant'))continue;
      const bubble=rows[i].querySelector('.bubble');
      if(bubble&&bubble.textContent.trim())return true;
    }
    return false;
  }
  function waitingLabel(){
    const text=(state&&state.textContent||'').trim();
    if(/источник|поиск|ищу|свер/i.test(text))return text||'Ищу и сверяю источники…';
    if(/очеред|ожид/i.test(text))return text;
    return text||'Готовлю ответ…';
  }
  function ensureWaiting(){
    if(waiting&&waiting.isConnected)return waiting;
    waiting=document.createElement('div');
    waiting.className='msg assistant olya-waiting-row';
    waiting.setAttribute('role','status');
    waiting.setAttribute('aria-live','polite');
    const mark=document.createElement('span');mark.className='olya-waiting-mark';mark.setAttribute('aria-hidden','true');
    mark.innerHTML='<i></i><i></i><i></i>';
    const label=document.createElement('span');label.className='olya-waiting-text';
    waiting.append(mark,label);
    messages.append(waiting);
    return waiting;
  }
  function syncWaiting(){
    const active=send.classList.contains('stop');
    const hasText=lastTurnHasVisibleAssistant();
    if(active&&!hasText){
      const row=ensureWaiting();
      const label=row.querySelector('.olya-waiting-text');
      const next=waitingLabel();
      if(label&&label.textContent!==next)label.textContent=next;
      if(row.hidden)row.hidden=false;
    }else if(waiting){waiting.remove();waiting=null}
  }

  function cleanHost(url){try{return new URL(url).hostname.replace(/^www\./,'')}catch(_){return ''}}
  function favicon(url){try{const u=new URL(url);return u.origin+'/favicon.ico'}catch(_){return ''}}
  function upgradeSources(){
    for(const box of messages.querySelectorAll('.msg.assistant')){
      if(box.classList.contains('olya-waiting-row'))continue;
      const links=[...box.querySelectorAll('.sources a[href^="http://"],.sources a[href^="https://"]')];
      if(!links.length)continue;
      const unique=[];const seen=new Set();
      for(const link of links){const href=link.href;if(!href||seen.has(href))continue;seen.add(href);unique.push(link);if(unique.length>=6)break}
      const key=unique.map(link=>link.href).join('|');
      let strip=box.querySelector(':scope > .olya-source-strip');
      if(strip&&strip.dataset.key===key)continue;
      if(strip)strip.remove();
      strip=document.createElement('div');strip.className='olya-source-strip';strip.dataset.key=key;
      const label=document.createElement('span');label.className='olya-source-label';label.textContent='Источники';strip.append(label);
      for(const link of unique){
        const href=link.href,host=cleanHost(href);if(!host)continue;
        const chip=document.createElement('a');chip.className='olya-source-chip';chip.href=href;chip.target='_blank';chip.rel='noopener noreferrer';
        chip.title=(link.textContent.trim()||host)+' · открыть '+host;
        const logo=document.createElement('span');logo.className='olya-source-logo';logo.textContent=(host[0]||'?').toUpperCase();
        const img=document.createElement('img');img.alt='';img.loading='lazy';img.referrerPolicy='no-referrer';img.src=favicon(href);img.addEventListener('error',()=>img.remove(),{once:true});logo.append(img);
        const domain=document.createElement('span');domain.className='olya-source-domain';domain.textContent=host;
        chip.append(logo,domain);strip.append(chip);
      }
      const firstDetails=box.querySelector(':scope > .sources');
      if(firstDetails)box.insertBefore(strip,firstDetails);else box.append(strip);
    }
  }

  const sendObserver=new MutationObserver(syncWaiting);
  sendObserver.observe(send,{attributes:true,attributeFilter:['class','disabled'],childList:true,subtree:true});
  if(state)new MutationObserver(syncWaiting).observe(state,{childList:true,subtree:true,characterData:true});
  const messagesObserver=new MutationObserver(()=>{syncWaiting();upgradeSources()});
  messagesObserver.observe(messages,{childList:true,subtree:true,characterData:true});
  syncWaiting();upgradeSources();
})();
'''

    document = document.replace("</head>", f'<style{nonce_attr}>{css}</style></head>', 1)
    document = document.replace("</body>", f'<script{nonce_attr}>{js}</script></body>', 1)
    return document
