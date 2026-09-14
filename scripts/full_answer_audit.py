#!/usr/bin/env python3
from __future__ import annotations

import asyncio
import json

from scripts.answer_pipeline_audit import audit as static_audit
from scripts.answer_pipeline_live_probe import probe as web_probe
from scripts.inference_latency_live_probe import probe as inference_probe


async def run() -> dict:
    static = static_audit()
    web = await web_probe()
    inference = await inference_probe()
    sections = {
        "routing_and_policy": static,
        "live_web_and_facts": web,
        "local_inference": inference,
    }
    failed = [name for name, value in sections.items() if value.get("status") not in {"passed"}]
    return {
        "format": "olya-full-answer-audit-v1",
        "status": "passed" if not failed else "degraded",
        "degraded_sections": failed,
        "sections": sections,
    }


def main() -> int:
    result = asyncio.run(run())
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["status"] == "passed" else 2


if __name__ == "__main__":
    raise SystemExit(main())
