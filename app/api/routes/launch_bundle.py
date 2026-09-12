from fastapi import APIRouter

from app.api.routes.owner_integrations import router as owner_integrations_router
from app.api.routes.payment_providers import router as payment_providers_router
from app.api.routes.support import router as support_router
from app.support_ui import router as support_ui_router

router = APIRouter(tags=["launch-operations"])
router.include_router(owner_integrations_router)
router.include_router(payment_providers_router)
router.include_router(support_router)
router.include_router(support_ui_router)
