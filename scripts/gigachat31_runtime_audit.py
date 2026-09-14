#!/usr/bin/env python3
from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
from time import perf_counter

import httpx

ROOT = Path(__file__).resolve().parents[1]
EXPECTED_NAME = "GigaChat3.1-10B-A1.8B-Q4_K_M"
EXPECTED_FILE = "GigaChat3.1-10B-A1.8B-q4_K_M.gguf"
EXPECTED_SHA = "68a8732fb5cee04f83ebffd7924e15c534d4442c5a43d2ba9e2041fe310b8deb"
EXPECTED_BYTES = 6474702976


def static_audit() -> tuple[list[str], dict]:
    errors: list[str] = []
    manifest = json.loads((ROOT / "model-manifest.json").read_text("utf-8"))
    primary = manifest.get("primary") or {}
    checks = {
        "model_name": primary.get("model_name"),
        "filename": primary.get("filename"),
        "repository": primary.get("repository"),
        "revision": primary.get("revision"),
        "sha256": primary.get("sha256"),
        "size_bytes": primary.get("size_bytes"),
    }
    if checks["model_name"] != EXPECTED_NAME: errors.append("manifest_model_name")
    if checks["filename"] != EXPECTED_FILE: errors.append("manifest_filename")
    if checks["repository"] != "ai-sage/GigaChat3.1-10B-A1.8B-GGUF": errors.append("manifest_repository")
    if checks["sha256"] != EXPECTED_SHA: errors.append("manifest_sha256")
    if int(checks["size_bytes"] or 0) != EXPECTED_BYTES: errors.append("manifest_size")

    runtime = (ROOT / "app" / "gigachat31_runtime_patch.py").read_text("utf-8")
    bootstrap = (ROOT / "app" / "api" / "routes" / "__init__.py").read_text("utf-8")
    compose = (ROOT / "docker-compose.yml").read_text("utf-8")
    for marker, code in {
        "native_runtime_patch": "install_gigachat31_runtime_patch",
        "qwen_thinking_omitted": "chat_template_kwargs",
        "model_default": EXPECTED_FILE,
        "memory_default": "X1_LLAMA_MEMORY_LIMIT:-8g",
    }.items():
        haystack = runtime if marker in {"native_runtime_patch", "qwen_thinking_omitted"} else compose
        if marker == "native_runtime_patch":
            if code not in runtime or code not in bootstrap: errors.append(marker)
        elif marker == "qwen_thinking_omitted":
            # Native Giga payload must not add Qwen-specific fields.
            payload_pos = runtime.find("def payload")
            body = runtime[payload_pos:] if payload_pos >= 0 else runtime
            if code in body: errors.append(marker)
        elif code not in haystack:
            errors.append(marker)

    return errors, checks


async def live_probe() -> dict:
    base = str(os.getenv("X1_LLAMA_BASE_URL", "http://llama:8080")).rstrip("/")
    result: dict = {"base_url": base, "health": False, "model_visible": False, "samples": []}
    timeout = httpx.Timeout(45.0, connect=5.0)
    async with httpx.AsyncClient(timeout=timeout, trust_env=False) as client:
        try:
            health = await client.get(base + "/health")
            result["health"] = health.is_success
        except httpx.HTTPError as exc:
            result["health_error"] = f"{type(exc).__name__}: {exc}"
            return result
        try:
            models = await client.get(base + "/v1/models")
            if models.is_success:
                text = models.text
                result["model_visible"] = "GigaChat3.1" in text or EXPECTED_FILE in text
                result["models_excerpt"] = text[:600]
        except httpx.HTTPError as exc:
            result["models_error"] = f"{type(exc).__name__}: {exc}"

        tests = [
            ("Кто написал роман «Мастер и Маргарита»? Ответь одним предложением.", "булгаков"),
            ("Сколько будет 17 * 23? Ответь только числом.", "391"),
            ("Кратко объясни, зачем нужен HTTP.", ""),
        ]
        for prompt, expected in tests:
            started = perf_counter()
            try:
                response = await client.post(
                    base + "/v1/chat/completions",
                    json={
                        "model": "local",
                        "messages": [{"role": "user", "content": prompt}],
                        "max_tokens": 180,
                        "temperature": 0.2,
                        "top_p": 0.9,
                        "stream": False,
                    },
                )
                response.raise_for_status()
                payload = response.json()
                text = str((((payload.get("choices") or [{}])[0].get("message") or {}).get("content") or "")).strip()
                elapsed_ms = int((perf_counter() - started) * 1000)
                ok = bool(text) and (not expected or expected in text.casefold())
                result["samples"].append({"prompt": prompt, "ok": ok, "elapsed_ms": elapsed_ms, "answer": text[:500]})
            except (httpx.HTTPError, ValueError, TypeError, KeyError) as exc:
                result["samples"].append({"prompt": prompt, "ok": False, "error": f"{type(exc).__name__}: {exc}"})
    return result


async def main_async() -> int:
    errors, manifest = static_audit()
    live = await live_probe()
    if not live.get("health"): errors.append("llama_health")
    # /v1/models representation differs across llama.cpp releases, so a healthy
    # model with passing generation samples is sufficient even if its display id
    # is generic.
    samples = list(live.get("samples") or [])
    if not samples or not all(bool(row.get("ok")) for row in samples): errors.append("generation_samples")
    result = {
        "format": "olya-gigachat31-runtime-audit-v1",
        "status": "passed" if not errors else "failed",
        "errors": errors,
        "manifest": manifest,
        "runtime_env": {
            "model_name": os.getenv("X1_LLAMA_MODEL_NAME", ""),
            "model_file": os.getenv("X1_LLAMA_MODEL_FILE", ""),
            "context_tokens": os.getenv("X1_DEEP_CONTEXT_TOKENS", ""),
            "llama_memory_limit": os.getenv("X1_LLAMA_MEMORY_LIMIT", ""),
        },
        "live": live,
    }
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["status"] == "passed" else 2


def main() -> int:
    return asyncio.run(main_async())


if __name__ == "__main__":
    raise SystemExit(main())
