"""Controlled vocabularies — the lists HSE maintains in Settings.

Fifteen separate lists (immediate causes, root causes, hazards, PPE, shifts,
positions, body parts, nature of injury, safety equipment types, ...) would be
fifteen tables, fifteen sets of CRUD and fifteen screens. They are one table
instead, with an optional parent for the lists that are trees.

Two rules are enforced everywhere these are referenced:

1. **Entries are retired, never deleted.** Once a cause has been used on an
   investigation it must keep existing. `is_active=False` removes it from new
   forms and leaves history intact. There is no hard-delete endpoint.
2. **Referencing records store both the id and the label.** The id is what
   reports group by, so renaming "Unsafe Design" to "Inadequate Design" does
   not split a trend across two buckets. The label is what the record renders,
   so a 2026 investigation still reads in the words it was signed with.

Some vocabularies are system-locked (`is_system_locked`): the hierarchy of
controls is an international standard and the injury classes drive statutory
reporting and the injury-rate calculations, so their entries cannot be added
to or removed — only relabelled, and that is audited.
"""
import uuid

from sqlalchemy import Boolean, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin, UUIDPrimaryKey


class Vocabulary(TimestampMixin, Base):
    """A named list. The code is the primary key — it is referenced in code."""

    __tablename__ = "ehs_vocabularies"

    code: Mapped[str] = mapped_column(String(40), primary_key=True)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    is_hierarchical: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    is_system_locked: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    # Declares which extra attributes an entry in this list may carry, so the
    # Settings UI can render the right fields (e.g. a course's "statutory"
    # flag, a severity level's colour).
    attr_schema: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict, server_default="{}")


class VocabularyItem(UUIDPrimaryKey, TimestampMixin, Base):
    """One entry in a vocabulary. Self-referencing for the lists that are trees."""

    __tablename__ = "ehs_vocabulary_items"
    __table_args__ = (
        UniqueConstraint("vocabulary_code", "code", name="uq_ehs_vocab_item_code"),
    )

    vocabulary_code: Mapped[str] = mapped_column(
        String(40), ForeignKey("ehs_vocabularies.code", ondelete="RESTRICT"),
        nullable=False, index=True,
    )
    parent_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("ehs_vocabulary_items.id", ondelete="RESTRICT"),
        nullable=True, index=True,
    )
    code: Mapped[str] = mapped_column(String(60), nullable=False)
    label: Mapped[str] = mapped_column(String(200), nullable=False)
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    # Retire, never delete — see the module docstring.
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, index=True)
    attrs: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict, server_default="{}")
    # Materialized path so a tree filter is one LIKE instead of a recursive CTE.
    path: Mapped[str | None] = mapped_column(String(500), nullable=True)
