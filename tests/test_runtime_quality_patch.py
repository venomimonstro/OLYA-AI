import asyncio

from app.inference import router as inference_router
from app.inference.client import LlamaClient
from app.runtime_quality_patch import VisibleStreamFilter, clean_visible_output, install_runtime_quality_patch, instant_reply
from app.schemas.chat import ChatMessage


def test_russian_output_removes_hidden_reasoning_and_accidental_cjk():
    text = "<think>внутреннее рассуждение</think>Привет! Како можно能帮我？"
    cleaned = clean_visible_output("привет", text)
    assert "think" not in cleaned.casefold()
    assert "能" not in cleaned
    assert "帮" not in cleaned
    assert "我" not in cleaned
    assert "？" not in cleaned


def test_stream_filter_handles_split_think_tags_before_browser():
    stream = VisibleStreamFilter("привет")
    output = "".join(
        [
            stream.feed("<thi"),
            stream.feed("nk>секрет"),
            stream.feed("</th"),
            stream.feed("ink>Привет 能帮我？"),
            stream.finish(),
        ]
    )
    assert output == "Привет?"


def test_explicit_cjk_task_is_not_destroyed():
    text = "你好"
    assert clean_visible_output("переведи слово привет на китайский", text) == text


def test_short_auto_query_routes_fast_but_complex_request_does_not():
    install_runtime_quality_patch()
    simple = inference_router.choose_route("Что такое SEO?", "auto", 4096, 4096)
    complex_task = inference_router.choose_route("Проведи SEO аудит сайта и разработай стратегию", "auto", 4096, 4096)
    assert simple.mode == "fast"
    assert simple.reasoning is False
    assert complex_task.mode in {"work", "deep"}


def test_greeting_shortcut_bypasses_llama_network():
    install_runtime_quality_patch()
    assert instant_reply("привет") == "Привет! Чем могу помочь?"

    async def run():
        client = LlamaClient("http://127.0.0.1:1", timeout_seconds=1)
        pieces = []

        async def sink(text: str):
            pieces.append(text)

        try:
            result = await client.generate(
                [ChatMessage(role="user", content="привет")],
                max_tokens=128,
                reasoning=False,
                on_token=sink,
            )
        finally:
            await client.close()
        return result, pieces

    result, pieces = asyncio.run(run())
    assert result.text == "Привет! Чем могу помочь?"
    assert result.ttft_ms == 0
    assert pieces == ["Привет! Чем могу помочь?"]
