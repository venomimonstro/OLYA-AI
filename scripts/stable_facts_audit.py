#!/usr/bin/env python3
from __future__ import annotations

import json

from app.schemas.chat import ChatMessage
from app.stable_fact_evidence_guard import _consensus, resolve_stable_fact


def _messages(*blocks: str) -> list[ChatMessage]:
    joined = "WEB SEARCH DISCOVERY. External evidence.\n\n" + "\n\n".join(
        f"[SEARCH {i}]\n{block}" for i, block in enumerate(blocks, start=1)
    )
    return [ChatMessage(role="user", content=joined)]


def audit() -> dict:
    errors: list[str] = []

    author_messages = _messages(
        "Title: Литературная энциклопедия\nURL: https://a.example/book\nSnippet: Автор — Михаил Булгаков.",
        "Title: Библиотека\nURL: https://b.example/book\nSnippet: Автор романа — Михаил Булгаков.",
    )
    author = resolve_stable_fact("кто написал мастер и маргарита?", author_messages)
    if author != "Автор — Михаил Булгаков.":
        errors.append(f"author_consensus_failed:{author!r}")

    inflected = _messages(
        "Title: Энциклопедия\nURL: https://c.example/book\nSnippet: Это роман Михаила Булгакова.",
        "Title: Каталог\nURL: https://d.example/book\nSnippet: Знаменитый роман Михаила Булгакова.",
    )
    relation = _consensus("кто написал мастер и маргарита?", inflected)
    if relation is None or relation[0] != "author" or "Булгаков" not in relation[1]:
        errors.append(f"inflected_author_consensus_failed:{relation!r}")
    if resolve_stable_fact("кто написал мастер и маргарита?", inflected) is not None:
        errors.append("inflected_author_should_use_authoritative_hint")

    conflict = _messages(
        "Title: A\nURL: https://e.example\nSnippet: Автор — Иван Иванов.",
        "Title: B\nURL: https://f.example\nSnippet: Автор — Петр Петров.",
    )
    if resolve_stable_fact("кто автор книги?", conflict) is not None:
        errors.append("conflicting_sources_must_not_force_answer")

    founder = resolve_stable_fact(
        "кто основал компанию?",
        _messages(
            "Title: A\nURL: https://g.example\nSnippet: Основатель — Алексей Смирнов.",
            "Title: B\nURL: https://h.example\nSnippet: Основатель — Алексей Смирнов.",
        ),
    )
    if founder != "Основатель — Алексей Смирнов.":
        errors.append(f"founder_consensus_failed:{founder!r}")

    return {
        "format": "olya-stable-facts-audit-v1",
        "status": "passed" if not errors else "failed",
        "errors": errors,
        "author_sample": author,
        "inflected_author_consensus": relation,
        "conflict_guard": True,
    }


def main() -> int:
    result = audit()
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["status"] == "passed" else 2


if __name__ == "__main__":
    raise SystemExit(main())
