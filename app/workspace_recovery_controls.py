from __future__ import annotations

import re

_MARKER = "OLYA_RECOVERY_CONTROLS_V1"


def _nonce(document: str) -> str:
    match = re.search(r'''nonce=["']([^"']+)["']''', document, flags=re.I)
    return match.group(1) if match else ""


def enhance_recovery_controls(document: str) -> str:
    """Expose cancel/retry/edit controls without depending on internal run maps."""
    if _MARKER in document or 'id="messages"' not in document or 'id="send"' not in document:
        return document
    nonce = _nonce(document)
    nonce_attr = f' nonce="{nonce}"' if nonce else ""
    css = r'''
/* OLYA_RECOVERY_CONTROLS_V1 */
.olya-turn-actions{display:flex;gap:6px;margin:5px 0 0;opacity:.72;flex-wrap:wrap}
.olya-turn-action{border:0;background:transparent;color:#777;font:inherit;font-size:11px;padding:3px 5px;border-radius:6px;cursor:pointer}
.olya-turn-action:hover{background:#f0f0f2;color:#222}.olya-turn-action:focus-visible{outline:2px solid #aaa}
.olya-stop-hint{position:fixed;right:22px;bottom:92px;z-index:30;border:1px solid #ddd;background:#fff;color:#333;border-radius:999px;padding:7px 11px;font-size:12px;box-shadow:0 4px 18px rgba(0,0,0,.08);cursor:pointer;display:none}
.olya-stop-hint.show{display:block}
@media(max-width:760px){.olya-stop-hint{right:12px;bottom:82px}.olya-turn-action{font-size:10px}}
'''
    js = r'''
(function(){
  const messages=document.getElementById('messages'),send=document.getElementById('send'),prompt=document.getElementById('prompt'),state=document.getElementById('state');
  if(!messages||!send||!prompt)return;
  const stop=document.createElement('button');stop.type='button';stop.className='olya-stop-hint';stop.textContent='Остановить';stop.title='Остановить текущий ответ';document.body.append(stop);
  function running(){return send.classList.contains('stop')||send.classList.contains('olya-current-running')}
  function syncStop(){const on=running();stop.classList.toggle('show',on);if(on){send.disabled=false;send.title='Остановить ответ';send.setAttribute('aria-label','Остановить ответ')}}
  stop.onclick=()=>{if(running())send.click()};
  function userText(row){const b=row.querySelector('.bubble');return b?b.textContent.trim():''}
  function setPrompt(text){prompt.value=text||'';prompt.dispatchEvent(new Event('input',{bubbles:true}));prompt.focus();try{prompt.setSelectionRange(prompt.value.length,prompt.value.length)}catch(_){}}
  function sendText(text){if(!text)return;setPrompt(text);if(!running()&&!send.disabled)setTimeout(()=>send.click(),0)}
  function decorateUser(row){if(row.dataset.olyaActions)return;const text=userText(row);if(!text)return;row.dataset.olyaActions='1';const actions=document.createElement('div');actions.className='olya-turn-actions';
    const edit=document.createElement('button');edit.type='button';edit.className='olya-turn-action';edit.textContent='Изменить';edit.onclick=()=>setPrompt(text);
    const retry=document.createElement('button');retry.type='button';retry.className='olya-turn-action';retry.textContent='Повторить';retry.onclick=()=>sendText(text);
    actions.append(edit,retry);row.append(actions)}
  function latestUserText(){const rows=[...messages.querySelectorAll('.msg.user')];return rows.length?userText(rows[rows.length-1]):''}
  function decorateErrors(){for(const row of messages.querySelectorAll('.msg.assistant')){if(row.dataset.olyaErrorAction)continue;const text=(row.textContent||'').toLowerCase();if(!/(ошиб|error|соедин|connection|прерван|interrupted|повтор)/.test(text))continue;row.dataset.olyaErrorAction='1';const actions=document.createElement('div');actions.className='olya-turn-actions';const retry=document.createElement('button');retry.type='button';retry.className='olya-turn-action';retry.textContent='Повторить запрос';retry.onclick=()=>sendText(latestUserText());const edit=document.createElement('button');edit.type='button';edit.className='olya-turn-action';edit.textContent='Изменить запрос';edit.onclick=()=>setPrompt(latestUserText());actions.append(retry,edit);row.append(actions)}}
  function sync(){messages.querySelectorAll('.msg.user').forEach(decorateUser);decorateErrors();syncStop()}
  new MutationObserver(sync).observe(messages,{childList:true,subtree:true,characterData:true});new MutationObserver(syncStop).observe(send,{attributes:true,attributeFilter:['class','disabled']});
  if(state)new MutationObserver(()=>{syncStop();const t=(state.textContent||'').toLowerCase();if(/(ошиб|прерван|соедин)/.test(t)&&latestUserText())decorateErrors()}).observe(state,{childList:true,subtree:true,characterData:true});
  sync();
})();
'''
    document = document.replace("</head>", f'<style{nonce_attr}>{css}</style></head>', 1)
    document = document.replace("</body>", f'<script{nonce_attr}>{js}</script></body>', 1)
    return document
