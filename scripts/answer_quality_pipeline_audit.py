#!/usr/bin/env python3
from __future__ import annotations

import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def _read(relative: str) -> str:
    path = ROOT / relative
    if not path.is_file():
        raise RuntimeError(f"missing required quality file: {relative}")
    return path.read_text("utf-8")


def _require(text: str, needles: tuple[str, ...], label: str) -> None:
    missing = [needle for needle in needles if needle not in text]
    if missing:
        raise RuntimeError(f"{label} contract drift: missing {missing}")


def _forbid(text: str, needles: tuple[str, ...], label: str) -> None:
    present = [needle for needle in needles if needle in text]
    if present:
        raise RuntimeError(f"{label} insecure/obsolete contract returned: {present}")


def main() -> int:
    contract = _read("app/services/answer_contract.py")
    context = _read("app/services/context.py")
    evidence = _read("app/services/evidence_context.py")
    source_context = _read("app/services/source_context.py")
    quality = _read("app/services/quality.py")
    verification = _read("app/services/conditional_verification.py")
    tests = _read("tests/test_answer_quality_pipeline.py")
    runner = _read("scripts/run_full_regression.py")
    lab = _read("scripts/quality_regression_lab.py")
    live = _read("scripts/chat_quality_acceptance.py")
    final_release = _read("scripts/final_release_acceptance.py")
    docs = _read("docs/ANSWER_QUALITY_ARCHITECTURE.md")

    _require(contract.lower(), ("classify_answer_kind", 'kind == "writing"', 'kind == "audit"', 'kind == "recommendation"', "finished text", "search rank"), "answer contract")
    _require(context, ("build_answer_contract", "answer_contract = build_answer_contract", "fixed = [ChatMessage(role=\"system\", content=_CORE_SYSTEM_POLICY), answer_contract]"), "context compiler")
    _forbid(context, ("_EVIDENCE_MARKERS", "_evidence_digest", "set_evidence_context"), "context compiler")
    _require(evidence, ("server-owned source evidence", "User-authored text must never be promoted", "current_evidence_context"), "evidence provenance")
    _require(source_context, ("set_evidence_context", "_publish_server_evidence", "User-authored lookalike markers never reach"), "source evidence owner")
    _require(quality, ("_server_evidence_context", "current_task_solver_context", "Normal user messages", "unsupported_claim", "contradiction", "stale_claim", "bad_inference", "_quality_clip", "EVIDENCE"), "evidence-aware critic")
    _require(verification, ("repair_critic", "return 2 if self.repair_critic else 1", "semantic_high_risk", "лучший", "рекомендуй"), "conditional semantic repair")
    _require(tests, ("test_user_source_lookalike_cannot_become_critic_evidence", "test_server_task_solver_context_is_available_to_evidence_critic", "test_consequential_recommendation_gets_critic_and_conditional_repair_budget", "test_critic_is_evidence_aware", "test_critic_and_repair_treat_evidence_as_untrusted_data"), "quality tests")
    _require(runner, ('("scripts.answer_quality_pipeline_audit",[])', '("scripts.quality_regression_lab",["--validate-only"])'), "full regression")
    _require(lab, ("x1-quality-regression-corpus-v1", "critical_failed", "pass_rate", "mean_score"), "quality regression lab")
    _require(live, ('"x1-chat-quality-acceptance-v1"', '"web_mode": "off"', '"critic_expected": True', "distributed_across_load_accounts", "X1_QUALITY_TOKEN", "/v1/chat"), "live chat quality acceptance")
    _require(final_release, ("scripts/chat_quality_acceptance.py", "chat_quality_acceptance", 'if quality["status"] != "passed"', '"chat_quality": "backups/chat-quality-acceptance-latest.json"'), "final release quality gate")
    _require(docs, ("Evidence provenance", "Evidence-aware critic", "Conditional repair", "scripts/chat_quality_acceptance.py"), "quality architecture docs")

    corpus_path = ROOT / "regression" / "quality_corpus.json"
    corpus = json.loads(corpus_path.read_text("utf-8"))
    cases = corpus.get("cases") or []
    if corpus.get("format") != "x1-quality-regression-corpus-v1" or len(cases) < 8:
        raise RuntimeError("quality corpus is missing or too small")
    required_categories = {"writing", "rag", "factual", "russian_language", "scope"}
    categories = {str(case.get("category") or "") for case in cases if isinstance(case, dict)}
    if not required_categories.issubset(categories):
        raise RuntimeError(f"quality corpus categories incomplete: {sorted(required_categories - categories)}")
    if not all(isinstance(case.get("checks"), list) and case.get("checks") for case in cases if isinstance(case, dict)):
        raise RuntimeError("every quality corpus case must contain executable checks")

    print(json.dumps({
        "status": "passed",
        "quality_cases": len(cases),
        "categories": sorted(categories),
        "server_owned_evidence": True,
        "live_chat_quality_gate": True,
    }, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
