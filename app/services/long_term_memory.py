from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.models import Conversation, ConversationMemory, Message
from app.schemas.chat import ChatMessage

_WORD_RE = re.compile(r"[0-9a-zA-Zа-яА-ЯёЁ_+.-]{2,}")
_SENTENCE_RE = re.compile(r"(?<=[.!?\n])\s+")
_STOP = {
    "это", "как", "что", "для", "или", "при", "над", "под", "без", "про", "уже", "ещё", "еще", "будет",
    "нужно", "надо", "можно", "мы", "вы", "они", "она", "оно", "его", "ее", "её", "наш", "наша", "наше",
    "the", "and", "for", "with", "this", "that", "from", "into", "our", "your", "you", "are", "not", "use",
}
_DECISION_MARKERS = (
    "решили", "решено", "используем", "будем использовать", "выбираем", "выбрали", "останавливаемся на",
    "не используем", "запрещено", "запрещаю", "обязательно", "фиксируем", "договорились", "оставляем",
    "we decided", "we use", "we will use", "do not use", "must use", "locked to",
)
_PREFERENCE_MARKERS = (
    "мне нравится", "мне не нравится", "я предпочитаю", "предпочитаю", "хочу чтобы", "хочу, чтобы",
    "не хочу", "важно чтобы", "важно, чтобы", "мой стиль", "обычно я", "для меня важно",
    "i prefer", "i like", "i don't like", "i do not like", "i want", "important to me",
)
_FACT_MARKERS = (
    "меня зовут", "я работаю", "я занимаюсь", "мой проект", "наш проект", "в проекте", "у нас",
    "сервер", "оператив", "ram", "модель", "бюджет", "ограничение", "цель проекта", "наша цель",
    "для проекта", "production", "продакшн", "мой сайт", "моя компания", "наш сайт", "наша компания",
)
_EXPLICIT_MEMORY_MARKERS = (
    "запомни", "запомни что", "запомни, что", "важно:", "учти:", "учитывай", "remember", "important:",
)


@dataclass(frozen=True)
class MemoryBundle:
    summary: str
    memories: tuple[ConversationMemory, ...]

    @property
    def active(self) -> bool:
        return bool(self.summary.strip() or self.memories)


def keywords(text: str, *, limit: int = 24) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for token in _WORD_RE.findall(str(text or "").casefold()):
        token = token.strip("._+-")
        if len(token) < 2 or token in _STOP or token in seen:
            continue
        seen.add(token)
        result.append(token)
        if len(result) >= limit:
            break
    return result


def _clean_sentence(text: str) -> str:
    return " ".join(str(text or "").strip().split())[:700]


def _strip_memory_prefix(value: str) -> str:
    text = value.strip()
    text = re.sub(r"^(?:запомни(?:\s*,?\s*что)?|учти(?:\s*,?\s*что)?|remember(?:\s+that)?)\s*[:,-]?\s*", "", text, flags=re.I)
    return text.strip() or value.strip()


def extract_user_memories(text: str) -> list[tuple[str, str, list[str]]]:
    candidates: list[tuple[str, str, list[str]]] = []
    seen_values: set[str] = set()
    for raw in _SENTENCE_RE.split(str(text or "")):
        sentence = _clean_sentence(raw)
        if len(sentence) < 8 or sentence.endswith("?"):
            continue
        low = sentence.casefold()
        kind = ""
        if any(marker in low for marker in _EXPLICIT_MEMORY_MARKERS):
            kind = "explicit"
        elif any(marker in low for marker in _DECISION_MARKERS):
            kind = "decision"
        elif any(marker in low for marker in _PREFERENCE_MARKERS):
            kind = "preference"
        elif any(marker in low for marker in _FACT_MARKERS):
            kind = "fact"
        if not kind:
            continue
        value = _clean_sentence(_strip_memory_prefix(sentence))
        key = value.casefold()
        if key in seen_values:
            continue
        terms = keywords(value)
        if not terms:
            continue
        seen_values.add(key)
        candidates.append((kind, value, terms))
    return candidates[:16]


def _memory_key(kind: str, value: str) -> str:
    digest = hashlib.sha256((kind + "\n" + value.casefold()).encode("utf-8")).hexdigest()[:24]
    return f"{kind}:{digest}"


def remember_user_turn(db: Session, *, conversation_id: str, project_id: str | None, text: str) -> int:
    created = 0
    for kind, value, terms in extract_user_memories(text):
        key = _memory_key(kind, value)
        existing = db.scalar(
            select(ConversationMemory).where(
                ConversationMemory.conversation_id == conversation_id,
                ConversationMemory.kind == kind,
                ConversationMemory.memory_key == key,
            )
        )
        if existing is None:
            db.add(
                ConversationMemory(
                    conversation_id=conversation_id,
                    project_id=project_id,
                    kind=kind,
                    memory_key=key,
                    value=value,
                    keywords=terms,
                    source_role="user",
                    confidence=1.0,
                )
            )
            created += 1
        else:
            existing.value = value
            existing.keywords = terms
            existing.confidence = 1.0
    if created:
        db.flush()
    _trim_memory(db, conversation_id, limit=180)
    return created


def _trim_memory(db: Session, conversation_id: str, *, limit: int) -> None:
    rows = list(
        db.scalars(
            select(ConversationMemory.id)
            .where(
                ConversationMemory.conversation_id == conversation_id,
                ConversationMemory.kind.in_(("explicit", "decision", "preference", "fact")),
            )
            .order_by(ConversationMemory.updated_at.desc())
        ).all()
    )
    stale = rows[limit:]
    if stale:
        db.execute(delete(ConversationMemory).where(ConversationMemory.id.in_(stale)))


def _clip_summary_line(role: str, content: str) -> str:
    clean = " ".join(str(content or "").split())
    if len(clean) > 180:
        clean = clean[:177].rstrip() + "..."
    prefix = "Пользователь" if role == "user" else "OLYA"
    return f"- {prefix}: {clean}"


def refresh_rolling_summary(
    db: Session,
    *,
    conversation_id: str,
    project_id: str | None,
    hot_messages: int = 6,
    source_messages: int = 24,
    max_chars: int = 900,
) -> str:
    rows = list(
        db.scalars(
            select(Message)
            .where(Message.conversation_id == conversation_id)
            .order_by(Message.created_at.desc())
            .offset(max(4, hot_messages))
            .limit(max(8, source_messages))
        ).all()
    )
    if not rows:
        return ""
    lines = ["Более ранний контекст этого чата:"]
    used = len(lines[0])
    for row in reversed(rows):
        if row.role not in {"user", "assistant"}:
            continue
        line = _clip_summary_line(row.role, row.content)
        if used + len(line) + 1 > max_chars:
            break
        lines.append(line)
        used += len(line) + 1
    value = "\n".join(lines)
    item = db.scalar(
        select(ConversationMemory).where(
            ConversationMemory.conversation_id == conversation_id,
            ConversationMemory.kind == "summary",
            ConversationMemory.memory_key == "rolling",
        )
    )
    terms = keywords(value, limit=24)
    if item is None:
        db.add(
            ConversationMemory(
                conversation_id=conversation_id,
                project_id=project_id,
                kind="summary",
                memory_key="rolling",
                value=value,
                keywords=terms,
                source_role="mixed",
                confidence=0.5,
            )
        )
        db.flush()
    elif item.value != value:
        item.value = value
        item.keywords = terms
        db.flush()
    return value


def _score(query_terms: set[str], item: ConversationMemory) -> float:
    item_terms = {str(value).casefold() for value in (item.keywords or [])}
    overlap = len(query_terms & item_terms) if query_terms and item_terms else 0
    recency_free_bonus = {"explicit": 5.0, "decision": 3.5, "preference": 3.0, "fact": 2.0}.get(item.kind, 0.0)
    if not query_terms:
        return recency_free_bonus
    if overlap == 0:
        return recency_free_bonus if item.kind == "explicit" else 0.0
    precision = overlap / max(1, len(item_terms))
    recall = overlap / max(1, len(query_terms))
    return recency_free_bonus + overlap * 2.0 + precision + recall


def retrieve_memories(
    db: Session,
    *,
    conversation_id: str,
    query: str,
    limit: int = 4,
    user_id: str | None = None,
) -> tuple[ConversationMemory, ...]:
    kinds = ("explicit", "decision", "preference", "fact")
    if user_id:
        stmt = (
            select(ConversationMemory)
            .join(Conversation, Conversation.id == ConversationMemory.conversation_id)
            .where(Conversation.owner_id == user_id, ConversationMemory.kind.in_(kinds))
            .order_by(ConversationMemory.updated_at.desc())
            .limit(500)
        )
    else:
        stmt = (
            select(ConversationMemory)
            .where(ConversationMemory.conversation_id == conversation_id, ConversationMemory.kind.in_(kinds))
            .order_by(ConversationMemory.updated_at.desc())
            .limit(180)
        )
    rows = list(db.scalars(stmt).all())
    query_terms = set(keywords(query, limit=28))
    ranked = sorted(((_score(query_terms, item), item) for item in rows), key=lambda pair: pair[0], reverse=True)
    result: list[ConversationMemory] = []
    seen: set[str] = set()
    for score, item in ranked:
        if score <= 0:
            continue
        normalized = " ".join(str(item.value or "").casefold().split())
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        result.append(item)
        if len(result) >= max(1, min(limit, 8)):
            break
    return tuple(result)


def build_memory_bundle(
    db: Session,
    *,
    conversation_id: str,
    project_id: str | None,
    query: str,
    hot_messages: int = 6,
    user_id: str | None = None,
) -> MemoryBundle:
    summary = refresh_rolling_summary(
        db,
        conversation_id=conversation_id,
        project_id=project_id,
        hot_messages=hot_messages,
        source_messages=24,
        max_chars=900,
    )
    return MemoryBundle(
        summary=summary,
        memories=retrieve_memories(
            db,
            conversation_id=conversation_id,
            query=query,
            limit=5,
            user_id=user_id,
        ),
    )


def memory_context_message(bundle: MemoryBundle) -> ChatMessage | None:
    if not bundle.active:
        return None
    sections = ["ПАМЯТЬ OLYA. Используй только если она относится к текущему запросу; новое сообщение пользователя приоритетнее."]
    labels = {
        "explicit": "Явно сохранено",
        "decision": "Решения и ограничения",
        "preference": "Предпочтения",
        "fact": "Факты",
    }
    for kind in ("explicit", "decision", "preference", "fact"):
        values = [item.value for item in bundle.memories if item.kind == kind]
        if values:
            sections.append(labels[kind] + ":\n" + "\n".join(f"- {value}" for value in values))
    if bundle.summary.strip():
        sections.append(bundle.summary.strip())
    return ChatMessage(role="system", content="\n\n".join(sections)[:2600])
