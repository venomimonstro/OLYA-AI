from __future__ import annotations

from fastapi.responses import HTMLResponse


def _replace_once(document: str, old: str, new: str, label: str) -> str:
    count = document.count(old)
    if count != 1:
        raise RuntimeError(f"Chat management template drift for {label}: expected 1 marker, found {count}")
    return document.replace(old, new, 1)


def enhance_chat_management(document: str) -> str:
    """Upgrade the base workspace into a chat-first conversation manager."""
    styles = r'''
.chat-side-head{display:flex;align-items:center;justify-content:space-between;gap:8px}.side-add{border:0;background:transparent;color:#dfe4ec;font-size:18px;line-height:1;padding:2px 7px;border-radius:7px}.side-add:hover{background:#1a202a}.project-tree{display:grid;gap:5px;max-height:42%;overflow:auto}.project-folder{border:1px solid transparent;border-radius:9px}.project-folder.open{background:#10151d;border-color:#1d2530}.project-folder-head{display:flex;align-items:center;gap:4px;padding:3px}.project-folder-main{min-width:0;flex:1;border:0;background:transparent;color:#dfe4ec;text-align:left;padding:6px 7px;border-radius:7px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}.project-folder-main:hover{background:#181e27}.project-count{color:#667386;font-size:10px;margin-left:5px}.chat-action{border:0;background:transparent;color:#7f8a9a;border-radius:7px;padding:5px 6px;line-height:1}.chat-action:hover{background:#222a36;color:#fff}.project-folder-chats{display:grid;padding:0 5px 5px 14px}.conv-row{display:flex;align-items:center;gap:2px;border-radius:8px;min-width:0}.conv-row:hover,.conv-row.active{background:#181e27}.conv-main{min-width:0;flex:1;border:0;background:transparent;color:#cfd6e2;text-align:left;padding:7px 6px;border-radius:8px}.conv-title{display:block;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}.conv-date{display:block;color:#657184;font-size:10px;margin-top:1px}.conv-actions{display:flex;align-items:center;opacity:0}.conv-row:hover .conv-actions,.conv-row.active .conv-actions{opacity:1}.history{display:grid;align-content:start;gap:2px}.chat-dialog-backdrop{position:fixed;z-index:40;inset:0;background:#0009;display:grid;place-items:center;padding:18px}.chat-dialog{width:min(430px,100%);background:#111620;border:1px solid #303846;border-radius:15px;padding:18px;box-shadow:0 24px 80px #000}.chat-dialog h3{margin:0 0 7px}.chat-dialog p{color:var(--muted);margin:0 0 13px}.chat-dialog select{width:100%;background:#0d1219;color:#fff;border:1px solid var(--line);border-radius:9px;padding:10px}.chat-dialog-actions{display:flex;justify-content:flex-end;gap:8px;margin-top:14px}.side-empty{color:#667386;font-size:12px;padding:7px 9px}.history-section{display:flex;align-items:center;justify-content:space-between}.history-section span{min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}@media(max-width:760px){.project-tree{max-height:35%}.conv-actions{opacity:1}}
'''
    document = _replace_once(document, "</style></head><body>", styles + "</style></head><body>", "chat management styles")

    old_sidebar = '''<button class="new" id="new-chat">+ Новый чат</button>
  <div class="side-section" id="history-label">Недавние чаты</div>
  <div class="history" id="history"></div>'''
    new_sidebar = '''<button class="new" id="new-chat">+ Новый чат</button>
  <div class="side-section chat-side-head"><span>Проекты</span><button class="side-add" id="quick-project-create" type="button" title="Создать проект">+</button></div>
  <div class="project-tree" id="project-tree"></div>
  <div class="side-section history-section" id="history-label"><span>Без проекта</span></div>
  <div class="history" id="history"></div>'''
    document = _replace_once(document, old_sidebar, new_sidebar, "sidebar project tree")

    old_load_conversations = "async function loadConversations(){try{const projectId=$('project-chat').value,path='/v1/conversations?limit=30'+(projectId?'&project_id='+encodeURIComponent(projectId):''),rows=await api(path,{},30000),h=$('history');$('history-label').textContent=projectId?'Чаты проекта':'Личные чаты';h.replaceChildren();for(const c of rows){const b=document.createElement('button');b.className='conv'+(c.id===conversationId?' active':'');b.textContent=c.title||'Новый чат';b.onclick=()=>openConversation(c.id,c.project_id);h.append(b)}}catch(_e){}}"
    new_load_conversations = r'''let chatRowsCache=[];
function chatDate(value){if(!value)return '';const d=new Date(value),now=new Date(),same=d.toDateString()===now.toDateString();return same?d.toLocaleTimeString('ru-RU',{hour:'2-digit',minute:'2-digit'}):d.toLocaleDateString('ru-RU',{day:'2-digit',month:'2-digit',year:d.getFullYear()===now.getFullYear()?undefined:'2-digit'})}
function chatAction(label,title,run,danger=false){const b=document.createElement('button');b.type='button';b.className='chat-action';b.textContent=label;b.title=title;if(danger)b.style.color='#ff9b9b';b.onclick=e=>{e.stopPropagation();run()};return b}
function chatRow(c){const row=document.createElement('div');row.className='conv-row'+(c.id===conversationId?' active':'');const main=document.createElement('button');main.type='button';main.className='conv-main';const title=document.createElement('span');title.className='conv-title';title.textContent=c.title||'Новый чат';const date=document.createElement('span');date.className='conv-date';date.textContent=chatDate(c.updated_at||c.created_at);main.append(title,date);main.onclick=()=>openConversation(c.id,c.project_id||'');const actions=document.createElement('span');actions.className='conv-actions';actions.append(chatAction('✎','Переименовать',()=>renameConversation(c)),chatAction('⌂','Переместить',()=>moveConversationDialog(c)),chatAction('×','Удалить',()=>deleteConversation(c),true));row.append(main,actions);return row}
function renderChatSidebar(){const history=$('history'),tree=$('project-tree');if(!history||!tree)return;history.replaceChildren();tree.replaceChildren();const ungrouped=chatRowsCache.filter(c=>!c.project_id);if(!ungrouped.length){const e=document.createElement('div');e.className='side-empty';e.textContent='Чатов пока нет';history.append(e)}else for(const c of ungrouped)history.append(chatRow(c));for(const p of projectsCache){const chats=chatRowsCache.filter(c=>c.project_id===p.id),folder=document.createElement('div');folder.className='project-folder open';const head=document.createElement('div');head.className='project-folder-head';const main=document.createElement('button');main.type='button';main.className='project-folder-main';main.title=p.name;const label=document.createElement('span');label.textContent='▾ '+p.name;const count=document.createElement('span');count.className='project-count';count.textContent=String(chats.length);main.append(label,count);const children=document.createElement('div');children.className='project-folder-chats';main.onclick=()=>{folder.classList.toggle('open');const open=folder.classList.contains('open');children.hidden=!open;label.textContent=(open?'▾ ':'▸ ')+p.name};const add=chatAction('+','Новый чат в проекте',()=>{$('project-chat').value=p.id;newChat()});head.append(main,add);if(p.role==='owner')head.append(chatAction('×','Удалить проект',()=>deleteProjectFolder(p),true));folder.append(head,children);if(!chats.length){const empty=document.createElement('div');empty.className='side-empty';empty.textContent='Нет чатов';children.append(empty)}else for(const c of chats)children.append(chatRow(c));tree.append(folder)}}
async function loadConversations(){try{chatRowsCache=await api('/v1/conversations?limit=100&all_projects=true',{},30000);renderChatSidebar()}catch(_e){}}
async function renameConversation(c){const name=prompt('Название чата',c.title||'Новый чат');if(name===null)return;const title=name.trim();if(!title||title===c.title)return;try{await api('/v1/conversations/'+encodeURIComponent(c.id),{method:'PATCH',body:JSON.stringify({title})},30000);await loadConversations()}catch(e){state.textContent=e.message}}
function closeChatDialog(){const x=$('chat-move-dialog');if(x)x.remove()}
function moveConversationDialog(c){closeChatDialog();const back=document.createElement('div');back.id='chat-move-dialog';back.className='chat-dialog-backdrop';const box=document.createElement('div');box.className='chat-dialog';const h=document.createElement('h3');h.textContent='Переместить чат';const p=document.createElement('p');p.textContent=c.title||'Новый чат';const select=document.createElement('select');const none=document.createElement('option');none.value='';none.textContent='Без проекта';select.append(none);for(const project of projectsCache){const o=document.createElement('option');o.value=project.id;o.textContent=project.name;select.append(o)}select.value=c.project_id||'';const actions=document.createElement('div');actions.className='chat-dialog-actions';const cancel=document.createElement('button');cancel.className='secondary';cancel.textContent='Отмена';cancel.onclick=closeChatDialog;const move=document.createElement('button');move.className='primary';move.textContent='Переместить';move.onclick=async()=>{move.disabled=true;try{const project_id=select.value||null;await api('/v1/conversations/'+encodeURIComponent(c.id),{method:'PATCH',body:JSON.stringify({project_id})},30000);if(c.id===conversationId)$('project-chat').value=project_id||'';closeChatDialog();await Promise.all([loadConversations(),loadProjects()])}catch(e){state.textContent=e.message;move.disabled=false}};actions.append(cancel,move);box.append(h,p,select,actions);back.append(box);back.onclick=e=>{if(e.target===back)closeChatDialog()};document.body.append(back)}
async function deleteConversation(c){if(!confirm('Удалить чат «'+(c.title||'Новый чат')+'»? Это действие нельзя отменить.'))return;try{await api('/v1/conversations/'+encodeURIComponent(c.id),{method:'DELETE'},30000);if(c.id===conversationId)newChat();else await loadConversations()}catch(e){state.textContent=e.message}}
async function quickCreateProject(){const value=prompt('Название нового проекта');if(value===null)return;const name=value.trim();if(!name)return;try{const p=await api('/v1/projects',{method:'POST',body:JSON.stringify({name,description:'',instructions:''})},30000);await loadProjects();await loadConversations();$('project-chat').value=p.id;newChat()}catch(e){state.textContent=e.message}}
async function deleteProjectFolder(p){if(!confirm('Удалить проект «'+p.name+'»? Чаты сохранятся и вернутся в «Без проекта». Файлы и память проекта будут удалены.'))return;try{await api('/v1/projects/'+encodeURIComponent(p.id),{method:'DELETE'},30000);if($('project-chat').value===p.id)$('project-chat').value='';if(workspaceProjectId===p.id)closeProjectWorkspace();await loadProjects();await loadConversations()}catch(e){state.textContent=e.message}}'''
    document = _replace_once(document, old_load_conversations, new_load_conversations, "conversation manager")

    old_load_projects = "async function loadProjects(){try{projectsCache=await api('/v1/projects',{},30000);fillProjectSelect($('project-chat'),'Без проекта');fillProjectSelect($('files-project'),'Выберите проект');const root=$('projects-list');root.replaceChildren();if(!projectsCache.length){const e=document.createElement('div');e.className='empty-panel';e.textContent='Проектов пока нет. Создайте первый проект выше.';root.append(e);return}for(const p of projectsCache){const row=document.createElement('div');row.className='item';const main=document.createElement('div');main.className='item-main';const title=document.createElement('div');title.className='item-title';title.textContent=p.name;const meta=document.createElement('div');meta.className='item-meta';meta.textContent=(p.role||'viewer')+(p.description?' · '+p.description.slice(0,100):'');main.append(title,meta);const use=document.createElement('button');use.className='secondary';use.textContent='Открыть проект';use.onclick=()=>openProject(p.id);row.append(main,use);root.append(row)}}catch(e){$('project-state').textContent=e.message}}"
    new_load_projects = r'''async function loadProjects(){try{projectsCache=await api('/v1/projects',{},30000);fillProjectSelect($('project-chat'),'Без проекта');fillProjectSelect($('files-project'),'Выберите проект');const root=$('projects-list');root.replaceChildren();if(!projectsCache.length){const e=document.createElement('div');e.className='empty-panel';e.textContent='Проектов пока нет. Создайте первый проект выше.';root.append(e);renderChatSidebar();return}for(const p of projectsCache){const row=document.createElement('div');row.className='item';const main=document.createElement('div');main.className='item-main';const title=document.createElement('div');title.className='item-title';title.textContent=p.name;const meta=document.createElement('div');meta.className='item-meta';meta.textContent=(p.role||'viewer')+(p.description?' · '+p.description.slice(0,100):'');main.append(title,meta);const use=document.createElement('button');use.className='secondary';use.textContent='Открыть проект';use.onclick=()=>openProject(p.id);row.append(main,use);if(p.role==='owner'){const del=document.createElement('button');del.className='danger';del.textContent='Удалить';del.onclick=()=>deleteProjectFolder(p);row.append(del)}root.append(row)}renderChatSidebar()}catch(e){$('project-state').textContent=e.message}}'''
    document = _replace_once(document, old_load_projects, new_load_projects, "project manager")

    document = _replace_once(
        document,
        "$('new-chat').onclick=newChat;",
        "$('new-chat').onclick=newChat;$('quick-project-create').onclick=quickCreateProject;",
        "quick project binding",
    )
    return document


def install_chat_management_ui() -> None:
    """Wrap the workspace source before user_ui applies its account/billing layer."""
    import app.user_ui as user_ui

    current = user_ui._base_workspace
    if getattr(current, "_x1_chat_management", False):
        return

    def managed_base_workspace() -> HTMLResponse:
        base = current()
        document = enhance_chat_management(base.body.decode("utf-8"))
        response = HTMLResponse(document, status_code=base.status_code)
        for key, value in base.headers.items():
            if key.lower() not in {"content-length", "content-type"}:
                response.headers[key] = value
        return response

    managed_base_workspace._x1_chat_management = True  # type: ignore[attr-defined]
    user_ui._base_workspace = managed_base_workspace
