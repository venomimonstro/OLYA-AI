from __future__ import annotations

import secrets

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from fastapi.responses import HTMLResponse
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.db import get_db
from app.models import Conversation, ConversationMemory, User
from app.services.auth import get_current_user

router = APIRouter(tags=["user-memory"])
_MEMORY_KINDS = ("explicit", "decision", "preference", "fact")


def _private(response: Response) -> None:
    response.headers["Cache-Control"] = "no-store, private"
    response.headers["X-Robots-Tag"] = "noindex, nofollow, noarchive, nosnippet"


@router.get("/v1/memory")
def list_user_memory(
    response: Response,
    limit: int = Query(default=200, ge=1, le=500),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> list[dict]:
    _private(response)
    rows = list(
        db.execute(
            select(ConversationMemory, Conversation)
            .join(Conversation, Conversation.id == ConversationMemory.conversation_id)
            .where(
                Conversation.owner_id == user.id,
                ConversationMemory.kind.in_(_MEMORY_KINDS),
            )
            .order_by(ConversationMemory.updated_at.desc())
            .limit(limit)
        ).all()
    )
    result: list[dict] = []
    seen: set[str] = set()
    for memory, conversation in rows:
        normalized = " ".join(str(memory.value or "").casefold().split())
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        result.append({
            "id": memory.id,
            "kind": memory.kind,
            "value": memory.value,
            "conversation_id": conversation.id,
            "conversation_title": conversation.title,
            "created_at": memory.created_at,
            "updated_at": memory.updated_at,
        })
    return result


@router.delete("/v1/memory/{memory_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_user_memory(
    memory_id: str,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> None:
    row = db.execute(
        select(ConversationMemory, Conversation)
        .join(Conversation, Conversation.id == ConversationMemory.conversation_id)
        .where(ConversationMemory.id == memory_id, Conversation.owner_id == user.id)
    ).first()
    if row is None or row[0].kind not in _MEMORY_KINDS:
        raise HTTPException(status_code=404, detail="Элемент памяти не найден")
    db.delete(row[0])
    db.commit()


@router.delete("/v1/memory", status_code=status.HTTP_204_NO_CONTENT)
def clear_user_memory(
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> None:
    conversation_ids = select(Conversation.id).where(Conversation.owner_id == user.id)
    db.execute(
        delete(ConversationMemory).where(
            ConversationMemory.conversation_id.in_(conversation_ids),
            ConversationMemory.kind.in_(_MEMORY_KINDS),
        )
    )
    db.commit()


PAGE = r'''<!doctype html><html lang="ru"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>OLYA AI · Память</title><style nonce="__NONCE__">
:root{--bg:#fff;--side:#f7f7f8;--line:#e7e7ea;--text:#202024;--muted:#777780}*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--text);font:14px/1.55 Inter,system-ui,-apple-system,Segoe UI,Arial,sans-serif}.top{height:56px;display:flex;align-items:center;gap:8px;border-bottom:1px solid var(--line);padding:0 max(14px,calc((100% - 900px)/2))}.top b{font-size:15px}.top a,.btn{border:1px solid var(--line);background:#fff;color:#333;border-radius:9px;padding:8px 10px;text-decoration:none;cursor:pointer}.grow{flex:1}.wrap{width:min(900px,100%);margin:auto;padding:26px 16px 70px}.lead{color:var(--muted);max-width:700px}.head{display:flex;align-items:center;gap:8px;margin:25px 0 10px}.head h2{margin:0;font-size:17px}.list{display:grid;gap:8px}.item{border:1px solid var(--line);border-radius:13px;padding:12px 13px;display:flex;gap:12px;align-items:flex-start}.main{min-width:0;flex:1}.kind{font-size:11px;color:var(--muted);margin-bottom:3px}.value{font-size:15px;overflow-wrap:anywhere}.meta{font-size:11px;color:#999;margin-top:5px}.delete{border:0;background:transparent;color:#888;border-radius:7px;width:30px;height:30px;cursor:pointer}.delete:hover{background:#f1f1f2;color:#b22}.empty{border:1px dashed var(--line);border-radius:13px;padding:28px;text-align:center;color:var(--muted)}.danger{color:#a33}@media(max-width:600px){.top{padding:0 10px}.wrap{padding:20px 12px 60px}}
</style></head><body><header class="top"><b>OLYA AI · Память</b><div class="grow"></div><a href="/app">← Вернуться в чат</a></header><main class="wrap"><h1>Что OLYA помнит</h1><p class="lead">OLYA сохраняет только полезные факты, предпочтения и решения из ваших сообщений и использует их в следующих чатах, когда они относятся к вопросу. Полная история чатов хранится отдельно и не пересылается модели целиком.</p><div class="head"><h2>Сохранённая память</h2><div class="grow"></div><button class="btn danger" id="clear">Очистить всё</button></div><div class="list" id="list"><div class="empty">Загружаю…</div></div></main><script nonce="__NONCE__">
const $=id=>document.getElementById(id),token=()=>sessionStorage.getItem('x1_access_token')||sessionStorage.getItem('x1_token')||'';
async function api(path,opts={}){const t=token();if(!t)throw new Error('Сессия не найдена. Откройте /app и войдите снова.');const headers={...(opts.headers||{}),Authorization:'Bearer '+t};const r=await fetch(path,{...opts,headers});let d=null;if(r.status!==204){try{d=await r.json()}catch(_e){}}if(!r.ok)throw new Error(typeof d?.detail==='string'?d.detail:'HTTP '+r.status);return d}
const labels={explicit:'Явно сохранено',decision:'Решение',preference:'Предпочтение',fact:'Факт'};
async function load(){const rows=await api('/v1/memory'),root=$('list');root.replaceChildren();if(!rows.length){const e=document.createElement('div');e.className='empty';e.textContent='Память пока пуста. Напишите в чате, например: «Запомни, что я предпочитаю короткий вывод в начале ответа». ';root.append(e);return}for(const row of rows){const item=document.createElement('div');item.className='item';const main=document.createElement('div');main.className='main';const kind=document.createElement('div');kind.className='kind';kind.textContent=labels[row.kind]||row.kind;const value=document.createElement('div');value.className='value';value.textContent=row.value;const meta=document.createElement('div');meta.className='meta';meta.textContent='Из чата «'+(row.conversation_title||'Новый чат')+'»';main.append(kind,value,meta);const del=document.createElement('button');del.className='delete';del.textContent='⌫';del.title='Удалить из памяти';del.setAttribute('aria-label','Удалить из памяти');del.onclick=async()=>{await api('/v1/memory/'+encodeURIComponent(row.id),{method:'DELETE'});await load()};item.append(main,del);root.append(item)}}
$('clear').onclick=async()=>{if(!confirm('Очистить всю память OLYA? История чатов при этом останется.'))return;try{await api('/v1/memory',{method:'DELETE'});await load()}catch(e){alert(e.message)}};load().catch(e=>{$('list').innerHTML='<div class="empty">'+e.message+'</div>'});
</script></body></html>'''


@router.get("/memory", response_class=HTMLResponse, include_in_schema=False)
def memory_page() -> HTMLResponse:
    nonce = secrets.token_urlsafe(18)
    response = HTMLResponse(PAGE.replace("__NONCE__", nonce))
    response.headers.update({
        "Content-Security-Policy": "default-src 'none'; base-uri 'none'; frame-ancestors 'none'; form-action 'none'; " + f"style-src 'nonce-{nonce}'; script-src 'nonce-{nonce}'; connect-src 'self'",
        "Cache-Control": "no-store", "Pragma": "no-cache", "Referrer-Policy": "no-referrer",
        "X-Content-Type-Options": "nosniff", "X-Frame-Options": "DENY",
    })
    return response
