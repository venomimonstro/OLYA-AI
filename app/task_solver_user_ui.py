from __future__ import annotations

from fastapi import APIRouter, Depends
from fastapi.responses import HTMLResponse
from sqlalchemy.orm import Session

from app.db import get_db
from app.user_ui import workspace as _workspace
from app.workspace_client_v4 import enhance_workspace_v4
from app.workspace_reliability_v5 import enhance_workspace_reliability_v5
from app.quality_levels_ui import enhance_quality_levels
from app.workspace_premium_v6 import enhance_workspace_premium_v6
from app.workspace_parallel_draft_patch import enhance_parallel_draft_safety
from app.workspace_recovery_controls import enhance_recovery_controls
from app.workspace_chat_library_v1 import enhance_chat_library

router = APIRouter(tags=["user-workspace"])


def _replace_once(document: str, old: str, new: str, label: str) -> str:
    count = document.count(old)
    if count != 1:
        raise RuntimeError(f"Task-solver UI drift for {label}: expected 1 marker, found {count}")
    return document.replace(old, new, 1)


@router.get("/app", response_class=HTMLResponse, include_in_schema=False)
def workspace(db: Session = Depends(get_db)) -> HTMLResponse:
    base = _workspace(db)
    document = base.body.decode("utf-8")

    document = _replace_once(
        document,
        "if(webMode==='always')ids=await research(text,true);else if(webMode==='auto')ids=await research(text,false);",
        "currentSources=[];",
        "central web orchestration",
    )
    document = _replace_once(
        document,
        "verification:$('verify').value,research_source_ids:ids,client_request_id:requestId",
        "verification:$('verify').value,web_mode:webMode,research_source_ids:ids,client_request_id:requestId",
        "web mode request contract",
    )
    document = _replace_once(
        document,
        "setBusy(true,'Проверяю доступный ресурс…');setProgress('queued');",
        "document.body.classList.add('olya-request-pending');setBusy(true,'Проверяю доступный ресурс…');setProgress('queued');",
        "immediate request waiting state",
    )
    document = _replace_once(
        document,
        "if(item.event==='status'){if(d.conversation_id)conversationId=d.conversation_id;if(d.resumed)state.textContent='Соединение восстановлено. Продолжаю получать ответ…'}else if(item.event==='token')",
        "if(item.event==='status'){if(d.conversation_id)conversationId=d.conversation_id;if(d.message)state.textContent=String(d.message);else if(d.resumed)state.textContent='Соединение восстановлено. Продолжаю получать ответ…';if(d.state==='researching')setProgress('researching');else if(d.state==='synthesizing')setProgress('thinking')}else if(item.event==='token')",
        "task progress status",
    )

    forced_scroll = "renderMarkdown(live.bubble,raw);messages.scrollTop=messages.scrollHeight"
    forced_count = document.count(forced_scroll)
    if forced_count != 2:
        raise RuntimeError(f"Task-solver UI drift for streaming scroll: expected 2 markers, found {forced_count}")
    document = document.replace(forced_scroll, "renderMarkdown(live.bubble,raw)", 2)

    old_final = "function renderFinal(live,data){renderMarkdown(live.bubble,data.text||live.text);attachMeta(live.box,{quality:data.quality&&data.quality.status,mode:data.usage&&data.usage.mode,ttft:data.usage&&data.usage.ttft_ms,tps:data.usage&&data.usage.tokens_per_second});attachSources(live.box,currentSources);attachFileCitations(live.box,data.file_citations);if(data.conversation_id)conversationId=data.conversation_id;state.textContent=((data.quality&&data.quality.warnings)||[]).join(' · ')}"
    new_final = r'''function renderFinal(live,data){renderMarkdown(live.bubble,data.text||live.text);attachMeta(live.box,{quality:data.quality&&data.quality.status,mode:data.usage&&data.usage.mode,ttft:data.usage&&data.usage.ttft_ms,tps:data.usage&&data.usage.tokens_per_second});const task=data.task_execution||null,seen=new Set(),sources=[];for(const src of [...(currentSources||[]),...((task&&task.sources)||[])]){const key=String(src.url||src.id||'');if(!key||seen.has(key))continue;seen.add(key);sources.push(src)}attachSources(live.box,sources);if(task){const d=document.createElement('details');d.className='sources';const s=document.createElement('summary');const n=Number(task.fetched_sources||0),h=Number(task.independent_hosts||0);s.textContent='Как OLYA проверила ответ · '+n+' источн. · '+h+' независимых доменов';const body=document.createElement('div');body.className='source-list';const method=document.createElement('div');method.className='source';const title=document.createElement('b');title.textContent='Проверка данных';const text=document.createElement('small');text.textContent=(task.steps||[]).join(' → ')||'Ответ сформирован без внешнего поиска.';method.append(title,text);body.append(method);for(const warning of (task.warnings||[]).slice(0,3)){const row=document.createElement('div');row.className='source';const w=document.createElement('small');w.textContent=String(warning);row.append(w);body.append(row)}d.append(s,body);live.box.append(d)}attachFileCitations(live.box,data.file_citations);if(data.conversation_id)conversationId=data.conversation_id;state.textContent=((data.quality&&data.quality.warnings)||[]).join(' · ');if(window.olyaRefreshChatLibrary)window.olyaRefreshChatLibrary()}'''
    document = _replace_once(document, old_final, new_final, "task execution result UX")

    document = enhance_workspace_v4(document)
    document = enhance_workspace_reliability_v5(document)
    document = enhance_quality_levels(document)
    document = enhance_workspace_premium_v6(document)
    document = enhance_parallel_draft_safety(document)
    document = enhance_recovery_controls(document)
    document = enhance_chat_library(document)

    response = HTMLResponse(document, status_code=base.status_code)
    for key, value in base.headers.items():
        if key.lower() not in {"content-length", "content-type"}:
            response.headers[key] = value
    return response
