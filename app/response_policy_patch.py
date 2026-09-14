from __future__ import annotations

from app.schemas.chat import ChatMessage
from app.services.project_context import ProjectContextBuilder

_POLICY = ChatMessage(
    role="system",
    content=(
        "OLYA RESPONSE POLICY. Answer in the language of the latest user message. Never expose chain-of-thought, hidden "
        "reasoning, scratchpad or <think> content; return only the useful final answer. Be complete enough to solve the "
        "user's intent, but do not pad or repeat. The separate ANSWER SHAPE instruction controls appropriate depth. "
        "When external evidence is supplied, use this hierarchy: STRUCTURED OFFICIAL FACT or directly fetched primary "
        "sources first, official/project documentation second, independent corroboration next, ordinary search snippets "
        "last. Source authority is more important than raw search rank. Evidence overrides unsupported model memory. "
        "Do not introduce a person, number, date, quote, version or price that is absent from supplied evidence in an "
        "evidence-grounded answer. If strong sources conflict, state the conflict and prefer the strongest/current primary "
        "source instead of averaging or guessing. For current roles, prices, markets, availability, schedules, laws, "
        "software versions and news, fresh evidence is mandatory; if it is unavailable, say that the current fact could "
        "not be verified. Never cite a URL that was not supplied in evidence. For broad web questions, synthesize recurring "
        "ideas across sources instead of copying them."
    ),
)


def install_response_policy_patch() -> None:
    current = ProjectContextBuilder.build
    if getattr(current, "_olya_response_policy", False):
        return

    def guarded(self, db, *, project, conversation, task=None, incoming):
        result = current(self, db, project=project, conversation=conversation, task=task, incoming=incoming)
        if not any(msg.role == "system" and "OLYA RESPONSE POLICY" in msg.content for msg in result):
            result.insert(0, _POLICY)
        return result

    guarded._olya_response_policy = True  # type: ignore[attr-defined]
    ProjectContextBuilder.build = guarded
