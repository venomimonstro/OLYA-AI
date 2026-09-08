from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
UI = (ROOT / "app" / "user_ui.py").read_text("utf-8")
CONVERSATIONS = (ROOT / "app" / "api" / "routes" / "conversations.py").read_text("utf-8")


def test_model_output_is_not_rendered_with_inner_html():
    # The trust boundary is structural DOM construction, never model-authored HTML.
    assert ".innerHTML" not in UI
    assert "renderMarkdown" in UI
    assert "document.createElement" in UI
    assert "textContent=code" in UI


def test_code_blocks_have_copy_action():
    assert "codewrap" in UI
    assert "navigator.clipboard.writeText" in UI
    assert "Копировать" in UI


def test_markdown_tables_are_rendered_as_dom_nodes():
    assert "document.createElement('table')" in UI
    assert "document.createElement(ri===0?'th':'td')" in UI
    assert "tablewrap" in UI


def test_sources_are_expanded_from_structured_research_results():
    assert "currentSources=(collected.sources||[]).slice(0,3)" in UI
    assert "attachSources(live.box,currentSources)" in UI
    assert "Источники · " in UI
    assert "noopener noreferrer" in UI
    assert "^https?:\\/\\/" in UI


def test_history_can_page_backward():
    assert "before: datetime | None = None" in CONVERSATIONS
    assert "Message.created_at < before" in CONVERSATIONS
    assert "Загрузить более ранние сообщения" in UI
    assert "messages?limit=100&before=" in UI
    assert "messages.scrollHeight-oldHeight" in UI


def test_progress_states_are_visible():
    for value in ("queued", "researching", "thinking", "generating", "verifying"):
        assert value in UI
    for label in ("В очереди", "Источники", "Обдумывание", "Ответ", "Проверка"):
        assert label in UI


def test_stop_and_retry_are_first_class_actions():
    assert "Останавливаю генерацию" in UI
    assert "stopActive" in UI
    assert "retryAfter" in UI
    assert "Повторить" in UI
    assert "()=>submit(text)" in UI


def test_overload_retry_after_is_preserved():
    assert "r.headers.get('Retry-After')" in UI
    assert "e.retryAfter" in UI


def test_mobile_layout_has_specific_regression_rules():
    assert "@media(max-width:760px)" in UI
    assert ".tablewrap{overflow:auto" in UI
    assert ".codewrap pre" in UI
    assert "inset:0 22% 0 0" in UI


def test_streaming_remains_incremental():
    assert "item.event==='token'" in UI
    assert "raw+=typeof d.text==='string'?d.text:''" in UI
    assert "renderMarkdown(live.bubble,raw)" in UI


def test_security_headers_remain_enabled():
    assert "Content-Security-Policy" in UI
    assert "default-src 'none'" in UI
    assert "X-Frame-Options" in UI
    assert "Permissions-Policy" in UI
