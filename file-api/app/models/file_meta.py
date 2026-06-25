"""File metadata — the only table owned by file-api."""
import uuid
from sqlalchemy import Boolean, Integer, String
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column
from app.db.base import Base, TimestampMixin, UUIDPrimaryKey


class FileMetadata(UUIDPrimaryKey, TimestampMixin, Base):
    __tablename__ = "file_metadata"

    original_filename: Mapped[str] = mapped_column(String(255), nullable=False)
    content_type: Mapped[str] = mapped_column(String(100), nullable=False)
    file_size: Mapped[int] = mapped_column(Integer, nullable=False)
    # Path relative to STORAGE_ROOT, e.g. "2026/04/uuid.pdf"
    storage_path: Mapped[str] = mapped_column(String(500), nullable=False)
    uploaded_by: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    # Which service and document this file belongs to
    service: Mapped[str] = mapped_column(String(50), nullable=False)   # "epms"
    doc_type: Mapped[str] = mapped_column(String(20), nullable=False)  # "pr" | "po" | "pa" | "gr"
    doc_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False, index=True)
    is_deleted: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
