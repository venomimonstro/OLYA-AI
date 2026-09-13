from __future__ import annotations

import re
from time import perf_counter

from app.schemas.chat import ChatMessage

_BASE_SYSTEM_TEXT = (
    "Ты X1, рабочий AI-ассистент. Отвечай на языке последнего сообщения пользователя. "
    "Если пользователь пишет по-русски, отвечай естественным грамотным русским языком и не смешивай его "
    "с китайскими, японскими или корейскими символами, если пользователь явно не просит перевод, цитату или работу "
    "с таким письмом. Не выводи скрытые рассуждения, chain-of-thought, служебные рассуждения и теги <think>, "
    "<analysis> или <reasoning>. Пользователь должен видеть только готовый полезный ответ. "
    "На простое приветствие отвечай кратко. На содержательную задачу отвечай по существу, без служебных комментариев."
)

_GREETING_RU = re.compile(
    r"^\s*(?:привет|здравствуй|здравствуйте|доброе\s+утро|добрый\s+день|добрый\s+вечер|хай)\s*[!?.…]*\s*$",
    re.IGNORECASE,
)
_GREETING_EN = re.compile(r"^\s*(?:hi|hello|hey)\s*[!?.…]*\s*$", re.IGNORECASE)
_CYRILLIC_RE = re.compile(r"[А-Яа-яЁё]")
_LATIN_RE = re.compile(r"[A-Za-z]")
_CJK_RE = re.compile(
    "["
    "\u3040-\u30ff"  # Hiragana/Katakana
    "\u3400-\u4dbf"  # CJK extension A
    "\u4e00-\u9fff"  # CJK unified
    "\uf900-\ufaff"  # CJK compatibility
    "\uac00-\ud7af"  # Hangul syllables
    "]"
)
_CJK_REQUEST_MARKERS = (
    "китай", "япон", "корей", "иероглиф", "кандзи", "хираган", "катакан", "хангыл",
    "chinese", "japanese", "korean", "hanzi", "kanji", "hiragana", "katakana", "hangul",
)
_FULLWIDTH_TRANSLATION = str.maketrans({"，": ",", "。": ".", "！": "!", "？": "?", "：": ":", "；": ";"})
_HIDDEN_TAGS = ("think", "analysis", "reasoning")


def base_system_message() -> ChatMessage:
    return ChatMessage(role="system", content=_BASE_SYSTEM_TEXT)


def instant_reply(user_text: str) -> str | None:
    if _GREETING_RU.fullmatch(user_text or ""):
        return "Привет! Чем могу помочь?"
    if _GREETING_EN.fullmatch(user_text or ""):
        return "Hello! How can I help?"
    return None


def _latest_user_text(messages: list[ChatMessage]) -> str:
    return next((message.content for message in reversed(messages) if message.role == "user"), "")


def _forbid_cjk(user_text: str) -> bool:
    normalized = (user_text or "").casefold()
    if any(marker in normalized for marker in _CJK_REQUEST_MARKERS):
        return False
    cyrillic = len(_CYRILLIC_RE.findall(user_text or ""))
    latin = len(_LATIN_RE.findall(user_text or ""))
    return cyrillic >= 2 and cyrillic >= max(2, latin // 2)


def _clean_visible_chars(text: str, *, forbid_cjk: bool) -> str:
    if not text:
        return ""
    value = text.translate(_FULLWIDTH_TRANSLATION)
    if forbid_cjk:
        value = _CJK_RE.sub("", value)
    value = re.sub(r"[ \t]+([,.;:!?])", r"\1", value)
    value = re.sub(r" {2,}", " ", value)
    return value


def clean_visible_output(user_text: str, text: str) -> str:
    value = str(text or "")
    for tag in _HIDDEN_TAGS:
        value = re.sub(
            rf"<{tag}\b[^>]*>.*?</{tag}\s*>",
            "",
            value,
            flags=re.IGNORECASE | re.DOTALL,
        )
        # Fail closed on an unterminated hidden-reasoning block.
        value = re.sub(rf"<{tag}\b[^>]*>.*$", "", value, flags=re.IGNORECASE | re.DOTALL)
        value = re.sub(rf"</?{tag}\b[^>]*>", "", value, flags=re.IGNORECASE)
    value = _clean_visible_chars(value, forbid_cjk=_forbid_cjk(user_text))
    return value.strip()


def _partial_suffix_length(value: str, tokens: tuple[str, ...]) -> int:
    lower = value.casefold()
    best = 0
    for token in tokens:
        folded = token.casefold()
        upper = min(len(lower), len(folded) - 1)
        for size in range(1, upper + 1):
            if lower.endswith(folded[:size]):
                best = max(best, size)
    return best


class VisibleStreamFilter:
    """Suppress hidden reasoning and accidental CJK before bytes reach the browser."""

    def __init__(self, user_text: str) -> None:
        self.user_text = user_text
        self.forbid_cjk = _forbid_cjk(user_text)
        self._carry = ""
        self._hidden: str | None = None

    @property
    def tokens(self) -> tuple[str, ...]:
        opens = tuple(f"<{tag}>" for tag in _HIDDEN_TAGS)
        closes = tuple(f"</{tag}>" for tag in _HIDDEN_TAGS)
        return opens + closes

    def feed(self, chunk: str) -> str:
        data = self._carry + str(chunk or "")
        self._carry = ""
        visible: list[str] = []

        while data:
            lower = data.casefold()
            if self._hidden is not None:
                close = f"</{self._hidden}>"
                index = lower.find(close)
                if index < 0:
                    keep = _partial_suffix_length(data, (close,))
                    self._carry = data[-keep:] if keep else ""
                    return ""
                data = data[index + len(close):]
                self._hidden = None
                continue

            candidates: list[tuple[int, str, bool, str]] = []
            for tag in _HIDDEN_TAGS:
                opening = f"<{tag}>"
                closing = f"</{tag}>"
                open_index = lower.find(opening)
                close_index = lower.find(closing)
                if open_index >= 0:
                    candidates.append((open_index, tag, True, opening))
                if close_index >= 0:
                    candidates.append((close_index, tag, False, closing))
            if candidates:
                index, tag, is_open, token = min(candidates, key=lambda item: item[0])
                if index:
                    visible.append(data[:index])
                data = data[index + len(token):]
                if is_open:
                    self._hidden = tag
                continue

            keep = _partial_suffix_length(data, self.tokens)
            if keep:
                visible.append(data[:-keep])
                self._carry = data[-keep:]
            else:
                visible.append(data)
            break

        return _clean_visible_chars("".join(visible), forbid_cjk=self.forbid_cjk)

    def finish(self) -> str:
        if self._hidden is not None:
            self._carry = ""
            return ""
        tail = self._carry
        self._carry = ""
        # A dangling prefix of a hidden tag is service text, not user content.
        if tail.startswith("<"):
            return ""
        return _clean_visible_chars(tail, forbid_cjk=self.forbid_cjk)


def _simple_auto_fast(text: str, requested_mode: str, base_decision) -> bool:
    if requested_mode != "auto" or base_decision.mode != "work" or base_decision.complexity_score > 0:
        return False
    normalized = " ".join((text or "").split())
    return 0 < len(normalized) <= 160 and normalized.count("\n") == 0


def install_runtime_quality_patch() -> None:
    """Install small runtime guards before chat routes bind inference functions."""
    from app.inference import router as inference_router
    from app.inference.client import LlamaClient, LlamaGeneration
    from app.services.project_context import ProjectContextBuilder

    if getattr(LlamaClient.generate, "_x1_quality_guard", False):
        return

    original_choose_route = inference_router.choose_route

    def guarded_choose_route(text: str, requested_mode: str, normal_context: int, deep_context: int):
        decision = original_choose_route(text, requested_mode, normal_context, deep_context)
        if not _simple_auto_fast(text, requested_mode, decision):
            return decision
        starter_4k = int(deep_context) <= 4096
        return inference_router.RouteDecision(
            mode="fast",
            max_context_tokens=min(max(1024, int(normal_context)), max(1024, int(deep_context)), 4096),
            max_output_tokens=384 if starter_4k else 600,
            reasoning=False,
            complexity_score=decision.complexity_score,
            reason="simple_short_auto_fast",
        )

    inference_router.choose_route = guarded_choose_route

    original_build = ProjectContextBuilder.build

    def guarded_build(self, db, *, project, conversation, task=None, incoming):
        result = original_build(self, db, project=project, conversation=conversation, task=task, incoming=incoming)
        if not result or result[0].role != "system" or _BASE_SYSTEM_TEXT not in result[0].content:
            result.insert(0, base_system_message())
        return result

    ProjectContextBuilder.build = guarded_build

    original_generate = LlamaClient.generate

    async def guarded_generate(self, messages, *, max_tokens: int, reasoning: bool, on_token=None):
        user_text = _latest_user_text(messages)
        shortcut = instant_reply(user_text) if not reasoning else None
        if shortcut is not None:
            if on_token is not None:
                await on_token(shortcut)
            return LlamaGeneration(
                text=shortcut,
                ttft_ms=0,
                output_tokens=max(1, len(shortcut) // 4),
                tokens_per_second=0.0,
                generation_ms=0,
            )

        visible = VisibleStreamFilter(user_text)
        started = perf_counter()
        first_visible_at: float | None = None

        async def guarded_sink(chunk: str) -> None:
            nonlocal first_visible_at
            piece = visible.feed(chunk)
            if not piece:
                return
            if first_visible_at is None:
                first_visible_at = perf_counter()
            if on_token is not None:
                await on_token(piece)

        result = await original_generate(
            self,
            messages,
            max_tokens=max_tokens,
            reasoning=reasoning,
            on_token=guarded_sink if on_token is not None else None,
        )
        tail = visible.finish()
        if tail and on_token is not None:
            if first_visible_at is None:
                first_visible_at = perf_counter()
            await on_token(tail)

        cleaned = clean_visible_output(user_text, result.text)
        if not cleaned:
            cleaned = "Не удалось сформировать корректный ответ. Попробуйте сформулировать запрос ещё раз."
            if on_token is not None and first_visible_at is None:
                first_visible_at = perf_counter()
                await on_token(cleaned)

        ttft_ms = result.ttft_ms
        if first_visible_at is not None:
            ttft_ms = max(0, int((first_visible_at - started) * 1000))
        return LlamaGeneration(
            text=cleaned,
            ttft_ms=ttft_ms,
            output_tokens=result.output_tokens,
            tokens_per_second=result.tokens_per_second,
            generation_ms=result.generation_ms,
        )

    guarded_generate._x1_quality_guard = True  # type: ignore[attr-defined]
    LlamaClient.generate = guarded_generate
