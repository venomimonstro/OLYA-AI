from __future__ import annotations

import re

from starlette.responses import HTMLResponse


_ACCOUNT_MODE_COPY = (
    "Fast = 1 единица, Work = 2, Deep = 4. "
    "Дневной лимит защищает очередь от резкого всплеска, месячный — ваш тариф."
)
_ACCOUNT_MODE_COPY_REPLACEMENT = (
    "Авто выбирает подходящую мощность ответа. «Стандарт» подходит для большинства задач, "
    "«Глубокий» — для сложного анализа. Актуальные лимиты показаны в вашем тарифе."
)
_CHAT_POLISH_MARKER = "OLYA_CHAT_FINAL_POLISH"


def _nonce_attr(document: str) -> str:
    match = re.search(r'nonce=["\']([^"\']+)["\']', document, flags=re.I)
    return f' nonce="{match.group(1)}"' if match else ""


def _chat_polish(document: str) -> str:
    if 'id="view-chat"' not in document or _CHAT_POLISH_MARKER in document:
        return document

    nonce = _nonce_attr(document)
    style = rf'''<style{nonce}>/*{_CHAT_POLISH_MARKER}*/
.composer{{transition:border-color .16s ease,box-shadow .16s ease!important}}
.composer:focus-within{{border-color:#b9b9be!important;box-shadow:0 8px 32px rgba(0,0,0,.09),0 0 0 1px rgba(17,24,39,.025)!important}}
.composer-foot>.health{{display:none!important}}
#send{{margin-left:auto!important;width:36px!important;height:36px!important;min-width:36px!important;padding:0!important;border-radius:999px!important;display:inline-grid!important;place-items:center!important;font-size:17px!important;line-height:1!important}}
#send.stop{{font-size:12px!important}}
#send:disabled{{background:#d9d9dd!important;color:#fff!important;opacity:1!important}}
.composer-more[open]>summary{{background:#f1f1f3!important;color:#252529!important}}
@media(max-width:760px){{#send{{width:34px!important;height:34px!important;min-width:34px!important}}}}
</style>'''
    document = document.replace("</head>", style + "</head>", 1)

    script = rf'''<script{nonce}>(function(){{
const send=document.getElementById('send');if(!send)return;
function syncSend(){{const stopping=send.classList.contains('stop');const label=stopping?'Остановить':'Отправить';const glyph=stopping?'■':'↑';if(send.textContent!==glyph)send.textContent=glyph;send.setAttribute('aria-label',label);send.title=label;}}
new MutationObserver(syncSend).observe(send,{{attributes:true,attributeFilter:['class','disabled'],childList:true,subtree:true}});syncSend();
}})();</script>'''
    return document.replace("</body>", script + "</body>", 1)


def _finalize(document: str) -> str:
    """Finalize HTML after layered user workspace composition.

    `user_ui` and task-solver surfaces build on top of the base workspace response.
    The main product patch therefore runs on the base response first; this final
    pass removes stale Fast copy and applies final composer behavior only after
    all user-facing layers have been composed.
    """
    if "OLYA_PRODUCT_SURFACE_V2" not in document:
        return document

    document = document.replace('<option value="fast">Fast</option>', "")
    document = document.replace(_ACCOUNT_MODE_COPY, _ACCOUNT_MODE_COPY_REPLACEMENT)
    document = document.replace("X1 · Поддержка", "OLYA AI · Поддержка")
    document = document.replace("← В X1", "← В OLYA AI")
    return _chat_polish(document)


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
