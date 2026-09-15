#!/usr/bin/env python3
from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import statistics
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from time import perf_counter
from uuid import uuid4

import httpx

from scripts.real_user_scenarios import SCENARIOS, UserScenario

_VENDOR_RE = re.compile(r"\b(?:gigachat|qwen|llama\.cpp|llamacpp|ai-sage|deepseek)\b", re.I)
_CJK_RE = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff]")
_CONTEXT_FAILURE_RE = re.compile(r"(?:не\s+вижу\s+(?:предыдущ|контекст)|нет\s+(?:предыдущ|контекст)|не\s+знаю,?\s+что\s+было\s+раньше)", re.I)
_MEMORY_EXPECTED = {
    "U074": ("кратк", "вывод"),
    "U075": ("вывод",),
    "U076": ("postgresql",),
    "U080": ("структур", "вывод"),
}
_SEEDS = {
    "U071": "Назови три преимущества автоматического резервного копирования сайта.",
    "U072": "Предложи классический вариант меню интернет-магазина: каталог, акции, доставка, контакты.",
    "U073": "Напиши короткий план SEO-аудита из 5 пунктов.",
    "U074": "Запомни, что я предпочитаю, чтобы ответ начинался с краткого вывода.",
    "U075": "Запомни, что я прошу сложные ответы давать сначала кратким выводом, затем деталями.",
    "U076": "Запомни: для тестового проекта мы решили использовать PostgreSQL.",
    "U077": "Напиши нумерованный список: 1. Проверить аналитику. 2. Проверить рекламу. 3. Проверить скорость сайта.",
    "U078": "Начни статью про SEO интернет-магазина: введение и первые два раздела. В конце напиши [ПРОДОЛЖЕНИЕ].",
    "U079": "Сделай шаблон ответа: сначала краткий вывод, затем 3 пункта, затем следующий шаг.",
    "U080": "Запомни, что мои предпочтения по формату ответов: структурировано, без воды, сначала краткий вывод.",
}
_CONTEXT_IDS = {"U071", "U072", "U073", "U077", "U078", "U079"}
_MEMORY_IDS = {"U074", "U075", "U076", "U080"}


def _quantile(values: list[int], q: float) -> int | None:
    if not values:
        return None
    rows = sorted(values)
    index = min(len(rows) - 1, max(0, int(round((len(rows) - 1) * q))))
    return int(rows[index])


def _token() -> str:
    return str(os.getenv("OLYA_BENCH_TOKEN", "") or "").strip()


async def _authenticate(client: httpx.AsyncClient, base_url: str) -> str:
    token = _token()
    if token:
        return token
    email = str(os.getenv("OLYA_BENCH_EMAIL", "") or "").strip()
    password = str(os.getenv("OLYA_BENCH_PASSWORD", "") or "")
    if not email or not password:
        raise RuntimeError("Set OLYA_BENCH_TOKEN or OLYA_BENCH_EMAIL + OLYA_BENCH_PASSWORD")
    response = await client.post(base_url + "/v1/auth/login", json={"email": email, "password": password}, timeout=15.0)
    response.raise_for_status()
    token = str((response.json() or {}).get("access_token") or "")
    if not token:
        raise RuntimeError("Login succeeded without access_token; check email verification/settings")
    return token


async def _surface_probe(client: httpx.AsyncClient, base_url: str, headers: dict[str, str]) -> dict:
    checks: dict[str, object] = {}
    app = await client.get(base_url + "/app", timeout=10.0)
    body = app.text
    checks["app_status"] = app.status_code
    checks["thinking_indicator"] = "olya-thinking-row" in body and "olya-thinking-dots" in body
    checks["separate_stop"] = "olya-stop-button" in body
    checks["chat_library"] = "OLYA_CHAT_LIBRARY_V2" in body
    me = await client.get(base_url + "/v1/auth/me", headers=headers, timeout=10.0)
    checks["auth_me_status"] = me.status_code
    conversations = await client.get(base_url + "/v1/conversations?limit=1&all_projects=true", headers=headers, timeout=10.0)
    checks["conversations_status"] = conversations.status_code
    memory = await client.get(base_url + "/v1/memory", headers=headers, timeout=10.0)
    checks["memory_status"] = memory.status_code
    checks["passed"] = all((
        app.status_code == 200,
        bool(checks["thinking_indicator"]),
        bool(checks["separate_stop"]),
        me.status_code == 200,
        conversations.status_code == 200,
        memory.status_code == 200,
    ))
    return checks


async def _stream_chat(
    client: httpx.AsyncClient,
    base_url: str,
    headers: dict[str, str],
    *,
    prompt: str,
    conversation_id: str | None = None,
    timeout_seconds: float = 390.0,
    label: str = "bench",
) -> dict:
    request_id = f"bench_{label}_{uuid4().hex[:20]}"
    payload = {
        "messages": [{"role": "user", "content": prompt}],
        "mode": "auto",
        "verification": "auto",
        "web_mode": "auto",
        "client_request_id": request_id,
    }
    if conversation_id:
        payload["conversation_id"] = conversation_id

    started = perf_counter()
    first_status_ms: int | None = None
    first_token_ms: int | None = None
    partial = ""
    result: dict | None = None
    terminal_error: dict | None = None
    current_event = ""
    timeout = httpx.Timeout(timeout_seconds, connect=5.0)
    try:
        async with client.stream("POST", base_url + "/v1/chat/stream", headers=headers, json=payload, timeout=timeout) as response:
            response.raise_for_status()
            async for raw in response.aiter_lines():
                line = raw.strip()
                if not line:
                    continue
                if line.startswith("event:"):
                    current_event = line[6:].strip()
                    continue
                if not line.startswith("data:"):
                    continue
                try:
                    data = json.loads(line[5:].strip())
                except json.JSONDecodeError:
                    continue
                elapsed_ms = int((perf_counter() - started) * 1000)
                if current_event == "status" and first_status_ms is None:
                    first_status_ms = elapsed_ms
                elif current_event == "token":
                    text = str(data.get("text") or "")
                    if text:
                        if first_token_ms is None:
                            first_token_ms = elapsed_ms
                        partial += text
                elif current_event == "replace":
                    partial = str(data.get("text") or "")
                    if partial and first_token_ms is None:
                        first_token_ms = elapsed_ms
                elif current_event == "result":
                    result = data
                    break
                elif current_event in {"error", "cancelled"}:
                    terminal_error = data
                    break
    except Exception as exc:
        terminal_error = {"detail": f"{type(exc).__name__}: {exc}"}

    total_ms = int((perf_counter() - started) * 1000)
    return {
        "request_id": request_id,
        "first_status_ms": first_status_ms,
        "first_token_ms": first_token_ms,
        "total_ms": total_ms,
        "partial_text": partial,
        "result": result,
        "error": terminal_error,
    }


async def _delete_conversation(client: httpx.AsyncClient, base_url: str, headers: dict[str, str], conversation_id: str | None) -> None:
    if not conversation_id:
        return
    try:
        await client.delete(base_url + "/v1/conversations/" + conversation_id, headers=headers, timeout=10.0)
    except Exception:
        pass


def _evaluate(case: UserScenario, run: dict) -> tuple[list[str], dict]:
    issues: list[str] = []
    result = run.get("result") or {}
    answer = str(result.get("text") or run.get("partial_text") or "").strip()
    task = result.get("task_execution") or {}
    usage = result.get("usage") or {}
    web_used = bool(task.get("web_used"))
    sources = task.get("sources") or []
    ttft = run.get("first_token_ms")
    total = int(run.get("total_ms") or 0)
    compiled_chars = int(usage.get("compiled_message_chars") or 0)

    if run.get("error"):
        issues.append("transport_or_runtime_error")
    if not answer:
        issues.append("empty_answer")
    if answer and len(answer) < case.min_chars:
        issues.append(f"answer_too_short:{len(answer)}<{case.min_chars}")
    if len(answer) > case.max_chars:
        issues.append(f"answer_too_long:{len(answer)}>{case.max_chars}")
    low = answer.casefold()
    for term in case.must_include:
        if term.casefold() not in low:
            issues.append(f"missing_required:{term}")
    for term in _MEMORY_EXPECTED.get(case.id, ()):
        if term.casefold() not in low:
            issues.append(f"memory_not_recalled:{term}")
    if case.id in _CONTEXT_IDS and _CONTEXT_FAILURE_RE.search(answer):
        issues.append("conversation_context_lost")
    if case.expect_web is not None and web_used != case.expect_web:
        issues.append(f"web_mismatch:{web_used}!={case.expect_web}")
    if case.expect_web and case.expected_path == "web" and not sources:
        issues.append("web_answer_without_sources")
    if case.category == "web_advice" and int(task.get("fetched_sources") or 0) < 1:
        issues.append("advice_did_not_read_any_page")
    if ttft is None:
        issues.append("no_visible_ttft")
    elif ttft > case.max_ttft_ms:
        issues.append(f"ttft_slow:{ttft}>{case.max_ttft_ms}")
    if total > case.max_total_ms:
        issues.append(f"total_slow:{total}>{case.max_total_ms}")
    if _VENDOR_RE.search(answer):
        issues.append("internal_model_leak")
    if _CJK_RE.search(answer) and re.search(r"[А-Яа-яЁё]", case.prompt):
        issues.append("unexpected_cjk_in_russian_answer")
    if str(result.get("model") or "") not in {"", "OLYA AI"}:
        issues.append("public_model_brand_invalid")
    if int(usage.get("verification_extra_inferences") or 0) != 0 or bool(usage.get("critic_used")) or bool(usage.get("repair_applied")):
        issues.append("unexpected_extra_llm_pass")

    if case.expected_path == "atomic" and compiled_chars > 900:
        issues.append(f"atomic_prompt_bloated:{compiled_chars}")
    elif case.expected_path == "direct" and compiled_chars > 1800:
        issues.append(f"direct_prompt_bloated:{compiled_chars}")
    elif case.expected_path == "web" and compiled_chars > 7000:
        issues.append(f"web_prompt_bloated:{compiled_chars}")

    source_urls = {str(item.get("url") or "") for item in sources if isinstance(item, dict)}
    for url in re.findall(r"https?://[^\s)>\]]+", answer):
        clean = url.rstrip(".,;:")
        if source_urls and clean not in source_urls:
            issues.append("answer_contains_unverified_url")
            break

    observation = {
        "id": case.id,
        "category": case.category,
        "prompt": case.prompt,
        "expected_path": case.expected_path,
        "ok": not issues,
        "issues": issues,
        "ttft_ms": ttft,
        "total_ms": total,
        "answer_chars": len(answer),
        "compiled_chars": compiled_chars,
        "mode": usage.get("mode"),
        "web_used": web_used,
        "sources": len(sources),
        "fetched_sources": int(task.get("fetched_sources") or 0),
        "task_kind": task.get("kind"),
        "answer_excerpt": answer[:900],
        "error": run.get("error"),
    }
    return issues, observation


async def _run(args) -> dict:
    base_url = str(os.getenv("OLYA_BENCH_BASE_URL", "http://127.0.0.1:8000") or "").rstrip("/")
    limits = httpx.Limits(max_connections=2, max_keepalive_connections=2)
    async with httpx.AsyncClient(trust_env=False, limits=limits) as client:
        token = await _authenticate(client, base_url)
        headers = {"Authorization": "Bearer " + token, "Accept": "text/event-stream"}
        surface = await _surface_probe(client, base_url, headers)

        selected = list(SCENARIOS)
        if args.category:
            wanted = set(args.category)
            selected = [case for case in selected if case.category in wanted]
        if args.skip_longform:
            selected = [case for case in selected if case.category != "longform"]
        selected = selected[: args.limit]

        observations: list[dict] = []
        for number, case in enumerate(selected, start=1):
            seed_conversation: str | None = None
            cleanup_ids: set[str] = set()
            seed = _SEEDS.get(case.id)
            if seed:
                seed_run = await _stream_chat(
                    client, base_url, headers, prompt=seed,
                    timeout_seconds=90.0, label=case.id + "_seed",
                )
                seed_result = seed_run.get("result") or {}
                seed_conversation = str(seed_result.get("conversation_id") or "") or None
                if seed_conversation:
                    cleanup_ids.add(seed_conversation)

            actual_conversation = seed_conversation if case.id in _CONTEXT_IDS else None
            timeout_seconds = max(45.0, case.max_total_ms / 1000 + 30.0)
            run = await _stream_chat(
                client, base_url, headers, prompt=case.prompt,
                conversation_id=actual_conversation,
                timeout_seconds=timeout_seconds, label=case.id,
            )
            result = run.get("result") or {}
            result_conversation = str(result.get("conversation_id") or "") or None
            if result_conversation:
                cleanup_ids.add(result_conversation)
            _issues, observation = _evaluate(case, run)
            observations.append(observation)
            print(
                f"[{number:03d}/{len(selected):03d}] {case.id} {case.category}: "
                f"{'OK' if observation['ok'] else 'FAIL'} ttft={observation['ttft_ms']}ms "
                f"total={observation['total_ms']}ms chars={observation['answer_chars']} "
                f"web={observation['web_used']} issues={','.join(observation['issues']) or '-'}",
                flush=True,
            )
            if not args.keep_chats:
                for conversation_id in cleanup_ids:
                    await _delete_conversation(client, base_url, headers, conversation_id)
            await asyncio.sleep(0.05)

    by_category: dict[str, list[dict]] = defaultdict(list)
    for row in observations:
        by_category[row["category"]].append(row)
    category_summary = {}
    for category, rows in sorted(by_category.items()):
        ttfts = [int(row["ttft_ms"]) for row in rows if row["ttft_ms"] is not None]
        totals = [int(row["total_ms"]) for row in rows]
        category_summary[category] = {
            "users": len(rows),
            "passed": sum(1 for row in rows if row["ok"]),
            "pass_rate": round(sum(1 for row in rows if row["ok"]) / max(1, len(rows)), 3),
            "ttft_p50_ms": _quantile(ttfts, 0.50),
            "ttft_p95_ms": _quantile(ttfts, 0.95),
            "total_p50_ms": _quantile(totals, 0.50),
            "total_p95_ms": _quantile(totals, 0.95),
        }

    issue_counts = Counter(issue.split(":", 1)[0] for row in observations for issue in row["issues"])
    passed = sum(1 for row in observations if row["ok"])
    pass_rate = passed / max(1, len(observations))
    report = {
        "format": "olya-real-user-live-simulation-v1",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "status": "passed" if pass_rate >= 0.95 and surface.get("passed") else "degraded",
        "base_url": base_url,
        "users": len(observations),
        "passed": passed,
        "failed": len(observations) - passed,
        "pass_rate": round(pass_rate, 3),
        "surface": surface,
        "issue_counts": dict(issue_counts.most_common()),
        "category_summary": category_summary,
        "failures": [row for row in observations if not row["ok"]],
        "observations": observations,
    }
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description="Simulate 100 realistic OLYA AI user sessions through the live chat API")
    parser.add_argument("--limit", type=int, default=100, choices=range(1, 101), metavar="1..100")
    parser.add_argument("--category", action="append", default=[])
    parser.add_argument("--skip-longform", action="store_true")
    parser.add_argument("--keep-chats", action="store_true")
    parser.add_argument("--output", default="")
    args = parser.parse_args()

    report = asyncio.run(_run(args))
    root = Path(os.getenv("X1_DATA_ROOT", "/app/data")) / "audits"
    root.mkdir(parents=True, exist_ok=True)
    output = Path(args.output) if args.output else root / "real_user_simulation_100.json"
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({
        "format": report["format"],
        "status": report["status"],
        "users": report["users"],
        "passed": report["passed"],
        "failed": report["failed"],
        "pass_rate": report["pass_rate"],
        "issue_counts": report["issue_counts"],
        "category_summary": report["category_summary"],
        "report_path": str(output),
    }, ensure_ascii=False, indent=2))
    return 0 if report["status"] == "passed" else 2


if __name__ == "__main__":
    raise SystemExit(main())
