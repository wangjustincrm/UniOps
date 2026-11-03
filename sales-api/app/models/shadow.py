"""Shadow-run comparison (design §5.4; migration sales09_shadow).

One row per Zoho reference 940 (`reference_document_id`), or per Sales order
no reference matched (`Sales only`, no reference); from P5 also one per real
997 / 945 read during the shadow run (`document_id`), matched to a reference
940 (or `No reference`), with what Sales would have recorded (`would`). `rows` is the side-by-side
diff [{"zoho", "sales", "same"}]; `diff_signature` identifies the set of
differences, so a person's "expected difference" (`expected_signature`,
`expected_reason`) holds only while the differences stay the same.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import DateTime, ForeignKey, Index, Integer, String, Text, select, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, column_property, mapped_column

from app.db.base import Base
from app.domain.shadow import STATUSES
from app.models.config import Partner, one_of
from app.platform.entities import Audited, Versioned


class ShadowMatch(Audited, Versioned, Base):
    __tablename__ = "sales_shadow_matches"
    __table_args__ = (
        one_of("status", STATUSES, "ck_sales_shadow_matches_status"),
        Index("uq_sales_shadow_matches_reference", "reference_document_id", unique=True,
              postgresql_where=text("reference_document_id IS NOT NULL AND kind = '940'")),
        Index("uq_sales_shadow_matches_sales_only", "order_id", unique=True,
              postgresql_where=text("reference_document_id IS NULL AND kind = '940'")),
        Index("uq_sales_shadow_matches_document", "document_id", unique=True,
              postgresql_where=text("document_id IS NOT NULL")),
    )

    kind: Mapped[str] = mapped_column(String(20), nullable=False)
    status: Mapped[str] = mapped_column(String(30), nullable=False)
    reference_document_id: Mapped[int | None] = mapped_column(ForeignKey("sales_documents.id"), nullable=True)
    order_id: Mapped[int | None] = mapped_column(ForeignKey("sales_orders.id"), nullable=True)
    reference_number: Mapped[str | None] = mapped_column(String(100), nullable=True)
    partner_code: Mapped[str | None] = mapped_column(String(50), nullable=True)
    sales_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    rows: Mapped[list[dict[str, Any]] | None] = mapped_column(JSONB, nullable=True)
    differences: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    diff_signature: Mapped[str | None] = mapped_column(String(64), nullable=True)
    expected_signature: Mapped[str | None] = mapped_column(String(64), nullable=True)
    expected_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    compared_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    # sales10: a 997 / 945 compared (kind "997" / "945") and what Sales would have recorded.
    document_id: Mapped[int | None] = mapped_column(ForeignKey("sales_documents.id"), nullable=True)
    would: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)

    @property
    def partner_name(self) -> str | None:  # loaded with the row (partner_name_sql); never a lazy load
        return self.__dict__.get("partner_name_sql")


# The partner's name on the match's row (Shadow compare shows and searches it).
ShadowMatch.partner_name_sql = column_property(
    select(Partner.name).where(Partner.code == ShadowMatch.partner_code).correlate_except(Partner).scalar_subquery())
