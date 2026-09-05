from __future__ import annotations

import base64
import json
from dataclasses import dataclass
from urllib.parse import urlsplit

import httpx

from app.services.image_runtime import ImageQAResult, ImageRuntimeError


class LocalVisionQA:
    """Optional local-only OpenAI-compatible vision QA endpoint.

    The endpoint must resolve to loopback by configuration. X1 never falls back to
    a remote vision API. This service is intentionally optional because semantic
    QA is expensive and should be enabled by policy/capacity, not silently.
    """

    def __init__(self, base_url: str, timeout_seconds: int = 45):
        parsed = urlsplit(base_url)
        if parsed.scheme not in {"http", "https"} or (parsed.hostname or "").lower() not in {"127.0.0.1", "localhost", "::1"}:
            raise ImageRuntimeError("Vision QA endpoint must be local loopback")
        self.url = base_url.rstrip("/") + "/v1/chat/completions"
        self.timeout = timeout_seconds

    def check(self, *, content: bytes, media_type: str, user_prompt: str, policy_superprompt: str = "") -> ImageQAResult:
        image_data = base64.b64encode(content).decode("ascii")
        instruction = (
            "Inspect this generated image for visible generation defects. Return JSON only with keys: "
            "passed:boolean, findings:[{code,severity,repairable,detail}]. Check malformed anatomy, duplicated limbs/objects, "
            "broken or nonsensical text, severe geometry artifacts, obvious face defects and composition failures. "
            "Do not infer private traits or identities."
        )
        if policy_superprompt.strip():
            instruction += " Apply this administrator image policy when judging safety/quality: " + policy_superprompt.strip()
        payload = {"model": "local-vision", "temperature": 0, "messages": [{"role": "user", "content": [
            {"type": "text", "text": instruction + "\nOriginal user request: " + user_prompt[:2000]},
            {"type": "image_url", "image_url": {"url": f"data:{media_type};base64,{image_data}"}},
        ]}]}
        try:
            with httpx.Client(timeout=self.timeout, trust_env=False) as client:
                response = client.post(self.url, json=payload)
                response.raise_for_status()
                data = response.json()
            text = data["choices"][0]["message"]["content"]
            parsed = json.loads(text)
            findings = parsed.get("findings", []) if isinstance(parsed, dict) else []
            if not isinstance(findings, list):
                raise ValueError("findings must be list")
            passed = bool(parsed.get("passed", not findings))
            return ImageQAResult(passed=passed, findings=findings[:20], metrics={"local_vision_qa": True})
        except Exception as exc:
            raise ImageRuntimeError(f"Local semantic vision QA failed: {exc}") from exc
