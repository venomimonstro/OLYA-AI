from __future__ import annotations

import re

_MARKER = "OLYA_CHAT_LIBRARY_V3"


def _nonce(document: str) -> str:
    match = re.search(r'''nonce=["']([^"']+)["']''', document, flags=re.I)
    return match.group(1) if match else ""


def enhance_chat_library(document: str) -> str:
    if _MARKER in document or 'id="history"' not in document:
        return document
    nonce = _nonce(document)
    nonce_attr = f' nonce="{nonce}"' if nonce else ""
    css = r'''
/* OLYA_CHAT_LIBRARY_V3 */
.olya-chat-row{display:flex;align-items:center;gap:2px;border-radius:9px;min-width:0}.olya-chat-row:hover,.olya-chat-row.active{background:#ececee}.olya-chat-open{flex:1;min-width:0;border:0;background:transparent;text-align:left;padding:8px 7px;color:#3f3f46;cursor:pointer}.olya-chat-title{display:block;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}.olya-chat-meta{display:block;font-size:10px;color:#999;margin-top:1px}.olya-chat-actions{display:flex;opacity:0;align-items:center;padding-right:4px}.olya-chat-row:hover .olya-chat-actions,.olya-chat-row.active .olya-chat-actions{opacity:1}.olya-chat-icon{width:27px;height:27px;border:0;background:transparent;border-radius:7px;color:#777;cursor:pointer;display:grid;place-items:center;font-size:14px}.olya-chat-icon:hover{background:#dedee2;color:#222}.olya-chat-icon.danger:hover{background:#fee;color:#a22}.olya-chat-icon.pinned{color:#c58a00}.olya-chat-group{margin:4px 0 9px}.olya-chat-group-title{font-size:11px;color:#999;padding:5px 8px}.olya-chat-empty{font-size:11px;color:#aaa;padding:7px 9px}.olya-memory-link{width:100%;display:flex;align-items:center;gap:10px;border:0;background:transparent;color:#3f3f46;text-decoration:none;text-align:left;padding:9px 10px;border-radius:10px;font-weight:500}.olya-memory-link:hover{background:#ececee}.olya-memory-link span:first-child{font-size:15px}.history,#project-tree{min-height:0}.side{overflow:hidden}.composer-foot{display:flex;align-items:center}@media(max-width:760px){.olya-chat-actions{opacity:1}}
'''
    js = r'''
(function(){
 const history=document.getElementById('history'),tree=document.getElementById('project-tree');if(!history)return;
 const token=()=>sessionStorage.getItem('x1_access_token')||'';
 let refreshPromise=null,refreshQueued=false,lastRefresh=0,projectsCache=null,projectsCacheAt=0;
 async function call(path,opts={}){const headers={...(opts.headers||{})},t=token();if(t)headers.Authorization='Bearer '+t;if(opts.body)headers['Content-Type']='application/json';const r=await fetch(path,{...opts,headers});let d=null;try{d=await r.json()}catch(_e){}if(r.status===401){location.replace('/login');throw new Error('Сессия завершена')}if(!r.ok)throw new Error((d&&d.detail)||('HTTP '+r.status));return d}
 function installMemoryLink(){const nav=document.querySelector('.side .nav');if(!nav||document.querySelector('.olya-memory-link'))return;const a=document.createElement('a');a.className='olya-memory-link';a.href='/memory';a.innerHTML='<span>◈</span><span>Память</span>';const account=document.getElementById('nav-account');if(account)nav.insertBefore(a,account);else nav.append(a)}
 function current(){try{return typeof conversationId!=='undefined'?conversationId:null}catch(_e){return null}}
 function date(v){if(!v)return '';const d=new Date(v),n=new Date();return d.toDateString()===n.toDateString()?d.toLocaleTimeString('ru-RU',{hour:'2-digit',minute:'2-digit'}):d.toLocaleDateString('ru-RU',{day:'2-digit',month:'2-digit'})}
 function icon(text,title,fn,cls=''){const b=document.createElement('button');b.type='button';b.className='olya-chat-icon '+cls;b.textContent=text;b.title=title;b.setAttribute('aria-label',title);b.onclick=e=>{e.stopPropagation();fn()};return b}
 async function patch(c,body){await call('/v1/conversations/'+encodeURIComponent(c.id),{method:'PATCH',body:JSON.stringify(body)});await refresh(true)}
 async function remove(c){if(!confirm('Удалить чат «'+(c.title||'Новый чат')+'»?'))return;await call('/v1/conversations/'+encodeURIComponent(c.id),{method:'DELETE'});if(current()===c.id){try{newChat()}catch(_e){location.reload()}}await refresh(true)}
 function row(c){const x=document.createElement('div');x.className='olya-chat-row'+(current()===c.id?' active':'');const open=document.createElement('button');open.type='button';open.className='olya-chat-open';const title=document.createElement('span');title.className='olya-chat-title';title.textContent=(c.pinned?'★ ':'')+(c.title||'Новый чат');const meta=document.createElement('span');meta.className='olya-chat-meta';meta.textContent=date(c.updated_at||c.created_at);open.append(title,meta);open.onclick=()=>{try{openConversation(c.id,c.project_id||'')}catch(_e){location.href='/app?conversation='+encodeURIComponent(c.id)}};const a=document.createElement('span');a.className='olya-chat-actions';a.append(icon(c.pinned?'★':'☆',c.pinned?'Открепить':'Закрепить',()=>patch(c,{pinned:!c.pinned}),c.pinned?'pinned':''),icon('✎','Переименовать',async()=>{const v=prompt('Название чата',c.title||'');if(v&&v.trim())await patch(c,{title:v.trim()})}),icon('⌫','Удалить',()=>remove(c),'danger'));x.append(open,a);return x}
 function group(root,title,rows){const wrap=document.createElement('div');wrap.className='olya-chat-group';const h=document.createElement('div');h.className='olya-chat-group-title';h.textContent=title;wrap.append(h);if(!rows.length){const e=document.createElement('div');e.className='olya-chat-empty';e.textContent='Нет чатов';wrap.append(e)}else rows.forEach(c=>wrap.append(row(c)));root.append(wrap)}
 async function projects(){const now=Date.now();if(projectsCache&&now-projectsCacheAt<30000)return projectsCache;projectsCache=await call('/v1/projects');projectsCacheAt=now;return projectsCache}
 async function refresh(force=false){
   const now=Date.now();if(!force&&now-lastRefresh<800)return refreshPromise||Promise.resolve();
   if(refreshPromise){refreshQueued=refreshQueued||force;return refreshPromise}
   refreshPromise=(async()=>{try{const [rows,projectRows]=await Promise.all([call('/v1/conversations?limit=100&all_projects=true'),projects()]);rows.sort((a,b)=>(Number(!!b.pinned)-Number(!!a.pinned))||(new Date(b.updated_at)-new Date(a.updated_at)));const historyFrag=document.createDocumentFragment();group(historyFrag,'Без проекта',rows.filter(c=>!c.project_id));history.replaceChildren(historyFrag);const projectRoot=tree||history;if(tree)tree.replaceChildren();for(const p of projectRows){group(projectRoot,p.name,rows.filter(c=>c.project_id===p.id))}lastRefresh=Date.now()}catch(e){console.warn('OLYA chat library:',e)}finally{refreshPromise=null;if(refreshQueued){refreshQueued=false;setTimeout(()=>refresh(true),0)}}})();
   return refreshPromise
 }
 installMemoryLink();try{loadConversations=refresh}catch(_e){}
 window.olyaRefreshChatLibrary=()=>refresh(false);refresh(true);
 document.addEventListener('visibilitychange',()=>{if(!document.hidden)refresh(false)});
})();
'''
    document = document.replace("</head>", f'<style{nonce_attr}>{css}</style></head>', 1)
    document = document.replace("</body>", f'<script{nonce_attr}>{js}</script></body>', 1)
    return document
