from __future__ import annotations

from app.schemas.chat import ChatMessage
from app.services.project_context import ProjectContextBuilder

_POLICY = ChatMessage(
    role="system",
    content=(
        "OLYA RESPONSE POLICY. Always answer in the language of the user's latest message. "
        "If the latest user message is Russian, the explanatory prose must be entirely in natural Russian; "
        "do not insert English boilerplate such as 'please note', 'as of', or similar phrases. Proper names, "
        "product names, code, URLs and necessary technical identifiers may remain in their original spelling. "
        "For current, recent, changing, political, official-role, price, market, availability, schedule, law, "
        "software-version or news questions, external evidence supplied in the context is authoritative over "
        "the model's memorized knowledge. Never answer a current fact from training memory when fresh evidence "
        "is required. Never mention a training cutoff as a substitute for checking current evidence. If fresh "
        "eligible evidence is absent or insufficient, explicitly say that the current fact could not be verified "
        "instead of guessing or presenting an old fact as current."
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
