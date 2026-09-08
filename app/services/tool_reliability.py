from __future__ import annotations

import asyncio
import hashlib
import inspect
import json
import re
import time
from collections import Counter, deque
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Literal, TypeVar
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, ValidationError


ToolEffect = Literal["read", "write"]
ToolHandler = Callable[[BaseModel], Any | Awaitable[Any]]


class ToolReliabilityError(RuntimeError):
    code = "tool_error"


class UnknownToolError(ToolReliabilityError):
    code = "unknown_tool"


class ToolValidationError(ToolReliabilityError):
    code = "invalid_arguments"


class ToolLoopError(ToolReliabilityError):
    code = "tool_loop_detected"


class ToolBudgetError(ToolReliabilityError):
    code = "tool_budget_exhausted"


class ToolTimeoutError(ToolReliabilityError):
    code = "tool_timeout"


class ToolExecutionError(ToolReliabilityError):
    code = "tool_execution_failed"


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

    def __post_init__(self) -> None:
        if not re.fullmatch(r"[a-z][a-z0-9_.-]{1,63}", self.name):
            raise ValueError("Invalid tool name")
        if self.effect not in {"read", "write"}:
            raise ValueError("Invalid tool effect")
        if self.timeout_seconds <= 0 or self.timeout_seconds > 300:
            raise ValueError("Invalid tool timeout")
        if self.max_retries < 0 or self.max_retries > 3:
            raise ValueError("Invalid retry count")
        if self.effect == "write" and self.max_retries:
            # Automatic replay of side effects after a timeout is unsafe because
            # the first invocation may have succeeded before its response was lost.
            raise ValueError("Write tools cannot have automatic retries")

    def openai_schema(self) -> dict[str, Any]:
        schema = self.args_model.model_json_schema()
        schema["additionalProperties"] = False
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description[:1000],
                "parameters": schema,
            },
        }


@dataclass(frozen=True)
class ToolCall:
    name: str
    arguments: dict[str, Any]
    call_id: str = field(default_factory=lambda: f"call_{uuid4().hex}")


@dataclass(frozen=True)
class ToolResult:
    call_id: str
    name: str
    status: Literal["ok", "error", "cached"]
    output: Any
    error_code: str | None
    attempts: int
    duration_ms: int
    fingerprint: str
    truncated: bool = False

    def as_tool_message(self) -> dict[str, Any]:
        payload = {
            "status": self.status,
            "tool": self.name,
            "result": self.output,
            "error_code": self.error_code,
        }
        return {
            "role": "tool",
            "tool_call_id": self.call_id,
            "content": json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
        }


class StrictToolArgs(BaseModel):
    """Convenient base for tool argument schemas: unknown fields fail closed."""

    model_config = ConfigDict(extra="forbid")


def _canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)


def call_fingerprint(name: str, arguments: dict[str, Any]) -> str:
    raw = f"{name}\n{_canonical(arguments)}".encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def _bounded_output(value: Any, max_chars: int) -> tuple[Any, bool]:
    if isinstance(value, str):
        if len(value) <= max_chars:
            return value, False
        return value[:max_chars] + "\n[X1 TOOL RESULT TRUNCATED]", True
    serialized = _canonical(value)
    if len(serialized) <= max_chars:
        return value, False
    # Keep result structurally valid for the model instead of returning a broken
    # partial JSON object.
    return {
        "truncated": True,
        "preview": serialized[:max_chars],
        "original_chars": len(serialized),
    }, True


class ToolRegistry:
    def __init__(self) -> None:
        self._items: dict[str, ToolSpec] = {}

    def register(self, spec: ToolSpec) -> None:
        if spec.name in self._items:
            raise ValueError(f"Duplicate tool registration: {spec.name}")
        self._items[spec.name] = spec

    def get(self, name: str) -> ToolSpec:
        try:
            return self._items[name]
        except KeyError as exc:
            raise UnknownToolError(f"Tool is not registered: {name}") from exc

    def schemas(self) -> list[dict[str, Any]]:
        return [self._items[name].openai_schema() for name in sorted(self._items)]

    @property
    def names(self) -> tuple[str, ...]:
        return tuple(sorted(self._items))


@dataclass
class ToolSession:
    registry: ToolRegistry
    max_calls: int = 12
    max_same_call: int = 2
    max_consecutive_same_tool: int = 4
    history_size: int = 24
    calls_used: int = 0
    _fingerprints: Counter[str] = field(default_factory=Counter)
    _recent_tools: deque[str] = field(default_factory=lambda: deque(maxlen=24))
    _cache: dict[str, ToolResult] = field(default_factory=dict)
    _call_ids: dict[str, str] = field(default_factory=dict)

    def _admit(self, call: ToolCall, spec: ToolSpec, fingerprint: str) -> ToolResult | None:
        if not call.call_id or len(call.call_id) > 128 or not re.fullmatch(r"[A-Za-z0-9_.:-]+", call.call_id):
            raise ToolValidationError("Invalid tool_call_id")

        prior_fp = self._call_ids.get(call.call_id)
        if prior_fp is not None:
            if prior_fp != fingerprint:
                raise ToolValidationError("tool_call_id was reused with different arguments")
            cached = self._cache.get(fingerprint)
            if cached is not None:
                return ToolResult(
                    call_id=call.call_id,
                    name=cached.name,
                    status="cached",
                    output=cached.output,
                    error_code=cached.error_code,
                    attempts=0,
                    duration_ms=0,
                    fingerprint=fingerprint,
                    truncated=cached.truncated,
                )

        if self.calls_used >= self.max_calls:
            raise ToolBudgetError("Tool call budget exhausted")
        if self._fingerprints[fingerprint] >= self.max_same_call:
            raise ToolLoopError("The same tool call was repeated too many times")
        if len(self._recent_tools) >= self.max_consecutive_same_tool:
            tail = list(self._recent_tools)[-self.max_consecutive_same_tool :]
            if tail and all(name == spec.name for name in tail):
                raise ToolLoopError("The agent is repeatedly calling the same tool without progress")
        return None

    async def execute(self, call: ToolCall) -> ToolResult:
        spec = self.registry.get(call.name)
        if not isinstance(call.arguments, dict):
            raise ToolValidationError("Tool arguments must be a JSON object")
        try:
            args = spec.args_model.model_validate(call.arguments)
        except ValidationError as exc:
            # Never feed the entire internal validation traceback back to the
            # model; bounded errors are enough for one corrected call.
            details = exc.errors(include_url=False)[:8]
            raise ToolValidationError(f"Arguments failed schema validation: {details}") from exc

        normalized = args.model_dump(mode="json", exclude_none=False)
        fingerprint = call_fingerprint(spec.name, normalized)
        cached = self._admit(call, spec, fingerprint)
        if cached is not None:
            return cached

        self.calls_used += 1
        self._fingerprints[fingerprint] += 1
        self._recent_tools.append(spec.name)
        self._call_ids[call.call_id] = fingerprint

        started = time.perf_counter()
        attempts = 0
        last_error: Exception | None = None
        max_attempts = 1 + (spec.max_retries if spec.effect == "read" else 0)

        while attempts < max_attempts:
            attempts += 1
            try:
                value = spec.handler(args)
                if inspect.isawaitable(value):
                    value = await asyncio.wait_for(value, timeout=spec.timeout_seconds)
                else:
                    # Sync handlers are expected to be bounded local operations.
                    # They execute in a thread so the async agent loop remains responsive.
                    # A handler that can block indefinitely must itself provide a bounded
                    # primitive; Python cannot safely kill an arbitrary worker thread.
                    async def finished() -> Any:
                        return value
                    value = await asyncio.wait_for(finished(), timeout=spec.timeout_seconds)
                bounded, truncated = _bounded_output(value, spec.result_max_chars)
                result = ToolResult(
                    call_id=call.call_id,
                    name=spec.name,
                    status="ok",
                    output=bounded,
                    error_code=None,
                    attempts=attempts,
                    duration_ms=max(0, int((time.perf_counter() - started) * 1000)),
                    fingerprint=fingerprint,
                    truncated=truncated,
                )
                # Read calls and successful write calls are both idempotent within
                # this session by fingerprint/call-id. This prevents the model from
                # applying the same write twice after receiving a delayed result.
                self._cache[fingerprint] = result
                return result
            except asyncio.TimeoutError as exc:
                last_error = exc
                if spec.effect == "write" or attempts >= max_attempts:
                    raise ToolTimeoutError(
                        "Tool timed out; side-effecting calls are not replayed automatically"
                        if spec.effect == "write"
                        else "Tool timed out after bounded retries"
                    ) from exc
            except ToolReliabilityError:
                raise
            except Exception as exc:  # handler boundary: fail closed and bounded
                last_error = exc
                if spec.effect == "write" or attempts >= max_attempts:
                    raise ToolExecutionError(f"Tool execution failed: {exc.__class__.__name__}") from exc
                await asyncio.sleep(min(0.25 * attempts, 0.75))

        raise ToolExecutionError("Tool execution failed") from last_error


def error_result(call: ToolCall, exc: ToolReliabilityError) -> ToolResult:
    fp = call_fingerprint(call.name, call.arguments if isinstance(call.arguments, dict) else {"invalid": True})
    return ToolResult(
        call_id=call.call_id,
        name=call.name,
        status="error",
        output={"message": str(exc)[:1200]},
        error_code=getattr(exc, "code", "tool_error"),
        attempts=0,
        duration_ms=0,
        fingerprint=fp,
    )
