from __future__ import annotations

from starlette.responses import HTMLResponse


_ACCOUNT_MODE_COPY = (
    "Fast = 1 единица, Work = 2, Deep = 4. "
    "Дневной лимит защищает очередь от резкого всплеска, месячный — ваш тариф."
)
_ACCOUNT_MODE_COPY_REPLACEMENT = (
    "Авто выбирает подходящую мощность ответа. «Стандарт» подходит для большинства задач, "
    "«Глубокий» — для сложного анализа. Актуальные лимиты показаны в вашем тарифе."
)


def _finalize(document: str) -> str:
    """Apply idempotent cleanup after layered workspace wrappers have composed HTML.

    `user_ui` and task-solver views build on top of the base workspace response.
    The main product surface patch therefore runs once on the base response before
    those layers add account copy. This final pass removes stale user-facing Fast
    controls/copy without touching admin pages or API compatibility.
    """
    if "OLYA_PRODUCT_SURFACE_V2" not in document:
        return document

    document = document.replace('<option value="fast">Fast</option>', "")
    document = document.replace(_ACCOUNT_MODE_COPY, _ACCOUNT_MODE_COPY_REPLACEMENT)
    document = document.replace("X1 · Поддержка", "OLYA AI · Поддержка")
    document = document.replace("← В X1", "← В OLYA AI")
    return document


def install_user_surface_finalizer() -> None:
    current = HTMLResponse.__init__
    if getattr(current, "_olya_user_surface_finalizer", False):
        return

    def finalized(self, content, *args, **kwargs):
        if isinstance(content, str):
            content = _finalize(content)
        return current(self, content, *args, **kwargs)

    finalized._olya_user_surface_finalizer = True  # type: ignore[attr-defined]
    HTMLResponse.__init__ = finalized
