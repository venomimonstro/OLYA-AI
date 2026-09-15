from __future__ import annotations

import re
from datetime import datetime, timezone
from functools import wraps

from app.schemas.chat import ChatMessage

_INSTALLED = False

_YEAR_RE = re.compile(
    r"^\s*(?:какой|который)\s+(?:сейчас\s+|ныне\s+|текущ(?:ий|его)\s+)?год\s*[?!.…]*$|"
    r"^\s*(?:what|which)\s+year\s+(?:is\s+it\s+)?(?:now|currently)?\s*[?!.…]*$",
    re.IGNORECASE,
)
_US_CURRENT_PRESIDENT_RE = re.compile(
    r"(?:"
    r"\bкто\b.{0,32}\bпрезидент\w*\b.{0,32}\b(?:сша|америк\w*|соедин[её]нн\w+\s+штат\w*)\b|"
    r"\bкто\b.{0,32}\b(?:сша|америк\w*|соедин[её]нн\w+\s+штат\w*)\b.{0,32}\bпрезидент\w*\b|"
    r"\b(?:current|who\s+is\s+(?:the\s+)?current)\b.{0,20}\bpresident\b.{0,20}\b(?:usa|us|united\s+states)\b"
    r")",
    re.IGNORECASE,
)


def _temporal_system_message() -> ChatMessage:
    now = datetime.now(timezone.utc)
    return ChatMessage(
        role="system",
        content=(
            "OLYA RUNTIME CLOCK (server-owned): current UTC date is "
            f"{now.date().isoformat()} and current year is {now.year}. "
            "For questions about the current date/year, use this runtime value, never the model training cutoff. "
            "For changing real-world facts (current office holders, news, prices, laws, versions, schedules), "
            "use fresh trusted web evidence when available and never infer that a role is vacant merely because model memory is stale."
        ),
    )


def install_temporal_freshness_patch() -> None:
    global _INSTALLED
    if _INSTALLED:
        return
    _INSTALLED = True

    # 1) Exact runtime answers for clock facts. These must never depend on the LLM.
    from app import utility_chat

    original_utility_reply = utility_chat.utility_reply

    @wraps(original_utility_reply)
    def utility_reply_with_runtime_clock(user_text: str):
        text = utility_chat._normalize_utility_input(user_text)
        if _YEAR_RE.fullmatch(text):
            now = datetime.now(timezone.utc)
            return utility_chat.UtilityReply(f"Сейчас {now.year} год.", "runtime_year")
        return original_utility_reply(user_text)

    utility_chat.utility_reply = utility_reply_with_runtime_clock

    # 2) Every inference gets an authoritative runtime date/year context.
    from app.services import project_context

    original_context_build = project_context.ProjectContextBuilder.build

    @wraps(original_context_build)
    def build_with_runtime_clock(self, *args, **kwargs):
        messages = list(original_context_build(self, *args, **kwargs))
        runtime = _temporal_system_message()
        # Keep server-owned policy before conversation/user data.
        return [runtime, *messages]

    project_context.ProjectContextBuilder.build = build_with_runtime_clock

    # 3) Never put changing facts into the long-lived atomic answer cache.
    from app.services import response_strategy

    original_is_atomic = response_strategy.is_atomic_knowledge_question

    @wraps(original_is_atomic)
    def stable_atomic_only(text: str) -> bool:
        if response_strategy.requires_fresh_data(text):
            return False
        return original_is_atomic(text)

    response_strategy.is_atomic_knowledge_question = stable_atomic_only

    # 4) For the US president, query the authoritative current administration page
    # instead of relying on a generic Russian-language search snippet.
    from app.services import clean_web

    original_build_clean_web_context = clean_web.build_clean_web_context

    @wraps(original_build_clean_web_context)
    async def build_clean_web_context_with_official_roles(*, discovery, fetcher, question: str, web_mode: str, deep: bool = False):
        if web_mode != "off" and _US_CURRENT_PRESIDENT_RE.search(str(question or "")):
            result = await original_build_clean_web_context(
                discovery=discovery,
                fetcher=fetcher,
                question="current President of the United States site:whitehouse.gov administration",
                web_mode="always",
                deep=True,
            )
            now = datetime.now(timezone.utc)
            result.context_messages.insert(
                0,
                ChatMessage(
                    role="system",
                    content=(
                        "CURRENT OFFICE-HOLDER CHECK. Original user asks who is currently President of the United States. "
                        f"Today is {now.date().isoformat()} UTC. Prefer whitehouse.gov current administration evidence over model memory. "
                        "If an official White House result identifies the President, answer the person's name directly. "
                        "Do not say the office has no president unless fresh authoritative evidence explicitly says the office is vacant."
                    ),
                ),
            )
            return result
        return await original_build_clean_web_context(
            discovery=discovery,
            fetcher=fetcher,
            question=question,
            web_mode=web_mode,
            deep=deep,
        )

    clean_web.build_clean_web_context = build_clean_web_context_with_official_roles
