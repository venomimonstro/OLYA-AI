import httpx

from app.schemas.chat import ChatMessage


class LlamaUnavailable(RuntimeError):
    pass


class LlamaClient:
    def __init__(self, base_url: str, timeout_seconds: int = 180) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout = httpx.Timeout(timeout_seconds, connect=min(10.0, float(timeout_seconds)))
        # Reuse a tiny bounded keep-alive pool. X1 deliberately runs one local
        # inference slot, so an unbounded HTTP pool provides no throughput value.
        self._client = httpx.AsyncClient(
            timeout=self.timeout,
            trust_env=False,
            limits=httpx.Limits(max_connections=4, max_keepalive_connections=2, keepalive_expiry=30.0),
        )

    async def close(self) -> None:
        await self._client.aclose()

    async def health(self) -> bool:
        try:
            response = await self._client.get(f"{self.base_url}/health", timeout=5.0)
            return response.is_success
        except httpx.HTTPError:
            return False

    async def chat(
        self,
        messages: list[ChatMessage],
        *,
        max_tokens: int,
        reasoning: bool,
    ) -> str:
        payload = {
            "model": "local",
            "messages": [message.model_dump() for message in messages],
            "max_tokens": max_tokens,
            "stream": False,
            "temperature": 0.3 if reasoning else 0.2,
            # Qwen3 is a hybrid thinking model. llama.cpp b10380 supports these
            # OpenAI-compatible request controls. Fast/Work must not silently use
            # the response budget for hidden reasoning. Deep keeps reasoning on,
            # while reasoning_format separates the trace from final content.
            "chat_template_kwargs": {"enable_thinking": bool(reasoning)},
            "reasoning_format": "deepseek" if reasoning else "none",
        }
        if not reasoning:
            payload["reasoning_effort"] = "none"
        try:
            response = await self._client.post(f"{self.base_url}/v1/chat/completions", json=payload)
            response.raise_for_status()
            data = response.json()
            content = data["choices"][0]["message"]["content"]
            if not isinstance(content, str) or not content.strip():
                raise ValueError("empty or invalid model content")
            return content.strip()
        except (httpx.HTTPError, ValueError, KeyError, IndexError, TypeError) as exc:
            raise LlamaUnavailable("local llama.cpp inference is unavailable or returned invalid data") from exc
