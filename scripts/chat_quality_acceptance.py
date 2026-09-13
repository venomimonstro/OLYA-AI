#!/usr/bin/env python3
from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import time
from pathlib import Path
from urllib.parse import urlsplit

import httpx

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_REPORT = ROOT / "backups" / "chat-quality-acceptance-latest.json"

CASES = (
    {
        "id": "finished-writing",
        "prompt": "Напиши готовое короткое сообщение клиенту: встречу переносим с четверга на пятницу из-за изменения графика, извинись и предложи согласовать удобное время. Только сообщение, без пояснений.",
        "mode": "fast",
        "must": ("пятниц", "врем"),
        "must_not": ("вот вариант", "конечно", "пояснение"),
        "max_chars": 900,
    },
    {
        "id": "fact-preservation",
        "prompt": "Напиши один короткий абзац для сайта только по этим фактам: сервис запущен в 2026 году, тариф стоит 300 рублей в месяц, данные хранятся на сервере проекта. Не добавляй достижения, гарантии и новые цифры.",
        "mode": "fast",
        "must": ("2026", "300", "сервер"),
        "must_not": ("100%", "лидер рынка", "№1", "миллион"),
        "max_chars": 1100,
    },
    {
        "id": "decision-quality",
        "prompt": "Сравни два режима для сервера 4 CPU и 6 ГБ RAM. А: два тяжёлых ответа параллельно и иногда swap. Б: один ответ, остальные ждут в ограниченной очереди. Главная цель — стабильность. Выбери вариант и дай конкретный вывод без дополнительных вопросов.",
        "mode": "work",
        "must": ("б", "очеред"),
        "must_not": ("нужно больше информации", "невозможно сказать"),
        "max_chars": 1500,
        "critic_expected": True,
    },
    {
        "id": "no-invented-metric",
        "prompt": "Известно только: у компании 12 офисов и есть бесплатная консультация. Какая у сайта конверсия в процентах? Не придумывай данные.",
        "mode": "work",
        "must_regex": r"(?i)(нет данных|неизвест|нельзя определить|не указан|недостаточно данных)",
        "must_not": ("10%", "15%", "20%", "25%", "30%"),
        "max_chars": 800,
    },
)


def _validate_transport(base_url: str) -> None:
    parsed = urlsplit(base_url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username or parsed.password:
        raise RuntimeError("Invalid quality acceptance target URL")
    if parsed.scheme != "https" and parsed.hostname.lower() not in {"127.0.0.1", "localhost", "::1"}:
        raise RuntimeError("Public quality acceptance requires HTTPS before sending Bearer token")


def _write_report(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", "utf-8")
    os.replace(tmp, path)


def _evaluate(text: str, case: dict) -> list[dict]:
    normalized = text.casefold()
    checks: list[dict] = []
    for value in case.get("must") or ():
        checks.append({"check": f"contains:{value}", "passed": str(value).casefold() in normalized})
    for value in case.get("must_not") or ():
        checks.append({"check": f"not_contains:{value}", "passed": str(value).casefold() not in normalized})
    pattern = case.get("must_regex")
    if pattern:
        checks.append({"check": "regex", "passed": re.search(str(pattern), text) is not None})
    max_chars = int(case.get("max_chars") or 0)
    if max_chars:
        checks.append({"check": "max_chars", "passed": len(text) <= max_chars})
    checks.append({"check": "non_empty", "passed": bool(text.strip())})
    return checks


async def run(base_url: str, tokens: list[str], timeout: float) -> dict:
    _validate_transport(base_url)
    quality_token = os.environ.get("X1_QUALITY_TOKEN", "").strip()
    pool = [quality_token] if quality_token else [value.strip() for value in tokens if value.strip()]
    if not pool:
        raise RuntimeError("Authenticated quality acceptance token is required")
    if not quality_token and len(pool) < len(CASES):
        raise RuntimeError("Quality acceptance needs at least one token per case when X1_QUALITY_TOKEN is not provided")

    results: list[dict] = []
    identities: dict[str, str] = {}
    async with httpx.AsyncClient(trust_env=False, timeout=timeout + 10) as client:
        for index, case in enumerate(CASES, start=1):
            token = pool[0] if quality_token else pool[index - 1]
            headers = {"Authorization": "Bearer " + token, "X-X1-Deadline-Ms": str(int(timeout * 1000))}
            if token not in identities:
                identity = await client.get(base_url.rstrip("/") + "/v1/auth/me", headers=headers)
                if identity.status_code != 200:
                    raise RuntimeError(f"Quality acceptance identity failed with HTTP {identity.status_code}")
                try:
                    identity_payload = identity.json()
                except ValueError as exc:
                    raise RuntimeError("Quality acceptance identity returned invalid JSON") from exc
                user_id = str(identity_payload.get("id") or "").strip()
                if not user_id:
                    raise RuntimeError("Quality acceptance identity returned no user id")
                identities[token] = user_id

            started = time.perf_counter()
            payload = {
                "messages": [{"role": "user", "content": case["prompt"]}],
                "mode": case["mode"],
                "verification": "auto",
                "web_mode": "off",
                "client_request_id": f"quality_accept_{int(time.time())}_{index:02d}",
            }
            response = await client.post(base_url.rstrip("/") + "/v1/chat", headers=headers, json=payload)
            latency_ms = int((time.perf_counter() - started) * 1000)
            if response.status_code != 200:
                results.append({"id": case["id"], "passed": False, "status": response.status_code, "latency_ms": latency_ms, "error": response.text[:500]})
                continue
            try:
                data = response.json()
            except ValueError:
                results.append({"id": case["id"], "passed": False, "status": 200, "latency_ms": latency_ms, "error": "invalid_json"})
                continue
            text = str(data.get("text") or "")
            checks = _evaluate(text, case)
            usage = data.get("usage") if isinstance(data.get("usage"), dict) else {}
            if case.get("critic_expected"):
                checks.append({"check": "critic_used", "passed": bool(usage.get("critic_used"))})
            passed = all(bool(row.get("passed")) for row in checks)
            results.append({
                "id": case["id"], "passed": passed, "status": 200, "latency_ms": latency_ms,
                "checks": checks,
                "quality_status": (data.get("quality") or {}).get("status") if isinstance(data.get("quality"), dict) else None,
                "critic_used": bool(usage.get("critic_used")), "repair_applied": bool(usage.get("repair_applied")),
                "mode": usage.get("mode"), "user_id": identities[token], "text_preview": text[:600],
            })

    critical_failed = [row["id"] for row in results if not row.get("passed")]
    return {
        "format": "x1-chat-quality-acceptance-v1",
        "target": base_url.rstrip("/"),
        "cases": len(results),
        "accounts_used": len(set(identities.values())),
        "distributed_across_load_accounts": not bool(quality_token),
        "passed_cases": sum(1 for row in results if row.get("passed")),
        "critical_failed": critical_failed,
        "passed": not critical_failed and len(results) == len(CASES),
        "results": results,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Run X1 real /v1/chat user-quality acceptance")
    parser.add_argument("--base-url", default=os.environ.get("X1_PRODUCTION_BASE_URL", "http://127.0.0.1:8000"))
    parser.add_argument("--timeout", type=float, default=180.0)
    parser.add_argument("--report", default=str(DEFAULT_REPORT))
    args = parser.parse_args()
    tokens = [value.strip() for value in os.environ.get("X1_LOAD_TOKENS", "").split(",") if value.strip()]
    try:
        result = asyncio.run(run(args.base_url, tokens, max(30.0, args.timeout)))
    except RuntimeError as exc:
        result = {"format": "x1-chat-quality-acceptance-v1", "target": args.base_url.rstrip("/"), "passed": False, "error": str(exc)}
    report = Path(args.report)
    if not report.is_absolute():
        report = ROOT / report
    _write_report(report, result)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result.get("passed") else 2


if __name__ == "__main__":
    raise SystemExit(main())
