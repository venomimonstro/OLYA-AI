from __future__ import annotations


def _replace_once(document: str, old: str, new: str, label: str) -> str:
    count = document.count(old)
    if count != 1:
        raise RuntimeError(f"Workspace reliability drift for {label}: expected 1 marker, found {count}")
    return document.replace(old, new, 1)


def enhance_workspace_reliability_v5(document: str) -> str:
    """Durable browser state with crash-aware, non-blocking recovery."""

    old_pending = """function readPending(){try{const raw=sessionStorage.getItem(pendingKey);if(!raw)return null;const item=JSON.parse(raw);return item&&item.body&&item.body.client_request_id?item:null}catch(_e){sessionStorage.removeItem(pendingKey);return null}}
function savePending(body){const existing=readPending();const started=existing&&existing.body.client_request_id===body.client_request_id?existing.started_at:Date.now();sessionStorage.setItem(pendingKey,JSON.stringify({body,conversation_id:body.conversation_id||null,started_at:started}))}
function clearPending(id){const item=readPending();if(!item)return;if(!id||item.body.client_request_id===id)sessionStorage.removeItem(pendingKey)}"""
    new_pending = """function readPending(){try{let raw=localStorage.getItem(pendingKey);if(!raw){raw=sessionStorage.getItem(pendingKey);if(raw){localStorage.setItem(pendingKey,raw);sessionStorage.removeItem(pendingKey)}}if(!raw)return null;const item=JSON.parse(raw);return item&&item.body&&item.body.client_request_id?item:null}catch(_e){localStorage.removeItem(pendingKey);sessionStorage.removeItem(pendingKey);return null}}
function savePending(body){const existing=readPending();const started=existing&&existing.body.client_request_id===body.client_request_id?existing.started_at:Date.now();localStorage.setItem(pendingKey,JSON.stringify({body,conversation_id:body.conversation_id||null,started_at:started}))}
function clearPending(id){const item=readPending();if(!item)return;if(!id||item.body.client_request_id===id)localStorage.removeItem(pendingKey)}"""
    document = _replace_once(document, old_pending, new_pending, "durable pending run")

    persistence = r'''const activeConversationKey='olya_active_conversation_v1',draftPrefix='olya_chat_draft_v2::',serverInstanceKey='olya_server_instance_v1',premiumRunStoreKey='olya_parallel_runs_v1';
function draftStorageKey(){const projectId=$('project-chat').value||'none',chatId=conversationId||'new';return draftPrefix+chatId+'::'+projectId}
function saveDraft(){try{const key=draftStorageKey(),text=String(prompt.value||'');if(!text){localStorage.removeItem(key);return}localStorage.setItem(key,JSON.stringify({text,conversation_id:conversationId||null,project_id:$('project-chat').value||null,updated_at:Date.now()}))}catch(_e){}}
function clearDraft(){try{localStorage.removeItem(draftStorageKey())}catch(_e){}}
function restoreDraft(){try{const raw=localStorage.getItem(draftStorageKey());if(!raw)return;const data=JSON.parse(raw);if(!data||typeof data.text!=='string'||!data.text)return;prompt.value=data.text;updateCounter();prompt.style.height='auto';prompt.style.height=Math.min(190,prompt.scrollHeight)+'px';state.textContent='Черновик восстановлен.'}catch(_e){}}
function rememberActiveConversation(){try{if(!conversationId)return;localStorage.setItem(activeConversationKey,JSON.stringify({id:conversationId,project_id:$('project-chat').value||null,updated_at:Date.now()}))}catch(_e){}}
function forgetActiveConversation(){try{localStorage.removeItem(activeConversationKey)}catch(_e){}}
async function restoreActiveConversation(){try{const raw=localStorage.getItem(activeConversationKey);if(!raw)return;const data=JSON.parse(raw);if(!data||!data.id){forgetActiveConversation();return}await openConversation(String(data.id),data.project_id==null?null:String(data.project_id))}catch(_e){forgetActiveConversation()}}
function pendingText(item){try{const rows=item&&item.body&&Array.isArray(item.body.messages)?item.body.messages:[];const last=rows.length?rows[rows.length-1]:null;return String(last&&last.content||'').trim()}catch(_e){return ''}}
function preservePendingDraft(item){try{const text=pendingText(item);if(!text)return;const body=item.body||{},chatId=body.conversation_id||item.conversation_id||conversationId||'new',projectId=body.project_id||$('project-chat').value||'none',key=draftPrefix+chatId+'::'+projectId;localStorage.setItem(key,JSON.stringify({text,conversation_id:chatId==='new'?null:chatId,project_id:projectId==='none'?null:projectId,updated_at:Date.now()}));if((conversationId||'new')===chatId&&!String(prompt.value||'').trim()){prompt.value=text;updateCounter()}}catch(_e){}}
function forceUnlockWorkspace(message=''){try{activeRequestId=null;if(activeController){try{activeController.abort()}catch(_e){}}activeController=null;stopRequested=false;busy=false;send.disabled=false;send.classList.remove('stop','olya-current-running');setProgress(null);if(message)state.textContent=message}catch(_e){}}
function clearCrashRunState(item=null){try{if(item)preservePendingDraft(item);clearPending();localStorage.removeItem(premiumRunStoreKey);forceUnlockWorkspace()}catch(_e){}}
async function waitForServerIdentity(){let attempt=0;for(;;){try{const health=await api('/health',{},3500),current=String(health&&health.instance_id||''),previous=String(localStorage.getItem(serverInstanceKey)||'');if(current)localStorage.setItem(serverInstanceKey,current);$('health').textContent='● OLYA AI';return{current,previous,restarted:Boolean(current&&previous&&current!==previous)}}catch(e){if(e&&e.status===401)throw e;forceUnlockWorkspace('Сервер временно недоступен. OLYA автоматически переподключится…');$('health').textContent='● Переподключение';await delay(Math.min(5000,1000+attempt*500));attempt++}}}
async function monitorPendingRun(item){const body=item&&item.body||{},id=body.client_request_id;if(!id)return;for(let attempt=0;attempt<20;attempt++){await delay(3000);try{const snapshot=await api('/v1/chat/runs/'+encodeURIComponent(id),{},3500);if(snapshot.status==='succeeded'||snapshot.status==='cancelled'||snapshot.status==='failed'){clearPending(id);if(snapshot.status==='succeeded'&&conversationId===(body.conversation_id||item.conversation_id||null)&&!busy){try{await openConversation(conversationId)}catch(_e){}}state.textContent=snapshot.status==='succeeded'?'Предыдущий ответ восстановлен.':'Предыдущий запрос завершён.';return}}catch(e){if(e&&e.status===404){clearPending(id);preservePendingDraft(item);state.textContent='Сервер был перезапущен. Незавершённый запрос сохранён как черновик.';return}if(attempt>=3)return}}}
async function safeRecoverPendingStartup(serverRestarted=false){const item=readPending();if(!item)return;if(serverRestarted){clearCrashRunState(item);state.textContent='Сервер восстановлен. Незавершённый запрос сохранён как черновик.';return}const body=item.body||{},id=body.client_request_id;if(!id){clearPending();return}preservePendingDraft(item);try{const snapshot=await api('/v1/chat/runs/'+encodeURIComponent(id),{},3500);if(snapshot.status==='succeeded'||snapshot.status==='cancelled'||snapshot.status==='failed'){clearPending(id);state.textContent=snapshot.status==='succeeded'?'Предыдущий запрос уже завершён.':'Предыдущий запрос завершён.';return}state.textContent='Предыдущий запрос ещё выполняется в фоне. Интерфейс доступен.';void monitorPendingRun(item)}catch(e){if(e&&e.status===404){clearPending(id);state.textContent='Сервер был перезапущен. Незавершённый запрос сохранён как черновик.';return}state.textContent='Не удалось проверить старый запрос. Интерфейс разблокирован; черновик сохранён.'}}
function clearLocalChatState(){try{localStorage.removeItem(pendingKey);localStorage.removeItem(activeConversationKey);localStorage.removeItem(serverInstanceKey);localStorage.removeItem(premiumRunStoreKey);for(let i=localStorage.length-1;i>=0;i--){const key=localStorage.key(i);if(key&&key.startsWith(draftPrefix))localStorage.removeItem(key)}}catch(_e){}}
'''
    document = _replace_once(document, "function delay(ms){return new Promise(resolve=>setTimeout(resolve,ms))}", persistence + "function delay(ms){return new Promise(resolve=>setTimeout(resolve,ms))}", "crash-safe persistence helpers")

    document = _replace_once(
        document,
        "conversationId=c.id;await loadConversations();return c.id",
        "conversationId=c.id;rememberActiveConversation();await loadConversations();return c.id",
        "remember created conversation",
    )

    old_open = "async function openConversation(id,projectId=null){if(busy)return;if(projectId!==null)$('project-chat').value=projectId||'';switchView('chat');conversationId=id;messages.replaceChildren();"
    new_open = "async function openConversation(id,projectId=null){if(busy)return;if(conversationId!==id)saveDraft();if(projectId!==null)$('project-chat').value=projectId||'';switchView('chat');conversationId=id;rememberActiveConversation();messages.replaceChildren();"
    document = _replace_once(document, old_open, new_open, "remember opened conversation")
    document = _replace_once(
        document,
        "messages.scrollTop=messages.scrollHeight;await loadConversations()}",
        "messages.scrollTop=messages.scrollHeight;restoreDraft();await loadConversations()}",
        "restore conversation draft",
    )

    old_new_chat = "function newChat(){if(busy)return;switchView('chat');conversationId=null;messages.replaceChildren(empty);empty.style.display='block';oldestMessageAt=null;hasOlder=false;state.textContent='';setProgress(null);loadConversations();prompt.focus()}"
    new_new_chat = "function newChat(){if(busy)return;saveDraft();switchView('chat');conversationId=null;forgetActiveConversation();messages.replaceChildren(empty);empty.style.display='block';oldestMessageAt=null;hasOlder=false;state.textContent='';setProgress(null);loadConversations();restoreDraft();prompt.focus()}"
    document = _replace_once(document, old_new_chat, new_new_chat, "new chat persistence")

    hook_marker = "$('nav-chat').onclick=()=>switchView('chat');"
    hooks = r'''const _olyaRenderFinal=renderFinal;renderFinal=function(live,data){_olyaRenderFinal(live,data);rememberActiveConversation();clearDraft()};
prompt.addEventListener('input',saveDraft);window.addEventListener('pagehide',saveDraft);
'''
    document = _replace_once(document, hook_marker, hooks + hook_marker, "reliability browser hooks")

    startup_old = "await Promise.all([loadConversations(),loadProjects(),refreshBudget(),loadImageStatus()]);await recoverPending();if(!busy)prompt.focus()"
    startup_new = "const boot=await waitForServerIdentity();if(boot.restarted)clearCrashRunState(readPending());await Promise.allSettled([loadConversations(),loadProjects(),refreshBudget(),loadImageStatus()]);await restoreActiveConversation();if(readPending())await safeRecoverPendingStartup(boot.restarted);restoreDraft();forceUnlockWorkspace(state.textContent);prompt.focus()"
    document = _replace_once(document, startup_old, startup_new, "crash-aware workspace restore")

    old_logout = "$('logout').onclick=async()=>{if(activeRequestId)await stopActive();try{await api('/v1/auth/logout',{method:'POST'},15000)}catch(_e){}sessionStorage.clear();location.replace('/login')};"
    new_logout = "$('logout').onclick=async()=>{if(activeRequestId)await stopActive();try{await api('/v1/auth/logout',{method:'POST'},15000)}catch(_e){}clearLocalChatState();sessionStorage.clear();location.replace('/login')};"
    document = _replace_once(document, old_logout, new_logout, "clear persisted chat state on explicit logout")

    return document
