#!/usr/bin/env python3
from __future__ import annotations

import argparse
import asyncio
from collections import Counter, defaultdict
from dataclasses import replace
from datetime import datetime, timezone
import json
import math
from pathlib import Path
import random
from uuid import uuid4

import httpx
from sqlalchemy import delete

from app.db import SessionLocal
from app.models import User
from app.services.auth import create_session
from scripts.real_user_live_simulation_100 import (
    _CONTEXT_IDS,
    _MEMORY_IDS,
    _SEEDS,
    _delete_conversation,
    _evaluate,
    _stream_chat,
    _surface_probe,
)
from scripts.real_user_population_10000 import POPULATION, SyntheticSession
from scripts.real_user_scenarios import SCENARIOS

_BASE = {row.id: row for row in SCENARIOS}


def _select_population(limit: int, *, seed: int, full: bool, longform_limit: int) -> list[SyntheticSession]:
    if full or limit >= len(POPULATION):
        return list(POPULATION)
    limit = max(100, min(int(limit), len(POPULATION)))
    rng = random.Random(seed)
    groups: defaultdict[tuple[str, str], list[SyntheticSession]] = defaultdict(list)
    for row in POPULATION:
        groups[(row.persona, row.style)].append(row)
    keys = sorted(groups)
    per_group = limit // len(keys)
    remainder = limit % len(keys)
    selected: list[SyntheticSession] = []
    for index, key in enumerate(keys):
        rows = list(groups[key])
        rng.shuffle(rows)
        take = per_group + (1 if index < remainder else 0)
        selected.extend(rows[:take])

    # Long-form generations are intentionally expensive on the single CPU slot.
    # Keep a representative sample unless the operator explicitly asks --full.
    long_rows = [row for row in selected if row.category == "longform"]
    if len(long_rows) > longform_limit:
        keep = set(row.id for row in rng.sample(long_rows, longform_limit))
        selected = [row for row in selected if row.category != "longform" or row.id in keep]
        missing = limit - len(selected)
        candidates = [row for row in POPULATION if row.category != "longform" and row not in selected]
        rng.shuffle(candidates)
        selected.extend(candidates[:missing])
    rng.shuffle(selected)
    return selected[:limit]


def _create_temp_accounts(count: int, run_id: str) -> list[dict]:
    rows: list[dict] = []
    with SessionLocal() as db:
        for index in range(count):
            user = User(
                email=f"olya-bench-{run_id}-{index:04d}@invalid.local",
                password_hash="benchmark-no-password-login",
                display_name=f"OLYA benchmark {index + 1}",
                is_active=True,
                is_admin=False,
            )
            db.add(user)
            db.flush()
            token, _session = create_session(db, user)
            rows.append({"id": user.id, "token": token, "used": 0})
    return rows


def _cleanup_temp_accounts(ids: list[str]) -> None:
    if not ids:
        return
    with SessionLocal() as db:
        db.execute(delete(User).where(User.id.in_(ids)))
        db.commit()


def _account_for(accounts: list[dict], cost: int, cursor: int) -> tuple[dict, int]:
    for offset in range(len(accounts)):
        index = (cursor + offset) % len(accounts)
        row = accounts[index]
        if int(row["used"]) + cost <= 20:
            row["used"] = int(row["used"]) + cost
            return row, (index + 1) % len(accounts)
    raise RuntimeError("benchmark account pool exhausted")


def _summary_rows(observations: list[dict], field: str) -> dict:
    groups: defaultdict[str, list[dict]] = defaultdict(list)
    for row in observations:
        groups[str(row.get(field) or "unknown")].append(row)
    result = {}
    for key, rows in sorted(groups.items()):
        good = sum(1 for item in rows if item.get("ok"))
        ttft = sorted(int(item["ttft_ms"]) for item in rows if item.get("ttft_ms") is not None)
        total = sorted(int(item["total_ms"]) for item in rows if item.get("total_ms") is not None)
        def p(values: list[int], q: float):
            if not values: return None
            return values[min(len(values) - 1, max(0, int(round((len(values) - 1) * q))))]
        result[key] = {
            "runs": len(rows),
            "pass_rate": round(good / len(rows), 4) if rows else 0,
            "ttft_p50_ms": p(ttft, 0.50),
            "ttft_p95_ms": p(ttft, 0.95),
            "total_p50_ms": p(total, 0.50),
            "total_p95_ms": p(total, 0.95),
        }
    return result


def _markdown(report: dict) -> str:
    lines = [
        "# OLYA AI — симуляция реальных пользователей",
        "",
        f"Дата: {report['created_at']}",
        f"Синтетическая панель: {report['population']} сессий",
        f"Live-прогон: {report['live_runs']} сессий",
        f"Успешно: {report['passed']} / {report['live_runs']} ({report['pass_rate']:.1%})",
        "",
        "## Основные метрики",
        "",
        f"- TTFT p50: {report['metrics'].get('ttft_p50_ms')} мс",
        f"- TTFT p95: {report['metrics'].get('ttft_p95_ms')} мс",
        f"- Полное время p50: {report['metrics'].get('total_p50_ms')} мс",
        f"- Полное время p95: {report['metrics'].get('total_p95_ms')} мс",
        f"- Ошибок транспорта/runtime: {report['issue_counts'].get('transport_or_runtime_error', 0)}",
        f"- Ответов без источников при обязательном web: {report['issue_counts'].get('web_answer_without_sources', 0)}",
        "",
        "## Самые частые проблемы",
        "",
    ]
    for key, count in list(report["issue_counts"].items())[:20]:
        lines.append(f"- {key}: {count}")
    lines += ["", "## Примеры провалов", ""]
    for row in report["failure_samples"][:30]:
        lines += [
            f"### {row['session_id']} · {row['persona_label']} · {row['style']}",
            f"Запрос: {row['prompt']}",
            f"Проблемы: {', '.join(row['issues'])}",
            f"Ответ: {row.get('answer_excerpt', '')[:500]}",
            "",
        ]
    return "\n".join(lines)


async def _run(args) -> dict:
    selected = _select_population(args.limit, seed=args.seed, full=args.full, longform_limit=args.longform_limit)
    estimated_requests = sum(1 + (1 if row.base_id in _SEEDS else 0) for row in selected)
    account_count = max(1, math.ceil(estimated_requests / 18))
    run_id = uuid4().hex[:10]
    accounts = _create_temp_accounts(account_count, run_id)
    created_ids = [row["id"] for row in accounts]
    base_url = args.base_url.rstrip("/")
    observations: list[dict] = []
    surface_results: list[dict] = []
    cursor = 0

    limits = httpx.Limits(max_connections=4, max_keepalive_connections=4)
    try:
        async with httpx.AsyncClient(trust_env=False, limits=limits) as client:
            # Verify the real site/auth/chat shell for every temporary account.
            for account in accounts:
                headers = {"Authorization": "Bearer " + account["token"], "Accept": "text/event-stream"}
                surface_results.append(await _surface_probe(client, base_url, headers))

            for number, case in enumerate(selected, start=1):
                seed_cost = 1 if case.base_id in _SEEDS else 0
                account, cursor = _account_for(accounts, 1 + seed_cost, cursor)
                headers = {"Authorization": "Bearer " + account["token"], "Accept": "text/event-stream"}
                base = _BASE[case.base_id]
                cleanup_ids: set[str] = set()
                seed_conversation: str | None = None

                if case.base_id in _MEMORY_IDS:
                    # Isolate memory recall from unrelated synthetic sessions on the same account.
                    await client.delete(base_url + "/v1/memory", headers=headers, timeout=10.0)

                seed_prompt = _SEEDS.get(case.base_id)
                if seed_prompt:
                    seed_run = await _stream_chat(
                        client, base_url, headers, prompt=seed_prompt,
                        timeout_seconds=90.0, label=case.id + "_seed",
                    )
                    seed_result = seed_run.get("result") or {}
                    seed_conversation = str(seed_result.get("conversation_id") or "") or None
                    if seed_conversation:
                        cleanup_ids.add(seed_conversation)

                conversation_id = seed_conversation if case.base_id in _CONTEXT_IDS else None
                timeout_seconds = max(45.0, case.max_total_ms / 1000 + 30.0)
                run = await _stream_chat(
                    client, base_url, headers,
                    prompt=case.prompt,
                    conversation_id=conversation_id,
                    timeout_seconds=timeout_seconds,
                    label=case.id,
                )
                result = run.get("result") or {}
                result_conversation = str(result.get("conversation_id") or "") or None
                if result_conversation:
                    cleanup_ids.add(result_conversation)

                eval_case = replace(base, prompt=case.prompt)
                _issues, observation = _evaluate(eval_case, run)
                observation.update({
                    "session_id": case.id,
                    "base_id": case.base_id,
                    "persona": case.persona,
                    "persona_label": case.persona_label,
                    "segment": case.segment,
                    "style": case.style,
                })
                observations.append(observation)

                for conversation in cleanup_ids:
                    await _delete_conversation(client, base_url, headers, conversation)
                if case.base_id in _MEMORY_IDS:
                    await client.delete(base_url + "/v1/memory", headers=headers, timeout=10.0)

                print(
                    f"[{number:04d}/{len(selected):04d}] {case.id} {case.persona}/{case.style}: "
                    f"{'OK' if observation['ok'] else 'FAIL'} ttft={observation.get('ttft_ms')}ms "
                    f"total={observation.get('total_ms')}ms web={observation.get('web_used')} "
                    f"issues={','.join(observation.get('issues') or [])}",
                    flush=True,
                )
    finally:
        _cleanup_temp_accounts(created_ids)

    passed = sum(1 for row in observations if row.get("ok"))
    issue_counter: Counter[str] = Counter()
    for row in observations:
        for issue in row.get("issues") or []:
            issue_counter[str(issue).split(":", 1)[0]] += 1

    ttft = sorted(int(row["ttft_ms"]) for row in observations if row.get("ttft_ms") is not None)
    total = sorted(int(row["total_ms"]) for row in observations if row.get("total_ms") is not None)
    def p(values: list[int], q: float):
        if not values: return None
        return values[min(len(values) - 1, max(0, int(round((len(values) - 1) * q))))]

    report = {
        "format": "olya-real-user-live-10000-v1",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "population": 10_000,
        "live_runs": len(observations),
        "full_live": bool(args.full),
        "temporary_accounts": account_count,
        "passed": passed,
        "failed": len(observations) - passed,
        "pass_rate": passed / len(observations) if observations else 0.0,
        "surface_pass_rate": sum(1 for row in surface_results if row.get("passed")) / len(surface_results) if surface_results else 0.0,
        "metrics": {
            "ttft_p50_ms": p(ttft, 0.50), "ttft_p95_ms": p(ttft, 0.95), "ttft_p99_ms": p(ttft, 0.99),
            "total_p50_ms": p(total, 0.50), "total_p95_ms": p(total, 0.95), "total_p99_ms": p(total, 0.99),
        },
        "issue_counts": dict(issue_counter.most_common()),
        "by_category": _summary_rows(observations, "category"),
        "by_persona": _summary_rows(observations, "persona"),
        "by_style": _summary_rows(observations, "style"),
        "failure_samples": [row for row in observations if not row.get("ok")][:100],
        "observations": observations if args.include_all else [],
    }
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description="Stratified live simulation over the 10,000-session OLYA user panel")
    parser.add_argument("--limit", type=int, default=500, help="Live sample size; static routing still covers all 10,000")
    parser.add_argument("--full", action="store_true", help="Actually run all 10,000 live chat sessions (very slow on one CPU inference slot)")
    parser.add_argument("--longform-limit", type=int, default=5, help="Max long-form generations in sampled mode")
    parser.add_argument("--seed", type=int, default=20260915)
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument("--include-all", action="store_true", help="Keep every observation in the JSON report")
    args = parser.parse_args()
    if args.full:
        args.limit = 10_000
        args.longform_limit = 10_000
    result = asyncio.run(_run(args))
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    json_path = Path(f"/tmp/olya-real-user-10000-{stamp}.json")
    md_path = Path(f"/tmp/olya-real-user-10000-{stamp}.md")
    json_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), "utf-8")
    md_path.write_text(_markdown(result), "utf-8")
    print(json.dumps({
        "format": result["format"], "population": result["population"], "live_runs": result["live_runs"],
        "pass_rate": result["pass_rate"], "metrics": result["metrics"], "issue_counts": result["issue_counts"],
        "json_report": str(json_path), "markdown_report": str(md_path),
    }, ensure_ascii=False, indent=2))
    return 0 if result["pass_rate"] >= 0.90 else 2


if __name__ == "__main__":
    raise SystemExit(main())
