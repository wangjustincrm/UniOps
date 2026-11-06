"""Read-only mirrors of the NC65 tables the Inventory module reads.

Every table is replaced wholesale on each sync (DELETE + INSERT in one
transaction — app/services/nc_sync/service.py). The volumes are small
(~75k IC flow rows, ~70k live IA ledger rows, ~110k GL inventory lines, all
years) and a full replace sidesteps the one thing an incremental sync of NC
gets wrong: rows NC deletes (dr=1) or vouchers it discards never reach a
watermark-based extract, so they would linger here as phantom stock.

Column names keep NC's spelling where the column is NC's, so a reader with an
NC data dictionary open can follow along. Two kinds of derived column exist:

* `*_time` — NC stores business dates as 'YYYY-MM-DD HH:MI:SS' strings in the
  NC application server's wall clock (China time, design D14). They are parsed
  to a naive `timestamp` holding that same wall-clock value; conversion to
  Toronto happens at query time, because the zone is a setting the user can
  flip (Settings → NC time zone).
* NC's CHAR(n) padding and the '~' it uses for "empty" are normalised to NULL
  on the way in (services/nc_sync/transform.py), so `IS NULL` means empty.
"""
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import Boolean, Date, DateTime, Index, Integer, Numeric, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base

PK = String(20)
QTY = Numeric(28, 8)
AMT = Numeric(28, 8)


class NcIcFlow(Base):
    """IC_FLOW — every stock movement since NC went live (2020-08)."""
    __tablename__ = "inv_nc_ic_flow"

    pk_flow: Mapped[str] = mapped_column(PK, primary_key=True)
    cwarehouseid: Mapped[str | None] = mapped_column(PK)
    cotherwhid: Mapped[str | None] = mapped_column(PK)  # the other side of a 4I/4A transfer pair
    cmaterialoid: Mapped[str | None] = mapped_column(PK)
    vbatchcode: Mapped[str | None] = mapped_column(String(200))
    pk_batchcode: Mapped[str | None] = mapped_column(PK)
    cstateid: Mapped[str | None] = mapped_column(PK)
    ninnum: Mapped[Decimal | None] = mapped_column(QTY)
    noutnum: Mapped[Decimal | None] = mapped_column(QTY)
    dbizdate: Mapped[str | None] = mapped_column(String(19))   # raw, as NC wrote it
    biz_time: Mapped[datetime | None] = mapped_column(DateTime(timezone=False))  # NC wall clock
    cbilltypecode: Mapped[str | None] = mapped_column(String(20))
    vtrantypecode: Mapped[str | None] = mapped_column(String(40))
    ctrantypeid: Mapped[str | None] = mapped_column(PK)
    vbillcode: Mapped[str | None] = mapped_column(String(60))
    cgeneralhid: Mapped[str | None] = mapped_column(PK)
    cgeneralbid: Mapped[str | None] = mapped_column(PK)
    vsourcebillcode: Mapped[str | None] = mapped_column(String(60))
    vfirstbillcode: Mapped[str | None] = mapped_column(String(60))
    vheaduserdef6: Mapped[str | None] = mapped_column(String(101))
    vheaduserdef13: Mapped[str | None] = mapped_column(String(101))
    fbillflag: Mapped[int | None] = mapped_column(Integer)
    taudittime: Mapped[str | None] = mapped_column(String(19))
    ts: Mapped[str | None] = mapped_column(String(19))

    __table_args__ = (
        Index("ix_inv_nc_ic_flow_mat_time", "cmaterialoid", "biz_time"),
        Index("ix_inv_nc_ic_flow_wh", "cwarehouseid"),
    )


class NcIcOnhand(Base):
    """IC_ONHANDDIM ⋈ IC_ONHANDNUM — current on-hand, the only place NC keeps
    the stock *state* and the *lock* (冻结) quantity. Rows where on-hand,
    lock and reserve are all zero are not mirrored."""
    __tablename__ = "inv_nc_ic_onhand"

    pk_onhanddim: Mapped[str] = mapped_column(PK, primary_key=True)
    cwarehouseid: Mapped[str | None] = mapped_column(PK)
    cmaterialoid: Mapped[str | None] = mapped_column(PK)
    vbatchcode: Mapped[str | None] = mapped_column(String(200))
    pk_batchcode: Mapped[str | None] = mapped_column(PK)
    cstateid: Mapped[str | None] = mapped_column(PK)
    clocationid: Mapped[str | None] = mapped_column(PK)
    cvendorid: Mapped[str | None] = mapped_column(PK)
    nonhandnum: Mapped[Decimal | None] = mapped_column(QTY)
    nlocknum: Mapped[Decimal | None] = mapped_column(QTY)
    nrsnum: Mapped[Decimal | None] = mapped_column(QTY)


class NcBatch(Base):
    """SCM_BATCHCODE — the batch master. `vbatchcode` is NC's QC batch,
    `vvendbatchcode` the supplier batch; matching against WMS uses both
    (design D10/D16)."""
    __tablename__ = "inv_nc_batch"

    pk_batchcode: Mapped[str] = mapped_column(PK, primary_key=True)
    cmaterialoid: Mapped[str | None] = mapped_column(PK)
    vbatchcode: Mapped[str | None] = mapped_column(String(200))
    vvendbatchcode: Mapped[str | None] = mapped_column(String(200))
    dproducedate: Mapped[date | None] = mapped_column(Date)
    dvalidate: Mapped[date | None] = mapped_column(Date)
    dinbounddate: Mapped[date | None] = mapped_column(Date)


class NcWarehouse(Base):
    """BD_STORDOC for the Canada stock org. `iscalculatedinvcost=False` marks
    a warehouse whose stock never reaches IA (SC16 today) — design C4."""
    __tablename__ = "inv_nc_warehouse"

    pk_stordoc: Mapped[str] = mapped_column(PK, primary_key=True)
    code: Mapped[str] = mapped_column(String(40))
    name: Mapped[str | None] = mapped_column(String(300))
    iscalculatedinvcost: Mapped[bool] = mapped_column(Boolean)
    enablestate: Mapped[int | None] = mapped_column(Integer)


class NcStoreState(Base):
    """IC_STORESTATE — Release / Block / Under Inspection / N/A …"""
    __tablename__ = "inv_nc_store_state"

    pk_storestate: Mapped[str] = mapped_column(PK, primary_key=True)
    vcode: Mapped[str | None] = mapped_column(String(40))
    vname: Mapped[str | None] = mapped_column(String(300))


class NcMaterial(Base):
    """BD_MATERIAL (+ unit and basic class). `name` is ENAME falling back to
    NAME — the webapi-fed MDM mirror only ever took ENAME and so has blanks
    (see the 2026-08-25 NC MDM survey)."""
    __tablename__ = "inv_nc_material"

    pk_material: Mapped[str] = mapped_column(PK, primary_key=True)
    code: Mapped[str] = mapped_column(String(60), index=True)
    name: Mapped[str | None] = mapped_column(String(300))
    name_local: Mapped[str | None] = mapped_column(String(300))
    uom_code: Mapped[str | None] = mapped_column(String(40))
    uom_name: Mapped[str | None] = mapped_column(String(100))
    class_code: Mapped[str | None] = mapped_column(String(60))
    class_name: Mapped[str | None] = mapped_column(String(300))


class NcTranType(Base):
    """BD_BILLTYPE rows referenced as transaction types by IC_FLOW or the IA
    ledger — gives the trace page a human name for '4I-Cxx-005'."""
    __tablename__ = "inv_nc_trantype"

    pk_billtypeid: Mapped[str] = mapped_column(PK, primary_key=True)
    code: Mapped[str | None] = mapped_column(String(60))
    name: Mapped[str | None] = mapped_column(String(300))       # BILLTYPENAME
    name_alt: Mapped[str | None] = mapped_column(String(300))   # BILLTYPENAME2 (English where maintained)


class NcIaLedger(Base):
    """IA_DETAILLEDGER for the Canada cost region, dr=0 rows only (deleted
    rows are counted in the sync state, not mirrored). `fdispatchflag` 0 = in,
    1 = out; `nnum`/`nmny` are always positive."""
    __tablename__ = "inv_nc_ia_ledger"

    cdetailledgerid: Mapped[str] = mapped_column(PK, primary_key=True)
    cinventoryid: Mapped[str | None] = mapped_column(PK)
    caccountperiod: Mapped[str | None] = mapped_column(String(20))
    dbizdate: Mapped[str | None] = mapped_column(String(19))
    biz_time: Mapped[datetime | None] = mapped_column(DateTime(timezone=False))
    fdispatchflag: Mapped[int | None] = mapped_column(Integer)
    nnum: Mapped[Decimal | None] = mapped_column(QTY)
    nmny: Mapped[Decimal | None] = mapped_column(AMT)
    vbatchcode: Mapped[str | None] = mapped_column(String(200))
    cstordocid: Mapped[str | None] = mapped_column(PK)
    cbilltypecode: Mapped[str | None] = mapped_column(String(20))
    ctrantypeid: Mapped[str | None] = mapped_column(PK)
    vbillcode: Mapped[str | None] = mapped_column(String(60))
    vsrccode: Mapped[str | None] = mapped_column(String(60))
    csrcbid: Mapped[str | None] = mapped_column(PK)
    bestimateflag: Mapped[bool | None] = mapped_column(Boolean)

    __table_args__ = (
        Index("ix_inv_nc_ia_ledger_inv_period", "cinventoryid", "caccountperiod"),
    )


class NcIaMonthNab(Base):
    """IA_MONTHNAB — month-end balance per material. NC only stores non-zero
    rows: a missing row means zero."""
    __tablename__ = "inv_nc_ia_monthnab"

    cmonthnabid: Mapped[str] = mapped_column(PK, primary_key=True)
    cinventoryid: Mapped[str | None] = mapped_column(PK)
    caccountperiod: Mapped[str | None] = mapped_column(String(7), index=True)
    nabnum: Mapped[Decimal | None] = mapped_column(QTY)
    nabmny: Mapped[Decimal | None] = mapped_column(AMT)


class NcIaPeriod(Base):
    """IA_ACCOUNT — one row per closed (关账) IA period."""
    __tablename__ = "inv_nc_ia_period"

    caccountid: Mapped[str] = mapped_column(PK, primary_key=True)
    caccountperiod: Mapped[str] = mapped_column(String(7))


class NcGlLine(Base):
    """GL_DETAIL ⋈ GL_VOUCHER on the inventory accounts of the Canada book.

    Deleted details/vouchers and discarded vouchers are filtered out at
    extract time (and counted). `material_pk` is the value of the
    '物料基本信息' auxiliary (GL_FREEVALUE), NULL when the line carries none —
    e.g. most of 140306 零配件, which is booked against a material class."""
    __tablename__ = "inv_nc_gl_line"

    pk_detail: Mapped[str] = mapped_column(PK, primary_key=True)
    pk_voucher: Mapped[str] = mapped_column(PK, index=True)
    year: Mapped[str] = mapped_column(String(4))
    period: Mapped[str] = mapped_column(String(2))
    voucher_num: Mapped[int | None] = mapped_column(Integer)
    prepareddate: Mapped[str | None] = mapped_column(String(19))
    prepared_time: Mapped[datetime | None] = mapped_column(DateTime(timezone=False))
    tallydate: Mapped[str | None] = mapped_column(String(19))  # NULL = not posted
    pk_system: Mapped[str | None] = mapped_column(String(20))  # IA / AP / GL / …
    voucherkind: Mapped[int | None] = mapped_column(Integer)
    account_code: Mapped[str] = mapped_column(String(40))
    account_name: Mapped[str | None] = mapped_column(String(300))
    material_pk: Mapped[str | None] = mapped_column(PK)
    debit_amount: Mapped[Decimal | None] = mapped_column(AMT)
    credit_amount: Mapped[Decimal | None] = mapped_column(AMT)
    debit_qty: Mapped[Decimal | None] = mapped_column(QTY)
    credit_qty: Mapped[Decimal | None] = mapped_column(QTY)
    explanation: Mapped[str | None] = mapped_column(Text)

    __table_args__ = (
        Index("ix_inv_nc_gl_line_year_period", "year", "period"),
        Index("ix_inv_nc_gl_line_material", "material_pk"),
    )
