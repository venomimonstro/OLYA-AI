import httpx

from app.schemas.chat import ChatMessage


class LlamaUnavailable(RuntimeError):
    pass


class LlamaClient:
    def __init__(self, base_url: str, timeout_seconds: int = 180) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout = httpx.Timeout(timeout_seconds)

    async def health(self) -> bool:
        try:
            async with httpx.AsyncClient(timeout=5) as client:
                response = await client.get(f"{self.base_url}/health")
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
            "messages": [m.model_dump() for m in messages],
            "max_tokens": max_tokens,
            "stream": False,
            "temperature": 0.3 if reasoning else 0.2,
        }
        # llama.cpp supports an OpenAI-compatible endpoint. Model-specific
        # reasoning controls will be added after Sprint 0 benchmarks prove the exact template.
        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                response = await client.post(f"{self.base_url}/v1/chat/completions", json=payload)
                response.raise_for_status()
                data = response.json()
                return data["choices"][0]["message"]["content"]
        except (httpx.HTTPError, KeyError, IndexError, TypeError) as exc:
            raise LlamaUnavailable("local llama.cpp inference is unavailable or returned invalid data") from exc
