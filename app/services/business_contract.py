from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import (
    AdminUserControl,
    ApiKey,
    AutonomousDevelopmentLedger,
    BillingSubscription,
    DevelopmentPlan,
    Organization,
    OrganizationMember,
    Project,
    ProjectMember,
    Task,
    User,
    UserQuota,
)
from app.services.access import ROLE_RANK
from app.services.admin_user_controls import control_is_active
from app.services.agent_contract import agent_completion_blockers
from app.services.measured_plans import runtime_plan_catalog
from app.services.tasks import completion_blockers

CONTRACT_VERSION = "x1-business-contract-v1"
_ORG_MEMBER_ROLES = {"member", "manager"}


def _aware(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


def _block(blockers: list[dict[str, Any]], code: str, kind: str, subject_id: str, message: str, **details: Any) -> None:
    blockers.append(
        {
            "code": code,
            "subject_type": kind,
            "subject_id": str(subject_id),
            "message": message,
            "details": details,
        }
    )


def _organization_api_access(db: Session, org: Organization, user_id: str) -> bool:
    if org.owner_id == user_id:
        return True
    role = db.scalar(
        select(OrganizationMember.role).where(
            OrganizationMember.organization_id == org.id,
            OrganizationMember.user_id == user_id,
        )
    )
    return role == "manager"


def evaluate_business_contract(db: Session, settings) -> dict[str, Any]:
    """Validate durable cross-domain business invariants without changing state."""
    now = datetime.now(timezone.utc)
    blockers: list[dict[str, Any]] = []
    catalog = runtime_plan_catalog(db, settings)
    plan_names = {str(item.get("name") or "") for item in catalog}

    users = {row.id: row for row in db.scalars(select(User)).all()}
    quotas = {row.user_id: row for row in db.scalars(select(UserQuota)).all()}
    subscriptions = {row.user_id: row for row in db.scalars(select(BillingSubscription)).all()}
    controls = {row.user_id: row for row in db.scalars(select(AdminUserControl)).all()}

    # Billing -> entitlement -> admin override -> effective quota is one ordered contract.
    for user_id, user in users.items():
        subscription = subscriptions.get(user_id)
        base_plan = "free"
        if subscription is not None:
            start = _aware(subscription.current_period_start)
            end = _aware(subscription.current_period_end)
            if start is None or end is None or start >= end:
                _block(blockers, "billing_period_invalid", "user", user_id, "Billing period is invalid")
            if subscription.plan not in plan_names or subscription.plan == "free":
                _block(blockers, "billing_plan_unknown", "user", user_id, "Subscription plan is not a paid runtime plan", plan=subscription.plan)
            if subscription.status == "active":
                if end is None or end <= now:
                    _block(blockers, "active_subscription_expired", "user", user_id, "Active subscription period already ended")
                else:
                    base_plan = subscription.plan

        control = controls.get(user_id)
        active_control = control if control_is_active(control, now=now) else None
        expected_plan = active_control.plan_override if active_control is not None and active_control.plan_override else base_plan
        if expected_plan not in plan_names:
            _block(blockers, "effective_plan_unknown", "user", user_id, "Effective plan is absent from runtime catalog", plan=expected_plan)

        quota = quotas.get(user_id)
        entitlement_requires_quota = base_plan != "free" or active_control is not None
        if quota is None:
            if entitlement_requires_quota:
                _block(blockers, "quota_missing_for_entitlement", "user", user_id, "Paid/admin entitlement has no UserQuota row", expected_plan=expected_plan)
            continue
        if quota.plan != expected_plan:
            _block(blockers, "quota_plan_mismatch", "user", user_id, "UserQuota plan disagrees with canonical billing/admin entitlement", expected_plan=expected_plan, actual_plan=quota.plan)
        if quota.monthly_compute_seconds_limit < 0:
            _block(blockers, "quota_compute_negative", "user", user_id, "Compute quota cannot be negative")
        if quota.max_concurrent_inference < 1 or quota.max_concurrent_jobs < 1:
            _block(blockers, "quota_concurrency_invalid", "user", user_id, "Quota concurrency must be positive")

    for user_id, subscription in subscriptions.items():
        if user_id not in users:
            _block(blockers, "subscription_user_missing", "subscription", subscription.id, "Subscription references a missing user")

    for user_id, control in controls.items():
        if user_id not in users:
            _block(blockers, "admin_control_user_missing", "admin_user_control", user_id, "Admin override references a missing user")
        if control.plan_override and control.plan_override not in plan_names:
            _block(blockers, "admin_control_plan_unknown", "admin_user_control", user_id, "Admin plan override is absent from runtime catalog", plan=control.plan_override)
        if all(value is None for value in (control.plan_override, control.monthly_compute_seconds_limit, control.max_concurrent_inference, control.max_concurrent_jobs)):
            _block(blockers, "empty_admin_control", "admin_user_control", user_id, "Admin override row contains no override")

    # Organization/API ownership must remain valid after membership changes.
    organizations = {row.id: row for row in db.scalars(select(Organization)).all()}
    for org in organizations.values():
        if org.owner_id not in users:
            _block(blockers, "organization_owner_missing", "organization", org.id, "Organization owner does not exist")
        if org.plan not in plan_names:
            _block(blockers, "organization_plan_unknown", "organization", org.id, "Organization plan is absent from runtime catalog", plan=org.plan)

    for member in db.scalars(select(OrganizationMember)).all():
        org = organizations.get(member.organization_id)
        if org is None:
            _block(blockers, "organization_member_org_missing", "organization_member", member.id, "Organization member references a missing organization")
            continue
        if member.user_id not in users:
            _block(blockers, "organization_member_user_missing", "organization_member", member.id, "Organization member references a missing user")
        if member.role not in _ORG_MEMBER_ROLES:
            _block(blockers, "organization_member_role_invalid", "organization_member", member.id, "Organization member role is invalid", role=member.role)
        if member.user_id == org.owner_id:
            _block(blockers, "organization_owner_duplicated_as_member", "organization_member", member.id, "Organization owner must not have a parallel member row")

    for key in db.scalars(select(ApiKey)).all():
        if key.owner_id not in users:
            _block(blockers, "api_key_owner_missing", "api_key", key.id, "API key owner does not exist")
        if key.rate_limit_per_minute < 1 or key.rate_limit_per_minute > 600:
            _block(blockers, "api_key_rate_invalid", "api_key", key.id, "API key rate limit is outside the supported contract", rate=key.rate_limit_per_minute)
        if not isinstance(key.scopes, list) or not key.scopes:
            _block(blockers, "api_key_scope_empty", "api_key", key.id, "API key must have at least one scope")
        if key.status == "active" and key.revoked_at is not None:
            _block(blockers, "api_key_active_but_revoked", "api_key", key.id, "Active API key has revoked_at")
        expires = _aware(key.expires_at)
        if key.status == "active" and expires is not None and expires <= now:
            _block(blockers, "api_key_active_but_expired", "api_key", key.id, "Expired API key is still marked active")
        if key.organization_id:
            org = organizations.get(key.organization_id)
            if org is None:
                _block(blockers, "api_key_org_missing", "api_key", key.id, "Organization API key references a missing organization")
            elif not _organization_api_access(db, org, key.owner_id):
                _block(blockers, "api_key_org_access_revoked", "api_key", key.id, "Organization API key owner no longer has manager/owner access", organization_id=org.id)

    # Project/task truth is evidence based, never prose/status alone.
    projects = {row.id: row for row in db.scalars(select(Project)).all()}
    for project in projects.values():
        if project.owner_id not in users:
            _block(blockers, "project_owner_missing", "project", project.id, "Project owner does not exist")
    for member in db.scalars(select(ProjectMember)).all():
        if member.project_id not in projects:
            _block(blockers, "project_member_project_missing", "project_member", member.id, "Project member references a missing project")
        if member.user_id not in users:
            _block(blockers, "project_member_user_missing", "project_member", member.id, "Project member references a missing user")
        if member.role not in ROLE_RANK:
            _block(blockers, "project_member_role_invalid", "project_member", member.id, "Project member role is invalid", role=member.role)

    tasks = list(db.scalars(select(Task)).all())
    for task in tasks:
        if task.project_id not in projects:
            _block(blockers, "task_project_missing", "task", task.id, "Task references a missing project")
        if task.completed_steps > task.max_steps:
            _block(blockers, "task_step_budget_exceeded", "task", task.id, "Task completed_steps exceeds max_steps")
        if task.compute_seconds_used > task.max_compute_seconds:
            _block(blockers, "task_compute_budget_exceeded", "task", task.id, "Task compute usage exceeds max_compute_seconds")
        if task.status == "completed":
            if task.completed_at is None:
                _block(blockers, "completed_task_missing_timestamp", "task", task.id, "Completed task has no completed_at")
            for reason in completion_blockers(db, task):
                _block(blockers, "completed_task_evidence_invalid", "task", task.id, "Completed task fails its evidence contract", reason=reason)
        elif task.completed_at is not None:
            _block(blockers, "noncompleted_task_has_timestamp", "task", task.id, "Non-completed task has completed_at", status=task.status)

    # Agent completion is independently re-proved from canonical tasks/evidence/execution.
    plans = list(db.scalars(select(DevelopmentPlan)).all())
    for plan in plans:
        if plan.status == "completed":
            for reason in agent_completion_blockers(db, plan.id):
                _block(blockers, "agent_plan_completion_unproven", "development_plan", plan.id, "Completed development plan fails independent proof", reason=reason)
    for ledger in db.scalars(select(AutonomousDevelopmentLedger)).all():
        if ledger.status == "completed":
            gate = dict((ledger.current_state or {}).get("completion_gate") or {})
            if gate.get("ready") is not True:
                _block(blockers, "agent_ledger_completion_unproven", "autonomous_ledger", ledger.id, "Completed autonomous ledger has no green completion gate")
            contract = dict((ledger.current_state or {}).get("contract") or {})
            if contract.get("drift_detected") is True:
                _block(blockers, "agent_ledger_contract_drift", "autonomous_ledger", ledger.id, "Completed autonomous ledger has immutable-contract drift")

    categories: dict[str, int] = {}
    for blocker in blockers:
        prefix = str(blocker["code"]).split("_", 1)[0]
        categories[prefix] = categories.get(prefix, 0) + 1
    return {
        "contract_version": CONTRACT_VERSION,
        "status": "passed" if not blockers else "failed",
        "checked_at": now.isoformat(),
        "catalog_plans": sorted(plan_names),
        "counts": {
            "users": len(users),
            "subscriptions": len(subscriptions),
            "quotas": len(quotas),
            "organizations": len(organizations),
            "projects": len(projects),
            "tasks": len(tasks),
            "development_plans": len(plans),
            "blockers": len(blockers),
        },
        "blockers_by_category": categories,
        "blockers": blockers[:500],
        "truncated": len(blockers) > 500,
    }
