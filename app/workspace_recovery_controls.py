from __future__ import annotations

import re

_MARKER = "OLYA_RECOVERY_CONTROLS_V6"


def _nonce(document: str) -> str:
    match = re.search(r'''nonce=["']([^"']+)["']''', document, flags=re.I)
    return match.group(1) if match else ""


def enhance_recovery_controls(document: str) -> str:
    """Separate send/cancel, compact turn actions and a low-overhead thinking indicator."""
    if _MARKER in document or 'id="messages"' not in document or 'id="send"' not in document:
        return document

    # Reduce repeated full-Markdown work and avoid nested smooth-scroll animations.
    document = document.replace("now-run.lastPaint>=28", "now-run.lastPaint>=90")
    document = document.replace("await new Promise(r=>setTimeout(r,18))", "await new Promise(r=>setTimeout(r,45))")
    document = document.replace("performance.now()-started<260", "performance.now()-started<90")
    # Yandex Metrica only needs direct message additions, not every nested Markdown mutation.
    document = document.replace(
        "observe(x1messages,{childList:true,subtree:true})",
        "observe(x1messages,{childList:true})",
    )

    nonce = _nonce(document)
    nonce_attr = f' nonce="{nonce}"' if nonce else ""
    css = r'''
/* OLYA_RECOVERY_CONTROLS_V6 */
.messages{scroll-behavior:auto!important}
.olya-turn-actions{display:flex;gap:3px;margin:5px 0 0;opacity:.66;align-items:center}
.olya-turn-action{width:28px;height:28px;border:0;background:transparent;color:#777;font:16px/1 system-ui;padding:0;border-radius:8px;cursor:pointer;display:grid;place-items:center}
.olya-turn-action:hover{background:#f0f0f2;color:#222}.olya-turn-action:focus-visible{outline:2px solid #aaa}
.olya-stop-button{width:36px;height:36px;min-width:36px;border:0;border-radius:50%;background:#efefef;color:#222;display:none;place-items:center;font:14px/1 system-ui;cursor:pointer;margin-left:5px}
.olya-stop-button.show{display:grid}.olya-stop-button:hover{background:#e4e4e5}.olya-stop-button:active{transform:scale(.94)}
#send.stop,#send.olya-current-running{pointer-events:none!important;background:#18181b!important;color:#fff!important}
.assistant.olya-premium-live .bubble:after{display:none!important;content:none!important}
.olya-thinking-host{width:100%;flex:0 0 auto;background:linear-gradient(180deg,rgba(255,255,255,0),#fff 38%)}
.olya-thinking-row{max-width:780px;margin:0 auto 4px;padding:0 18px;min-height:30px;display:none;align-items:center;gap:9px;color:#62626b;animation:olyaThinkIn .16s ease-out}
.olya-thinking-row.show{display:flex}.olya-thinking-orb{width:24px;height:24px;min-width:24px;border-radius:50%;position:relative;background:#f0f0f2;display:grid;place-items:center;overflow:hidden}
.olya-thinking-orb:before{content:"";position:absolute;width:14px;height:14px;border-radius:50%;border:2px solid #a5a5ad;border-top-color:#27272a;animation:olyaSpin .9s linear infinite}
.olya-thinking-body{min-width:0;display:flex;align-items:baseline;gap:7px;flex-wrap:wrap}.olya-thinking-label{font-size:13px;font-weight:600;color:#44444b}.olya-thinking-dots{display:inline-flex;gap:3px;align-items:center;height:12px}.olya-thinking-dots i{display:block;width:4px;height:4px;border-radius:50%;background:#777780;animation:olyaDot 1.05s ease-in-out infinite}.olya-thinking-dots i:nth-child(2){animation-delay:.14s}.olya-thinking-dots i:nth-child(3){animation-delay:.28s}.olya-thinking-time{font-size:11px;color:#a0a0a7;font-variant-numeric:tabular-nums}
@keyframes olyaSpin{to{transform:rotate(360deg)}}@keyframes olyaDot{0%,60%,100%{opacity:.28;transform:translateY(0)}30%{opacity:1;transform:translateY(-2px)}}@keyframes olyaThinkIn{from{opacity:0;transform:translateY(3px)}to{opacity:1;transform:none}}
@media(max-width:760px){.olya-turn-action{width:30px;height:30px}.olya-stop-button{width:36px;height:36px}.olya-thinking-row{padding:0 10px;margin-bottom:2px}}
@media(prefers-reduced-motion:reduce){.olya-thinking-orb:before,.olya-thinking-dots i{animation:none!important}.olya-thinking-row{animation:none!important}}
'''
    js = r'''
(function(){
  const messages=document.getElementById('messages'),send=document.getElementById('send'),prompt=document.getElementById('prompt'),state=document.getElementById('state');
  if(!messages||!send||!prompt)return;
  const stop=document.createElement('button');stop.type='button';stop.className='olya-stop-button';stop.textContent='■';stop.title='Остановить ответ';stop.setAttribute('aria-label','Остановить ответ');
  send.parentNode.insertBefore(stop,send);

  const host=document.createElement('div');host.className='olya-thinking-host';
  const thinking=document.createElement('div');thinking.className='olya-thinking-row';thinking.setAttribute('role','status');thinking.setAttribute('aria-live','polite');
  const orb=document.createElement('span');orb.className='olya-thinking-orb';
  const body=document.createElement('span');body.className='olya-thinking-body';
  const label=document.createElement('span');label.className='olya-thinking-label';label.textContent='Думаю над ответом';
  const dots=document.createElement('span');dots.className='olya-thinking-dots';dots.innerHTML='<i></i><i></i><i></i>';
  const elapsed=document.createElement('span');elapsed.className='olya-thinking-time';elapsed.textContent='0,0 с';
  body.append(label,dots,elapsed);thinking.append(orb,body);host.append(thinking);
  const composerWrap=document.querySelector('#view-chat .composer-wrap')||document.querySelector('.composer-wrap');
  if(composerWrap&&composerWrap.parentNode)composerWrap.parentNode.insertBefore(host,composerWrap);else messages.parentNode&&messages.parentNode.append(host);

  let bypass=false,thinkingStarted=0,timer=0,lastLabel='';
  function running(){return send.classList.contains('stop')||send.classList.contains('olya-current-running')}
  function liveHasText(){const rows=[...messages.querySelectorAll('.assistant.olya-premium-live .bubble')];if(!rows.length)return false;return Boolean((rows[rows.length-1].textContent||'').trim())}
  function labelForState(){const t=((state&&state.textContent)||'').toLowerCase();if(/очеред|слот|ресурс/.test(t))return 'Жду вычислительный слот';if(/источник|ищу|поиск|актуальн|данн/.test(t))return 'Ищу и проверяю источники';if(/факт|проверяю/.test(t))return 'Проверяю факт';if(/собран|формир|готовлю/.test(t))return 'Формирую ответ';return 'Думаю над ответом'}
  function startTimer(){if(timer)return;thinkingStarted=performance.now();timer=setInterval(()=>{const sec=Math.max(0,(performance.now()-thinkingStarted)/1000);const value=sec.toFixed(1).replace('.',',')+' с';if(elapsed.textContent!==value)elapsed.textContent=value},250)}
  function stopTimer(){if(timer){clearInterval(timer);timer=0}if(elapsed.textContent!=='0,0 с')elapsed.textContent='0,0 с'}
  function syncThinking(){const show=running()&&!liveHasText(),nextLabel=labelForState();if(nextLabel!==lastLabel){label.textContent=nextLabel;lastLabel=nextLabel}thinking.classList.toggle('show',show);if(show)startTimer();else stopTimer()}
  function syncStop(){const on=running();stop.classList.toggle('show',on);const title=on?'Ответ формируется':'Отправить';if(send.title!==title)send.title=title;if(send.getAttribute('aria-label')!==title)send.setAttribute('aria-label',title);if(send.textContent!=='↑')send.textContent='↑';syncThinking()}
  stop.onclick=()=>{if(!running())return;bypass=true;send.style.pointerEvents='auto';try{send.click()}finally{bypass=false;send.style.pointerEvents=''}};
  send.addEventListener('click',e=>{if(running()&&!bypass){e.preventDefault();e.stopImmediatePropagation()}},true);

  function userText(row){const b=row.querySelector('.bubble');return b?b.textContent.trim():''}
  function setPrompt(text){prompt.value=text||'';prompt.dispatchEvent(new Event('input',{bubbles:true}));prompt.focus();try{prompt.setSelectionRange(prompt.value.length,prompt.value.length)}catch(_){}}
  function sendText(text){if(!text)return;setPrompt(text);if(!running()&&!send.disabled)setTimeout(()=>send.click(),0)}
  function action(icon,title,fn){const b=document.createElement('button');b.type='button';b.className='olya-turn-action';b.textContent=icon;b.title=title;b.setAttribute('aria-label',title);b.onclick=fn;return b}
  function decorateUser(row){if(row.dataset.olyaActions)return;const text=userText(row);if(!text)return;row.dataset.olyaActions='1';const actions=document.createElement('div');actions.className='olya-turn-actions';actions.append(action('✎','Изменить сообщение',()=>setPrompt(text)),action('↻','Повторить сообщение',()=>sendText(text)));row.append(actions)}
  function latestUserText(){const rows=[...messages.querySelectorAll('.msg.user')];return rows.length?userText(rows[rows.length-1]):''}
  function decorateErrors(){for(const row of messages.querySelectorAll('.msg.assistant,.msg.error')){if(row.dataset.olyaErrorAction)continue;const text=(row.textContent||'').toLowerCase();if(!/(ошиб|error|соедин|connection|прерван|interrupted|повтор|failed)/.test(text))continue;row.dataset.olyaErrorAction='1';const actions=document.createElement('div');actions.className='olya-turn-actions';actions.append(action('↻','Повторить запрос',()=>sendText(latestUserText())),action('✎','Изменить запрос',()=>setPrompt(latestUserText())));row.append(actions)}}
  function syncRows(){messages.querySelectorAll('.msg.user').forEach(decorateUser);decorateErrors();syncStop()}
  new MutationObserver(syncRows).observe(messages,{childList:true});
  new MutationObserver(syncStop).observe(send,{attributes:true,attributeFilter:['class','disabled']});
  if(state)new MutationObserver(()=>{syncThinking();const t=(state.textContent||'').toLowerCase();if(/(ошиб|прерван|соедин)/.test(t)&&latestUserText())decorateErrors()}).observe(state,{childList:true,subtree:true,characterData:true});
  syncRows();
})();
'''
    document = document.replace("</head>", f'<style{nonce_attr}>{css}</style></head>', 1)
    document = document.replace("</body>", f'<script{nonce_attr}>{js}</script></body>', 1)
    return document
