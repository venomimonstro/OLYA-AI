from app.inference.client import LlamaClient


def test_qwen36_non_thinking_sampling_matches_supported_upstream_profile():
    sampling = LlamaClient._sampling(False)
    assert sampling == {
        "temperature": 0.7,
        "top_p": 0.8,
        "top_k": 20,
        "min_p": 0.0,
        "presence_penalty": 1.5,
        "repeat_penalty": 1.0,
    }


def test_qwen36_thinking_sampling_is_not_legacy_overconstrained_profile():
    sampling = LlamaClient._sampling(True)
    assert sampling["temperature"] == 1.0
    assert sampling["top_p"] == 0.95
    assert sampling["top_k"] == 20
    assert sampling["presence_penalty"] == 1.5
    assert sampling["repeat_penalty"] == 1.0
