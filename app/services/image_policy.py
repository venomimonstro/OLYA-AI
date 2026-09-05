from __future__ import annotations

import unicodedata
from dataclasses import dataclass
from datetime import datetime, timezone
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import ImagePolicyTestCase, ImageSafetyPolicy, User


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class ImagePolicyError(ValueError):
    pass


@dataclass(frozen=True)
class PolicyDecision:
    allowed: bool
    rule: str = ""
    reason: str = ""


def _norm(text: str) -> str:
    return " ".join(unicodedata.normalize("NFKC", text).casefold().split())


def validate_rules(rules: dict) -> dict:
    if not isinstance(rules, dict):
        raise ImagePolicyError("Policy rules must be an object")
    blocked = rules.get("blocked_phrases", [])
    if not isinstance(blocked, list) or len(blocked) > 200:
        raise ImagePolicyError("blocked_phrases must be a list with at most 200 items")
    clean: list[str] = []
    for item in blocked:
        if not isinstance(item, str):
            raise ImagePolicyError("blocked phrase must be text")
        value = _norm(item)
        if not value or len(value) > 160:
            raise ImagePolicyError("blocked phrase length is invalid")
        if value not in clean:
            clean.append(value)
    required_negative = rules.get("required_negative_phrases", [])
    if not isinstance(required_negative, list) or len(required_negative) > 100:
        raise ImagePolicyError("required_negative_phrases must be a list with at most 100 items")
    neg = []
    for item in required_negative:
        if not isinstance(item, str) or not item.strip() or len(item) > 160:
            raise ImagePolicyError("required negative phrase is invalid")
        if item.strip() not in neg:
            neg.append(item.strip())
    return {"blocked_phrases": clean, "required_negative_phrases": neg}


def evaluate_prompt(policy: ImageSafetyPolicy | None, prompt: str) -> PolicyDecision:
    if policy is None:
        return PolicyDecision(True, reason="no_published_policy")
    rules = validate_rules(policy.rules or {})
    normalized = _norm(prompt)
    for phrase in rules["blocked_phrases"]:
        if phrase in normalized:
            return PolicyDecision(False, rule=f"blocked_phrase:{phrase}", reason="Prompt matched a blocked policy phrase")
    return PolicyDecision(True, reason="allowed_by_policy")


def compose_effective_prompt(policy: ImageSafetyPolicy | None, user_prompt: str) -> str:
    if policy is None or not policy.superprompt.strip():
        return user_prompt.strip()
    return f"{policy.superprompt.strip()}\n\nUSER IMAGE REQUEST:\n{user_prompt.strip()}"


def compose_negative_prompt(policy: ImageSafetyPolicy | None, user_negative: str) -> str:
    parts = [user_negative.strip()] if user_negative.strip() else []
    if policy:
        rules = validate_rules(policy.rules or {})
        parts.extend(rules["required_negative_phrases"])
    return ", ".join(dict.fromkeys(x for x in parts if x))


def published_policy(db: Session) -> ImageSafetyPolicy | None:
    return db.scalar(select(ImageSafetyPolicy).where(ImageSafetyPolicy.state == "published").order_by(ImageSafetyPolicy.version.desc()).limit(1))


def next_policy_version(db: Session) -> int:
    return int(db.scalar(select(func.coalesce(func.max(ImageSafetyPolicy.version), 0))) or 0) + 1


def run_policy_regression(db: Session, policy: ImageSafetyPolicy) -> list[dict]:
    rows = db.scalars(select(ImagePolicyTestCase).where(ImagePolicyTestCase.policy_id == policy.id, ImagePolicyTestCase.active.is_(True)).order_by(ImagePolicyTestCase.created_at)).all()
    results: list[dict] = []
    for row in rows:
        decision = evaluate_prompt(policy, row.prompt)
        actual = "allow" if decision.allowed else "block"
        results.append({"test_id": row.id, "expected": row.expected, "actual": actual, "passed": actual == row.expected, "rule": decision.rule})
    return results


def publish_policy(db: Session, policy: ImageSafetyPolicy, actor: User) -> list[dict]:
    if policy.state not in {"staging", "published", "archived"}:
        raise ImagePolicyError("Policy must be staged before publishing")
    results = run_policy_regression(db, policy)
    if not results:
        raise ImagePolicyError("Policy requires regression tests before publishing")
    expectations = {x["expected"] for x in results}
    if expectations != {"allow", "block"}:
        raise ImagePolicyError("Policy regression set must contain both allow and block cases")
    if any(not x["passed"] for x in results):
        raise ImagePolicyError("Policy regression tests failed")
    for current in db.scalars(select(ImageSafetyPolicy).where(ImageSafetyPolicy.state == "published", ImageSafetyPolicy.id != policy.id)).all():
        current.state = "archived"
    policy.state = "published"
    policy.published_by = actor.id
    policy.published_at = utcnow()
    db.flush()
    return results
