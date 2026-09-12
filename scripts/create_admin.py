#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys

from sqlalchemy import select

from app.db import SessionLocal
from app.models import User
from app.services.auth import hash_password, normalize_email
from app.services.owner_integrations import ensure_email_state


def _payload_from_stdin() -> dict:
    try:
        value = json.load(sys.stdin)
    except Exception as exc:
        raise SystemExit("invalid admin provisioning JSON") from exc
    if not isinstance(value, dict):
        raise SystemExit("admin provisioning payload must be an object")
    return value


def ensure_admin(payload: dict, *, reset_password: bool = False) -> dict:
    email = normalize_email(str(payload.get("email") or ""))
    password = str(payload.get("password") or "")
    display_name = str(payload.get("display_name") or "").strip()[:120]
    if "@" not in email or len(email) > 320:
        raise ValueError("valid admin email is required")
    if len(password) < 12 or len(password) > 256:
        raise ValueError("admin password must be 12..256 characters")
    db = SessionLocal()
    try:
        user = db.scalar(select(User).where(User.email == email))
        created = user is None
        password_changed = False
        if user is None:
            user = User(
                email=email,
                password_hash=hash_password(password),
                display_name=display_name,
                is_admin=True,
                is_active=True,
            )
            db.add(user)
            db.flush()
            password_changed = True
        else:
            user.is_admin = True
            user.is_active = True
            if display_name and not user.display_name:
                user.display_name = display_name
            if reset_password:
                user.password_hash = hash_password(password)
                password_changed = True
        ensure_email_state(db, user, mark_verified=True)
        db.commit()
        return {
            "status": "created" if created else "existing_promoted_or_verified",
            "user_id": user.id,
            "email": user.email,
            "password_changed": password_changed,
        }
    finally:
        db.close()


def main() -> int:
    parser = argparse.ArgumentParser(description="Create or ensure the first X1 administrator without exposing password in argv")
    parser.add_argument("--stdin-json", action="store_true", required=True)
    parser.add_argument("--reset-password", action="store_true", help="explicitly replace password for an existing account")
    args = parser.parse_args()
    payload = _payload_from_stdin()
    try:
        result = ensure_admin(payload, reset_password=args.reset_password)
    except ValueError as exc:
        print(json.dumps({"status": "failed", "error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 2
    # Never echo the submitted password.
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
