from __future__ import annotations


def _replace_once(document: str, old: str, new: str, label: str) -> str:
    count = document.count(old)
    if count != 1:
        raise RuntimeError(f"Workspace reliability drift for {label}: expected 1 marker, found {count}")
    return document.replace(old, new, 1)


def enhance_workspace_reliability_v5(document: str) -> str:
    """Durable browser state for chats, pending runs and unsent drafts."""

    # Pending runs used to live in sessionStorage, so closing/reopening the tab
    # lost the recovery pointer. localStorage keeps the idempotent request body.
    old_pending = """function readPending(){try{const raw=sessionStorage.getItem(pendingKey);if(!raw)return null;const item=JSON.parse(raw);return item&&item.body&&item.body.client_request_id?item:null}catch(_e){sessionStorage.removeItem(pendingKey);return null}}
function savePending(body){const existing=readPending();const started=existing&&existing.body.client_request_id===body.client_request_id?existing.started_at:Date.now();sessionStorage.setItem(pendingKey,JSON.stringify({body,conversation_id:body.conversation_id||null,started_at:started}))}
function clearPending(id){const item=readPending();if(!item)return;if(!id||item.body.client_request_id===id)sessionStorage.removeItem(pendingKey)}"""
    new_pending = """function readPending(){try{let raw=localStorage.getItem(pendingKey);if(!raw){raw=sessionStorage.getItem(pendingKey);if(raw){localStorage.setItem(pendingKey,raw);sessionStorage.removeItem(pendingKey)}}if(!raw)return null;const item=JSON.parse(raw);return item&&item.body&&item.body.client_request_id?item:null}catch(_e){localStorage.removeItem(pendingKey);sessionStorage.removeItem(pendingKey);return null}}
function savePending(body){const existing=readPending();const started=existing&&existing.body.client_request_id===body.client_request_id?existing.started_at:Date.now();localStorage.setItem(pendingKey,JSON.stringify({body,conversation_id:body.conversation_id||null,started_at:started}))}
function clearPending(id){const item=readPending();if(!item)return;if(!id||item.body.client_request_id===id)localStorage.removeItem(pendingKey)}"""
    document = _replace_once(document, old_pending, new_pending, "durable pending run")

    persistence = r'''const activeConversationKey='olya_active_conversation_v1',draftPrefix='olya_chat_draft_v2::';
function draftStorageKey(){const projectId=$('project-chat').value||'none',chatId=conversationId||'new';return draftPrefix+chatId+'::'+projectId}
function saveDraft(){try{const key=draftStorageKey(),text=String(prompt.value||'');if(!text){localStorage.removeItem(key);return}localStorage.setItem(key,JSON.stringify({text,conversation_id:conversationId||null,project_id:$('project-chat').value||null,updated_at:Date.now()}))}catch(_e){}}
function clearDraft(){try{localStorage.removeItem(draftStorageKey())}catch(_e){}}
function restoreDraft(){try{const pending=readPending();if(pending&&((pending.conversation_id||null)===(conversationId||null)))return;const raw=localStorage.getItem(draftStorageKey());if(!raw)return;const data=JSON.parse(raw);if(!data||typeof data.text!=='string'||!data.text)return;prompt.value=data.text;updateCounter();prompt.style.height='auto';prompt.style.height=Math.min(190,prompt.scrollHeight)+'px';state.textContent='Черновик восстановлен.'}catch(_e){}}
function rememberActiveConversation(){try{if(!conversationId)return;localStorage.setItem(activeConversationKey,JSON.stringify({id:conversationId,project_id:$('project-chat').value||null,updated_at:Date.now()}))}catch(_e){}}
function forgetActiveConversation(){try{localStorage.removeItem(activeConversationKey)}catch(_e){}}
async function restoreActiveConversation(){try{const raw=localStorage.getItem(activeConversationKey);if(!raw)return;const data=JSON.parse(raw);if(!data||!data.id){forgetActiveConversation();return}await openConversation(String(data.id),data.project_id==null?null:String(data.project_id))}catch(_e){forgetActiveConversation()}}
function clearLocalChatState(){try{localStorage.removeItem(pendingKey);localStorage.removeItem(activeConversationKey);for(let i=localStorage.length-1;i>=0;i--){const key=localStorage.key(i);if(key&&key.startsWith(draftPrefix))localStorage.removeItem(key)}}catch(_e){}}
'''
    document = _replace_once(document, "function delay(ms){return new Promise(resolve=>setTimeout(resolve,ms))}", persistence + "function delay(ms){return new Promise(resolve=>setTimeout(resolve,ms))}", "draft persistence helpers")

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

    # Successful finalization is the point at which an unsent-draft backup is no
    # longer needed. Failed/cancelled requests intentionally keep the draft.
    hook_marker = "$('nav-chat').onclick=()=>switchView('chat');"
    hooks = r'''const _olyaRenderFinal=renderFinal;renderFinal=function(live,data){_olyaRenderFinal(live,data);rememberActiveConversation();clearDraft()};
prompt.addEventListener('input',saveDraft);window.addEventListener('pagehide',saveDraft);
'''
    document = _replace_once(document, hook_marker, hooks + hook_marker, "reliability browser hooks")

    startup_old = "await Promise.all([loadConversations(),loadProjects(),refreshBudget(),loadImageStatus()]);await recoverPending();if(!busy)prompt.focus()"
    startup_new = "await Promise.all([loadConversations(),loadProjects(),refreshBudget(),loadImageStatus()]);if(readPending())await recoverPending();else await restoreActiveConversation();restoreDraft();if(!busy)prompt.focus()"
    document = _replace_once(document, startup_old, startup_new, "workspace restore on reload")

    document = _replace_once(
        document,
        "sessionStorage.clear();location.replace('/login')",
        "clearLocalChatState();sessionStorage.clear();location.replace('/login')",
        "clear persisted chat state on logout",
    )

    return document
