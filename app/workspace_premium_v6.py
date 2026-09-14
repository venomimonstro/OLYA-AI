from __future__ import annotations

import re

_MARKER = "OLYA_PREMIUM_WORKSPACE_V6"


def _nonce(document: str) -> str:
    match = re.search(r'nonce=["\']([^"\']+)["\']', document, flags=re.I)
    return match.group(1) if match else ""


def enhance_workspace_premium_v6(document: str) -> str:
    """Premium interaction/design layer applied after reliability and quality UI.

    The base workspace intentionally stays dependency-free. This final layer owns
    presentation, smooth incremental rendering and per-conversation browser runs
    so a long request never freezes the rest of the product.
    """
    if _MARKER in document or 'id="view-chat"' not in document or 'id="prompt"' not in document:
        return document

    nonce = _nonce(document)
    nonce_attr = f' nonce="{nonce}"' if nonce else ""

    css = r'''
/* OLYA_PREMIUM_WORKSPACE_V6 */
:root{
  --olya-bg:#ffffff;--olya-side:#f7f7f8;--olya-hover:#ececee;--olya-text:#18181b;
  --olya-muted:#71717a;--olya-line:#e7e7ea;--olya-soft:#f4f4f5;--olya-code:#18181b;
  --olya-shadow:0 8px 30px rgba(0,0,0,.06),0 1px 2px rgba(0,0,0,.05);
  --olya-focus:0 0 0 3px rgba(24,24,27,.08);
}
html,body{background:var(--olya-bg)!important;color:var(--olya-text)!important}
body{font-family:Inter,ui-sans-serif,-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,Arial,sans-serif!important;-webkit-font-smoothing:antialiased;text-rendering:optimizeLegibility}
.shell{grid-template-columns:260px minmax(0,1fr)!important;background:#fff!important}
.side{background:var(--olya-side)!important;border-right:0!important;padding:10px 9px 9px!important;box-shadow:inset -1px 0 0 rgba(0,0,0,.035)}
.brand{height:44px;display:flex;align-items:center;padding:0 11px!important;font-size:15px!important;font-weight:700!important;letter-spacing:-.015em!important;color:#202023!important}
.nav{gap:2px!important}.navbtn,.side .new,.side .conv,.side .linkbtn{font-weight:500!important;letter-spacing:-.005em!important}
.navbtn,.side .conv{min-height:38px!important;border-radius:10px!important;padding:8px 10px!important;color:#3f3f46!important;transition:background .12s ease,color .12s ease,transform .12s ease!important}
.navbtn:hover,.navbtn.active,.side .conv:hover,.side .conv.active{background:var(--olya-hover)!important;color:#18181b!important}
.side .new{min-height:40px!important;border:0!important;background:transparent!important;color:#27272a!important;border-radius:10px!important;padding:8px 10px!important;text-align:left!important;margin-top:5px!important;box-shadow:none!important}
.side .new:hover{background:var(--olya-hover)!important}
.side-section{font-size:11px!important;text-transform:none!important;letter-spacing:0!important;color:#96969f!important;margin:15px 10px 5px!important}
.history{padding-right:2px;scrollbar-width:thin;scrollbar-color:#d7d7db transparent}
.conv{position:relative!important;padding-right:28px!important}.conv .olya-run-dot{position:absolute;right:10px;top:50%;width:7px;height:7px;margin-top:-3px;border-radius:50%;background:#18181b;box-shadow:0 0 0 3px rgba(24,24,27,.08);animation:olyaRunPulse 1.3s ease-in-out infinite}
.account-mini{border-top:0!important;padding:9px 9px 3px!important;color:#85858e!important}
.main{background:#fff!important}.top{min-height:54px!important;height:54px!important;padding:7px 14px!important;border-bottom:0!important;gap:7px!important;background:rgba(255,255,255,.92)!important;backdrop-filter:blur(14px);z-index:5}
.page-title{font-size:14px!important;font-weight:600!important;color:#3f3f46!important}
.chat-controls{gap:5px!important;flex-wrap:nowrap!important}.top select{height:36px!important;max-width:180px;background:#fff!important;color:#3f3f46!important;border:1px solid transparent!important;border-radius:10px!important;padding:0 30px 0 10px!important;box-shadow:none!important;transition:background .12s ease,border-color .12s ease!important}
.top select:hover,.top select:focus{background:#f5f5f6!important;border-color:#ececef!important;outline:0!important}
#verify,#web{max-width:190px}.budget{display:none!important}.health{color:#92929b!important}
.olya-advanced-wrap{position:relative;display:inline-flex}.olya-advanced-button{height:36px;width:36px;border:0;border-radius:10px;background:transparent;color:#52525b;font-size:18px;display:grid;place-items:center}.olya-advanced-button:hover,.olya-advanced-button[aria-expanded="true"]{background:#f3f3f4}.olya-advanced-menu{position:absolute;right:0;top:42px;width:245px;padding:8px;background:#fff;border:1px solid #e8e8eb;border-radius:14px;box-shadow:0 16px 50px rgba(0,0,0,.12);z-index:40;display:none}.olya-advanced-menu.open{display:grid;gap:7px;animation:olyaMenuIn .12s ease-out}.olya-advanced-menu label{font-size:11px;color:#8a8a93;padding:2px 5px 0}.olya-advanced-menu select{width:100%!important;max-width:none!important;border:1px solid #ececef!important;background:#fafafa!important}
.view{background:#fff!important}.messages{padding:24px max(18px,calc((100% - 780px)/2)) 34px!important;scrollbar-width:thin;scrollbar-color:#dddde1 transparent;overscroll-behavior:contain}
.msg{max-width:760px!important;margin:0 auto 28px!important;animation:olyaMessageIn .16s ease-out}.msg .role{display:none!important}.bubble{color:#202024!important;font-size:16px!important;line-height:1.68!important;letter-spacing:-.006em}.user{display:flex!important;justify-content:flex-end!important}.user .bubble{max-width:min(78%,620px)!important;background:#f4f4f4!important;border:0!important;padding:10px 14px!important;border-radius:19px 19px 5px 19px!important;color:#27272a!important;box-shadow:none!important}.assistant .bubble{padding:0 1px!important;background:transparent!important;border:0!important}.assistant.olya-premium-live .bubble:after{content:"";display:inline-block;width:7px;height:18px;margin-left:3px;vertical-align:-3px;border-radius:2px;background:#b7b7bd;animation:olyaCaret 1s ease-in-out infinite}.error .bubble{max-width:680px;padding:11px 13px;border:1px solid #f0d6d6;border-radius:12px;background:#fffafa;color:#9f3030!important}
.bubble p{margin:.48em 0!important}.bubble h1,.bubble h2,.bubble h3{letter-spacing:-.02em!important;color:#18181b!important}.bubble h1{font-size:1.45em!important}.bubble h2{font-size:1.24em!important}.bubble h3{font-size:1.1em!important}.bubble a{color:#18181b;text-decoration-color:#a1a1aa;text-underline-offset:3px}.bubble blockquote{border-left:2px solid #d4d4d8!important;color:#52525b!important}
.codewrap{border:0!important;border-radius:13px!important;background:var(--olya-code)!important;box-shadow:0 0 0 1px rgba(0,0,0,.06)!important}.codehead{background:#242427!important;border-bottom:1px solid #323238!important;color:#b5b5bc!important}.copy{background:#303035!important;border:0!important;color:#e9e9ec!important;border-radius:7px!important}.tablewrap{border-color:#e4e4e7!important;border-radius:12px!important}.bubble th{background:#fafafa!important}.bubble td,.bubble th{border-color:#e8e8eb!important}
.meta{opacity:0;transition:opacity .15s ease!important;margin-top:8px!important}.msg:hover>.meta,.meta:focus-within{opacity:1}.badge{border:0!important;background:#f5f5f6!important;color:#85858e!important;padding:3px 7px!important;font-size:10px!important}
.olya-source-strip{max-width:760px!important}.olya-source-chip{border-color:#e7e7ea!important;background:#fafafa!important}.olya-source-chip:hover{background:#f1f1f2!important}
.chat-empty{max-width:650px!important;margin:19vh auto 0!important}.chat-empty h1{font-size:32px!important;font-weight:650!important;letter-spacing:-.035em!important;color:#202024!important}.chat-empty p{max-width:520px;margin:7px auto!important;font-size:15px!important;line-height:1.55!important;color:#92929b!important}
.composer-wrap{border-top:0!important;background:linear-gradient(180deg,rgba(255,255,255,0),#fff 24%,#fff)!important;padding:12px 18px 18px!important;position:relative;z-index:8}.composer{max-width:780px!important;border:1px solid #dedee2!important;background:#fff!important;border-radius:25px!important;padding:7px 8px 7px 12px!important;box-shadow:var(--olya-shadow)!important;transition:border-color .15s ease,box-shadow .15s ease,transform .15s ease!important}.composer:focus-within{border-color:#cacacf!important;box-shadow:var(--olya-shadow),var(--olya-focus)!important}.composer textarea,#prompt{min-height:34px!important;max-height:240px!important;height:38px;padding:8px 8px 5px!important;color:#202024!important;background:transparent!important;font-size:16px!important;line-height:1.5!important;transition:height .12s ease!important;scrollbar-width:thin;scrollbar-color:#d4d4d8 transparent}.composer textarea::placeholder{color:#a1a1aa}.composer-foot{min-height:38px;padding:1px 1px 0 3px!important}.composer-foot #counter{font-size:10px!important;color:#a1a1aa!important}.send{margin-left:auto!important;width:36px!important;height:36px!important;min-width:36px!important;padding:0!important;border-radius:50%!important;background:#18181b!important;color:#fff!important;font-size:18px!important;font-weight:600!important;display:grid!important;place-items:center!important;transition:transform .1s ease,background .12s ease,opacity .12s ease!important}.send:hover{background:#303036!important}.send:active{transform:scale(.94)}.send:disabled{opacity:.34!important}.send.olya-current-running{background:#27272a!important}.send.stop{background:#27272a!important}
.progress{max-width:780px!important;min-height:0!important;margin:0 auto 6px!important;gap:5px!important}.progress:empty{display:none}.step{border:0!important;background:#f5f5f6!important;color:#92929b!important;padding:3px 7px!important}.step.active{color:#34343a!important;background:#ededf0!important}.step.done{color:#73737b!important}.state{max-width:780px!important;margin:5px auto 0!important;min-height:14px!important;font-size:11px!important;color:#9999a1!important;padding-left:8px!important}.state:empty{display:none}
.older{background:#fff!important;color:#71717a!important;border-color:#e5e5e8!important;border-radius:999px!important}.retry{background:#fff!important;color:#3f3f46!important;border-color:#dedee2!important;border-radius:999px!important}
.card,.item,.empty-panel{background:#fff!important;border-color:#e7e7ea!important;box-shadow:0 1px 2px rgba(0,0,0,.02)!important}.content{color:#27272a!important}.lead,.muted,.item-meta{color:#85858e!important}.primary{background:#18181b!important;border-color:#18181b!important;border-radius:10px!important}.secondary{background:#fff!important;color:#3f3f46!important;border-color:#dedee2!important;border-radius:10px!important}.field,input[type=text],input[type=file],.project-editor textarea{background:#fff!important;color:#27272a!important;border-color:#dedee2!important}
.olya-background-toast{position:fixed;right:18px;bottom:18px;z-index:90;max-width:330px;padding:10px 13px;border:1px solid #e7e7ea;border-radius:13px;background:rgba(255,255,255,.96);box-shadow:0 12px 40px rgba(0,0,0,.12);font-size:12px;color:#52525b;backdrop-filter:blur(12px);animation:olyaToastIn .18s ease-out}
@keyframes olyaMessageIn{from{opacity:0;transform:translateY(3px)}to{opacity:1;transform:none}}@keyframes olyaCaret{0%,100%{opacity:.22}50%{opacity:1}}@keyframes olyaRunPulse{0%,100%{opacity:.35;transform:scale(.82)}50%{opacity:1;transform:scale(1)}}@keyframes olyaMenuIn{from{opacity:0;transform:translateY(-4px) scale(.98)}to{opacity:1;transform:none}}@keyframes olyaToastIn{from{opacity:0;transform:translateY(7px)}to{opacity:1;transform:none}}
@media(max-width:760px){.shell{grid-template-columns:1fr!important}.side{inset:0 14% 0 0!important;box-shadow:22px 0 70px rgba(0,0,0,.16)!important}.top{height:50px!important;min-height:50px!important;padding:6px 8px!important}.chat-controls{min-width:0;overflow:hidden}.top #project-chat{display:none}.top select{max-width:140px!important}.messages{padding:18px 12px 26px!important}.msg{margin-bottom:23px!important}.user .bubble{max-width:88%!important}.composer-wrap{padding:8px 8px max(10px,env(safe-area-inset-bottom))!important}.composer{border-radius:22px!important;padding-left:9px!important}.chat-empty{margin-top:15vh!important;padding:0 18px}.chat-empty h1{font-size:28px!important}.olya-advanced-menu{position:fixed;right:10px;left:10px;top:55px;width:auto}.meta{opacity:1}}
@media(prefers-reduced-motion:reduce){*,*:before,*:after{animation-duration:.001ms!important;animation-iteration-count:1!important;transition-duration:.001ms!important;scroll-behavior:auto!important}}
'''

    js = r'''
(function(){
  const PREMIUM='OLYA_PREMIUM_WORKSPACE_V6';
  const runStoreKey='olya_parallel_runs_v1';
  const runs=new Map();
  const reduceMotion=window.matchMedia&&window.matchMedia('(prefers-reduced-motion: reduce)').matches;
  const brand=document.querySelector('.brand');if(brand)brand.textContent='OLYA AI';
  if(empty){const h=empty.querySelector('h1'),p=empty.querySelector('p');if(h)h.textContent='Чем могу помочь?';if(p)p.textContent='Задайте вопрос, поручите задачу или продолжите работу в проекте.'}

  function currentKey(){return conversationId||'new'}
  function activeForConversation(id){for(const run of runs.values())if(!run.done&&run.conversationId===id)return run;return null}
  function persistRuns(){try{const rows=[];for(const run of runs.values())if(!run.done)rows.push({body:run.body,conversation_id:run.conversationId,project_id:run.projectId||null,started_at:run.startedAt||Date.now()});localStorage.setItem(runStoreKey,JSON.stringify(rows.slice(-12)))}catch(_e){}}
  function forgetPersistedRun(id){try{const raw=localStorage.getItem(runStoreKey),rows=raw?JSON.parse(raw):[];localStorage.setItem(runStoreKey,JSON.stringify((Array.isArray(rows)?rows:[]).filter(x=>x&&x.body&&x.body.client_request_id!==id)))}catch(_e){}}
  function clearPremiumRuns(){try{localStorage.removeItem(runStoreKey)}catch(_e){}}
  function toast(text){const old=document.querySelector('.olya-background-toast');if(old)old.remove();const el=document.createElement('div');el.className='olya-background-toast';el.textContent=text;document.body.append(el);setTimeout(()=>{if(el.isConnected)el.remove()},3600)}
  function stickToBottom(){return messages.scrollHeight-messages.scrollTop-messages.clientHeight<170}
  function resizeComposer(){const max=Math.min(240,Math.max(120,window.innerHeight*.32));prompt.style.height='auto';const next=Math.max(38,Math.min(max,prompt.scrollHeight));prompt.style.height=next+'px'}
  prompt.addEventListener('input',resizeComposer);window.addEventListener('resize',resizeComposer);resizeComposer();

  function setupAdvancedMenu(){
    const controls=document.getElementById('chat-controls'),verify=document.getElementById('verify'),web=document.getElementById('web');
    if(!controls||!verify||!web||document.querySelector('.olya-advanced-wrap'))return;
    const wrap=document.createElement('div');wrap.className='olya-advanced-wrap';
    const button=document.createElement('button');button.type='button';button.className='olya-advanced-button';button.setAttribute('aria-label','Настройки ответа');button.setAttribute('aria-expanded','false');button.textContent='⋯';
    const menu=document.createElement('div');menu.className='olya-advanced-menu';
    const vl=document.createElement('label');vl.textContent='Проверка ответа';const wl=document.createElement('label');wl.textContent='Интернет';
    menu.append(vl,verify,wl,web);wrap.append(button,menu);controls.append(wrap);
    button.addEventListener('click',()=>{const open=!menu.classList.contains('open');menu.classList.toggle('open',open);button.setAttribute('aria-expanded',String(open))});
    document.addEventListener('pointerdown',e=>{if(!wrap.contains(e.target)){menu.classList.remove('open');button.setAttribute('aria-expanded','false')}});
  }
  setupAdvancedMenu();

  function syncRunControls(){
    const run=activeForConversation(conversationId);
    send.classList.toggle('olya-current-running',Boolean(run));
    send.textContent=run?'■':'↑';
    send.title=run?'Остановить ответ в этом чате':'Отправить';
    send.setAttribute('aria-label',send.title);
    send.disabled=!run&&!String(prompt.value||'').trim();
    if(run){state.textContent=run.status||'Запрос выполняется…'}
  }
  prompt.addEventListener('input',syncRunControls);

  function createLiveFor(run){
    if(run.live&&run.live.box&&run.live.box.isConnected)return run.live;
    if(conversationId!==run.conversationId)return null;
    const live=add('assistant','');live.box.classList.add('olya-premium-live');live.box.dataset.requestId=run.id;run.live=live;return live;
  }
  function schedulePaint(run){
    if(run.painting)return;run.painting=true;
    const frame=()=>{
      if(run.done&&!run.raw){run.painting=false;return}
      const live=createLiveFor(run);
      if(!live){run.painting=false;return}
      const target=run.raw.length,delta=target-run.visible;
      if(delta<=0){run.painting=false;return}
      const now=performance.now();
      let step=reduceMotion?delta:Math.max(1,Math.min(34,Math.ceil(delta*.2)));
      if(delta>220)step=Math.min(70,Math.ceil(delta*.28));
      run.visible=Math.min(target,run.visible+step);
      if(reduceMotion||!run.lastPaint||now-run.lastPaint>=28||run.visible===target){
        const keep=stickToBottom();renderMarkdown(live.bubble,run.raw.slice(0,run.visible));run.lastPaint=now;if(keep)messages.scrollTop=messages.scrollHeight;
      }
      if(run.visible<target)requestAnimationFrame(frame);else run.painting=false;
    };
    requestAnimationFrame(frame);
  }
  async function finishPaint(run){
    const started=performance.now();
    while(run.visible<run.raw.length&&performance.now()-started<260){run.visible=Math.min(run.raw.length,run.visible+Math.max(16,Math.ceil((run.raw.length-run.visible)*.42)));const live=createLiveFor(run);if(live)renderMarkdown(live.bubble,run.raw.slice(0,run.visible));await new Promise(r=>setTimeout(r,18))}
    run.visible=run.raw.length;
  }

  function parseBlocks(buffer){const blocks=[];let idx;while((idx=buffer.indexOf('\n\n'))>=0){blocks.push(buffer.slice(0,idx));buffer=buffer.slice(idx+2)}return{blocks,buffer}}
  function parseEvent(block){let event='message';const rows=[];for(const line of block.split('\n')){if(line.startsWith('event:'))event=line.slice(6).trim();else if(line.startsWith('data:'))rows.push(line.slice(5).trimStart())}if(!rows.length)return null;try{return{event,data:JSON.parse(rows.join('\n'))}}catch(_e){return null}}

  async function premiumStream(run){
    const headers={Authorization:'Bearer '+token,'Content-Type':'application/json',Accept:'text/event-stream'};
    let lastError=null;
    for(let attempt=0;attempt<4;attempt++){
      const controller=new AbortController();run.controller=controller;
      try{
        const response=await fetch('/v1/chat/stream',{method:'POST',headers,credentials:'omit',body:JSON.stringify(run.body),signal:controller.signal});
        if(!response.ok){let data=null;try{data=await response.json()}catch(_e){}const err=new Error(detailOf(data,response.status));err.status=response.status;if(response.status>=400&&response.status<500)err.terminal=true;throw err}
        if(!response.body)throw new Error('Поток ответа недоступен.');
        const reader=response.body.getReader(),decoder=new TextDecoder();let buffer='';
        while(true){
          const part=await reader.read();if(part.done)break;
          buffer+=decoder.decode(part.value,{stream:true}).replace(/\r\n/g,'\n');const parsed=parseBlocks(buffer);buffer=parsed.buffer;
          for(const block of parsed.blocks){
            const item=parseEvent(block);if(!item)continue;const d=item.data||{};
            if(item.event==='status'){
              if(d.conversation_id&&!run.conversationId){run.conversationId=d.conversation_id;run.body.conversation_id=d.conversation_id;persistRuns()}
              if(d.message)run.status=String(d.message);else if(d.state==='queued')run.status='В очереди…';
              if(conversationId===run.conversationId)state.textContent=run.status||'';
            }else if(item.event==='token'){
              run.raw+=typeof d.text==='string'?d.text:'';run.status='Формирую ответ…';schedulePaint(run);
            }else if(item.event==='replace'){
              run.raw=typeof d.text==='string'?d.text:run.raw;run.visible=Math.min(run.visible,run.raw.length);schedulePaint(run);
            }else if(item.event==='heartbeat'){
              if(!run.raw)run.status=d.queue_waiting>0?'Запрос в очереди…':'Обрабатываю запрос…';if(conversationId===run.conversationId)state.textContent=run.status;
            }else if(item.event==='result')return d;
            else if(item.event==='cancelled')return{cancelled:true};
            else if(item.event==='error'){const err=new Error(detailOf({detail:d.detail},d.status_code||500));err.terminal=true;throw err}
          }
        }
        throw new Error('Соединение завершилось до результата.');
      }catch(e){
        if(e.name==='AbortError'&&run.cancelled)return{cancelled:true};lastError=e;if(e.terminal)throw e;if(attempt===3)throw e;run.status='Восстанавливаю соединение…';await new Promise(r=>setTimeout(r,350*(attempt+1)));
      }
    }
    throw lastError||new Error('Не удалось получить ответ.');
  }

  async function finishRun(run,result){
    if(result&&result.cancelled){run.cancelled=true;run.done=true;forgetPersistedRun(run.id);runs.delete(run.id);if(conversationId===run.conversationId){state.textContent='Генерация остановлена.';syncRunControls()}await loadConversations();return}
    run.raw=String(result&&result.text||run.raw||'');schedulePaint(run);await finishPaint(run);
    if(conversationId===run.conversationId){let live=createLiveFor(run);if(!live)live=add('assistant','');live.box.classList.remove('olya-premium-live');renderFinal(live,result||{text:run.raw});state.textContent='';}
    run.done=true;forgetPersistedRun(run.id);runs.delete(run.id);persistRuns();syncRunControls();await Promise.all([loadConversations(),refreshBudget()]);
    if(conversationId!==run.conversationId)toast('Ответ в другом чате готов.');
  }

  async function runInBackground(run){
    runs.set(run.id,run);persistRuns();syncRunControls();loadConversations();
    try{const result=await premiumStream(run);await finishRun(run,result)}catch(e){run.done=true;forgetPersistedRun(run.id);runs.delete(run.id);if(conversationId===run.conversationId){const live=run.live;if(live&&!live.bubble.textContent)live.box.remove();addError(e.message||'Не удалось получить ответ',null,0);state.textContent='Запрос завершился с ошибкой.';syncRunControls()}else toast('Фоновый запрос завершился с ошибкой.');await loadConversations()}
  }

  async function premiumSubmit(){
    const text=String(prompt.value||'').trim();if(!text)return;
    if(activeForConversation(conversationId))return;
    const projectId=$('project-chat').value||null;
    const draftKey=typeof draftStorageKey==='function'?draftStorageKey():null;
    let cid=conversationId;
    try{
      cid=await ensureConversation(text);
      if(conversationId!==cid)conversationId=cid;
      add('user',text);prompt.value='';updateCounter();resizeComposer();if(draftKey)try{localStorage.removeItem(draftKey)}catch(_e){}
      const live=add('assistant','');live.box.classList.add('olya-premium-live');
      const requestId=newRequestId(),body={messages:[{role:'user',content:text}],conversation_id:cid,mode:$('mode').value,verification:$('verify').value,web_mode:$('web').value,research_source_ids:[],client_request_id:requestId};if(projectId)body.project_id=projectId;
      const run={id:requestId,conversationId:cid,projectId,body,live,raw:'',visible:0,painting:false,lastPaint:0,status:'Запрос принят…',startedAt:Date.now(),done:false,cancelled:false,controller:null};
      void runInBackground(run);prompt.focus();
    }catch(e){addError(e.message||'Не удалось отправить запрос');state.textContent='Не удалось отправить запрос.'}
  }

  async function stopPremiumRun(run){if(!run)return;run.cancelled=true;try{await api('/v1/chat/runs/'+encodeURIComponent(run.id)+'/cancel',{method:'POST'},12000)}catch(_e){}if(run.controller)run.controller.abort();run.done=true;forgetPersistedRun(run.id);runs.delete(run.id);syncRunControls();loadConversations()}

  send.onclick=()=>{const run=activeForConversation(conversationId);if(run)void stopPremiumRun(run);else void premiumSubmit()};
  prompt.addEventListener('keydown',e=>{if(e.key==='Enter'&&!e.shiftKey&&!e.isComposing){e.preventDefault();e.stopImmediatePropagation();const run=activeForConversation(conversationId);if(!run)void premiumSubmit()}},true);
  document.addEventListener('keydown',e=>{if((e.ctrlKey||e.metaKey)&&e.key.toLowerCase()==='k'){e.preventDefault();prompt.focus()}if(e.key==='Escape'){const menu=document.querySelector('.olya-advanced-menu.open');if(menu)menu.classList.remove('open')}});

  const baseOpen=openConversation;openConversation=async function(id,projectId=null){await baseOpen(id,projectId);const run=activeForConversation(id);if(run){run.live=null;createLiveFor(run);schedulePaint(run)}syncRunControls();return undefined};
  const baseNew=newChat;newChat=function(){baseNew();syncRunControls();resizeComposer()};

  loadConversations=async function(){
    try{const projectId=$('project-chat').value,path='/v1/conversations?limit=40'+(projectId?'&project_id='+encodeURIComponent(projectId):''),rows=await api(path,{},30000),h=$('history');$('history-label').textContent=projectId?'Чаты проекта':'Недавние чаты';h.replaceChildren();for(const c of rows){const b=document.createElement('button');b.className='conv'+(c.id===conversationId?' active':'');b.dataset.conversationId=c.id;b.textContent=c.title||'Новый чат';if(activeForConversation(c.id)){const dot=document.createElement('span');dot.className='olya-run-dot';dot.title='Ответ формируется';b.append(dot)}b.onclick=()=>openConversation(c.id,c.project_id);h.append(b)}}catch(_e){}
  };

  async function restorePremiumRuns(){
    let rows=[];try{const raw=localStorage.getItem(runStoreKey);rows=raw?JSON.parse(raw):[]}catch(_e){}if(!Array.isArray(rows)||!rows.length)return;
    for(const saved of rows.slice(-8)){
      const body=saved&&saved.body;if(!body||!body.client_request_id||!body.conversation_id)continue;
      try{
        const snapshot=await api('/v1/chat/runs/'+encodeURIComponent(body.client_request_id),{},10000);
        if(snapshot.status==='succeeded'&&snapshot.result){forgetPersistedRun(body.client_request_id);continue}
        if(['failed','cancelled','interrupted'].includes(snapshot.status)){forgetPersistedRun(body.client_request_id);continue}
        const run={id:body.client_request_id,conversationId:body.conversation_id,projectId:saved.project_id||body.project_id||null,body,live:null,raw:String(snapshot.partial_text||''),visible:0,painting:false,lastPaint:0,status:'Восстанавливаю фоновый запрос…',startedAt:saved.started_at||Date.now(),done:false,cancelled:false,controller:null};void runInBackground(run);
      }catch(_e){}
    }
    await loadConversations();syncRunControls();
  }
  setTimeout(()=>{void restorePremiumRuns();syncRunControls();resizeComposer()},450);

  const logout=document.getElementById('logout');if(logout){const old=logout.onclick;logout.onclick=async e=>{clearPremiumRuns();if(old)return old.call(logout,e)}}
})();
'''

    document = document.replace("</head>", f'<style{nonce_attr}>{css}</style></head>', 1)
    document = document.replace("</body>", f'<script{nonce_attr}>{js}</script></body>', 1)
    return document
