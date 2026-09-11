#!/usr/bin/env python3
from __future__ import annotations

"""Regenerate the readable ORM extension from the canonical Alembic chain."""

import importlib.util
from pathlib import Path
from typing import Any

import sqlalchemy as sa


ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "app" / "models_migrations.py"

TABLE_CLASSES = {
    "answer_audits": "AnswerAudit",
    "engineering_executions": "EngineeringExecution",
    "engineering_execution_events": "EngineeringExecutionEvent",
    "research_sources": "ResearchSource",
    "source_evidence": "SourceEvidence",
    "admin_audit_logs": "AdminAuditLog",
    "system_settings": "SystemSetting",
    "research_runs": "ResearchRun",
    "risk_events": "RiskEvent",
    "safety_cases": "SafetyCase",
    "user_restrictions": "UserRestriction",
    "legal_reviews": "LegalReview",
    "frustration_events": "FrustrationEvent",
    "search_query_cache": "SearchQueryCache",
    "search_provider_stats": "SearchProviderStat",
    "performance_snapshots": "PerformanceSnapshot",
    "optimization_experiments": "OptimizationExperiment",
    "image_safety_policies": "ImageSafetyPolicy",
    "image_policy_test_cases": "ImagePolicyTestCase",
    "project_sandbox_runs": "ProjectSandboxRun",
    "project_preview_sessions": "ProjectPreviewSession",
    "git_repository_bindings": "GitRepositoryBinding",
    "git_operations": "GitOperation",
    "document_artifacts": "DocumentArtifact",
    "document_revisions": "DocumentRevision",
    "document_qa_events": "DocumentQAEvent",
    "image_feedback": "ImageFeedback",
    "image_training_examples": "ImageTrainingExample",
    "image_dataset_snapshots": "ImageDatasetSnapshot",
    "image_improvement_runs": "ImageImprovementRun",
    "development_chat_sessions": "DevelopmentChatSession",
    "project_runtimes": "ProjectRuntime",
    "project_runtime_snapshots": "ProjectRuntimeSnapshot",
    "project_runtime_secrets": "ProjectRuntimeSecret",
    "code_workspaces": "CodeWorkspace",
    "code_agent_runs": "CodeAgentRun",
    "development_plans": "DevelopmentPlan",
    "development_sprints": "DevelopmentSprint",
    "development_work_items": "DevelopmentWorkItem",
    "architecture_decisions": "ArchitectureDecision",
    "development_checkpoints": "DevelopmentCheckpoint",
    "engineering_runs": "EngineeringRun",
    "engineering_role_turns": "EngineeringRoleTurn",
    "image_blobs": "ImageBlob",
    "image_generations": "ImageGeneration",
    "image_variants": "ImageVariant",
    "image_qa_events": "ImageQAEvent",
}

# These tables are owned by models_core.py or an explicit models_sprint*.py
# extension. Every other table created by Alembic must be generated here.
EXTERNAL_TABLES = {
    "users",
    "api_keys", "api_rate_limit_windows", "api_request_telemetry",
    "autonomous_development_checkpoints", "autonomous_development_ledgers",
    "beta_participants", "beta_snapshots", "beta_waves", "capacity_plans",
    "chat_runs", "circuit_breaker_events", "complaint_cases",
    "compute_breakdown_events", "conversation_memories", "image_edit_requests",
    "image_references", "measured_plan_catalogs", "organization_budgets",
    "organization_members", "organizations", "payment_records",
    "persistent_api_contexts", "product_events", "public_rollouts",
    "regression_cases", "regression_runs", "release_gate_decisions",
    "resource_expense_events", "system_checkpoints", "system_health_snapshots",
    "user_onboarding",
}

LIST_JSON_COLUMNS = {
    "checks", "warnings", "risk_event_ids", "requested_scope", "visited_urls",
    "commands", "results", "verification_results", "reasons",
}

# Alembic records database shape, while application defaults belong to the ORM.
# Keep the small semantic delta explicit and reviewable instead of guessing it.
ORM_DEFAULTS = {
    "research_sources.error_message": "''",
    "risk_events.state": "'open'",
    "safety_cases.decision": "''",
    "safety_cases.status": "'open'",
    "user_restrictions.active": "True",
    "legal_reviews.export_sha256": "''",
    "frustration_events.resolved": "False",
    "search_query_cache.hit_count": "0",
    "image_policy_test_cases.active": "True",
    "project_preview_sessions.container_ref": "''",
    "project_preview_sessions.failure_reason": "''",
    "project_preview_sessions.public_url": "''",
    "git_repository_bindings.credential_secret_name": "''",
    "git_repository_bindings.last_local_head": "''",
    "git_repository_bindings.last_remote_head": "''",
    "git_repository_bindings.push_enabled": "False",
    "git_repository_bindings.repository_name": "''",
    "git_repository_bindings.repository_owner": "''",
    "git_repository_bindings.repository_url": "''",
    "git_repository_bindings.status": "'local'",
    "git_repository_bindings.working_branch": "'main'",
    "git_operations.failure_reason": "''",
    "git_operations.head_after": "''",
    "git_operations.head_before": "''",
    "git_operations.remote_head": "''",
    "document_revisions.docx_path": "''",
    "document_revisions.docx_sha256": "''",
    "document_revisions.page_count": "0",
    "document_revisions.pdf_path": "''",
    "document_revisions.pdf_sha256": "''",
    "document_revisions.qa_status": "'pending'",
    "image_training_examples.review_note": "''",
    "image_dataset_snapshots.state": "'candidate'",
    "project_runtimes.isolation_backend": "'local'",
    "project_runtimes.status": "'ready'",
    "project_runtime_snapshots.state": "'ready'",
    "code_workspaces.file_count": "0",
    "code_workspaces.status": "'ready'",
    "code_workspaces.total_bytes": "0",
    "code_agent_runs.commands_used": "0",
    "code_agent_runs.status": "'created'",
    "development_plans.status": "'planned'",
    "development_sprints.status": "'planned'",
    "development_work_items.status": "'planned'",
    "architecture_decisions.status": "'accepted'",
    "image_generations.error_message": "''",
    "image_generations.moderation_note": "''",
    "image_generations.qa_status": "'pending'",
    "image_generations.repair_attempts": "0",
}


class _Batch:
    def __init__(self, table: str, recorder: "_Recorder") -> None:
        self.table = table
        self.recorder = recorder

    def __enter__(self):
        return self

    def __exit__(self, *_args) -> None:
        return None

    def add_column(self, column: sa.Column) -> None:
        self.recorder.add_column(self.table, column)

    def alter_column(self, name: str, **changes: Any) -> None:
        self.recorder.alter_column(self.table, name, **changes)

    def create_index(self, name: str, columns: list[str], unique: bool = False, **_kwargs) -> None:
        self.recorder.create_index(name, self.table, columns, unique=unique)

    def __getattr__(self, _name: str):
        return lambda *_args, **_kwargs: None


class _Recorder:
    def __init__(self) -> None:
        self.tables: dict[str, list[Any]] = {}
        self.indexes: dict[str, list[tuple[str, list[str], bool]]] = {}

    def create_table(self, name: str, *items: Any, **_kwargs) -> None:
        self.tables[name] = list(items)

    def add_column(self, table: str, column: sa.Column) -> None:
        self.tables.setdefault(table, []).append(column)

    def alter_column(self, table: str, name: str, **changes: Any) -> None:
        for item in self.tables.get(table, []):
            if isinstance(item, sa.Column) and item.name == name and "nullable" in changes:
                item.nullable = bool(changes["nullable"])

    def create_index(self, name: str, table: str, columns: list[str], unique: bool = False, **_kwargs) -> None:
        self.indexes.setdefault(table, []).append((name, list(columns), bool(unique)))

    def batch_alter_table(self, table: str, *_args, **_kwargs) -> _Batch:
        return _Batch(table, self)

    def __getattr__(self, _name: str):
        return lambda *_args, **_kwargs: None


def _migration_modules() -> list[Any]:
    modules: dict[str, Any] = {}
    for path in (ROOT / "alembic" / "versions").glob("*.py"):
        spec = importlib.util.spec_from_file_location(f"x1_migration_{path.stem}", path)
        if spec is None or spec.loader is None:
            raise RuntimeError(f"Cannot load migration: {path.name}")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        modules[str(module.revision)] = module
    ordered: list[Any] = []
    remaining = dict(modules)
    while remaining:
        ready = [
            module for module in remaining.values()
            if module.down_revision not in remaining
        ]
        if not ready:
            raise RuntimeError("Alembic migration graph contains a cycle")
        ready.sort(key=lambda module: str(module.revision))
        for module in ready:
            ordered.append(module)
            remaining.pop(str(module.revision))
    return ordered


def _type_source(column: sa.Column) -> str:
    kind = column.type
    if isinstance(kind, sa.Text):
        return "Text()"
    if isinstance(kind, sa.String):
        return f"String({kind.length})" if kind.length else "String()"
    if isinstance(kind, sa.Integer):
        return "Integer()"
    if isinstance(kind, sa.Float):
        return "Float()"
    if isinstance(kind, sa.Boolean):
        return "Boolean()"
    if isinstance(kind, sa.DateTime):
        return f"DateTime(timezone={bool(kind.timezone)})"
    if isinstance(kind, sa.JSON):
        return "JSON()"
    raise TypeError(f"Unsupported SQL type for {column.name}: {kind!r}")


def _server_default(column: sa.Column) -> str | None:
    clause = column.server_default
    if clause is None:
        return None
    raw = clause.arg
    class_name = type(raw).__name__
    if class_name == "True_":
        return "True"
    if class_name == "False_":
        return "False"
    value = str(raw).strip().strip("'\"")
    if isinstance(column.type, sa.Integer):
        try:
            return str(int(value))
        except ValueError:
            return None
    if isinstance(column.type, sa.Float):
        try:
            return repr(float(value))
        except ValueError:
            return None
    if isinstance(column.type, (sa.String, sa.Text)):
        return repr(value)
    if isinstance(column.type, sa.JSON):
        return "dict"
    return None


def _column_source(table: str, column: sa.Column, primary_keys: set[str], foreign_keys: dict[str, tuple[str, str | None]]) -> str:
    args = [repr(column.name), _type_source(column)]
    target = foreign_keys.get(column.name)
    if target is not None:
        reference, ondelete = target
        suffix = f", ondelete={ondelete!r}" if ondelete else ""
        args.append(f"ForeignKey({reference!r}{suffix})")
    options: list[str] = []
    is_primary = bool(column.primary_key or column.name in primary_keys)
    if is_primary:
        options.append("primary_key=True")
    if column.nullable is not None and not is_primary:
        options.append(f"nullable={bool(column.nullable)}")
    if column.unique:
        options.append("unique=True")
    if column.name == "id" and isinstance(column.type, sa.String):
        options.append("default=new_id")
    elif f"{table}.{column.name}" in ORM_DEFAULTS:
        options.append(f"default={ORM_DEFAULTS[f'{table}.{column.name}']}")
    elif isinstance(column.type, sa.DateTime) and not column.nullable:
        options.append("default=utcnow")
        if column.name == "updated_at":
            options.append("onupdate=utcnow")
    elif isinstance(column.type, sa.JSON) and not column.nullable:
        options.append("default=list" if column.name in LIST_JSON_COLUMNS else "default=dict")
    else:
        default = _server_default(column)
        if default is not None:
            options.append(f"default={default}")
    return "    Column(" + ", ".join(args + options) + "),"


def generate() -> str:
    recorder = _Recorder()
    for module in _migration_modules():
        module.op = recorder
        module.upgrade()

    unowned = set(recorder.tables) - set(TABLE_CLASSES) - EXTERNAL_TABLES
    if unowned:
        raise RuntimeError(f"Alembic tables have no ORM owner: {sorted(unowned)}")

    lines = [
        "# Generated by scripts/generate_orm_models.py; do not edit manually.",
        "from sqlalchemy import Boolean, Column, DateTime, Float, ForeignKey, Index, Integer, JSON, String, Text, UniqueConstraint",
        "",
        "from app.models_core import _declare, new_id, utcnow",
        "",
    ]
    for table, class_name in TABLE_CLASSES.items():
        items = recorder.tables.get(table)
        if not items:
            raise RuntimeError(f"Migration schema missing for {table}")
        columns = [item for item in items if isinstance(item, sa.Column)]
        constraints = [item for item in items if isinstance(item, sa.Constraint)]
        primary_keys: set[str] = set()
        foreign_keys: dict[str, tuple[str, str | None]] = {}
        unique_constraints: list[sa.UniqueConstraint] = []
        for constraint in constraints:
            if isinstance(constraint, sa.PrimaryKeyConstraint):
                primary_keys.update(str(name) for name in constraint._pending_colargs)
            elif isinstance(constraint, sa.ForeignKeyConstraint):
                local_names = [str(name) for name in constraint._pending_colargs]
                for local, remote in zip(local_names, constraint.elements):
                    foreign_keys[local] = (remote.target_fullname, constraint.ondelete)
            elif isinstance(constraint, sa.UniqueConstraint):
                unique_constraints.append(constraint)
        for column in columns:
            for key in column.foreign_keys:
                foreign_keys[column.name] = (key.target_fullname, key.ondelete)
        lines.append(f"{class_name} = _declare(")
        lines.append(f"    {class_name!r}, {table!r},")
        for column in columns:
            lines.append(_column_source(table, column, primary_keys, foreign_keys))
        for constraint in unique_constraints:
            names = ", ".join(repr(str(name)) for name in constraint._pending_colargs)
            suffix = f", name={constraint.name!r}" if constraint.name else ""
            lines.append(f"    UniqueConstraint({names}{suffix}),")
        for name, names, unique in recorder.indexes.get(table, []):
            args = ", ".join(repr(column) for column in names)
            suffix = ", unique=True" if unique else ""
            lines.append(f"    Index({name!r}, {args}{suffix}),")
        if any(column.name == "state_version" for column in columns):
            lines.append("    versioned=True,")
        lines.append(")")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def main() -> int:
    source = generate()
    if "--check" in __import__("sys").argv:
        return 0 if OUTPUT.is_file() and OUTPUT.read_text("utf-8") == source else 2
    OUTPUT.write_text(source, "utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
