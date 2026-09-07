import httpx

from app.schemas.chat import ChatMessage


class LlamaUnavailable(RuntimeError):
    pass


class LlamaClient:
    def __init__(self, base_url: str, timeout_seconds: int = 180) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout = httpx.Timeout(timeout_seconds, connect=min(10.0, float(timeout_seconds)))
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

    @staticmethod
    def _sampling(reasoning: bool) -> dict:
        # Qwen3.6 is sensitive to over-constrained sampling. These are the
        # upstream-recommended general presets, deliberately kept explicit so a
        # future model migration cannot silently inherit unsuitable parameters.
        if reasoning:
            return {
                "temperature": 1.0,
                "top_p": 0.95,
                "top_k": 20,
                "min_p": 0.0,
                "presence_penalty": 1.5,
                "repeat_penalty": 1.0,
            }
        return {
            "temperature": 0.7,
            "top_p": 0.8,
            "top_k": 20,
            "min_p": 0.0,
            "presence_penalty": 1.5,
            "repeat_penalty": 1.0,
        }

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
            **self._sampling(reasoning),
            # Qwen3.6 thinks by default. OLYA explicitly owns this switch so Fast
            # and simple Work requests do not burn the output/CPU budget on hidden
            # reasoning, while analytical Work/Deep can opt in.
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
