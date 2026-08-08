"""Per-source sync watermark/status for mdm-api's NC-sourced CANONICAL syncs
(BOM explosion/where-used's `POST /boms/sync`, Task 7) — mirrors mrp-api's
`mrp_sync_state` idiom (app/models/sync_state.py there), NOT a reuse of this
service's existing `erp_sync_state` (see app/models/erp_sync_state.py).

Why a new table instead of extending `erp_sync_state`: that table's `kind`
column is purpose-built for the incremental ERP-interface pollers in
app/services/erp_sync.py ('material'/'supplier'/'person'/'uom_conversion') —
each row tracks a `last_ts` HIGH-WATER MARK used to compute the next
incremental fetch window, and `last_row_count` is a single int (one number
is enough, since those syncs upsert exactly one mirror table each). The NC
BOM sync is a different shape entirely: always a full scan (no watermark —
see nc_bom_sync/reader.py's docstring for why incremental isn't done there),
and its one meaningful result is a multi-field breakdown across THREE
canonical tables (`boms`/`bom_lines`/`bom_substitutes`) plus skip/warning/
tombstone tallies — exactly the dict `nc_bom_sync/canonical_sync.py's
sync_boms()` already returns. Bolting that onto `erp_sync_state` would mean
either dropping the breakdown into `last_message` as an ad-hoc string (the
design spec's "warnings/skipped must be visible" requirement needs real
structure, not a string to parse) or adding a `kind`-specific jsonb column
nothing else in that table needs. A dedicated `source`-keyed table with a
proper `last_stats` JSONB column — one row per NC-sourced canonical sync,
'nc_bom' today, room for e.g. 'nc_material'/'nc_vendor' later without a
schema change — is the same "future sources add rows" idiom `mrp_sync_state`
already established for exactly this kind of source, so this follows suit
instead of inventing a third shape.
"""
from datetime import datetime

from sqlalchemy import DateTime, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class NcSyncState(Base):
    __tablename__ = "nc_sync_state"

    source: Mapped[str] = mapped_column(String(20), primary_key=True)  # 'nc_bom' (Task 7); future NC-sourced canonical syncs add rows
    last_success_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    last_stats: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
