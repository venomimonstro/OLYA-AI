#!/usr/bin/env python3
from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
from time import perf_counter

import httpx

from app.utility_chat import utility_reply

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
    dockerfile_path = ROOT / "Dockerfile"
    if not dockerfile_path.exists():
        dockerfile_path = ROOT / "build-inputs" / "Dockerfile"
    dockerfile = dockerfile_path.read_text("utf-8") if dockerfile_path.exists() else ""
    start_path = ROOT / "scripts" / "start_app.sh"
    start_script = start_path.read_text("utf-8") if start_path.exists() else ""
    if "install_gigachat31_runtime_patch" not in runtime or "install_gigachat31_runtime_patch" not in bootstrap:
        errors.append("native_runtime_patch")
    payload_pos = runtime.find("def payload")
    body = runtime[payload_pos:] if payload_pos >= 0 else runtime
    if "chat_template_kwargs" in body or "reasoning_format" in body or "thinking_budget_tokens" in body:
        errors.append("qwen_payload_leak")
    if '"temperature": 0.0' not in runtime:
        errors.append("gigachat_not_deterministic")
    if "start_app.sh" not in dockerfile:
        errors.append("startup_wrapper_missing")
    if "scripts.warm_local_llm" not in start_script or "&" not in start_script:
        errors.append("background_warmup_missing")

    calc = utility_reply("Сколько будет 17 * 23? Ответь только числом.")
    checks["calculator"] = calc.text if calc else None
    if calc is None or calc.kind != "calculator" or calc.text != "391":
        errors.append("calculator_utility")
    return errors, checks


def _telemetry(payload: dict, elapsed_ms: int) -> dict:
    usage = payload.get("usage") or {}
    timings = payload.get("timings") or {}
    completion_tokens = int(usage.get("completion_tokens") or timings.get("predicted_n") or 0)
    tps = float(timings.get("predicted_per_second") or 0.0)
    if not tps and completion_tokens > 0 and elapsed_ms > 0:
        tps = completion_tokens / (elapsed_ms / 1000.0)
    prompt_tps = float(timings.get("prompt_per_second") or 0.0)
    return {"completion_tokens": completion_tokens, "tokens_per_second": round(tps, 3), "prompt_tokens_per_second": round(prompt_tps, 3)}


async def _nonstream_sample(client: httpx.AsyncClient, base: str, prompt: str, expected: str, max_tokens: int) -> dict:
    started = perf_counter()
    try:
        response = await client.post(base + "/v1/chat/completions", json={"model": "local", "messages": [{"role": "user", "content": prompt}], "max_tokens": max_tokens, "temperature": 0, "stream": False}, timeout=httpx.Timeout(60.0, connect=5.0))
        response.raise_for_status(); payload = response.json()
        text = str((((payload.get("choices") or [{}])[0].get("message") or {}).get("content") or "")).strip()
        elapsed_ms = int((perf_counter() - started) * 1000)
        return {"prompt": prompt, "ok": bool(text) and (not expected or expected in text.casefold()), "elapsed_ms": elapsed_ms, "answer": text[:500], **_telemetry(payload, elapsed_ms)}
    except (httpx.HTTPError, ValueError, TypeError, KeyError) as exc:
        return {"prompt": prompt, "ok": False, "error": f"{type(exc).__name__}: {exc}"}


async def _stream_perf(client: httpx.AsyncClient, base: str) -> dict:
    started = perf_counter(); first = None; text_parts: list[str] = []; tokens = 0; reported_tps = 0.0
    request = {"model": "local", "messages": [{"role": "user", "content": "Объясни в 4 коротких предложениях, зачем нужен HTTP."}], "max_tokens": 96, "temperature": 0, "stream": True}
    try:
        async with client.stream("POST", base + "/v1/chat/completions", json=request, timeout=httpx.Timeout(60.0, connect=5.0)) as response:
            response.raise_for_status()
            async for raw in response.aiter_lines():
                line = raw.strip()
                if not line or line.startswith(":"): continue
                body = line[5:].strip() if line.startswith("data:") else line
                if body == "[DONE]": break
                try: data = json.loads(body)
                except json.JSONDecodeError: continue
                choice = ((data.get("choices") or [{}])[0] or {}); delta = choice.get("delta") or {}; piece = delta.get("content") if isinstance(delta, dict) else ""
                if isinstance(piece, str) and piece:
                    if first is None: first = perf_counter()
                    text_parts.append(piece)
                usage = data.get("usage") or {}; timings = data.get("timings") or {}
                tokens = int(usage.get("completion_tokens") or timings.get("predicted_n") or tokens or 0); reported_tps = float(timings.get("predicted_per_second") or reported_tps or 0.0)
        end = perf_counter(); elapsed = end - started; text = "".join(text_parts).strip(); tps = reported_tps or (tokens / elapsed if tokens and elapsed > 0 else 0.0)
        return {"ok": bool(text), "ttft_ms": int(((first or end) - started) * 1000), "elapsed_ms": int(elapsed * 1000), "completion_tokens": tokens, "tokens_per_second": round(tps, 3), "answer": text[:500]}
    except (httpx.HTTPError, ValueError, TypeError) as exc:
        return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}


async def live_probe() -> dict:
    base = str(os.getenv("X1_LLAMA_BASE_URL", "http://llama:8080")).rstrip("/")
    result: dict = {"base_url": base, "health": False, "model_visible": False, "samples": []}
    async with httpx.AsyncClient(trust_env=False) as client:
        try:
            health = await client.get(base + "/health", timeout=5.0); result["health"] = health.is_success
        except httpx.HTTPError as exc:
            result["health_error"] = f"{type(exc).__name__}: {exc}"; return result
        try:
            models = await client.get(base + "/v1/models", timeout=5.0)
            if models.is_success:
                text = models.text; result["model_visible"] = "GigaChat3.1" in text or EXPECTED_FILE in text; result["models_excerpt"] = text[:600]
        except httpx.HTTPError as exc:
            result["models_error"] = f"{type(exc).__name__}: {exc}"
        result["samples"].append(await _nonstream_sample(client, base, "Кто написал роман «Мастер и Маргарита»? Ответь только фамилией.", "булгаков", 24))
        result["samples"].append(await _nonstream_sample(client, base, "Столица Франции? Ответь только названием города.", "париж", 24))
        result["model_math_diagnostic"] = await _nonstream_sample(client, base, "Сколько будет 17 * 23? Ответь только числом.", "391", 16)
        result["stream_performance"] = await _stream_perf(client, base)
    return result


async def main_async() -> int:
    errors, manifest = static_audit(); live = await live_probe()
    if not live.get("health"): errors.append("llama_health")
    samples = list(live.get("samples") or [])
    if not samples or not all(bool(row.get("ok")) for row in samples): errors.append("generation_correctness")
    perf = live.get("stream_performance") or {}
    if not perf.get("ok"): errors.append("stream_generation")
    result = {"format": "olya-gigachat31-runtime-audit-v5", "status": "passed" if not errors else "failed", "errors": errors, "manifest": manifest, "runtime_env": {"model_name": os.getenv("X1_LLAMA_MODEL_NAME", ""), "model_file": os.getenv("X1_LLAMA_MODEL_FILE", ""), "context_tokens": os.getenv("X1_DEEP_CONTEXT_TOKENS", ""), "llama_memory_limit": os.getenv("X1_LLAMA_MEMORY_LIMIT", ""), "threads": os.getenv("X1_LLAMA_THREADS", ""), "batch_threads": os.getenv("X1_LLAMA_THREADS_BATCH", "")}, "live": live}
    print(json.dumps(result, ensure_ascii=False, indent=2)); return 0 if result["status"] == "passed" else 2


def main() -> int:
    return asyncio.run(main_async())


if __name__ == "__main__":
    raise SystemExit(main())
