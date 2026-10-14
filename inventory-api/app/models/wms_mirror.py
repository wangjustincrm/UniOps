"""Read-only mirrors of the Flux WMS transaction history (design §4.7).

WMS keeps only CURRENT stock in INV_LOT, but ACT_TRANSACTION_LOG has every
completed movement since go-live (2025-02-03, never archived). Summing it per
SKU × lot reproduces INV_LOT for 99.97% of live lots (2026-10-14), so WMS
stock on any past date is computable — no daily snapshots needed.

`inv_wms_move` stores each transaction as signed quantity LEGS, already
applying the verified rules, so every consumer sums one column:

    IN  +TOQTY_EACH  on (TOSKU, TOLOTNUM)
    SO  −FMQTY_EACH  on (FMSKU, FMLOTNUM)
    AD  −FMQTY_EACH  on FM and +TOQTY_EACH on TO   (FM = before, TO = after)
    TR  −TOQTY_EACH  on FM and +TOQTY_EACH on TO   (★ TO quantity on BOTH legs:
                                                   FMQTY is the source pallet's
                                                   whole content, not the move)

Only STATUS '99' (completed) is mirrored; '90' are cancellations. *_EACH
columns only: plain FMQTY/TOQTY are in case/pallet units on 360 rows.
TR legs whose SKU and lot do not change (a location move) net to zero and
are skipped — counted, not mirrored.

WMS times are Toronto wall clock (the WMS database runs in America/Toronto).
"""
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import Date, DateTime, Index, Integer, Numeric, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class WmsMove(Base):
    __tablename__ = "inv_wms_move"

    move_id: Mapped[str] = mapped_column(String(24), primary_key=True)   # TRANSACTIONID + ':F'/':T'
    transactionid: Mapped[str] = mapped_column(String(20), index=True)
    txn_type: Mapped[str] = mapped_column(String(20))      # IN / SO / AD / TR
    doctype: Mapped[str | None] = mapped_column(String(10))
    docno: Mapped[str | None] = mapped_column(String(20), index=True)
    doclineno: Mapped[int | None] = mapped_column(Integer)
    sku: Mapped[str] = mapped_column(String(50))
    lotnum: Mapped[str | None] = mapped_column(String(10))
    qty: Mapped[Decimal] = mapped_column(Numeric(28, 8))    # signed
    txn_time: Mapped[datetime | None] = mapped_column(DateTime(timezone=False))  # Toronto wall clock
    reasoncode: Mapped[str | None] = mapped_column(String(20))
    reason: Mapped[str | None] = mapped_column(String(100))
    addwho: Mapped[str | None] = mapped_column(String(40))

    __table_args__ = (Index("ix_inv_wms_move_sku_time", "sku", "txn_time"),)


class WmsLot(Base):
    """INV_LOT_ATT for the Canada customer, all lots including depleted ones.
    att04 ≈ NC QC batch, att05 = supplier batch; the WMS batch key used for
    matching is both (design D10/D16)."""
    __tablename__ = "inv_wms_lot"

    sku: Mapped[str] = mapped_column(String(50), primary_key=True)
    lotnum: Mapped[str] = mapped_column(String(10), primary_key=True)
    qc_batch: Mapped[str | None] = mapped_column(String(100))        # LOTATT04
    supplier_batch: Mapped[str | None] = mapped_column(String(100))  # LOTATT05
    quality_status: Mapped[str | None] = mapped_column(String(100))  # LOTATT08
    produced: Mapped[date | None] = mapped_column(Date)              # LOTATT01
    expires: Mapped[date | None] = mapped_column(Date)               # LOTATT02
    received: Mapped[date | None] = mapped_column(Date)              # LOTATT03


class WmsDoc(Base):
    """Document headers referenced by the moves: ASN (inbound), SO
    (outbound), AJ (adjustment), TR (transfer). The reference fields carry
    the NC document numbers that make one-to-one pairing possible (design
    §4.8)."""
    __tablename__ = "inv_wms_doc"

    doc_kind: Mapped[str] = mapped_column(String(4), primary_key=True)   # ASN / SO / AJ / TR
    docno: Mapped[str] = mapped_column(String(20), primary_key=True)
    doc_type: Mapped[str | None] = mapped_column(String(20))   # ASNTYPE / ORDERTYPE / ADJTYPE / TDOCTYPE
    ref1: Mapped[str | None] = mapped_column(String(50))
    ref2: Mapped[str | None] = mapped_column(String(50))
    ref3: Mapped[str | None] = mapped_column(String(50))
    ref4: Mapped[str | None] = mapped_column(String(50))
    ref5: Mapped[str | None] = mapped_column(String(50))
    pono: Mapped[str | None] = mapped_column(String(20))
    udf01: Mapped[str | None] = mapped_column(String(500))
    hedi03: Mapped[str | None] = mapped_column(String(200))
    reasoncode: Mapped[str | None] = mapped_column(String(20))
    reason: Mapped[str | None] = mapped_column(String(100))
    added: Mapped[datetime | None] = mapped_column(DateTime(timezone=False))


class WmsSku(Base):
    """Each SKU's base unit in WMS: the EA level of its packaging ladder
    (BAS_PACKAGE_DETAILS.PACKUOM='EA' → UOMDESCR: KG / PIECES / EA). Not
    BAS_SKU's own unit columns — those say 'EA' everywhere and are
    unmaintained (same finding as mrp-api's WMS reader)."""
    __tablename__ = "inv_wms_sku"

    sku: Mapped[str] = mapped_column(String(50), primary_key=True)
    uom: Mapped[str | None] = mapped_column(String(40))


class WmsCode(Base):
    """BSM_CODE_ML labels for TRN_TYP / DOC_TYP / ASN_TYP / SO_TYP / ADJ_TYP."""
    __tablename__ = "inv_wms_code"

    codetype: Mapped[str] = mapped_column(String(50), primary_key=True)
    codeid: Mapped[str] = mapped_column(String(30), primary_key=True)
    languageid: Mapped[str] = mapped_column(String(10), primary_key=True)
    descr: Mapped[str | None] = mapped_column(String(800))
