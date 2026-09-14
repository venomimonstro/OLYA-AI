from __future__ import annotations

import re

from starlette.responses import HTMLResponse


_MARKER = "OLYA_WORKSPACE_CLIENT_V3"


def _nonce(document: str) -> str:
    match = re.search(r'nonce=["\']([^"\']+)["\']', document, flags=re.I)
    return match.group(1) if match else ""


def _workspace_document(document: str) -> bool:
    return (
        "OLYA_PRODUCT_SURFACE_V2" in document
        and 'id="view-chat"' in document
        and 'id="messages"' in document
        and 'id="prompt"' in document
        and 'id="send"' in document
    )


def _enhance(document: str) -> str:
    if not _workspace_document(document) or _MARKER in document:
        return document

    nonce = _nonce(document)
    nonce_attr = f' nonce="{nonce}"' if nonce else ""

    css = r'''
/* OLYA_WORKSPACE_CLIENT_V3 */
:root{--olya-app-height:100dvh;--olya-sidebar:260px;--olya-chat-width:800px;--olya-composer-width:780px;--olya-ui:#ffffff;--olya-side:#f7f7f8;--olya-hover:#ececee;--olya-line:#e6e6e8;--olya-text:#202123;--olya-muted:#74747a}
html,body{height:var(--olya-app-height)!important;max-height:var(--olya-app-height)!important;overflow:hidden!important;background:#fff!important}
body{overscroll-behavior:none!important}
.shell{height:var(--olya-app-height)!important;max-height:var(--olya-app-height)!important;min-height:0!important;grid-template-columns:var(--olya-sidebar) minmax(0,1fr)!important;overflow:hidden!important}
.side{height:var(--olya-app-height)!important;min-height:0!important;overflow:hidden!important;padding:10px 9px 9px!important;background:var(--olya-side)!important;border-right:0!important}
.brand{flex:0 0 auto!important;padding:9px 10px 12px!important;font-size:17px!important;letter-spacing:-.025em!important}
.new{order:1!important;flex:0 0 auto!important;margin:0 0 8px!important;padding:10px!important;border:0!important;background:transparent!important;color:#242426!important;border-radius:9px!important;text-align:left!important;font-weight:600!important}
.new:hover{background:var(--olya-hover)!important}
.nav{order:2!important;flex:0 0 auto!important;gap:2px!important}
.navbtn{min-height:38px!important;border-radius:9px!important;color:#323236!important;padding:8px 10px!important}
.navbtn:hover,.navbtn.active{background:var(--olya-hover)!important;color:#151516!important}
#nav-files,#nav-api{display:none!important}
.side-section{order:3!important;flex:0 0 auto!important;margin:17px 9px 5px!important;color:#8a8a90!important;font-size:11px!important;text-transform:none!important;letter-spacing:0!important}
.history{order:4!important;min-height:0!important;overflow-y:auto!important;overflow-x:hidden!important;overscroll-behavior:contain!important;scrollbar-gutter:stable!important}
.conv{min-height:36px!important;padding:8px 10px!important;color:#404044!important;border-radius:8px!important}
.conv:hover,.conv.active{background:var(--olya-hover)!important}
.account-mini{order:5!important;flex:0 0 auto!important;border-top:1px solid #e3e3e5!important;padding:10px 8px 2px!important;color:#77777d!important}
.account-row{min-height:34px!important}.linkbtn{color:#353538!important}

.main{height:var(--olya-app-height)!important;max-height:var(--olya-app-height)!important;min-height:0!important;overflow:hidden!important;background:#fff!important;display:flex!important;flex-direction:column!important}
.top{flex:0 0 48px!important;min-height:48px!important;height:48px!important;border:0!important;padding:6px 14px!important;background:rgba(255,255,255,.94)!important;backdrop-filter:blur(12px)!important;z-index:5!important}
.page-title,.top>.budget,.top>.health{display:none!important}
.chat-controls{display:none!important}
.mobile-menu{min-width:38px!important;min-height:38px!important;border:0!important;background:transparent!important;color:#303034!important;border-radius:9px!important}
.mobile-menu:hover{background:#f1f1f3!important}
.view{min-height:0!important}
#view-chat{position:relative!important;flex:1 1 auto!important;min-height:0!important;overflow:hidden!important;background:#fff!important;display:flex!important;flex-direction:column!important}
#view-chat:not(.active){display:none!important}
.messages{flex:1 1 auto!important;min-height:0!important;overflow-y:auto!important;overflow-x:hidden!important;overscroll-behavior:contain!important;scrollbar-gutter:stable!important;scroll-behavior:auto!important;padding:18px max(18px,calc((100% - var(--olya-chat-width))/2)) 30px!important;overflow-anchor:auto!important}
.messages>*{min-width:0!important}
.msg{max-width:var(--olya-chat-width)!important;width:100%!important;margin:0 auto 26px!important;animation:olya-v3-msg-in .16s ease-out!important}
.user{display:flex!important;justify-content:flex-end!important}.user .bubble{max-width:min(78%,680px)!important;border:0!important;background:#f4f4f4!important;color:#252527!important;border-radius:20px!important;padding:10px 15px!important}
.assistant .bubble{max-width:100%!important;color:var(--olya-text)!important;font-size:16px!important;line-height:1.72!important;overflow-wrap:anywhere!important;word-break:normal!important}
.assistant .bubble>*{max-width:100%!important}.assistant .bubble img{max-width:100%!important;height:auto!important}
.assistant .bubble pre,.assistant .bubble code{max-width:100%!important}.tablewrap,.codewrap{max-width:100%!important;overflow:auto!important}
.meta{display:none!important}.role{display:none!important}.sources{background:#fff!important;border-color:#e8e8ea!important}.sources summary{color:#45454a!important}
.older{background:#fff!important;color:#4a4a4f!important;border-color:#dedee2!important}
.chat-empty{max-width:720px!important;margin:auto!important;padding:7vh 16px 18vh!important;text-align:center!important}
.chat-empty h1{font-size:32px!important;font-weight:600!important;color:#202123!important;letter-spacing:-.04em!important}.chat-empty p{display:none!important}
.prompt-suggestions{grid-template-columns:repeat(2,minmax(0,1fr))!important;gap:8px!important;max-width:620px!important;margin:26px auto 0!important}.prompt-suggestion{min-height:52px!important;border-radius:14px!important;background:#fff!important;color:#444448!important;border-color:#e4e4e7!important}.prompt-suggestion:hover{background:#f7f7f8!important;transform:none!important}

.composer-wrap{position:relative!important;flex:0 0 auto!important;border:0!important;background:linear-gradient(180deg,rgba(255,255,255,0),#fff 22%)!important;padding:9px 18px max(12px,env(safe-area-inset-bottom))!important;z-index:6!important}
.progress{display:none!important}
.composer{max-width:var(--olya-composer-width)!important;width:100%!important;margin:0 auto!important;border:1px solid #d8d8dc!important;background:#fff!important;border-radius:26px!important;padding:8px 9px 7px!important;box-shadow:0 5px 22px rgba(0,0,0,.075)!important;transition:border-color .14s ease,box-shadow .14s ease!important}
.composer:focus-within{border-color:#b9b9bf!important;box-shadow:0 7px 28px rgba(0,0,0,.09)!important}
.composer textarea{display:block!important;width:100%!important;min-height:46px!important;max-height:min(210px,35dvh)!important;overflow-y:auto!important;resize:none!important;padding:8px 10px 5px!important;color:#202123!important;font-size:16px!important;line-height:1.45!important;background:transparent!important;border:0!important;outline:0!important}
.composer textarea::placeholder{color:#8b8b90!important}
.state{display:block!important;width:100%!important;max-width:none!important;height:20px!important;min-height:20px!important;overflow:hidden!important;white-space:nowrap!important;text-overflow:ellipsis!important;margin:0!important;padding:1px 10px 0!important;color:#7a7a80!important;font-size:11px!important;line-height:18px!important}
.state:empty{visibility:hidden!important}
.composer-foot{display:flex!important;align-items:center!important;gap:5px!important;min-height:40px!important;padding:2px 1px 0!important;flex-wrap:nowrap!important}
.composer-foot>.health{display:none!important}.attachment-state{max-width:145px!important;overflow:hidden!important;text-overflow:ellipsis!important;white-space:nowrap!important;color:#737379!important;font-size:11px!important}
.attach{flex:0 0 36px!important;width:36px!important;height:36px!important;min-width:36px!important;border:1px solid transparent!important;border-radius:999px!important;background:#fff!important;color:#303034!important;font-size:22px!important;display:grid!important;place-items:center!important;padding:0!important}
.attach:hover{background:#f1f1f3!important}
.composer-tools{display:flex!important;align-items:center!important;gap:4px!important;min-width:0!important}
.composer-select{height:36px!important;min-height:36px!important;max-width:126px!important;border:1px solid transparent!important;background:#fff!important;color:#444449!important;border-radius:999px!important;padding:5px 28px 5px 10px!important;font-size:12px!important;cursor:pointer!important}
.composer-select:hover{background:#f1f1f3!important}
.composer-more{position:relative!important}.composer-more>summary{width:36px!important;height:36px!important;display:grid!important;place-items:center!important;border-radius:999px!important;padding:0!important;font-size:18px!important;letter-spacing:1px!important;background:#fff!important;color:#4a4a4f!important;cursor:pointer!important;list-style:none!important}.composer-more>summary::-webkit-details-marker{display:none!important}.composer-more>summary:hover,.composer-more[open]>summary{background:#f1f1f3!important}
.composer-more-panel{position:absolute!important;left:0!important;bottom:43px!important;width:min(280px,calc(100vw - 30px))!important;padding:11px!important;background:#fff!important;border:1px solid #dedee2!important;border-radius:14px!important;box-shadow:0 16px 46px rgba(0,0,0,.14)!important;display:grid!important;gap:9px!important;z-index:40!important}.composer-more-panel label{display:grid!important;gap:5px!important;color:#6d6d72!important;font-size:11px!important}.composer-more-panel select{width:100%!important;min-height:38px!important;border:1px solid #dedee2!important;border-radius:9px!important;background:#fff!important;color:#28282b!important;padding:7px 9px!important;font-size:13px!important}
#send{margin-left:auto!important;flex:0 0 36px!important;width:36px!important;height:36px!important;min-width:36px!important;border-radius:999px!important;padding:0!important;display:grid!important;place-items:center!important;background:#111827!important;color:#fff!important;border:0!important;font-size:17px!important;line-height:1!important;transition:background .14s ease,transform .14s ease!important}
#send:hover:not(:disabled){background:#262d3a!important}#send:active:not(:disabled){transform:scale(.96)!important}#send.stop{background:#111827!important;font-size:12px!important}#send:disabled{background:#d8d8dc!important;color:#fff!important;opacity:1!important;cursor:default!important}
.composer-note{max-width:var(--olya-composer-width)!important;margin:5px auto 0!important;color:#a0a0a5!important;font-size:10px!important;text-align:center!important;height:14px!important;overflow:hidden!important}
.jump-bottom{position:absolute!important;right:max(18px,calc((100% - var(--olya-composer-width))/2))!important;top:-42px!important;width:34px!important;height:34px!important;border-radius:999px!important;border:1px solid #dedee2!important;background:#fff!important;color:#343438!important;box-shadow:0 4px 16px rgba(0,0,0,.1)!important;display:grid!important;place-items:center!important;font-size:17px!important;z-index:8!important}.jump-bottom[hidden]{display:none!important}.jump-bottom:hover{background:#f5f5f6!important}

.content{width:min(980px,100%)!important;padding:28px 22px 60px!important}.content h1{color:#202123!important}.card,.item{background:#fff!important;border-color:#e7e7e9!important;color:#202123!important}.card{border-radius:16px!important}.item{border-radius:11px!important}.project-editor textarea{background:#fff!important;color:#202123!important;border-color:#dedee2!important}
#view-files{display:none!important}

.olya-mobile-overlay{display:none;position:fixed;inset:0;z-index:29;background:rgba(0,0,0,.28);backdrop-filter:blur(1px)}
@keyframes olya-v3-msg-in{from{opacity:0;transform:translateY(3px)}to{opacity:1;transform:none}}
@media(prefers-reduced-motion:reduce){.msg,#send,.composer,.prompt-suggestion{animation:none!important;transition:none!important}.messages{scroll-behavior:auto!important}}
@media(max-width:760px){
 :root{--olya-sidebar:290px;--olya-chat-width:100%;--olya-composer-width:100%}
 .shell{grid-template-columns:1fr!important}.side{display:flex!important;position:fixed!important;z-index:30!important;left:0!important;top:0!important;bottom:0!important;width:min(290px,86vw)!important;height:var(--olya-app-height)!important;transform:translateX(-102%)!important;transition:transform .18s ease!important;box-shadow:none!important}.side.open{transform:translateX(0)!important;box-shadow:22px 0 60px rgba(0,0,0,.18)!important}.side.open~.main .olya-mobile-overlay{display:block!important}
 .main{width:100%!important}.top{height:48px!important;min-height:48px!important;padding:5px 7px!important}.mobile-menu{display:grid!important;place-items:center!important;min-width:42px!important;min-height:42px!important;font-size:18px!important}
 .messages{padding:10px 12px 24px!important;scrollbar-gutter:auto!important}.msg{margin-bottom:22px!important}.user .bubble{max-width:90%!important}.assistant .bubble{font-size:15.5px!important;line-height:1.68!important}
 .chat-empty{padding:8vh 10px 15vh!important}.chat-empty h1{font-size:29px!important}.prompt-suggestions{grid-template-columns:1fr!important;margin-top:20px!important}.prompt-suggestion:nth-child(n+3){display:none!important}
 .composer-wrap{padding:7px 7px max(8px,env(safe-area-inset-bottom))!important}.composer{border-radius:23px!important;padding:7px 7px 6px!important}.composer textarea{max-height:35dvh!important;padding:8px 9px 4px!important}.composer-foot{min-height:42px!important}.attach,#send,.composer-more>summary{width:38px!important;height:38px!important;min-width:38px!important}.composer-select{height:38px!important;min-height:38px!important;max-width:118px!important}.attachment-state{display:none!important}.composer-more-panel{left:-88px!important;bottom:45px!important}.jump-bottom{right:14px!important;top:-43px!important;width:36px!important;height:36px!important}.composer-note{display:none!important}
 .content{padding:20px 13px 50px!important}
 .olya-mobile-overlay{display:none}
}
'''

    js = r'''
(function(){
  const byId=id=>document.getElementById(id);
  const root=document.documentElement;
  const messages=byId('messages'),prompt=byId('prompt'),send=byId('send'),composer=document.querySelector('.composer');
  const foot=document.querySelector('.composer-foot'),state=byId('state'),mode=byId('mode'),project=byId('project-chat'),web=byId('web'),verify=byId('verify');
  const more=document.querySelector('.composer-more'),panel=document.querySelector('.composer-more-panel');
  const side=byId('side'),menu=byId('menu'),newChat=byId('new-chat');
  if(!messages||!prompt||!send||!composer||!foot)return;

  function setViewportHeight(){
    const h=(window.visualViewport&&window.visualViewport.height)||window.innerHeight;
    if(h>200)root.style.setProperty('--olya-app-height',Math.round(h)+'px');
  }
  setViewportHeight();
  window.addEventListener('resize',setViewportHeight,{passive:true});
  if(window.visualViewport){window.visualViewport.addEventListener('resize',setViewportHeight,{passive:true});window.visualViewport.addEventListener('scroll',setViewportHeight,{passive:true})}

  if(mode){
    const labels={auto:'Простая',work:'Средняя',deep:'Сложная'};
    for(const option of [...mode.options]){
      if(option.value==='fast'){option.remove();continue}
      if(labels[option.value])option.textContent=labels[option.value];
    }
    if(mode.value==='fast')mode.value='auto';
    mode.setAttribute('aria-label','Мощность ответа');
    mode.title='Мощность ответа: Простая / Средняя / Сложная';
  }

  if(more){
    const summary=more.querySelector('summary');
    if(summary){summary.textContent='⋯';summary.setAttribute('aria-label','Дополнительные настройки');summary.title='Дополнительные настройки'}
  }
  if(panel&&project){
    const existing=[...panel.querySelectorAll('select')].includes(project);
    if(!existing){
      const label=document.createElement('label');label.textContent='Проект / контекст';label.append(project);panel.prepend(label);
    }
    project.classList.remove('composer-select');
  }
  if(web)web.setAttribute('aria-label','Интернет');
  if(verify)verify.setAttribute('aria-label','Проверка ответа');

  if(state&&state.parentElement!==composer){composer.insertBefore(state,foot)}
  if(state){
    const stateTitle=()=>{state.title=state.textContent||''};
    new MutationObserver(stateTitle).observe(state,{childList:true,subtree:true,characterData:true});stateTitle();
  }

  const attach=byId('chat-attach');if(attach){attach.setAttribute('aria-label','Прикрепить файл');attach.title='Прикрепить файл'}
  send.setAttribute('aria-label',send.classList.contains('stop')?'Остановить':'Отправить');

  const wrap=document.querySelector('.composer-wrap');
  let jump=byId('jump-bottom');
  if(wrap&&!jump){jump=document.createElement('button');jump.type='button';jump.id='jump-bottom';jump.className='jump-bottom';jump.textContent='↓';jump.hidden=true;jump.setAttribute('aria-label','К последнему сообщению');jump.title='К последнему сообщению';wrap.prepend(jump)}

  let autoFollow=true;
  let internalScroll=false;
  const distanceToBottom=()=>Math.max(0,messages.scrollHeight-messages.scrollTop-messages.clientHeight);
  const nearBottom=()=>distanceToBottom()<150;
  function updateJump(){if(jump)jump.hidden=autoFollow||nearBottom()}
  function goBottom(smooth=false){internalScroll=true;messages.scrollTo({top:messages.scrollHeight,behavior:smooth?'smooth':'auto'});requestAnimationFrame(()=>{internalScroll=false;autoFollow=true;updateJump()})}
  messages.addEventListener('scroll',()=>{if(internalScroll)return;autoFollow=nearBottom();updateJump()},{passive:true});
  if(jump)jump.addEventListener('click',()=>goBottom(true));

  const messageObserver=new MutationObserver(()=>{
    if(autoFollow){requestAnimationFrame(()=>goBottom(false))}
    else updateJump();
  });
  messageObserver.observe(messages,{childList:true,subtree:true,characterData:true});

  send.addEventListener('click',()=>{
    if(!send.classList.contains('stop')&&prompt.value.trim()){autoFollow=true;requestAnimationFrame(()=>goBottom(false))}
  },true);
  prompt.addEventListener('keydown',event=>{
    if(event.key==='Enter'&&!event.shiftKey&&prompt.value.trim()){autoFollow=true}
  },true);
  if(newChat)newChat.addEventListener('click',()=>{autoFollow=true;requestAnimationFrame(()=>goBottom(false))},true);

  const sendObserver=new MutationObserver(()=>{
    const stopping=send.classList.contains('stop');
    const label=stopping?'Остановить':'Отправить';
    const glyph=stopping?'■':'↑';
    if(send.textContent!==glyph)send.textContent=glyph;
    send.setAttribute('aria-label',label);send.title=label;
  });
  sendObserver.observe(send,{attributes:true,attributeFilter:['class','disabled'],childList:true,subtree:true});

  let overlay=document.querySelector('.olya-mobile-overlay');
  if(!overlay){overlay=document.createElement('div');overlay.className='olya-mobile-overlay';overlay.setAttribute('aria-hidden','true');document.querySelector('.main')?.append(overlay)}
  function syncOverlay(){if(!overlay||!side)return;overlay.style.display=(window.innerWidth<=760&&side.classList.contains('open'))?'block':'none'}
  if(side)new MutationObserver(syncOverlay).observe(side,{attributes:true,attributeFilter:['class']});
  if(overlay)overlay.addEventListener('click',()=>{side?.classList.remove('open');syncOverlay()});
  if(menu)menu.setAttribute('aria-label','Открыть меню');

  document.addEventListener('keydown',event=>{
    if(event.key!=='Escape')return;
    if(more&&more.open)more.open=false;
    if(side&&side.classList.contains('open')){side.classList.remove('open');syncOverlay()}
  });
  window.addEventListener('resize',syncOverlay,{passive:true});syncOverlay();

  requestAnimationFrame(()=>{autoFollow=true;goBottom(false);if(!prompt.disabled)prompt.focus({preventScroll:true})});
})();
'''

    document = document.replace("</head>", f'<style{nonce_attr}>{css}</style></head>', 1)
    document = document.replace("</body>", f'<script{nonce_attr}>{js}</script></body>', 1)
    return document


def install_workspace_client_v3() -> None:
    current = HTMLResponse.__init__
    if getattr(current, "_olya_workspace_client_v3", False):
        return

    def enhanced(self, content, *args, **kwargs):
        if isinstance(content, str):
            content = _enhance(content)
        return current(self, content, *args, **kwargs)

    enhanced._olya_workspace_client_v3 = True  # type: ignore[attr-defined]
    HTMLResponse.__init__ = enhanced
