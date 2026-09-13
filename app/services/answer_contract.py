from __future__ import annotations

import re
from dataclasses import dataclass

from app.schemas.chat import ChatMessage


_WRITING_ACTION = re.compile(
    r"\b(?:напиши|перепиши|отредактируй|сделай\s+текст|подготовь\s+(?:текст|письмо|статью|пост)|"
    r"rewrite|write|edit|draft)\b",
    re.IGNORECASE,
)
_WRITING_NOUN = re.compile(
    r"\b(?:статья|письмо|сообщение|пост|описание|копирайт|продающ|сопроводительн|copywriting|email|article)\b",
    re.IGNORECASE,
)
_AUDIT = re.compile(r"\b(?:аудит|проверь|проверка|диагност|ревью|audit|review|inspect)\b", re.IGNORECASE)
_RESEARCH = re.compile(r"\b(?:исследован|найди|сравни|источник|рынок|актуальн|research|compare|find|sources?)\b", re.IGNORECASE)
_RECOMMENDATION = re.compile(r"\b(?:лучший|лучшая|лучшие|подбери|посоветуй|рекомендуй|best|recommend)\b", re.IGNORECASE)
_STRATEGY = re.compile(r"\b(?:стратег|план|архитектур|оптимиз|финмодель|экономик|strategy|architecture|optimi[sz])\b", re.IGNORECASE)


@dataclass(frozen=True)
class AnswerContract:
    kind: str
    instruction: str

    def as_message(self) -> ChatMessage:
        return ChatMessage(role="system", content="X1 ANSWER CONTRACT: " + self.instruction)


def classify_answer_kind(user_text: str) -> str:
    text = " ".join(str(user_text or "").split())
    if not text:
        return "direct"
    # Explicit task verbs have priority over incidental content-type nouns.
    # "Проведи аудит статьи" is an audit; "напиши сравнительную статью" is writing.
    if _AUDIT.search(text):
        return "audit"
    if _WRITING_ACTION.search(text):
        return "writing"
    if _RECOMMENDATION.search(text):
        return "recommendation"
    if _RESEARCH.search(text):
        return "research"
    if _STRATEGY.search(text):
        return "analysis"
    if _WRITING_NOUN.search(text):
        return "writing"
    return "direct"


def build_answer_contract(user_text: str) -> AnswerContract:
    kind = classify_answer_kind(user_text)
    if kind == "writing":
        return AnswerContract(
            kind,
            "Deliver the finished text the user can use immediately. Preserve supplied facts and intent; never invent supporting facts merely to make copy sound stronger. Write natural Russian when the user writes Russian: concrete wording, varied sentence length, coherent paragraphs, minimal headings, no canned AI preamble, no bureaucratic filler, no repetitive conclusion. Match the requested tone and format. If the user asks only for the text, output only the text.",
        )
    if kind == "audit":
        return AnswerContract(
            kind,
            "Deliver an actionable audit, not a generic checklist. Lead with the most important conclusion. Separate directly observed findings from assumptions or unavailable measurements. For each material problem prefer: observation -> impact -> priority -> concrete fix. Rank by business/user impact before cosmetic issues. Do not claim a test, metric or access that was not actually performed.",
        )
    if kind == "recommendation":
        return AnswerContract(
            kind,
            "Answer the decision the user actually needs to make. State the criteria, compare credible alternatives, explain trade-offs, and recommend a winner only when evidence justifies it. Search rank, one review site or one article is never enough by itself. When evidence is mixed, give a short ranked shortlist and say what makes each option stronger or weaker.",
        )
    if kind == "research":
        return AnswerContract(
            kind,
            "Start with the answer, then support it with the strongest evidence. Cross-check important claims, distinguish verified facts from inference, surface meaningful conflicts, and prefer primary/official evidence where relevant. Avoid padding the response with every source found; synthesize what changes the conclusion. End with a decision or next action when the task implies one.",
        )
    if kind == "analysis":
        return AnswerContract(
            kind,
            "Solve the decision problem rather than merely listing considerations. Make assumptions explicit only when they materially affect the result. Compare alternatives using the same criteria, quantify when evidence allows it, identify the binding constraint, and finish with a concrete recommendation and implementation sequence.",
        )
    return AnswerContract(
        kind,
        "Answer directly and completely. Prefer the conclusion before background. Do not repeat the user's question, add ceremonial introductions, or create sections unless they improve comprehension. Distinguish known facts from uncertainty and avoid unsupported precision.",
    )
