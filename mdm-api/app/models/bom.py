"""Canonical BOM tables — normalized from the nc_bom/nc_bom_b/nc_bom_repl raw
mirror (Task 4) via app/services/nc_bom_sync/transform.py (Task 5).

Column choices follow docs/superpowers/specs/2026-08-03-nc-bom-survey.md, not
the task-5-brief's guessed field names:
  - `bom_type` (milling/drymix/packaging) has NO reliable NC source column
    (FBOMTYPE is not a layer discriminator — survey §3). It is derived at
    transform time from the parent material CODE PREFIX (CS/CW/CF) and
    stored here so `GET /boms/effective` never needs to re-derive it or
    join back to nc_bom.
  - `effective_from`/`effective_to` live on `bom_lines`, NOT `boms` — NC's
    BD_BOM header has no date columns at all; the effective window is a
    line-level fact (BD_BOM_B.CBEGINPERIOD/CENDPERIOD, survey §8). `boms`
    intentionally has no effective_from/to columns; a header is "in scope"
    for a query date only through its lines.
  - `scrap_rate` defaults to 0 — NC's candidate loss columns
    (NBFIXSHRINKNUM/NBFIXSHRINKASTNUM/NDISSIPATIONUM) are NULL across all
    10169 sampled BD_BOM_B rows in this NC instance (survey §7). This is a
    real absence of data, not an unmapped field — do not wire a "percentage
    to decimal" conversion here without new evidence a loss field is
    populated.
  - Every table carries a unique `nc_source_pk` (CBOMID / CBOM_BID /
    CBOM_REPLACEID respectively) so the sync service can upsert
    idempotently, matching the nc_bom mirror's pattern.
  - `bom_lines.qty_per_secondary`/`uom_secondary` (migration 0014, PATCH 3):
    S-prefixed finished-good BOM lines carry a SECOND unit alongside the
    main `qty_per`/`uom` (<- NC BD_BOM_B.NASSITEMNUM/CASSMEASUREID, e.g.
    main unit KG + secondary unit PIECES, business-confirmed conversion
    lives in the material master). Both nullable — most lines are
    single-unit and never populate these. `uom`/`uom_secondary` now hold a
    real BD_MEASDOC unit CODE (e.g. 'KGM'), not the raw measure-doc PK the
    original sync left them as (PATCH 2); NC's EA/PIECES codes are
    normalized to one canonical 'EA' at transform time (PATCH 4) — see
    app/services/nc_bom_sync/transform.py's `_UOM_NORMALIZE`.
"""
from sqlalchemy import Date, ForeignKey, Integer, Numeric, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin, UUIDPrimaryKey


class Bom(Base, UUIDPrimaryKey, TimestampMixin):
    """A single BOM header/version (<- NC BD_BOM). One product code can have
    several `Bom` rows (multiple approved HVERSIONs coexisting — survey §8,
    e.g. CS0026 has 7 approved versions 1.0-1.6); `GET /boms/effective`
    picks among them by max version + line-level date coverage."""

    __tablename__ = "boms"
    __table_args__ = (UniqueConstraint("nc_source_pk", name="uq_boms_nc_pk"),)

    product_material_code: Mapped[str] = mapped_column(String(50), index=True)
    bom_type: Mapped[str | None] = mapped_column(String(20))  # milling/drymix/packaging/unknown
    version: Mapped[str | None] = mapped_column(String(30))
    factory_code: Mapped[str | None] = mapped_column(String(50))  # <- PK_ORG, opaque NC org pk (no human-readable map yet, survey §294)
    status: Mapped[str] = mapped_column(String(20), default="approved")  # FBILLSTATUS: 1->approved, -1->draft, other->inactive
    effective_from: Mapped[object | None] = mapped_column(Date)  # always None from sync; header has no NC source date (see docstring)
    effective_to: Mapped[object | None] = mapped_column(Date)
    yield_rate: Mapped[object] = mapped_column(Numeric(18, 6), default=1)  # <- HVCHANGERATE "num/den" parsed
    nc_source_pk: Mapped[str] = mapped_column(String(50))  # <- CBOMID

    lines: Mapped[list["BomLine"]] = relationship(back_populates="bom", cascade="all, delete-orphan")


class BomLine(Base, UUIDPrimaryKey, TimestampMixin):
    """A component line (<- NC BD_BOM_B). Carries its own effective window —
    see Bom's docstring for why dating lives here, not on the header."""

    __tablename__ = "bom_lines"
    __table_args__ = (UniqueConstraint("nc_source_pk", name="uq_bom_lines_nc_pk"),)

    bom_id: Mapped[str] = mapped_column(ForeignKey("boms.id", ondelete="CASCADE"), index=True)
    line_no: Mapped[int] = mapped_column(Integer, default=0)  # <- VROWNO, string->int
    component_material_code: Mapped[str] = mapped_column(String(50), index=True)
    qty_per: Mapped[object] = mapped_column(Numeric(18, 6))  # <- NITEMNUM, taken as-is (IBASENUM/VCHANGERATE observed 1:1)
    uom: Mapped[str | None] = mapped_column(String(20))  # <- CMEASUREID, resolved to a BD_MEASDOC unit code (e.g. 'KGM'); EA/PIECES normalized to 'EA'
    qty_per_secondary: Mapped[object | None] = mapped_column(Numeric(18, 6))  # <- NASSITEMNUM, assistant-unit qty (S* finished goods only; null for single-unit lines)
    uom_secondary: Mapped[str | None] = mapped_column(String(20))  # <- CASSMEASUREID, resolved unit code; same EA/PIECES normalization as `uom`
    scrap_rate: Mapped[object] = mapped_column(Numeric(10, 4), default=0)  # always 0 in this NC instance, see docstring
    effective_from: Mapped[object | None] = mapped_column(Date)  # <- CBEGINPERIOD
    effective_to: Mapped[object | None] = mapped_column(Date)  # <- CENDPERIOD (often 2999-12-31 = "long-term valid")
    nc_source_pk: Mapped[str | None] = mapped_column(String(50))  # <- CBOM_BID

    bom: Mapped[Bom] = relationship(back_populates="lines")
    substitutes: Mapped[list["BomSubstitute"]] = relationship(
        back_populates="line", cascade="all, delete-orphan"
    )


class BomSubstitute(Base, UUIDPrimaryKey, TimestampMixin):
    """A suggested substitute component (<- NC BD_BOM_REPL). Sparse by
    nature: 664 rows vs 10169 BD_BOM_B lines in the surveyed instance — that
    is expected, not a sync gap (survey §10)."""

    __tablename__ = "bom_substitutes"
    __table_args__ = (UniqueConstraint("nc_source_pk", name="uq_bom_substitutes_nc_pk"),)

    bom_line_id: Mapped[str] = mapped_column(ForeignKey("bom_lines.id", ondelete="CASCADE"), index=True)
    substitute_material_code: Mapped[str] = mapped_column(String(50))
    priority: Mapped[int] = mapped_column(Integer, default=1)  # <- VROWNO, string->int
    mode: Mapped[str] = mapped_column(String(10), default="suggest")  # NC has no active/suggest distinction; always 'suggest'
    nc_source_pk: Mapped[str | None] = mapped_column(String(50))  # <- CBOM_REPLACEID

    line: Mapped[BomLine] = relationship(back_populates="substitutes")
