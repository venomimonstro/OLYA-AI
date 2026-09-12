from __future__ import annotations

# One canonical business owner per cross-cutting domain. HTTP/UI modules are
# adapters and must not fork these policies into parallel state machines.
DOMAIN_OWNERS = {
    "authentication": "app.services.auth",
    "billing": "app.services.billing",
    "quota_entitlement": "app.services.quota",
    "plan_policy": "app.services.measured_plans",
    "admin_user_override": "app.services.admin_user_controls",
    "capability_availability": "app.services.capabilities",
    "inference_admission": "app.services.resource_governor",
    "request_deadline": "app.services.deadline",
    "agent_completion": "app.services.agent_contract",
    "agent_recovery": "app.services.autonomous_development",
    "image_beta_contract": "app.services.image_beta",
    "product_analytics": "app.services.product_analytics",
    "background_jobs": "app.services.jobs",
    "recovery_integrity": "app.services.recovery_integrity",
    "maintenance_reconciliation": "app.services.maintenance",
}


def domain_owner(domain: str) -> str:
    try:
        return DOMAIN_OWNERS[domain]
    except KeyError as exc:
        raise KeyError(f"Unknown X1 domain: {domain}") from exc
