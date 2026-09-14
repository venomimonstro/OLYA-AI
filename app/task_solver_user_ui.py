from __future__ import annotations

from fastapi import APIRouter, Depends
from fastapi.responses import HTMLResponse
from sqlalchemy.orm import Session

from app.db import get_db
from app.user_ui import workspace as _workspace
from app.workspace_client_v4 import enhance_workspace_v4

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

    # One server-side orchestration path owns web research. This avoids the old
    # browser research -> chat research double round-trip and makes the toggle
    # authoritative: auto=solver decides, always=forced, off=no automatic web.
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
        "if(item.event==='status'){if(d.conversation_id)conversationId=d.conversation_id;if(d.resumed)state.textContent='Соединение восстановлено. Продолжаю получать ответ…'}else if(item.event==='token')",
        "if(item.event==='status'){if(d.conversation_id)conversationId=d.conversation_id;if(d.message)state.textContent=String(d.message);else if(d.resumed)state.textContent='Соединение восстановлено. Продолжаю получать ответ…';if(d.state==='researching')setProgress('researching');else if(d.state==='synthesizing')setProgress('thinking')}else if(item.event==='token')",
        "task progress status",
    )

    # The old stream loop forced scrollTop=scrollHeight for every token. That
    # defeated the v3 near-bottom controller and dragged users away from text
    # they were reading. Streaming now mutates only the answer; v3 owns scroll.
    forced_scroll = "renderMarkdown(live.bubble,raw);messages.scrollTop=messages.scrollHeight"
    forced_count = document.count(forced_scroll)
    if forced_count != 2:
        raise RuntimeError(f"Task-solver UI drift for streaming scroll: expected 2 markers, found {forced_count}")
    document = document.replace(forced_scroll, "renderMarkdown(live.bubble,raw)", 2)

    old_final = "function renderFinal(live,data){renderMarkdown(live.bubble,data.text||live.text);attachMeta(live.box,{quality:data.quality&&data.quality.status,mode:data.usage&&data.usage.mode,ttft:data.usage&&data.usage.ttft_ms,tps:data.usage&&data.usage.tokens_per_second});attachSources(live.box,currentSources);attachFileCitations(live.box,data.file_citations);if(data.conversation_id)conversationId=data.conversation_id;state.textContent=((data.quality&&data.quality.warnings)||[]).join(' · ')}"
    new_final = r'''function renderFinal(live,data){renderMarkdown(live.bubble,data.text||live.text);attachMeta(live.box,{quality:data.quality&&data.quality.status,mode:data.usage&&data.usage.mode,ttft:data.usage&&data.usage.ttft_ms,tps:data.usage&&data.usage.tokens_per_second});const task=data.task_execution||null,seen=new Set(),sources=[];for(const src of [...(currentSources||[]),...((task&&task.sources)||[])]){const key=String(src.url||src.id||'');if(!key||seen.has(key))continue;seen.add(key);sources.push(src)}attachSources(live.box,sources);if(task){const d=document.createElement('details');d.className='sources';const s=document.createElement('summary');const n=Number(task.fetched_sources||0),h=Number(task.independent_hosts||0);s.textContent='Как OLYA проверила ответ · '+n+' источн. · '+h+' независимых доменов';const body=document.createElement('div');body.className='source-list';const method=document.createElement('div');method.className='source';const title=document.createElement('b');title.textContent='Проверка данных';const text=document.createElement('small');text.textContent=(task.steps||[]).join(' → ')||'Ответ сформирован без внешнего поиска.';method.append(title,text);body.append(method);for(const warning of (task.warnings||[]).slice(0,3)){const row=document.createElement('div');row.className='source';const w=document.createElement('small');w.textContent=String(warning);row.append(w);body.append(row)}d.append(s,body);live.box.append(d)}attachFileCitations(live.box,data.file_citations);if(data.conversation_id)conversationId=data.conversation_id;state.textContent=((data.quality&&data.quality.warnings)||[]).join(' · ')}'''
    document = _replace_once(document, old_final, new_final, "task execution result UX")

    # This must run after all historical workspace composition layers so its
    # light hover states, waiting animation and compact source chips win the CSS
    # cascade instead of being overwritten by old dark-theme rules.
    document = enhance_workspace_v4(document)

    response = HTMLResponse(document, status_code=base.status_code)
    for key, value in base.headers.items():
        if key.lower() not in {"content-length", "content-type"}:
            response.headers[key] = value
    return response
