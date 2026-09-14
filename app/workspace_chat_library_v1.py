from __future__ import annotations

import re

_MARKER = "OLYA_CHAT_LIBRARY_V1"


def _nonce(document: str) -> str:
    match = re.search(r'''nonce=["']([^"']+)["']''', document, flags=re.I)
    return match.group(1) if match else ""


def enhance_chat_library(document: str) -> str:
    if _MARKER in document or 'id="history"' not in document:
        return document
    nonce = _nonce(document)
    nonce_attr = f' nonce="{nonce}"' if nonce else ""
    css = r'''
/* OLYA_CHAT_LIBRARY_V1 */
.olya-chat-row{display:flex;align-items:center;gap:2px;border-radius:9px;min-width:0}.olya-chat-row:hover,.olya-chat-row.active{background:#ececee}.olya-chat-open{flex:1;min-width:0;border:0;background:transparent;text-align:left;padding:8px 7px;color:#3f3f46;cursor:pointer}.olya-chat-title{display:block;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}.olya-chat-meta{display:block;font-size:10px;color:#999;margin-top:1px}.olya-chat-actions{display:flex;opacity:0;align-items:center;padding-right:4px}.olya-chat-row:hover .olya-chat-actions,.olya-chat-row.active .olya-chat-actions{opacity:1}.olya-chat-icon{width:27px;height:27px;border:0;background:transparent;border-radius:7px;color:#777;cursor:pointer;display:grid;place-items:center;font-size:14px}.olya-chat-icon:hover{background:#dedee2;color:#222}.olya-chat-icon.danger:hover{background:#fee;color:#a22}.olya-chat-icon.pinned{color:#c58a00}.olya-chat-group{margin:4px 0 9px}.olya-chat-group-title{font-size:11px;color:#999;padding:5px 8px}.olya-chat-empty{font-size:11px;color:#aaa;padding:7px 9px}@media(max-width:760px){.olya-chat-actions{opacity:1}}
'''
    js = r'''
(function(){
 const MARK='OLYA_CHAT_LIBRARY_V1',history=document.getElementById('history'),tree=document.getElementById('project-tree');if(!history)return;
 const token=()=>sessionStorage.getItem('x1_access_token')||sessionStorage.getItem('x1_token')||'';
 async function call(path,opts={}){const headers={...(opts.headers||{})},t=token();if(t)headers.Authorization='Bearer '+t;if(opts.body)headers['Content-Type']='application/json';const r=await fetch(path,{...opts,headers});let d=null;try{d=await r.json()}catch(_e){}if(!r.ok)throw new Error((d&&d.detail)||('HTTP '+r.status));return d}
 function current(){try{return typeof conversationId!=='undefined'?conversationId:null}catch(_e){return null}}
 function date(v){if(!v)return '';const d=new Date(v),n=new Date();return d.toDateString()===n.toDateString()?d.toLocaleTimeString('ru-RU',{hour:'2-digit',minute:'2-digit'}):d.toLocaleDateString('ru-RU',{day:'2-digit',month:'2-digit'})}
 function icon(text,title,fn,cls=''){const b=document.createElement('button');b.type='button';b.className='olya-chat-icon '+cls;b.textContent=text;b.title=title;b.setAttribute('aria-label',title);b.onclick=e=>{e.stopPropagation();fn()};return b}
 async function patch(c,body){await call('/v1/conversations/'+encodeURIComponent(c.id),{method:'PATCH',body:JSON.stringify(body)});await refresh()}
 async function remove(c){if(!confirm('Удалить чат «'+(c.title||'Новый чат')+'»?'))return;await call('/v1/conversations/'+encodeURIComponent(c.id),{method:'DELETE'});if(current()===c.id){try{newChat()}catch(_e){location.reload()}}await refresh()}
 function row(c){const x=document.createElement('div');x.className='olya-chat-row'+(current()===c.id?' active':'');const open=document.createElement('button');open.type='button';open.className='olya-chat-open';const title=document.createElement('span');title.className='olya-chat-title';title.textContent=(c.pinned?'★ ':'')+(c.title||'Новый чат');const meta=document.createElement('span');meta.className='olya-chat-meta';meta.textContent=date(c.updated_at||c.created_at);open.append(title,meta);open.onclick=()=>{try{openConversation(c.id,c.project_id||'')}catch(_e){location.href='/app?conversation='+encodeURIComponent(c.id)}};const a=document.createElement('span');a.className='olya-chat-actions';a.append(icon(c.pinned?'★':'☆',c.pinned?'Открепить':'Закрепить',()=>patch(c,{pinned:!c.pinned}),c.pinned?'pinned':''),icon('✎','Переименовать',async()=>{const v=prompt('Название чата',c.title||'');if(v&&v.trim())await patch(c,{title:v.trim()})}),icon('⌫','Удалить',()=>remove(c),'danger'));x.append(open,a);return x}
 function group(root,title,rows){const wrap=document.createElement('div');wrap.className='olya-chat-group';const h=document.createElement('div');h.className='olya-chat-group-title';h.textContent=title;wrap.append(h);if(!rows.length){const e=document.createElement('div');e.className='olya-chat-empty';e.textContent='Нет чатов';wrap.append(e)}else rows.forEach(c=>wrap.append(row(c)));root.append(wrap)}
 async function refresh(){try{const [rows,projects]=await Promise.all([call('/v1/conversations?limit=100&all_projects=true'),call('/v1/projects')]);rows.sort((a,b)=>(Number(!!b.pinned)-Number(!!a.pinned))||(new Date(b.updated_at)-new Date(a.updated_at)));history.replaceChildren();group(history,'Без проекта',rows.filter(c=>!c.project_id));if(tree){tree.replaceChildren();for(const p of projects){group(tree,p.name,rows.filter(c=>c.project_id===p.id))}}}catch(e){console.warn('OLYA chat library:',e)}}
 try{loadConversations=refresh}catch(_e){}
 window.olyaRefreshChatLibrary=refresh;refresh();
 document.addEventListener('visibilitychange',()=>{if(!document.hidden)refresh()});
})();
'''
    document = document.replace("</head>", f'<style{nonce_attr}>{css}</style></head>', 1)
    document = document.replace("</body>", f'<script{nonce_attr}>{js}</script></body>', 1)
    return document
