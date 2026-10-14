"""Sync bookkeeping: one state row per source, and the tie-out findings of the
latest run."""
from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import BigInteger, Boolean, DateTime, Integer, Numeric, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class InvSyncState(Base):
    __tablename__ = "inv_sync_state"

    source: Mapped[str] = mapped_column(String(20), primary_key=True)  # 'nc' (later: 'wms_txn')
    status: Mapped[str | None] = mapped_column(String(20))  # 'success' | 'failed'
    last_error: Mapped[str | None] = mapped_column(Text)
    # Rows mirrored per table, and rows NC handed us that were deliberately
    # NOT mirrored (deleted, discarded …) — "filtered rows must be counted and
    # findable" is a red line of this module (design §3).
    row_counts: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    filtered_counts: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    duration_ms: Mapped[int | None] = mapped_column(Integer)
    # Last ATTEMPT (drives the scheduler's due check) vs last attempt that
    # actually replaced the mirror (drives every "data as of" on screen).
    # mrp-api learned the difference the hard way — see its migration mrp16.
    last_synced_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_success_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    tieout_ok: Mapped[bool | None] = mapped_column(Boolean)
    tieout_issue_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now())


class InvTieoutIssue(Base):
    """One key that did not tie out in the latest successful sync. Replaced on
    every sync; an empty table plus `tieout_ok=True` is the positive proof."""
    __tablename__ = "inv_tieout_issue"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    check_name: Mapped[str] = mapped_column(String(40), index=True)
    key: Mapped[str] = mapped_column(Text)
    label: Mapped[str | None] = mapped_column(Text)
    mirror_value: Mapped[Decimal | None] = mapped_column(Numeric(28, 8))
    nc_value: Mapped[Decimal | None] = mapped_column(Numeric(28, 8))
    measure: Mapped[str] = mapped_column(String(10))  # 'qty' | 'amount'
