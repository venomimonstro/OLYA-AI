from app.inference.client import LlamaClient
from app.qwen4b_runtime_patch import install_qwen4b_runtime_patch


def test_qwen4b_non_thinking_sampling_matches_upstream_profile():
    install_qwen4b_runtime_patch()
    sampling = LlamaClient._sampling(False)
    assert sampling == {
        "temperature": 0.7,
        "top_p": 0.8,
        "top_k": 20,
        "min_p": 0.0,
        "presence_penalty": 1.5,
        "repeat_penalty": 1.0,
    }


def test_qwen4b_thinking_sampling_matches_upstream_profile():
    install_qwen4b_runtime_patch()
    sampling = LlamaClient._sampling(True)
    assert sampling == {
        "temperature": 0.6,
        "top_p": 0.95,
        "top_k": 20,
        "min_p": 0.0,
        "presence_penalty": 1.5,
        "repeat_penalty": 1.0,
    }
