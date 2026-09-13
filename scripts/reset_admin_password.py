from __future__ import annotations

import argparse
import getpass
import secrets
import string
from datetime import datetime, timezone

from sqlalchemy import select, update

from app.db import SessionLocal
from app.models import AuthSession, User
from app.services.auth import hash_password, normalize_email


def _generated_password(length: int = 24) -> str:
    alphabet = string.ascii_letters + string.digits + "!@#$%^&*-_=+"
    while True:
        value = "".join(secrets.choice(alphabet) for _ in range(length))
        if (
            any(c.islower() for c in value)
            and any(c.isupper() for c in value)
            and any(c.isdigit() for c in value)
            and any(c in "!@#$%^&*-_=+" for c in value)
        ):
            return value


def main() -> int:
    parser = argparse.ArgumentParser(description="Reset an OLYA AI administrator password safely.")
    parser.add_argument("--email", required=True, help="Administrator email address")
    parser.add_argument(
        "--generate",
        action="store_true",
        help="Generate a strong temporary password and print it once after a successful reset",
    )
    args = parser.parse_args()

    email = normalize_email(args.email)
    password = _generated_password() if args.generate else getpass.getpass("New admin password: ")
    if not args.generate:
        confirm = getpass.getpass("Repeat new admin password: ")
        if password != confirm:
            raise SystemExit("Passwords do not match")
    if len(password) < 12:
        raise SystemExit("Password must contain at least 12 characters")

    db = SessionLocal()
    try:
        user = db.scalar(select(User).where(User.email == email))
        if user is None:
            raise SystemExit(f"User not found: {email}")

        user.password_hash = hash_password(password)
        user.is_admin = True
        user.is_active = True
        now = datetime.now(timezone.utc)
        db.execute(
            update(AuthSession)
            .where(AuthSession.user_id == user.id, AuthSession.revoked_at.is_(None))
            .values(revoked_at=now)
        )
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()

    print(f"Admin password updated for {email}; all previous sessions revoked.")
    if args.generate:
        print(f"TEMPORARY_PASSWORD={password}")
        print("Sign in once and replace this temporary password with your own strong password.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
