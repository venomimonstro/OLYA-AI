from __future__ import annotations

import re

from fastapi.responses import HTMLResponse

# Backend model/vendor names are implementation details. OLYA is the product;
# user-facing HTML must not expose the underlying inference vendor/model family.
_VENDOR_RE = re.compile(r"\bQwen(?:[\w.\- ]*)?\b", re.IGNORECASE)


def sanitize_public_branding(document: str) -> str:
    if not isinstance(document, str) or "qwen" not in document.casefold():
        return document
    return _VENDOR_RE.sub("локальная AI-модель", document)


def install_public_brand_policy() -> None:
    current = HTMLResponse.__init__
    if getattr(current, "_olya_public_brand_policy", False):
        return

    def enhanced(self, content, *args, **kwargs):
        if isinstance(content, str):
            content = sanitize_public_branding(content)
        return current(self, content, *args, **kwargs)

    enhanced._olya_public_brand_policy = True  # type: ignore[attr-defined]
    HTMLResponse.__init__ = enhanced
