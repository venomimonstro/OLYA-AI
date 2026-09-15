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
_CREATOR_RE = re.compile(
    r"(?:"
    r"^\s*кто\s+(?:тебя|вас)\s+(?:создал|сделал|разработал|придумал)\s*[?!.…]*$|"
    r"^\s*кто\s+(?:твой|ваш)\s+(?:создатель|разработчик|автор)\s*[?!.…]*$|"
    r"^\s*(?:who\s+(?:created|made|developed|built)\s+you|who\s+is\s+your\s+(?:creator|developer|author))\s*[?!.…]*$"
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
            "For changing real-world facts (current office holders, news, prices, laws, versions, schedules, weather, "
            "availability, local information and other time-sensitive facts), use fresh trusted web evidence and never "
            "substitute stale model memory for a current fact. "
            "OLYA creator identity is fixed: Компания BBTEC (Лысенко Артём). If asked who created/developed OLYA, answer exactly from this identity."
        ),
    )


def install_temporal_freshness_patch() -> None:
    global _INSTALLED
    if _INSTALLED:
        return
    _INSTALLED = True

    # 1) Exact runtime/identity answers that must never depend on the LLM.
    from app import utility_chat

    original_utility_reply = utility_chat.utility_reply

    @wraps(original_utility_reply)
    def utility_reply_with_runtime_clock(user_text: str):
        text = utility_chat._normalize_utility_input(user_text)
        if _YEAR_RE.fullmatch(text):
            now = datetime.now(timezone.utc)
            return utility_chat.UtilityReply(f"Сейчас {now.year} год.", "runtime_year")
        if _CREATOR_RE.fullmatch(text):
            return utility_chat.UtilityReply("Компания BBTEC (Лысенко Артём).", "creator_identity")
        return original_utility_reply(user_text)

    utility_chat.utility_reply = utility_reply_with_runtime_clock

    # 2) Every inference gets authoritative runtime date/year and creator identity.
    from app.services import project_context

    original_context_build = project_context.ProjectContextBuilder.build

    @wraps(original_context_build)
    def build_with_runtime_clock(self, *args, **kwargs):
        messages = list(original_context_build(self, *args, **kwargs))
        runtime = _temporal_system_message()
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

    # 4) Fresh/local web lookups get enough wall-clock budget for the keyless
    # metasearch + direct SERP fallback. Ordinary chat keeps its smaller budget.
    from app.services import clean_web

    original_clean_search = clean_web._search

    @wraps(original_clean_search)
    async def resilient_clean_search(discovery, query: str, *, count: int, country: str, language: str, timeout: float):
        effective_timeout = max(float(timeout), 7.0) if int(count) >= 7 else timeout
        return await original_clean_search(
            discovery,
            query,
            count=count,
            country=country,
            language=language,
            timeout=effective_timeout,
        )

    clean_web._search = resilient_clean_search

    # 5) Every question classified as freshness-sensitive is forced through live web grounding.
    # This is deliberately generic: current people/roles, news, prices, weather, schedules,
    # software versions, laws/taxes, shopping/availability, local information and similar facts.
    original_build_clean_web_context = clean_web.build_clean_web_context

    @wraps(original_build_clean_web_context)
    async def build_clean_web_context_with_global_freshness(*, discovery, fetcher, question: str, web_mode: str, deep: bool = False):
        fresh_required = response_strategy.requires_fresh_data(str(question or ""))
        effective_mode = "always" if fresh_required and web_mode != "off" else web_mode
        effective_deep = bool(deep or fresh_required)

        result = await original_build_clean_web_context(
            discovery=discovery,
            fetcher=fetcher,
            question=question,
            web_mode=effective_mode,
            deep=effective_deep,
        )

        if fresh_required:
            now = datetime.now(timezone.utc)
            result.context_messages.insert(
                0,
                ChatMessage(
                    role="system",
                    content=(
                        "FRESHNESS REQUIRED. This user request depends on current real-world information. "
                        f"Current UTC date is {now.date().isoformat()}. "
                        "Use the live web evidence supplied for this request as the source of truth for changing facts. "
                        "Prefer authoritative/primary and recent sources. Cross-check when sources disagree. "
                        "Do not answer a current fact from model memory when live evidence is missing or contradictory; "
                        "state briefly that the current fact could not be verified instead of guessing."
                    ),
                ),
            )
        return result

    clean_web.build_clean_web_context = build_clean_web_context_with_global_freshness
