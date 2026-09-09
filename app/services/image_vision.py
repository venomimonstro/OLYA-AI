from __future__ import annotations

import base64
import json

import httpx

from app.services.image_runtime import ImageQAResult, ImageRuntimeError
from app.services.image_vision_endpoint import VisionEndpointError, validate_vision_endpoint


class LocalVisionQA:
    """Local/private OpenAI-compatible vision QA endpoint.

    Development may use loopback. Docker production may use exactly the configured
    internal llama.cpp origin. User images are never sent to arbitrary remote
    hosts and HTTP environment proxies are ignored.
    """

    def __init__(
        self,
        base_url: str,
        timeout_seconds: int = 45,
        *,
        trusted_internal_base_url: str = "",
        model_name: str = "local-vision",
    ):
        try:
            endpoint = validate_vision_endpoint(base_url, trusted_internal_base_url=trusted_internal_base_url)
        except VisionEndpointError as exc:
            raise ImageRuntimeError(str(exc)) from exc
        self.url = endpoint.base_url + "/v1/chat/completions"
        self.timeout = max(5, int(timeout_seconds))
        self.model_name = str(model_name or "local-vision")[:160]
        self.endpoint_source = endpoint.source

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
        payload = {"model": self.model_name, "temperature": 0, "messages": [{"role": "user", "content": [
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
            return ImageQAResult(
                passed=passed,
                findings=findings[:20],
                metrics={"local_vision_qa": True, "vision_endpoint": self.endpoint_source},
            )
        except Exception as exc:
            raise ImageRuntimeError(f"Local semantic vision QA failed: {type(exc).__name__}") from exc
