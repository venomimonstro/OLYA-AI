from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from app.inference.client import LlamaClient, LlamaUnavailable
from app.services.tool_reliability import (
    ToolBudgetError,
    ToolLoopError,
    ToolReliabilityError,
    ToolReplayBlockedError,
    ToolSession,
    error_result,
)


class ToolAgentError(RuntimeError):
    pass


@dataclass(frozen=True)
class ToolAgentOutcome:
    text: str
    steps: int
    tool_calls: int
    tool_errors: int
    stopped_reason: str
    trace: tuple[dict[str, Any], ...]


def _assistant_message(text: str, calls) -> dict[str, Any]:
    message: dict[str, Any] = {"role": "assistant", "content": text or None}
    if calls:
        message["tool_calls"] = [
            {
                "id": call.call_id,
                "type": "function",
                "function": {
                    "name": call.name,
                    "arguments": json.dumps(call.arguments, ensure_ascii=False, separators=(",", ":")),
                },
            }
            for call in calls
        ]
    return message


async def run_tool_agent(
    llama: LlamaClient,
    *,
    messages: list[dict[str, Any]],
    session: ToolSession,
    max_steps: int = 8,
    max_tool_errors: int = 2,
    final_max_tokens: int = 1200,
) -> ToolAgentOutcome:
    """Run a serial, bounded model/tool loop.

    The ToolRegistry supplied by the caller is the complete capability allowlist.
    Tool errors are shown back to the model only in bounded form so it may correct
    a malformed call without entering an infinite recovery loop.
    """
    if not messages:
        raise ValueError("Tool agent requires messages")
    max_steps = max(1, min(int(max_steps), 12))
    max_tool_errors = max(0, min(int(max_tool_errors), 4))
    conversation = list(messages)
    trace: list[dict[str, Any]] = []
    total_calls = 0
    tool_errors = 0

    for step in range(1, max_steps + 1):
        try:
            turn = await llama.tool_turn(
                conversation,
                tools=session.registry.schemas(),
                max_tokens=final_max_tokens,
                reasoning=False,
            )
        except LlamaUnavailable as exc:
            raise ToolAgentError("Model tool-selection turn failed") from exc

        conversation.append(_assistant_message(turn.text, turn.tool_calls))
        trace.append({
            "step": step,
            "assistant_text": turn.text[:2000],
            "requested_tools": [call.name for call in turn.tool_calls],
            "output_tokens": turn.output_tokens,
            "generation_ms": turn.generation_ms,
        })

        if not turn.tool_calls:
            return ToolAgentOutcome(
                text=turn.text,
                steps=step,
                tool_calls=total_calls,
                tool_errors=tool_errors,
                stopped_reason="final_answer",
                trace=tuple(trace),
            )

        for call in turn.tool_calls:
            total_calls += 1
            try:
                result = await session.execute(call)
            except (ToolLoopError, ToolBudgetError, ToolReplayBlockedError) as exc:
                trace.append({
                    "step": step,
                    "tool": call.name,
                    "call_id": call.call_id,
                    "status": "blocked",
                    "error_code": exc.code,
                })
                return ToolAgentOutcome(
                    text="",
                    steps=step,
                    tool_calls=total_calls,
                    tool_errors=tool_errors + 1,
                    stopped_reason=exc.code,
                    trace=tuple(trace),
                )
            except ToolReliabilityError as exc:
                tool_errors += 1
                result = error_result(call, exc)
            conversation.append(result.as_tool_message())
            trace.append({
                "step": step,
                "tool": call.name,
                "call_id": call.call_id,
                "status": result.status,
                "error_code": result.error_code,
                "attempts": result.attempts,
                "duration_ms": result.duration_ms,
                "truncated": result.truncated,
            })
            if tool_errors > max_tool_errors:
                return ToolAgentOutcome(
                    text="",
                    steps=step,
                    tool_calls=total_calls,
                    tool_errors=tool_errors,
                    stopped_reason="tool_error_budget_exhausted",
                    trace=tuple(trace),
                )

    return ToolAgentOutcome(
        text="",
        steps=max_steps,
        tool_calls=total_calls,
        tool_errors=tool_errors,
        stopped_reason="max_steps_exhausted",
        trace=tuple(trace),
    )
