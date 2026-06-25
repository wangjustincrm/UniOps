"""Read-only mirror of file-api's `file_metadata` table.

vms-api lists visit attachments by querying this table directly (it can't
push a list-by-doc-id endpoint into file-api just for VMS). Same pattern
as the company_config mirror used for SMTP.

Registered with `Base.metadata` so test infra (Base.metadata.create_all)
materializes the table. Production alembic migrations DO NOT touch it —
the table is created by file-api.
"""
import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, Integer, String, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class FileMetadata(Base):
    """Read-only mirror — fields only as needed by vms-api."""
    __tablename__ = "file_metadata"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    original_filename: Mapped[str] = mapped_column(String(255), nullable=False)
    content_type:      Mapped[str] = mapped_column(String(100), nullable=False)
    file_size:         Mapped[int] = mapped_column(Integer, nullable=False)
    storage_path:      Mapped[str] = mapped_column(String(500), nullable=False)
    uploaded_by:       Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    service:           Mapped[str] = mapped_column(String(20), nullable=False)
    doc_type:          Mapped[str] = mapped_column(String(20), nullable=False)
    doc_id:            Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    is_deleted:        Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_at:        Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False,
    )
