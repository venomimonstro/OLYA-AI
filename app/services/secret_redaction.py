from __future__ import annotations

import re


_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH |DSA )?PRIVATE KEY-----.*?-----END (?:RSA |EC |OPENSSH |DSA )?PRIVATE KEY-----", re.I | re.S), "[REDACTED_PRIVATE_KEY]"),
    (re.compile(r"\bAKIA[0-9A-Z]{16}\b"), "[REDACTED_ACCESS_KEY]"),
    (re.compile(r"\bgh[pousr]_[A-Za-z0-9_]{30,255}\b"), "[REDACTED_GITHUB_TOKEN]"),
    (re.compile(r"\bBearer\s+[A-Za-z0-9._~+/=-]{20,}\b", re.I), "Bearer [REDACTED_TOKEN]"),
    (re.compile(r"(?im)^(\s*(?:password|passwd|pwd|secret|secret_key|api[_-]?key|access[_-]?token|refresh[_-]?token|private[_-]?key|database_url)\s*[=:]\s*)([^\s#]{8,})"), r"\1[REDACTED]"),
)


def redact_secrets(text: str) -> tuple[str, int]:
    value = str(text)
    total = 0
    for pattern, replacement in _PATTERNS:
        value, count = pattern.subn(replacement, value)
        total += count
    return value, total
