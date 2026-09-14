from __future__ import annotations

import re

from app.inference.client import LlamaClient, LlamaGeneration

_THINK_BLOCK = re.compile(r"<think\b[^>]*>.*?</think\s*>", re.IGNORECASE | re.DOTALL)
_REASONING_BLOCK = re.compile(r"<(?:reasoning|analysis)\b[^>]*>.*?</(?:reasoning|analysis)\s*>", re.IGNORECASE | re.DOTALL)


def strip_private_reasoning(text: str) -> str:
    value = str(text or "")
    value = _THINK_BLOCK.sub("", value)
    value = _REASONING_BLOCK.sub("", value)
    # Defensive cleanup for an unterminated private block. Never expose its
    # contents merely because the backend stopped before writing a closing tag.
    for opener in ("<think", "<reasoning", "<analysis"):
        pos = value.casefold().find(opener)
        if pos >= 0:
            value = value[:pos]
    return value.strip()


class _PrivateReasoningStream:
    """Incremental filter for accidental XML-style thinking in content chunks."""

    def __init__(self, sink):
        self.sink = sink
        self.buffer = ""
        self.private = False

    async def feed(self, chunk: str) -> None:
        if not chunk:
            return
        self.buffer += chunk
        out: list[str] = []
        while self.buffer:
            lower = self.buffer.casefold()
            if self.private:
                closes = [(lower.find(tag), tag) for tag in ("</think>", "</reasoning>", "</analysis>")]
                closes = [(pos, tag) for pos, tag in closes if pos >= 0]
                if not closes:
                    # Keep only enough tail to recognize a closing tag split
                    # across chunks; all preceding private content is discarded.
                    self.buffer = self.buffer[-16:]
                    break
                pos, tag = min(closes, key=lambda item: item[0])
                self.buffer = self.buffer[pos + len(tag):]
                self.private = False
                continue

            openings = [(lower.find(tag), tag) for tag in ("<think", "<reasoning", "<analysis")]
            openings = [(pos, tag) for pos, tag in openings if pos >= 0]
            if openings:
                pos, _tag = min(openings, key=lambda item: item[0])
                if pos:
                    out.append(self.buffer[:pos])
                gt = self.buffer.find(">", pos)
                if gt < 0:
                    self.buffer = self.buffer[pos:]
                    break
                self.buffer = self.buffer[gt + 1:]
                self.private = True
                continue

            # Hold a short suffix so an opening tag split between chunks is not
            # emitted prematurely. This adds negligible latency (<10 chars).
            if len(self.buffer) <= 12:
                break
            out.append(self.buffer[:-12])
            self.buffer = self.buffer[-12:]
            break

        visible = "".join(out)
        if visible and self.sink is not None:
            await self.sink(visible)

    async def finish(self) -> None:
        if not self.private and self.buffer and self.sink is not None:
            cleaned = strip_private_reasoning(self.buffer)
            if cleaned:
                await self.sink(cleaned)
        self.buffer = ""


def install_reasoning_privacy_patch() -> None:
    current = LlamaClient.generate
    if getattr(current, "_olya_reasoning_privacy", False):
        return

    async def guarded(self, messages, *, max_tokens: int, reasoning: bool, on_token=None):
        if on_token is None:
            result = await current(self, messages, max_tokens=max_tokens, reasoning=reasoning, on_token=None)
            clean = strip_private_reasoning(result.text)
            return LlamaGeneration(
                text=clean,
                ttft_ms=result.ttft_ms,
                output_tokens=result.output_tokens,
                tokens_per_second=result.tokens_per_second,
                generation_ms=result.generation_ms,
            )

        stream = _PrivateReasoningStream(on_token)
        result = await current(self, messages, max_tokens=max_tokens, reasoning=reasoning, on_token=stream.feed)
        await stream.finish()
        clean = strip_private_reasoning(result.text)
        return LlamaGeneration(
            text=clean,
            ttft_ms=result.ttft_ms,
            output_tokens=result.output_tokens,
            tokens_per_second=result.tokens_per_second,
            generation_ms=result.generation_ms,
        )

    guarded._olya_reasoning_privacy = True  # type: ignore[attr-defined]
    LlamaClient.generate = guarded
