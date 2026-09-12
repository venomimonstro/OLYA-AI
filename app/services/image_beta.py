from __future__ import annotations

from sqlalchemy.orm import Session

from app.models import User
from app.services.capabilities import capability_decision


IMAGE_BETA_CONTRACT_VERSION = "x1-image-beta-v1"


def image_beta_contract(app, db: Session, user: User, *, live: bool = False) -> dict:
    generation = capability_decision(app, db, user, "images.generate", live=live)
    editing = capability_decision(app, db, user, "images.edit", live=live)
    return {
        "contract_version": IMAGE_BETA_CONTRACT_VERSION,
        "beta": True,
        "generation": generation,
        "editing": editing,
        "studio_available": bool(editing["available"]),
        "privacy": {
            "reference_delivery": "private_no_store",
            "generated_delivery": "authenticated_owner_or_project_acl",
            "training_default": "disabled",
            "training_requires_explicit_feedback_consent": True,
            "raw_reference_public_urls": False,
        },
        "quality": {
            "delivery_requires_generation_ready": True,
            "delivery_requires_qa_passed": True,
            "strict_edit_qa_server_owned": True,
            "failed_or_unverified_results_hidden": True,
        },
        "limits": {
            "storage_used_bytes": editing.get("details", {}).get("storage_used_bytes"),
            "storage_quota_bytes": editing.get("details", {}).get("storage_quota_bytes"),
            "active_jobs": editing.get("details", {}).get("active_jobs"),
            "max_active_jobs": editing.get("details", {}).get("max_active_jobs"),
        },
    }
