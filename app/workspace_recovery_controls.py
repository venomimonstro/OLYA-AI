from __future__ import annotations

import re

_MARKER = "OLYA_RECOVERY_CONTROLS_V2"


def _nonce(document: str) -> str:
    match = re.search(r'''nonce=["']([^"']+)["']''', document, flags=re.I)
    return match.group(1) if match else ""


def enhance_recovery_controls(document: str) -> str:
    """Separate send/cancel controls and expose compact retry/edit actions."""
    if _MARKER in document or 'id="messages"' not in document or 'id="send"' not in document:
        return document
    nonce = _nonce(document)
    nonce_attr = f' nonce="{nonce}"' if nonce else ""
    css = r'''
/* OLYA_RECOVERY_CONTROLS_V2 */
.olya-turn-actions{display:flex;gap:3px;margin:5px 0 0;opacity:.66;align-items:center}
.olya-turn-action{width:28px;height:28px;border:0;background:transparent;color:#777;font:16px/1 system-ui;padding:0;border-radius:8px;cursor:pointer;display:grid;place-items:center}
.olya-turn-action:hover{background:#f0f0f2;color:#222}.olya-turn-action:focus-visible{outline:2px solid #aaa}
.olya-stop-button{width:36px;height:36px;min-width:36px;border:0;border-radius:50%;background:#efefef;color:#222;display:none;place-items:center;font:14px/1 system-ui;cursor:pointer;margin-left:5px}
.olya-stop-button.show{display:grid}.olya-stop-button:hover{background:#e4e4e5}.olya-stop-button:active{transform:scale(.94)}
#send.stop,#send.olya-current-running{pointer-events:none!important;background:#18181b!important;color:#fff!important}
@media(max-width:760px){.olya-turn-action{width:30px;height:30px}.olya-stop-button{width:36px;height:36px}}
'''
    js = r'''
(function(){
  const messages=document.getElementById('messages'),send=document.getElementById('send'),prompt=document.getElementById('prompt'),state=document.getElementById('state');
  if(!messages||!send||!prompt)return;
  const stop=document.createElement('button');stop.type='button';stop.className='olya-stop-button';stop.textContent='■';stop.title='Остановить ответ';stop.setAttribute('aria-label','Остановить ответ');
  send.parentNode.insertBefore(stop,send);
  let bypass=false;
  function running(){return send.classList.contains('stop')||send.classList.contains('olya-current-running')}
  function syncStop(){const on=running();stop.classList.toggle('show',on);send.title=on?'Ответ формируется':'Отправить';send.setAttribute('aria-label',on?'Ответ формируется':'Отправить');send.textContent='↑'}
  stop.onclick=()=>{if(!running())return;bypass=true;send.style.pointerEvents='auto';try{send.click()}finally{bypass=false;send.style.pointerEvents=''}};
  send.addEventListener('click',e=>{if(running()&&!bypass){e.preventDefault();e.stopImmediatePropagation()}},true);
  function userText(row){const b=row.querySelector('.bubble');return b?b.textContent.trim():''}
  function setPrompt(text){prompt.value=text||'';prompt.dispatchEvent(new Event('input',{bubbles:true}));prompt.focus();try{prompt.setSelectionRange(prompt.value.length,prompt.value.length)}catch(_){}}
  function sendText(text){if(!text)return;setPrompt(text);if(!running()&&!send.disabled)setTimeout(()=>send.click(),0)}
  function action(icon,title,fn){const b=document.createElement('button');b.type='button';b.className='olya-turn-action';b.textContent=icon;b.title=title;b.setAttribute('aria-label',title);b.onclick=fn;return b}
  function decorateUser(row){if(row.dataset.olyaActions)return;const text=userText(row);if(!text)return;row.dataset.olyaActions='1';const actions=document.createElement('div');actions.className='olya-turn-actions';actions.append(action('✎','Изменить сообщение',()=>setPrompt(text)),action('↻','Повторить сообщение',()=>sendText(text)));row.append(actions)}
  function latestUserText(){const rows=[...messages.querySelectorAll('.msg.user')];return rows.length?userText(rows[rows.length-1]):''}
  function decorateErrors(){for(const row of messages.querySelectorAll('.msg.assistant,.msg.error')){if(row.dataset.olyaErrorAction)continue;const text=(row.textContent||'').toLowerCase();if(!/(ошиб|error|соедин|connection|прерван|interrupted|повтор|failed)/.test(text))continue;row.dataset.olyaErrorAction='1';const actions=document.createElement('div');actions.className='olya-turn-actions';actions.append(action('↻','Повторить запрос',()=>sendText(latestUserText())),action('✎','Изменить запрос',()=>setPrompt(latestUserText())));row.append(actions)}}
  function sync(){messages.querySelectorAll('.msg.user').forEach(decorateUser);decorateErrors();syncStop()}
  new MutationObserver(sync).observe(messages,{childList:true,subtree:true,characterData:true});new MutationObserver(syncStop).observe(send,{attributes:true,attributeFilter:['class','disabled']});
  if(state)new MutationObserver(()=>{syncStop();const t=(state.textContent||'').toLowerCase();if(/(ошиб|прерван|соедин)/.test(t)&&latestUserText())decorateErrors()}).observe(state,{childList:true,subtree:true,characterData:true});
  sync();
})();
'''
    document = document.replace("</head>", f'<style{nonce_attr}>{css}</style></head>', 1)
    document = document.replace("</body>", f'<script{nonce_attr}>{js}</script></body>', 1)
    return document
