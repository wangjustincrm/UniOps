"""NC65 BOM raw mirror (read-only, thin-mode sync).

Mirrors three NC65 Oracle tables verbatim (columns copied per the survey at
docs/superpowers/specs/2026-08-03-nc-bom-survey.md, 2026-08-03):
  - NCSC.BD_BOM       -> nc_bom       (BOM header,   1016 rows measured)
  - NCSC.BD_BOM_B     -> nc_bom_b     (BOM lines,   10169 rows measured)
  - NCSC.BD_BOM_REPL  -> nc_bom_repl  (substitutes,   664 rows measured)

Column names are the lower-cased Oracle column names verbatim (matches the
dict keys `reader.py` produces via `cur.description`), so a fetched row can be
passed straight into the model constructor after adding `nc_source_pk`. Every
mirrored column is a nullable String/Numeric — this is a raw, untransformed
mirror; Task 5 owns turning these into normalized `boms`/`bom_lines`/
`bom_substitutes` rows (bom_type derivation, material code joins, HVERSION
version selection, etc. per the survey's mapping tables).

`nc_source_pk` is NOT an NC column — it's a synthetic unique-index column the
sync service populates from each table's real PK column (cbomid / cbom_bid /
cbom_replaceid respectively), so service.py can share one upsert-by-pk helper
across all three tables regardless of the underlying PK column name.

Skipped columns (explicitly marked by the survey as unused/always-blank
custom-field slots — kept out to avoid mirroring ~70 near-always-'~' VARCHAR2
columns for no downstream consumer):
  - nc_bom:      HVDEF1..HVDEF20 (20 cols) — survey's column list literally
    annotates these "20个自定义列，样本均为'~'". (The survey's narrative note
    on APPROVER also mentions HVDEF1 held a process-doc code on ONE sampled
    header — noted here for Task 5's awareness in case it re-adds this column
    later; the explicit column-list annotation is what governs the Task 4 skip.)
  - nc_bom_b:    VDEF1..VDEF20, VFREE1..VFREE10 (30 cols) — same custom-field
    slot convention; the survey confirms this pattern is empty/unused for the
    sibling nc_bom_repl table (see below) and BD_BOM_B's column list groups
    them identically with no distinct commentary, so the same call applies.
  - nc_bom_repl: VDEF1..VDEF20, VFREE1..VFREE10 (30 cols) — survey states
    directly: "其余为VDEF1-20/VFREE1-10自定义列样本恒'~'".

Also skipped (not custom fields, just not selected by the survey's "关键字段"
review for BD_BOM_REPL — the survey table only documents 13 of the reported
44 total columns as meaningful, with the remainder being the VDEF/VFREE slots
above): none beyond the VDEF/VFREE slots — the 13 documented columns are
mirrored in full.
"""
from sqlalchemy import Numeric, String
from sqlalchemy.orm import Mapped, mapped_column
from app.db.base import Base, UUIDPrimaryKey, TimestampMixin


class NcBom(Base, UUIDPrimaryKey, TimestampMixin):
    """Raw mirror of NCSC.BD_BOM (BOM header)."""

    __tablename__ = "nc_bom"

    nc_source_pk: Mapped[str] = mapped_column(String(50), unique=True, index=True, nullable=False)

    cbomid: Mapped[str | None] = mapped_column(String(50))
    approver: Mapped[str | None] = mapped_column(String(255))
    billmaker: Mapped[str | None] = mapped_column(String(50))
    bkititem: Mapped[str | None] = mapped_column(String(5))
    creationtime: Mapped[str | None] = mapped_column(String(30))
    creator: Mapped[str | None] = mapped_column(String(50))
    dr: Mapped[object | None] = mapped_column(Numeric(18, 4))
    fbillstatus: Mapped[object | None] = mapped_column(Numeric(5, 0))
    fbomtype: Mapped[object | None] = mapped_column(Numeric(5, 0))
    hbcustomized: Mapped[str | None] = mapped_column(String(5))
    hbdefault: Mapped[str | None] = mapped_column(String(5))
    hbisfeature: Mapped[str | None] = mapped_column(String(5))
    hcassmeasureid: Mapped[str | None] = mapped_column(String(50))
    hcecnid: Mapped[str | None] = mapped_column(String(50))
    hcfeatureclassid: Mapped[str | None] = mapped_column(String(50))
    hcfeaturecode: Mapped[str | None] = mapped_column(String(100))
    hcmaterialid: Mapped[str | None] = mapped_column(String(50), index=True)
    hcmaterialvid: Mapped[str | None] = mapped_column(String(50))
    hcmeasureid: Mapped[str | None] = mapped_column(String(50))
    hcprojectid: Mapped[str | None] = mapped_column(String(50))
    hfbomsource: Mapped[object | None] = mapped_column(Numeric(5, 0))
    hfversiontype: Mapped[object | None] = mapped_column(Numeric(5, 0))
    hnassparentnum: Mapped[object | None] = mapped_column(Numeric(24, 8))
    hnparentnum: Mapped[object | None] = mapped_column(Numeric(24, 8))
    hrtversion: Mapped[str | None] = mapped_column(String(50))
    hsrcid: Mapped[str | None] = mapped_column(String(50))
    hvchangerate: Mapped[str | None] = mapped_column(String(50))
    hvecnbillcode: Mapped[str | None] = mapped_column(String(255))
    hversion: Mapped[str | None] = mapped_column(String(50))
    hvnote: Mapped[str | None] = mapped_column(String(1000))
    modifiedtime: Mapped[str | None] = mapped_column(String(30))
    modifier: Mapped[str | None] = mapped_column(String(50))
    pk_group: Mapped[str | None] = mapped_column(String(50))
    pk_org: Mapped[str | None] = mapped_column(String(50), index=True)
    pk_org_v: Mapped[str | None] = mapped_column(String(50))
    taudittime: Mapped[str | None] = mapped_column(String(30))
    tmaketime: Mapped[str | None] = mapped_column(String(30))
    ts: Mapped[str | None] = mapped_column(String(30), index=True)
    vbillcode: Mapped[str | None] = mapped_column(String(500))
    vbilltype: Mapped[str | None] = mapped_column(String(20))


class NcBomB(Base, UUIDPrimaryKey, TimestampMixin):
    """Raw mirror of NCSC.BD_BOM_B (BOM component lines)."""

    __tablename__ = "nc_bom_b"

    nc_source_pk: Mapped[str] = mapped_column(String(50), unique=True, index=True, nullable=False)

    # CHAR('Y'/'N') flag columns
    batpcheck: Mapped[str | None] = mapped_column(String(5))
    bbchkitemforwr: Mapped[str | None] = mapped_column(String(5))
    bbisfeature: Mapped[str | None] = mapped_column(String(5))
    bbsteploss: Mapped[str | None] = mapped_column(String(5))
    bbunibatch: Mapped[str | None] = mapped_column(String(5))
    bcanreplace: Mapped[str | None] = mapped_column(String(5))
    bcfeatureclassid: Mapped[str | None] = mapped_column(String(50))
    bcfeaturecode: Mapped[str | None] = mapped_column(String(100))
    bcustommaterial: Mapped[str | None] = mapped_column(String(5))
    bdeliver: Mapped[str | None] = mapped_column(String(5))
    bischoice: Mapped[str | None] = mapped_column(String(5))
    bkitmaterial: Mapped[str | None] = mapped_column(String(5))
    bmainmaterial: Mapped[str | None] = mapped_column(String(5))
    bmixedmaterial: Mapped[str | None] = mapped_column(String(5))
    boutsource: Mapped[str | None] = mapped_column(String(5))
    bprojectmaterial: Mapped[str | None] = mapped_column(String(5))
    bupint: Mapped[str | None] = mapped_column(String(5))

    cassmeasureid: Mapped[str | None] = mapped_column(String(50))
    cbeginperiod: Mapped[str | None] = mapped_column(String(30))
    cbom_bid: Mapped[str | None] = mapped_column(String(50))
    cbomid: Mapped[str | None] = mapped_column(String(50), index=True)
    ccustomerid: Mapped[str | None] = mapped_column(String(50))
    cendperiod: Mapped[str | None] = mapped_column(String(30))
    cmaterialid: Mapped[str | None] = mapped_column(String(50), index=True)
    cmaterialvid: Mapped[str | None] = mapped_column(String(50))
    cmeasureid: Mapped[str | None] = mapped_column(String(50))
    cnumfeature: Mapped[str | None] = mapped_column(String(50))
    cproductorid: Mapped[str | None] = mapped_column(String(50))
    cprojectid: Mapped[str | None] = mapped_column(String(50))
    cvendorid: Mapped[str | None] = mapped_column(String(50))

    dr: Mapped[object | None] = mapped_column(Numeric(18, 4))

    fbackflushtime: Mapped[object | None] = mapped_column(Numeric(10, 0))
    fbackflushtype: Mapped[object | None] = mapped_column(Numeric(5, 0))
    fcontrol: Mapped[object | None] = mapped_column(Numeric(5, 0))
    fitemsource: Mapped[object | None] = mapped_column(Numeric(5, 0))
    fitemtype: Mapped[object | None] = mapped_column(Numeric(5, 0))
    freplacetype: Mapped[object | None] = mapped_column(Numeric(5, 0))
    fsupplymode: Mapped[object | None] = mapped_column(Numeric(5, 0))

    ibasenum: Mapped[object | None] = mapped_column(Numeric(24, 8))
    ileadtimenum: Mapped[object | None] = mapped_column(Numeric(24, 8))
    nassitemnum: Mapped[object | None] = mapped_column(Numeric(24, 8))
    nbfixshrinkastnum: Mapped[object | None] = mapped_column(Numeric(24, 8))
    nbfixshrinknum: Mapped[object | None] = mapped_column(Numeric(24, 8))
    ndissipationum: Mapped[object | None] = mapped_column(Numeric(24, 8))
    nitemnum: Mapped[object | None] = mapped_column(Numeric(24, 8))

    pk_group: Mapped[str | None] = mapped_column(String(50))
    pk_org: Mapped[str | None] = mapped_column(String(50))
    pk_org_v: Mapped[str | None] = mapped_column(String(50))

    ts: Mapped[str | None] = mapped_column(String(30), index=True)

    vchangerate: Mapped[str | None] = mapped_column(String(50))
    vconfigversion: Mapped[str | None] = mapped_column(String(50))
    vitemversion: Mapped[str | None] = mapped_column(String(50))
    vmatingno: Mapped[str | None] = mapped_column(String(50))
    vnote: Mapped[str | None] = mapped_column(String(1000))
    vpackversion: Mapped[str | None] = mapped_column(String(50))
    vrowno: Mapped[str | None] = mapped_column(String(20))
    vselectcond: Mapped[str | None] = mapped_column(String(500))


class NcBomRepl(Base, UUIDPrimaryKey, TimestampMixin):
    """Raw mirror of NCSC.BD_BOM_REPL (substitute materials).

    Source for Task 5's `bom_substitutes`. Sparse table by nature (664 rows vs
    10169 BD_BOM_B lines) — that is expected, not a sync gap (see survey §10/§5
    under "未决问题").
    """

    __tablename__ = "nc_bom_repl"

    nc_source_pk: Mapped[str] = mapped_column(String(50), unique=True, index=True, nullable=False)

    cbom_bid: Mapped[str | None] = mapped_column(String(50), index=True)
    cbom_replaceid: Mapped[str | None] = mapped_column(String(50))
    creplmaterialoid: Mapped[str | None] = mapped_column(String(50), index=True)
    creplmaterialvid: Mapped[str | None] = mapped_column(String(50))
    ireplorder: Mapped[object | None] = mapped_column(Numeric(10, 0))
    dr: Mapped[object | None] = mapped_column(Numeric(18, 4))
    pk_group: Mapped[str | None] = mapped_column(String(50))
    pk_org: Mapped[str | None] = mapped_column(String(50))
    pk_org_v: Mapped[str | None] = mapped_column(String(50))
    ts: Mapped[str | None] = mapped_column(String(30), index=True)
    vreplaceindex: Mapped[str | None] = mapped_column(String(50))
    vrowno: Mapped[str | None] = mapped_column(String(20))
    vnote: Mapped[str | None] = mapped_column(String(500))
