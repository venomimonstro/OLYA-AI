"""Restore the missing Sprint 0-26 core schema baseline.

Revision ID: bff29ea4eab8
Revises: None
"""
from alembic import op
import sqlalchemy as sa

revision = "bff29ea4eab8"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table("users",
        sa.Column('id', sa.String(36), primary_key=True),
        sa.Column('email', sa.String(320), nullable=False),
        sa.Column('password_hash', sa.String(512), nullable=False),
        sa.Column('display_name', sa.String(120), nullable=False),
        sa.Column('is_active', sa.Boolean(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index('ix_users_email', 'users', ['email'], unique=True)
    op.create_index('ix_users_is_active', 'users', ['is_active'], unique=False)

    op.create_table("auth_sessions",
        sa.Column('id', sa.String(36), primary_key=True),
        sa.Column('user_id', sa.String(36), sa.ForeignKey('users.id', ondelete='CASCADE'), nullable=False),
        sa.Column('token_hash', sa.String(64), nullable=False),
        sa.Column('expires_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('last_seen_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('revoked_at', sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index('ix_auth_sessions_expires_at', 'auth_sessions', ['expires_at'], unique=False)
    op.create_index('ix_auth_sessions_token_hash', 'auth_sessions', ['token_hash'], unique=True)
    op.create_index('ix_auth_sessions_user_id', 'auth_sessions', ['user_id'], unique=False)

    op.create_table("projects",
        sa.Column('id', sa.String(36), primary_key=True),
        sa.Column('owner_id', sa.String(36), sa.ForeignKey('users.id', ondelete='RESTRICT'), nullable=False),
        sa.Column('name', sa.String(160), nullable=False),
        sa.Column('description', sa.Text(), nullable=False),
        sa.Column('instructions', sa.Text(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index('ix_projects_owner_id', 'projects', ['owner_id'], unique=False)

    op.create_table("project_members",
        sa.Column('id', sa.String(36), primary_key=True),
        sa.Column('project_id', sa.String(36), sa.ForeignKey('projects.id', ondelete='CASCADE'), nullable=False),
        sa.Column('user_id', sa.String(36), sa.ForeignKey('users.id', ondelete='CASCADE'), nullable=False),
        sa.Column('role', sa.String(24), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint('project_id', 'user_id', name='uq_project_member'),
    )
    op.create_index('ix_project_member_user_project', 'project_members', ['user_id', 'project_id'], unique=False)
    op.create_index('ix_project_members_project_id', 'project_members', ['project_id'], unique=False)
    op.create_index('ix_project_members_user_id', 'project_members', ['user_id'], unique=False)

    op.create_table("project_memories",
        sa.Column('id', sa.String(36), primary_key=True),
        sa.Column('project_id', sa.String(36), sa.ForeignKey('projects.id', ondelete='CASCADE'), nullable=False),
        sa.Column('key', sa.String(160), nullable=False),
        sa.Column('value', sa.Text(), nullable=False),
        sa.Column('source', sa.String(32), nullable=False),
        sa.Column('created_by', sa.String(36), sa.ForeignKey('users.id', ondelete='CASCADE'), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint('project_id', 'key', name='uq_project_memory_key'),
    )
    op.create_index('ix_project_memories_created_by', 'project_memories', ['created_by'], unique=False)
    op.create_index('ix_project_memories_project_id', 'project_memories', ['project_id'], unique=False)

    op.create_table("project_files",
        sa.Column('id', sa.String(36), primary_key=True),
        sa.Column('project_id', sa.String(36), sa.ForeignKey('projects.id', ondelete='CASCADE'), nullable=False),
        sa.Column('uploaded_by', sa.String(36), sa.ForeignKey('users.id', ondelete='RESTRICT'), nullable=False),
        sa.Column('logical_name', sa.String(240), nullable=False),
        sa.Column('original_name', sa.String(240), nullable=False),
        sa.Column('version', sa.Integer(), nullable=False),
        sa.Column('content_sha256', sa.String(64), nullable=False),
        sa.Column('media_type', sa.String(120), nullable=False),
        sa.Column('size_bytes', sa.Integer(), nullable=False),
        sa.Column('storage_path', sa.String(1024), nullable=False),
        sa.Column('status', sa.String(24), nullable=False),
        sa.Column('error_message', sa.Text(), nullable=False),
        sa.Column('is_current', sa.Boolean(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint('project_id', 'logical_name', 'version', name='uq_project_file_version'),
    )
    op.create_index('ix_project_file_project_current', 'project_files', ['project_id', 'is_current'], unique=False)
    op.create_index('ix_project_file_project_hash', 'project_files', ['project_id', 'content_sha256'], unique=False)
    op.create_index('ix_project_files_is_current', 'project_files', ['is_current'], unique=False)
    op.create_index('ix_project_files_project_id', 'project_files', ['project_id'], unique=False)
    op.create_index('ix_project_files_status', 'project_files', ['status'], unique=False)
    op.create_index('ix_project_files_uploaded_by', 'project_files', ['uploaded_by'], unique=False)

    op.create_table("file_chunks",
        sa.Column('id', sa.String(36), primary_key=True),
        sa.Column('file_id', sa.String(36), sa.ForeignKey('project_files.id', ondelete='CASCADE'), nullable=False),
        sa.Column('ordinal', sa.Integer(), nullable=False),
        sa.Column('page_number', sa.Integer(), nullable=True),
        sa.Column('content', sa.Text(), nullable=False),
        sa.Column('content_sha256', sa.String(64), nullable=False),
        sa.Column('char_count', sa.Integer(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint('file_id', 'ordinal', name='uq_file_chunk_ordinal'),
    )
    op.create_index('ix_file_chunk_file_ordinal', 'file_chunks', ['file_id', 'ordinal'], unique=False)
    op.create_index('ix_file_chunks_content_sha256', 'file_chunks', ['content_sha256'], unique=False)
    op.create_index('ix_file_chunks_file_id', 'file_chunks', ['file_id'], unique=False)

    op.create_table("tasks",
        sa.Column('id', sa.String(36), primary_key=True),
        sa.Column('project_id', sa.String(36), sa.ForeignKey('projects.id', ondelete='CASCADE'), nullable=False),
        sa.Column('created_by', sa.String(36), sa.ForeignKey('users.id', ondelete='RESTRICT'), nullable=False),
        sa.Column('title', sa.String(200), nullable=False),
        sa.Column('goal', sa.Text(), nullable=False),
        sa.Column('constraints', sa.JSON(), nullable=False),
        sa.Column('status', sa.String(24), nullable=False),
        sa.Column('current_step', sa.Text(), nullable=False),
        sa.Column('state_version', sa.Integer(), nullable=False),
        sa.Column('completed_steps', sa.Integer(), nullable=False),
        sa.Column('max_steps', sa.Integer(), nullable=False),
        sa.Column('compute_seconds_used', sa.Integer(), nullable=False),
        sa.Column('max_compute_seconds', sa.Integer(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('completed_at', sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index('ix_tasks_created_by', 'tasks', ['created_by'], unique=False)
    op.create_index('ix_tasks_project_id', 'tasks', ['project_id'], unique=False)
    op.create_index('ix_tasks_project_status', 'tasks', ['project_id', 'status'], unique=False)
    op.create_index('ix_tasks_project_updated', 'tasks', ['project_id', 'updated_at'], unique=False)
    op.create_index('ix_tasks_status', 'tasks', ['status'], unique=False)

    op.create_table("task_criteria",
        sa.Column('id', sa.String(36), primary_key=True),
        sa.Column('task_id', sa.String(36), sa.ForeignKey('tasks.id', ondelete='CASCADE'), nullable=False),
        sa.Column('ordinal', sa.Integer(), nullable=False),
        sa.Column('text', sa.Text(), nullable=False),
        sa.Column('required', sa.Boolean(), nullable=False),
        sa.Column('verification_method', sa.String(16), nullable=False),
        sa.Column('satisfied', sa.Boolean(), nullable=False),
        sa.Column('satisfied_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('verified_evidence_id', sa.String(36), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint('task_id', 'ordinal', name='uq_task_criterion_ordinal'),
    )
    op.create_index('ix_task_criteria_satisfied', 'task_criteria', ['satisfied'], unique=False)
    op.create_index('ix_task_criteria_task', 'task_criteria', ['task_id', 'ordinal'], unique=False)
    op.create_index('ix_task_criteria_task_id', 'task_criteria', ['task_id'], unique=False)

    op.create_table("task_evidence",
        sa.Column('id', sa.String(36), primary_key=True),
        sa.Column('task_id', sa.String(36), sa.ForeignKey('tasks.id', ondelete='CASCADE'), nullable=False),
        sa.Column('criterion_id', sa.String(36), sa.ForeignKey('task_criteria.id', ondelete='SET NULL'), nullable=True),
        sa.Column('kind', sa.String(64), nullable=False),
        sa.Column('source_ref', sa.Text(), nullable=False),
        sa.Column('summary', sa.Text(), nullable=False),
        sa.Column('content_sha256', sa.String(64), nullable=True),
        sa.Column('state', sa.String(16), nullable=False),
        sa.Column('created_by', sa.String(36), sa.ForeignKey('users.id', ondelete='SET NULL'), nullable=True),
        sa.Column('verifier', sa.String(120), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('verified_at', sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index('ix_task_evidence_content_sha256', 'task_evidence', ['content_sha256'], unique=False)
    op.create_index('ix_task_evidence_created_by', 'task_evidence', ['created_by'], unique=False)
    op.create_index('ix_task_evidence_criterion_id', 'task_evidence', ['criterion_id'], unique=False)
    op.create_index('ix_task_evidence_state', 'task_evidence', ['state'], unique=False)
    op.create_index('ix_task_evidence_task_created', 'task_evidence', ['task_id', 'created_at'], unique=False)
    op.create_index('ix_task_evidence_task_id', 'task_evidence', ['task_id'], unique=False)

    op.create_table("task_checkpoints",
        sa.Column('id', sa.String(36), primary_key=True),
        sa.Column('task_id', sa.String(36), sa.ForeignKey('tasks.id', ondelete='CASCADE'), nullable=False),
        sa.Column('sequence', sa.Integer(), nullable=False),
        sa.Column('task_state_version', sa.Integer(), nullable=False),
        sa.Column('reason', sa.String(200), nullable=False),
        sa.Column('current_step', sa.Text(), nullable=False),
        sa.Column('working_state', sa.JSON(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint('task_id', 'sequence', name='uq_task_checkpoint_sequence'),
    )
    op.create_index('ix_task_checkpoints_task_created', 'task_checkpoints', ['task_id', 'created_at'], unique=False)
    op.create_index('ix_task_checkpoints_task_id', 'task_checkpoints', ['task_id'], unique=False)

    op.create_table("conversations",
        sa.Column('id', sa.String(36), primary_key=True),
        sa.Column('owner_id', sa.String(36), sa.ForeignKey('users.id', ondelete='CASCADE'), nullable=False),
        sa.Column('project_id', sa.String(36), sa.ForeignKey('projects.id', ondelete='SET NULL'), nullable=True),
        sa.Column('title', sa.String(200), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index('ix_conversations_owner_id', 'conversations', ['owner_id'], unique=False)
    op.create_index('ix_conversations_project_id', 'conversations', ['project_id'], unique=False)
    op.create_index('ix_conversations_updated_at', 'conversations', ['updated_at'], unique=False)

    op.create_table("messages",
        sa.Column('id', sa.String(36), primary_key=True),
        sa.Column('conversation_id', sa.String(36), sa.ForeignKey('conversations.id', ondelete='CASCADE'), nullable=False),
        sa.Column('role', sa.String(32), nullable=False),
        sa.Column('content', sa.Text(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index('ix_messages_conversation_created', 'messages', ['conversation_id', 'created_at'], unique=False)
    op.create_index('ix_messages_conversation_id', 'messages', ['conversation_id'], unique=False)
    op.create_index('ix_messages_created_at', 'messages', ['created_at'], unique=False)

    op.create_table("usage_events",
        sa.Column('id', sa.String(36), primary_key=True),
        sa.Column('user_id', sa.String(36), sa.ForeignKey('users.id', ondelete='CASCADE'), nullable=False),
        sa.Column('project_id', sa.String(36), sa.ForeignKey('projects.id', ondelete='SET NULL'), nullable=True),
        sa.Column('conversation_id', sa.String(36), sa.ForeignKey('conversations.id', ondelete='SET NULL'), nullable=True),
        sa.Column('mode', sa.String(32), nullable=False),
        sa.Column('raw_chars', sa.Integer(), nullable=False),
        sa.Column('compiled_chars', sa.Integer(), nullable=False),
        sa.Column('output_chars', sa.Integer(), nullable=False),
        sa.Column('duration_ms', sa.Integer(), nullable=False),
        sa.Column('inference_ms', sa.Integer(), nullable=False),
        sa.Column('queue_ms', sa.Integer(), nullable=False),
        sa.Column('success', sa.Boolean(), nullable=False),
        sa.Column('request_id', sa.String(64), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index('ix_usage_events_conversation_id', 'usage_events', ['conversation_id'], unique=False)
    op.create_index('ix_usage_events_created_at', 'usage_events', ['created_at'], unique=False)
    op.create_index('ix_usage_events_project_id', 'usage_events', ['project_id'], unique=False)
    op.create_index('ix_usage_events_request_id', 'usage_events', ['request_id'], unique=False)
    op.create_index('ix_usage_events_success', 'usage_events', ['success'], unique=False)
    op.create_index('ix_usage_events_user_id', 'usage_events', ['user_id'], unique=False)

    op.create_table("user_quotas",
        sa.Column('user_id', sa.String(36), sa.ForeignKey('users.id', ondelete='CASCADE'), primary_key=True),
        sa.Column('plan', sa.String(32), nullable=False),
        sa.Column('monthly_compute_seconds_limit', sa.Integer(), nullable=False),
        sa.Column('max_concurrent_inference', sa.Integer(), nullable=False),
        sa.Column('max_concurrent_jobs', sa.Integer(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    )

    op.create_table("background_jobs",
        sa.Column('id', sa.String(36), primary_key=True),
        sa.Column('kind', sa.String(120), nullable=False),
        sa.Column('payload', sa.JSON(), nullable=False),
        sa.Column('result', sa.JSON(), nullable=False),
        sa.Column('user_id', sa.String(36), sa.ForeignKey('users.id', ondelete='SET NULL'), nullable=True),
        sa.Column('project_id', sa.String(36), sa.ForeignKey('projects.id', ondelete='SET NULL'), nullable=True),
        sa.Column('task_id', sa.String(36), sa.ForeignKey('tasks.id', ondelete='SET NULL'), nullable=True),
        sa.Column('status', sa.String(24), nullable=False),
        sa.Column('priority', sa.Integer(), nullable=False),
        sa.Column('max_attempts', sa.Integer(), nullable=False),
        sa.Column('attempt_count', sa.Integer(), nullable=False),
        sa.Column('idempotency_key', sa.String(255), nullable=True),
        sa.Column('available_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('lease_owner', sa.String(255), nullable=True),
        sa.Column('lease_token', sa.String(255), nullable=True),
        sa.Column('lease_expires_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('heartbeat_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('error_message', sa.Text(), nullable=False),
        sa.Column('started_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('finished_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint('idempotency_key'),
    )
    op.create_index('ix_background_jobs_available_at', 'background_jobs', ['available_at'], unique=False)
    op.create_index('ix_background_jobs_kind', 'background_jobs', ['kind'], unique=False)
    op.create_index('ix_background_jobs_lease_expires_at', 'background_jobs', ['lease_expires_at'], unique=False)
    op.create_index('ix_background_jobs_project_id', 'background_jobs', ['project_id'], unique=False)
    op.create_index('ix_background_jobs_ready', 'background_jobs', ['status', 'available_at', 'priority'], unique=False)
    op.create_index('ix_background_jobs_status', 'background_jobs', ['status'], unique=False)
    op.create_index('ix_background_jobs_task_id', 'background_jobs', ['task_id'], unique=False)
    op.create_index('ix_background_jobs_user_id', 'background_jobs', ['user_id'], unique=False)


def downgrade() -> None:
    op.drop_table("background_jobs")
    op.drop_table("user_quotas")
    op.drop_table("usage_events")
    op.drop_table("messages")
    op.drop_table("conversations")
    op.drop_table("task_checkpoints")
    op.drop_table("task_evidence")
    op.drop_table("task_criteria")
    op.drop_table("tasks")
    op.drop_table("file_chunks")
    op.drop_table("project_files")
    op.drop_table("project_memories")
    op.drop_table("project_members")
    op.drop_table("projects")
    op.drop_table("auth_sessions")
    op.drop_table("users")


