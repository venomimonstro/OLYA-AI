from __future__ import annotations

import hashlib

from app.schemas.chat import ChatMessage


class ContextCompiler:
    """Deterministic low-RAM compiler for long chats."""
    def __init__(self,max_chars:int=48_000,max_message_chars:int|None=None)->None:
        self.max_chars=max(128,int(max_chars)); self.max_message_chars=max_message_chars or max(128,min(24_000,self.max_chars//2))
    def _clip(self,text:str)->str:
        if len(text)<=self.max_message_chars: return text
        head=self.max_message_chars//3; tail=self.max_message_chars-head
        return text[:head]+"\n[…older content compacted by X1…]\n"+text[-tail:]
    @staticmethod
    def _key(msg:ChatMessage)->tuple[str,bytes]:
        return msg.role,hashlib.blake2b(msg.content.encode("utf-8",errors="replace"),digest_size=12).digest()
    def compile(self,messages:list[ChatMessage])->list[ChatMessage]:
        systems=[ChatMessage(role="system",content=self._clip(m.content)) for m in messages if m.role=="system"][-2:]
        conversational=[m for m in messages if m.role!="system"]
        budget=max(0,self.max_chars-sum(len(m.content) for m in systems)); seen=set(); kept=[]; used=0
        for msg in reversed(conversational):
            key=self._key(msg)
            if key in seen: continue
            seen.add(key); content=self._clip(msg.content); remaining=budget-used
            if remaining<=0: break
            if len(content)>remaining:
                if not kept: content=content[-remaining:]
                else: break
            kept.append(ChatMessage(role=msg.role,content=content)); used+=len(content)
        return systems+list(reversed(kept))
