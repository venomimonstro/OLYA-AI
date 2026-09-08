from __future__ import annotations

from pathlib import Path

from scripts.model_regression_lab import aggregate_results, compare, evaluate_checks, load_corpus

ROOT = Path(__file__).resolve().parents[1]


def test_golden_corpus_has_all_product_categories_and_critical_cases():
    corpus = load_corpus(ROOT / "regression" / "golden_corpus.json")
    categories = {case["category"] for case in corpus["cases"]}
    assert {"factual", "writing", "code", "rag", "long_chat", "tools", "scope", "russian_language"} <= categories
    assert sum(1 for case in corpus["cases"] if case.get("critical")) >= 6
    assert len(corpus["cases"]) >= 12


def test_deterministic_checks_do_not_need_an_llm_judge():
    checks = [
        {"type": "contains_all", "values": ["Москва"]},
        {"type": "not_contains", "values": ["London"]},
        {"type": "regex", "pattern": r"Москва"},
        {"type": "cyrillic_ratio", "minimum": 0.5},
    ]
    result = evaluate_checks("Москва — столица России", checks)
    assert all(row["passed"] for row in result)


def test_json_contract_check_is_strict():
    checks = [{"type": "json_object", "required_keys": ["city", "active"]}]
    assert evaluate_checks('{"city":"Москва","active":true}', checks)[0]["passed"] is True
    assert evaluate_checks('```json\n{"city":"Москва","active":true}\n```', checks)[0]["passed"] is False


def _report(*, pass_rate=1.0, mean_score=1.0, critical_failed=None, ttft=1000, latency=5000, tokens=100, tool=1.0):
    return {
        "aggregate": {
            "pass_rate": pass_rate,
            "mean_score": mean_score,
            "critical_failed": critical_failed or [],
            "p95_ttft_ms": ttft,
            "p95_latency_ms": latency,
            "mean_output_tokens": tokens,
            "tool_success_rate": tool,
            "by_category": {"factual": {"pass_rate": pass_rate, "mean_score": mean_score}},
        }
    }


def test_candidate_with_critical_failure_is_blocked():
    result = compare(_report(critical_failed=["json-contract"]), _report())
    assert result["passed"] is False
    assert any(item["metric"] == "critical_cases" for item in result["failures"])


def test_latency_regression_is_blocked_but_small_noise_is_allowed():
    baseline = _report(ttft=1000, latency=5000)
    assert compare(_report(ttft=1200, latency=6000), baseline)["passed"] is True
    blocked = compare(_report(ttft=3000, latency=10000), baseline)
    assert blocked["passed"] is False
    metrics = {item["metric"] for item in blocked["failures"]}
    assert "p95_ttft_ms" in metrics
    assert "p95_latency_ms" in metrics


def test_token_waste_regression_is_blocked():
    blocked = compare(_report(tokens=200), _report(tokens=100))
    assert blocked["passed"] is False
    assert any(item["metric"] == "mean_output_tokens" for item in blocked["failures"])


def test_tool_success_cannot_regress():
    blocked = compare(_report(tool=0.5), _report(tool=1.0))
    assert blocked["passed"] is False
    assert any(item["metric"] == "tool_success_rate" for item in blocked["failures"])


def test_aggregate_tracks_category_and_critical_failures():
    rows = [
        {"id": "a", "category": "factual", "critical": True, "passed": True, "score": 1.0, "ttft_ms": 100, "latency_ms": 300, "output_tokens": 10},
        {"id": "b", "category": "tools", "critical": True, "passed": False, "score": 0.0, "ttft_ms": None, "latency_ms": 400, "output_tokens": 5, "tool_success": False},
    ]
    agg = aggregate_results(rows)
    assert agg["pass_rate"] == 0.5
    assert agg["critical_failed"] == ["b"]
    assert agg["tool_success_rate"] == 0.0


def test_full_regression_suite_validates_corpus_before_pytest():
    text = (ROOT / "scripts" / "run_full_regression.py").read_text("utf-8")
    assert "scripts.model_regression_lab" in text
    assert "--validate-only" in text


def test_runtime_component_acceptance_runs_model_regression_as_required_gate():
    text = (ROOT / "scripts" / "component_acceptance.py").read_text("utf-8")
    assert "_run_model_regression" in text
    assert "scripts.model_regression_lab" in text
    assert "model_prompt_regression" in text
    assert "model-regression-baseline.json" in text
    assert "if not regression_ok" in text


def test_regression_snapshot_fingerprints_model_and_prompt_runtime():
    text = (ROOT / "scripts" / "model_regression_lab.py").read_text("utf-8")
    for path in (
        "model-manifest.json",
        "app/inference/client.py",
        "app/inference/router.py",
        "app/services/context.py",
        "app/services/quality.py",
        "app/services/scope_lock.py",
        "app/services/conditional_verification.py",
        "app/services/tool_reliability.py",
    ):
        assert path in text
    assert "template_runtime_sha256" in text


def test_baseline_is_not_silently_overwritten():
    text = (ROOT / "scripts" / "model_regression_lab.py").read_text("utf-8")
    assert "--record-baseline" in text
    assert "--force-baseline" in text
    assert "Baseline already exists" in text
    assert "Accepted regression baseline is missing" in text
