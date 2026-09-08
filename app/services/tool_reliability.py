from __future__ import annotations

import asyncio
import hashlib
import inspect
import json
import re
import time
from collections import Counter, deque
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, ValidationError

ToolEffect = Literal["read", "write"]
ToolHandler = Callable[[BaseModel], Any | Awaitable[Any]]

class ToolReliabilityError(RuntimeError): code = "tool_error"
class UnknownToolError(ToolReliabilityError): code = "unknown_tool"
class ToolValidationError(ToolReliabilityError): code = "invalid_arguments"
class ToolLoopError(ToolReliabilityError): code = "tool_loop_detected"
class ToolBudgetError(ToolReliabilityError): code = "tool_budget_exhausted"
class ToolTimeoutError(ToolReliabilityError): code = "tool_timeout"
class ToolExecutionError(ToolReliabilityError): code = "tool_execution_failed"
class ToolReplayBlockedError(ToolReliabilityError): code = "tool_replay_blocked"

@dataclass(frozen=True)
class ToolSpec:
    name: str
    description: str
    args_model: type[BaseModel]
    handler: ToolHandler
    effect: ToolEffect = "read"
    timeout_seconds: float = 15.0
    max_retries: int = 1
    result_max_chars: int = 20_000
    cacheable: bool = True
    def __post_init__(self) -> None:
        if not re.fullmatch(r"[a-z][a-z0-9_.-]{1,63}", self.name): raise ValueError("Invalid tool name")
        if self.effect not in {"read","write"}: raise ValueError("Invalid tool effect")
        if self.timeout_seconds <= 0 or self.timeout_seconds > 300: raise ValueError("Invalid tool timeout")
        if self.max_retries < 0 or self.max_retries > 3: raise ValueError("Invalid retry count")
        if self.effect == "write" and self.max_retries: raise ValueError("Write tools cannot have automatic retries")
    def openai_schema(self) -> dict[str,Any]:
        schema=self.args_model.model_json_schema(); schema["additionalProperties"]=False
        return {"type":"function","function":{"name":self.name,"description":self.description[:1000],"parameters":schema}}

@dataclass(frozen=True)
class ToolCall:
    name: str
    arguments: dict[str,Any]
    call_id: str = field(default_factory=lambda:f"call_{uuid4().hex}")

@dataclass(frozen=True)
class ToolResult:
    call_id: str; name: str; status: Literal["ok","error","cached"]; output: Any; error_code: str|None; attempts: int; duration_ms: int; fingerprint: str; truncated: bool=False
    def as_tool_message(self)->dict[str,Any]:
        payload={"status":self.status,"tool":self.name,"result":self.output,"error_code":self.error_code}
        return {"role":"tool","tool_call_id":self.call_id,"content":json.dumps(payload,ensure_ascii=False,separators=(",",":"))}

class StrictToolArgs(BaseModel): model_config=ConfigDict(extra="forbid")

def _canonical(value:Any)->str: return json.dumps(value,ensure_ascii=False,sort_keys=True,separators=(",",":"),default=str)
def call_fingerprint(name:str,arguments:dict[str,Any])->str: return hashlib.sha256(f"{name}\n{_canonical(arguments)}".encode()).hexdigest()

def _bounded_output(value:Any,max_chars:int)->tuple[Any,bool]:
    if isinstance(value,str): return (value,False) if len(value)<=max_chars else (value[:max_chars]+"\n[X1 TOOL RESULT TRUNCATED]",True)
    serialized=_canonical(value)
    return (value,False) if len(serialized)<=max_chars else ({"truncated":True,"preview":serialized[:max_chars],"original_chars":len(serialized)},True)

class ToolRegistry:
    def __init__(self)->None: self._items:dict[str,ToolSpec]={}
    def register(self,spec:ToolSpec)->None:
        if spec.name in self._items: raise ValueError(f"Duplicate tool registration: {spec.name}")
        self._items[spec.name]=spec
    def get(self,name:str)->ToolSpec:
        try:return self._items[name]
        except KeyError as exc: raise UnknownToolError(f"Tool is not registered: {name}") from exc
    def schemas(self)->list[dict[str,Any]]: return [self._items[name].openai_schema() for name in sorted(self._items)]
    @property
    def names(self)->tuple[str,...]: return tuple(sorted(self._items))

@dataclass
class ToolSession:
    registry:ToolRegistry; max_calls:int=12; max_same_call:int=2; max_consecutive_same_tool:int=4; history_size:int=24; calls_used:int=0
    _fingerprints:Counter[str]=field(default_factory=Counter); _recent_tools:deque[str]=field(default_factory=lambda:deque(maxlen=24)); _cache:dict[str,ToolResult]=field(default_factory=dict); _call_ids:dict[str,str]=field(default_factory=dict); _blocked_fingerprints:set[str]=field(default_factory=set); _write_state_uncertain:bool=False
    def __post_init__(self)->None:
        self.max_calls=max(1,min(int(self.max_calls),64)); self.max_same_call=max(1,min(int(self.max_same_call),4)); self.max_consecutive_same_tool=max(2,min(int(self.max_consecutive_same_tool),8)); self.history_size=max(8,min(int(self.history_size),64)); self._recent_tools=deque(maxlen=self.history_size)
    def _cached_result(self,call:ToolCall,fingerprint:str)->ToolResult|None:
        cached=self._cache.get(fingerprint)
        if cached is None:return None
        return ToolResult(call_id=call.call_id,name=cached.name,status="cached",output=cached.output,error_code=cached.error_code,attempts=0,duration_ms=0,fingerprint=fingerprint,truncated=cached.truncated)
    def _admit(self,call:ToolCall,spec:ToolSpec,fingerprint:str)->ToolResult|None:
        if not call.call_id or len(call.call_id)>128 or not re.fullmatch(r"[A-Za-z0-9_.:-]+",call.call_id): raise ToolValidationError("Invalid tool_call_id")
        prior=self._call_ids.get(call.call_id)
        if prior is not None and prior!=fingerprint: raise ToolValidationError("tool_call_id was reused with different arguments")
        if fingerprint in self._blocked_fingerprints: raise ToolReplayBlockedError("Previous side-effect outcome is uncertain; replay is blocked")
        if spec.effect=="write" and self._write_state_uncertain: raise ToolReplayBlockedError("A previous write has uncertain outcome; further writes require reconciliation")
        self._fingerprints[fingerprint]+=1
        if self._fingerprints[fingerprint]>self.max_same_call: raise ToolLoopError("The same tool call was repeated too many times")
        if len(self._recent_tools)>=self.max_consecutive_same_tool:
            tail=list(self._recent_tools)[-self.max_consecutive_same_tool:]
            if tail and all(name==spec.name for name in tail): raise ToolLoopError("The agent is repeatedly calling the same tool without progress")
        self._call_ids[call.call_id]=fingerprint; self._recent_tools.append(spec.name)
        if spec.cacheable:
            cached=self._cached_result(call,fingerprint)
            if cached is not None:return cached
        if self.calls_used>=self.max_calls: raise ToolBudgetError("Tool call budget exhausted")
        return None
    async def _invoke(self,spec:ToolSpec,args:BaseModel)->Any:
        if inspect.iscoroutinefunction(spec.handler): return await asyncio.wait_for(spec.handler(args),timeout=spec.timeout_seconds)
        return await asyncio.wait_for(asyncio.to_thread(spec.handler,args),timeout=spec.timeout_seconds)
    async def execute(self,call:ToolCall)->ToolResult:
        spec=self.registry.get(call.name)
        if not isinstance(call.arguments,dict): raise ToolValidationError("Tool arguments must be a JSON object")
        try: args=spec.args_model.model_validate(call.arguments)
        except ValidationError as exc: raise ToolValidationError(f"Arguments failed schema validation: {exc.errors(include_url=False)[:8]}") from exc
        normalized=args.model_dump(mode="json",exclude_none=False); fingerprint=call_fingerprint(spec.name,normalized); cached=self._admit(call,spec,fingerprint)
        if cached is not None:return cached
        self.calls_used+=1; started=time.perf_counter(); attempts=0; last_error:Exception|None=None; max_attempts=1+(spec.max_retries if spec.effect=="read" else 0)
        while attempts<max_attempts:
            attempts+=1
            try:
                value=await self._invoke(spec,args); bounded,truncated=_bounded_output(value,spec.result_max_chars); result=ToolResult(call_id=call.call_id,name=spec.name,status="ok",output=bounded,error_code=None,attempts=attempts,duration_ms=max(0,int((time.perf_counter()-started)*1000)),fingerprint=fingerprint,truncated=truncated)
                if spec.cacheable:self._cache[fingerprint]=result
                return result
            except asyncio.TimeoutError as exc:
                last_error=exc
                if spec.effect=="write": self._blocked_fingerprints.add(fingerprint); self._write_state_uncertain=True; raise ToolTimeoutError("Tool timed out; side-effecting call outcome is uncertain and replay is blocked") from exc
                if attempts>=max_attempts: raise ToolTimeoutError("Tool timed out after bounded retries") from exc
            except ToolReliabilityError: raise
            except Exception as exc:
                last_error=exc
                if spec.effect=="write": self._blocked_fingerprints.add(fingerprint); self._write_state_uncertain=True; raise ToolExecutionError(f"Tool execution failed with uncertain side-effect outcome: {exc.__class__.__name__}") from exc
                if attempts>=max_attempts: raise ToolExecutionError(f"Tool execution failed: {exc.__class__.__name__}") from exc
                await asyncio.sleep(min(.25*attempts,.75))
        raise ToolExecutionError("Tool execution failed") from last_error

def error_result(call:ToolCall,exc:ToolReliabilityError)->ToolResult:
    fp=call_fingerprint(call.name,call.arguments if isinstance(call.arguments,dict) else {"invalid":True})
    return ToolResult(call_id=call.call_id,name=call.name,status="error",output={"message":str(exc)[:1200]},error_code=getattr(exc,"code","tool_error"),attempts=0,duration_ms=0,fingerprint=fp)