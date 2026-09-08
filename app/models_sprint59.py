from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

from sqlalchemy import Boolean, DateTime, ForeignKey, Index, JSON, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def new_id() -> str:
    return str(uuid4())


class ImageReference(Base):
    """User-owned image input used for editing, identity reference or a mask.

    The bytes live in the existing content-addressed ImageBlob store. Ownership
    and project access are deliberately separate from ImageBlob because blobs are
    globally deduplicated and therefore must never be used as an authorization
    primitive.
    """

    __tablename__ = "image_references"
    __table_args__ = (
        Index("ix_image_references_user_created", "user_id", "created_at"),
        Index("ix_image_references_project_created", "project_id", "created_at"),
        Index("ix_image_references_blob", "blob_id"),
        Index("ix_image_references_status", "status"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    user_id: Mapped[str] = mapped_column(String(36), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    project_id: Mapped[str | None] = mapped_column(String(36), ForeignKey("projects.id", ondelete="CASCADE"), nullable=True, index=True)
    blob_id: Mapped[str] = mapped_column(String(36), ForeignKey("image_blobs.id", ondelete="RESTRICT"), nullable=False)
    kind: Mapped[str] = mapped_column(String(24), nullable=False, default="edit_source")
    original_name: Mapped[str] = mapped_column(String(240), nullable=False, default="image.png")
    status: Mapped[str] = mapped_column(String(24), nullable=False, default="ready")
    metadata_json: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow, index=True)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class ImageEditRequest(Base):
    """Durable intent/plan/QA ledger for one image-edit generation."""

    __tablename__ = "image_edit_requests"
    __table_args__ = (
        UniqueConstraint("generation_id", name="uq_image_edit_generation"),
        Index("ix_image_edits_user_created", "user_id", "created_at"),
        Index("ix_image_edits_project_created", "project_id", "created_at"),
        Index("ix_image_edits_status", "status"),
        Index("ix_image_edits_source", "source_reference_id"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    generation_id: Mapped[str] = mapped_column(String(36), ForeignKey("image_generations.id", ondelete="CASCADE"), nullable=False, unique=True, index=True)
    user_id: Mapped[str] = mapped_column(String(36), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    project_id: Mapped[str | None] = mapped_column(String(36), ForeignKey("projects.id", ondelete="CASCADE"), nullable=True, index=True)
    source_reference_id: Mapped[str] = mapped_column(String(36), ForeignKey("image_references.id", ondelete="RESTRICT"), nullable=False)
    mask_reference_id: Mapped[str | None] = mapped_column(String(36), ForeignKey("image_references.id", ondelete="SET NULL"), nullable=True)
    identity_reference_id: Mapped[str | None] = mapped_column(String(36), ForeignKey("image_references.id", ondelete="SET NULL"), nullable=True)
    mode: Mapped[str] = mapped_column(String(32), nullable=False, default="auto")
    instruction: Mapped[str] = mapped_column(Text, nullable=False)
    preserve_identity: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    preserve_outside_mask: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    strict_quality: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    status: Mapped[str] = mapped_column(String(24), nullable=False, default="queued")
    plan: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    qa_summary: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    error_message: Mapped[str] = mapped_column(Text, nullable=False, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow, index=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)
