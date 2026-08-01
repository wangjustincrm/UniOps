# NC Procurement → UniOps Payment Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Mirror NC65 purchase orders + arrivals into EPMS `purchase_orders`/`goods_receipts` (type=1) so the existing invoice-upload → 3-way-match → PA → payment pipeline drives payment for raw-material/packaging procurement.

**Architecture:** A new epms-api "NC purchase sync" service reads NC65 Oracle (read-only, incremental by `modifiedtime`) and idempotently bulk-upserts (psycopg2) NC orders/arrivals into the existing EPMS tables, tagged `source='nc'` + `nc_source_pk`. From invoice upload onward, **zero existing business logic changes** — mirrored POs are `status='issued'` (matchable) and mirrored GRs carry `po_line_id`+`line_total` so match/gate work unchanged. It mirrors the proven `finance-api` NC JV sync harness.

**Tech Stack:** Python 3.12 / FastAPI / SQLAlchemy (async ORM) + psycopg2 (sync bulk writer) + `oracledb` (thin) / Alembic / React + TypeScript (portal admin).

## Global Constraints

- **Design spec:** `docs/superpowers/specs/2026-08-01-nc-procurement-to-payment-design.md` — authoritative for decisions.
- **Only additive:** never modify existing invoice/match/PA/payment logic. New columns are nullable; new code paths are gated by `source='nc'`.
- **Read-only on NC:** never write back to NC. Never run against NC prod except the manual sync + the existing read-only introspect helper.
- **Provenance key:** every mirrored row carries `source='nc'` and `nc_source_pk` (the NC pk). Upserts key on `nc_source_pk`.
- **Status filter:** only sync NC `po_order.forderstatus=3` and `po_arriveorder.fbillstatus=3`. **Cutover filter:** only orders with `dbilldate >= <cutover_date>`.
- **Mirrored PO status = `issued`**; mirrored GR status = `confirmed`. Mirrored PO `type=1`, `pr_id=NULL`. `procurement_type=1`, `gr_type='physical'`.
- **Vendor resolution:** NC `pk_supplier` → (Oracle join `bd_supplier`) → `code` → `business_partners.erp_id` (exact match). Unresolved → skip PO + report. Never auto-create vendors.
- **Material:** no mapping — write NC `bd_material.code` into line `material_id`, NC name into `description`.
- **Alembic:** new migrations `down_revision` chains onto the real head; verify heads first (`feedback_uniops_alembic_new_migration_check_heads`). Current epms head: `af_add_receipt_override_to_pa`.
- **NC env vars:** `NC_HOST`/`NC_PORT`(1521)/`NC_SERVICE`/`NC_USER`/`NC_PASSWORD`. Feature hidden if not all set.
- **epms test DB:** run pytest with `POSTGRES_*` → local docker `uniops_postgres` (password `docker exec uniops_postgres printenv POSTGRES_PASSWORD`), `JWT_SECRET_KEY=test-secret`; test db is `epms_test`. Only one epms suite at a time. Baseline has ~72 pre-existing failures — compare against baseline, don't read "0 failures".
- **NC data facts (measured):** `po_arriveorder_b.pk_order`/`pk_order_b`/`pk_arriveorder` are all populated (6011/6011). One arrival maps to ≤3 orders (avg 1.01). Over-delivery = 27% of order lines (arrival qty > order qty). QC fields unused — ignore.
- **NC read-only creds for manual verification:** `c:/Project/nc65_conn.env`, helper `c:/Project/nc65_introspect/conn.py` (schema `NCSC`).

---

## File Structure

**epms-api (backend):**
- Modify `epms-api/app/models/po.py` — add `source`, `nc_source_pk` to `PurchaseOrder` + `nc_source_pk` to `PoLineItem`.
- Modify `epms-api/app/models/gr.py` — add `source`, `nc_source_pk` to `GoodsReceipt` + `nc_source_pk` to `GrLineItem`.
- Create `epms-api/app/models/nc_purchase_sync.py` — `NcPurchaseSyncRun` (`nc_purchase_sync_runs`).
- Create `epms-api/alembic/versions/nc01_add_nc_provenance_and_sync_runs.py` — columns + indexes + runs table.
- Modify `epms-api/app/core/config.py` — add `nc_host/nc_port/nc_service/nc_user/nc_password`.
- Modify `epms-api/requirements.txt` — add `oracledb==2.5.1`.
- Create `epms-api/app/services/nc_purchase_sync/__init__.py`
- Create `epms-api/app/services/nc_purchase_sync/reader.py` — Oracle fetch (pk→code joins, filters, watermark).
- Create `epms-api/app/services/nc_purchase_sync/transform.py` — NC rows → PO/GR dict payloads (pure, unit-tested).
- Create `epms-api/app/services/nc_purchase_sync/writer.py` — psycopg2 idempotent upsert + run registry + single-flight lock + watermark.
- Create `epms-api/app/services/nc_purchase_sync/service.py` — orchestration (`start_run`, `_run_worker`, `nc_configured`, `FULL_CONFIRM`).
- Create `epms-api/app/api/v1/nc_purchase_sync.py` — `/admin/nc-purchase-sync` status + trigger.
- Modify `epms-api/app/api/v1/__init__.py` — register router.

**portal (frontend):**
- Create `portal/src/pages/admin/NcPurchaseSyncSection.tsx` — admin section (status + trigger modal).
- Modify `portal/src/pages/admin/AdminPanel.tsx` — register `nc_purchase` section.

**epms (frontend, read-only context):**
- Modify invoice/PA/PO detail pages to show `source='nc'` badge + linked NC order/arrival read-only context; unhide type-1 label for NC-source rows.

**Tests:**
- `epms-api/tests/test_nc_purchase_transform.py`
- `epms-api/tests/test_nc_purchase_writer.py`
- `epms-api/tests/test_nc_purchase_sync_api.py`
- `epms-api/tests/test_nc_mirror_pipeline.py` (end-to-end: mirror → match → PA gate)

---

## Phase 1 — Schema & provenance

### Task 1: Add `source`/`nc_source_pk` provenance columns + sync-runs table

**Files:**
- Modify: `epms-api/app/models/po.py:16-59` (PurchaseOrder), `:67-87` (PoLineItem)
- Modify: `epms-api/app/models/gr.py:16-60` (GoodsReceipt), `:68-90` (GrLineItem)
- Create: `epms-api/app/models/nc_purchase_sync.py`
- Create: `epms-api/alembic/versions/nc01_add_nc_provenance_and_sync_runs.py`
- Test: `epms-api/tests/test_nc_purchase_writer.py` (schema smoke only in this task)

**Interfaces:**
- Produces: `PurchaseOrder.source: str|None`, `PurchaseOrder.nc_source_pk: str|None`, `PoLineItem.nc_source_pk: str|None`, `GoodsReceipt.source: str|None`, `GoodsReceipt.nc_source_pk: str|None`, `GrLineItem.nc_source_pk: str|None`; model `NcPurchaseSyncRun` (table `nc_purchase_sync_runs`) with columns listed below.

- [ ] **Step 1: Verify current alembic head**

Run: `cd epms-api && python -m alembic heads`
Expected: single head `af_add_receipt_override_to_pa`. If not single, STOP and reconcile before writing the migration.

- [ ] **Step 2: Add columns to PO models**

In `epms-api/app/models/po.py`, inside `PurchaseOrder` (after line 40 `notes`):
```python
    # NC ERP provenance (NULL for non-NC POs)
    source: Mapped[str | None] = mapped_column(String(10), nullable=True, index=True)
    nc_source_pk: Mapped[str | None] = mapped_column(String(20), nullable=True)
```
Inside `PoLineItem` (after line 86 `notes`):
```python
    nc_source_pk: Mapped[str | None] = mapped_column(String(20), nullable=True)
```

- [ ] **Step 3: Add columns to GR models**

In `epms-api/app/models/gr.py`, inside `GoodsReceipt` (after line 48 `notes`):
```python
    source: Mapped[str | None] = mapped_column(String(10), nullable=True, index=True)
    nc_source_pk: Mapped[str | None] = mapped_column(String(20), nullable=True)
```
Inside `GrLineItem` (after line 88 `discrepancy_notes` / before `actual_qty`):
```python
    nc_source_pk: Mapped[str | None] = mapped_column(String(20), nullable=True)
```

- [ ] **Step 4: Create the sync-runs model**

Create `epms-api/app/models/nc_purchase_sync.py`:
```python
"""Run registry for the NC purchase (order + arrival) sync."""
import uuid
from datetime import datetime

from sqlalchemy import DateTime, Integer, String, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin, UUIDPrimaryKey

RUNNING = "running"
SUCCESS = "success"
FAILED = "failed"


class NcPurchaseSyncRun(UUIDPrimaryKey, TimestampMixin, Base):
    __tablename__ = "nc_purchase_sync_runs"

    mode: Mapped[str] = mapped_column(String(15), nullable=False)            # full | incremental
    status: Mapped[str] = mapped_column(String(10), nullable=False, default=RUNNING, index=True)
    started_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    watermark_from: Mapped[str | None] = mapped_column(String(19), nullable=True)
    watermark_to: Mapped[str | None] = mapped_column(String(19), nullable=True)
    pos_upserted: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    po_lines_upserted: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    grs_upserted: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    gr_lines_upserted: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    skipped_no_vendor: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    skipped_consumed: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
```

- [ ] **Step 5: Register the model for metadata**

In `epms-api/app/models/__init__.py`, add `from app.models.nc_purchase_sync import NcPurchaseSyncRun` (follow the existing import style there). Confirm the file imports all models so Alembic autogenerate/metadata sees it.

- [ ] **Step 6: Write the migration**

Create `epms-api/alembic/versions/nc01_add_nc_provenance_and_sync_runs.py`:
```python
"""add NC provenance columns + nc_purchase_sync_runs

Revision ID: nc01_nc_provenance
Revises: af_add_receipt_override_to_pa
Create Date: 2026-08-01
"""
import sqlalchemy as sa
from alembic import op

revision = "nc01_nc_provenance"
down_revision = "af_add_receipt_override_to_pa"
branch_labels = None
depends_on = None


def upgrade() -> None:
    for tbl in ("purchase_orders", "goods_receipts"):
        op.add_column(tbl, sa.Column("source", sa.String(length=10), nullable=True))
        op.add_column(tbl, sa.Column("nc_source_pk", sa.String(length=20), nullable=True))
        op.create_index(f"ix_{tbl}_source", tbl, ["source"])
        op.create_index(
            f"uq_{tbl}_nc_source_pk", tbl, ["nc_source_pk"],
            unique=True, postgresql_where=sa.text("source = 'nc'"),
        )
    for tbl in ("po_line_items", "gr_line_items"):
        op.add_column(tbl, sa.Column("nc_source_pk", sa.String(length=20), nullable=True))
        op.create_index(
            f"uq_{tbl}_nc_source_pk", tbl, ["nc_source_pk"],
            unique=True, postgresql_where=sa.text("nc_source_pk IS NOT NULL"),
        )

    op.create_table(
        "nc_purchase_sync_runs",
        sa.Column("id", sa.dialects.postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("mode", sa.String(length=15), nullable=False),
        sa.Column("status", sa.String(length=10), nullable=False, server_default="running"),
        sa.Column("started_by", sa.dialects.postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("watermark_from", sa.String(length=19), nullable=True),
        sa.Column("watermark_to", sa.String(length=19), nullable=True),
        sa.Column("pos_upserted", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("po_lines_upserted", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("grs_upserted", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("gr_lines_upserted", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("skipped_no_vendor", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("skipped_consumed", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("error", sa.Text(), nullable=True),
    )
    op.create_index("ix_nc_purchase_sync_runs_status", "nc_purchase_sync_runs", ["status"])


def downgrade() -> None:
    op.drop_table("nc_purchase_sync_runs")
    for tbl in ("po_line_items", "gr_line_items"):
        op.drop_index(f"uq_{tbl}_nc_source_pk", table_name=tbl)
        op.drop_column(tbl, "nc_source_pk")
    for tbl in ("purchase_orders", "goods_receipts"):
        op.drop_index(f"uq_{tbl}_nc_source_pk", table_name=tbl)
        op.drop_index(f"ix_{tbl}_source", table_name=tbl)
        op.drop_column(tbl, "nc_source_pk")
        op.drop_column(tbl, "source")
```

- [ ] **Step 7: Apply migration to test DB and verify**

Run: `cd epms-api && POSTGRES_HOST=localhost POSTGRES_USER=epms POSTGRES_DB=epms_test POSTGRES_PASSWORD=$(docker exec uniops_postgres printenv POSTGRES_PASSWORD) python -m alembic upgrade head`
Expected: completes; `\d purchase_orders` shows `source`, `nc_source_pk`; `\d nc_purchase_sync_runs` exists. (If `epms_test` doesn't exist yet, the test harness creates it — run Step 8's smoke test which builds schema.)

- [ ] **Step 8: Schema smoke test**

In `epms-api/tests/test_nc_purchase_writer.py`:
```python
import pytest
from sqlalchemy import inspect
from app.db.base import Base

def test_provenance_columns_exist():
    po = Base.metadata.tables["purchase_orders"]
    assert "source" in po.c and "nc_source_pk" in po.c
    gr = Base.metadata.tables["goods_receipts"]
    assert "source" in gr.c and "nc_source_pk" in gr.c
    assert "nc_source_pk" in Base.metadata.tables["po_line_items"].c
    assert "nc_source_pk" in Base.metadata.tables["gr_line_items"].c
    assert "nc_purchase_sync_runs" in Base.metadata.tables
```

- [ ] **Step 9: Run + commit**

Run: `cd epms-api && POSTGRES_HOST=localhost POSTGRES_USER=epms POSTGRES_DB=epms_test POSTGRES_PASSWORD=$(docker exec uniops_postgres printenv POSTGRES_PASSWORD) JWT_SECRET_KEY=test-secret python -m pytest tests/test_nc_purchase_writer.py::test_provenance_columns_exist -v`
Expected: PASS.
```bash
git add epms-api/app/models/po.py epms-api/app/models/gr.py epms-api/app/models/nc_purchase_sync.py epms-api/app/models/__init__.py epms-api/alembic/versions/nc01_add_nc_provenance_and_sync_runs.py epms-api/tests/test_nc_purchase_writer.py
git commit -m "feat(nc): add NC provenance columns + nc_purchase_sync_runs table"
```

---

## Phase 2 — NC connection config

### Task 2: Add NC Oracle settings + oracledb dependency + reader

**Files:**
- Modify: `epms-api/app/core/config.py` (after `:105` region)
- Modify: `epms-api/requirements.txt`
- Create: `epms-api/app/services/nc_purchase_sync/__init__.py` (empty)
- Create: `epms-api/app/services/nc_purchase_sync/reader.py`
- Test: `epms-api/tests/test_nc_purchase_transform.py` (config guard only here)

**Interfaces:**
- Produces: `settings.nc_host/nc_port/nc_service/nc_user/nc_password`; `nc_configured() -> bool`; `reader.fetch_nc(cutover: str, watermark: str | None) -> NcRaw` where `NcRaw` is a dict `{"orders": [...], "order_lines": [...], "arrivals": [...], "arrival_lines": [...], "suppliers": {pk: code}, "materials": {pk: (code, name)}, "uoms": {pk: code}, "currencies": {pk: code}, "taxcodes": {pk: code}, "max_modifiedtime": str|None}`.

- [ ] **Step 1: Add config fields**

In `epms-api/app/core/config.py` `Settings`:
```python
    nc_host: str | None = None
    nc_port: int = 1521
    nc_service: str | None = None
    nc_user: str | None = None
    nc_password: str | None = None
```

- [ ] **Step 2: Add oracledb to requirements**

In `epms-api/requirements.txt` add a line: `oracledb==2.5.1`

- [ ] **Step 3: Write config-guard test (failing)**

In `epms-api/tests/test_nc_purchase_transform.py`:
```python
from app.services.nc_purchase_sync.reader import nc_configured

def test_nc_configured_false_when_unset(monkeypatch):
    from app.core.config import settings
    monkeypatch.setattr(settings, "nc_host", None, raising=False)
    assert nc_configured() is False

def test_nc_configured_true_when_all_set(monkeypatch):
    from app.core.config import settings
    for f, v in [("nc_host","h"),("nc_service","ORCL"),("nc_user","u"),("nc_password","p")]:
        monkeypatch.setattr(settings, f, v, raising=False)
    assert nc_configured() is True
```

- [ ] **Step 4: Run to verify it fails**

Run: `cd epms-api && python -m pytest tests/test_nc_purchase_transform.py::test_nc_configured_false_when_unset -v`
Expected: FAIL (module `reader` not found).

- [ ] **Step 5: Write the reader**

Create `epms-api/app/services/nc_purchase_sync/reader.py`. `nc_configured` + `fetch_nc`. The SQL below is the NC-specific core (verified against NC prod):
```python
"""Read-only NC65 Oracle reader for purchase orders + arrivals."""
from app.core.config import settings


def nc_configured() -> bool:
    return all([settings.nc_host, settings.nc_service, settings.nc_user, settings.nc_password])


def _connect():
    import oracledb
    oracledb.defaults.fetch_decimals = True
    dsn = oracledb.makedsn(settings.nc_host, settings.nc_port, service_name=settings.nc_service)
    return oracledb.connect(user=settings.nc_user, password=settings.nc_password, dsn=dsn)


def fetch_nc(cutover: str, watermark: str | None) -> dict:
    """cutover: 'YYYY-MM-DD HH24:MI:SS' (only orders with dbilldate >= cutover).
    watermark: last synced NC modifiedtime, or None for full."""
    con = _connect()
    try:
        cur = con.cursor()
        def lookup(sql):
            cur.execute(sql)
            return {r[0]: r[1] for r in cur.fetchall()}
        suppliers = lookup("select pk_supplier, code from NCSC.BD_SUPPLIER")
        uoms = lookup("select pk_measdoc, code from NCSC.BD_MEASDOC")
        currencies = lookup("select pk_currtype, code from NCSC.BD_CURRTYPE")
        cur.execute("select pk_material, code, name from NCSC.BD_MATERIAL")
        materials = {r[0]: (r[1], r[2]) for r in cur.fetchall()}

        wm = " and modifiedtime >= :wm" if watermark else ""
        binds = {"cut": cutover, **({"wm": watermark} if watermark else {})}

        cur.execute(
            "select pk_order, vbillcode, dbilldate, pk_supplier, corigcurrencyid, "
            "ntotalorigmny, forderstatus, modifiedtime, vmemo "
            "from NCSC.PO_ORDER where forderstatus=3 and dbilldate >= :cut" + wm, binds)
        ocols = [c[0].lower() for c in cur.description]
        orders = [dict(zip(ocols, r)) for r in cur.fetchall()]
        order_pks = [o["pk_order"] for o in orders]

        order_lines, arrivals, arrival_lines = [], [], []
        maxmt = max([o["modifiedtime"] for o in orders], default=None)
        for chunk in _chunks(order_pks, 900):
            ph = ",".join(f":p{i}" for i in range(len(chunk)))
            b = {f"p{i}": v for i, v in enumerate(chunk)}
            cur.execute(
                "select pk_order_b, pk_order, crowno, pk_material, vvendinventoryname, "
                "castunitid, nastnum, norigtaxprice, ntaxrate, ctaxcodeid, norigtaxmny, norigmny, ntax "
                f"from NCSC.PO_ORDER_B where pk_order in ({ph})", b)
            lcols = [c[0].lower() for c in cur.description]
            order_lines += [dict(zip(lcols, r)) for r in cur.fetchall()]
            cur.execute(
                "select pk_arriveorder, vbillcode, dbilldate, pk_supplier, fbillstatus, modifiedtime "
                "from NCSC.PO_ARRIVEORDER ah where fbillstatus=3 and exists("
                "select 1 from NCSC.PO_ARRIVEORDER_B ab where ab.pk_arriveorder=ah.pk_arriveorder "
                f"and ab.pk_order in ({ph}))", b)
            acols = [c[0].lower() for c in cur.description]
            arrivals += [dict(zip(acols, r)) for r in cur.fetchall()]
            cur.execute(
                "select pk_arriveorder_b, pk_arriveorder, pk_order, pk_order_b, crowno, "
                "pk_material, nastnum, norigtaxprice, norigtaxmny, norigmny "
                f"from NCSC.PO_ARRIVEORDER_B where pk_order in ({ph})", b)
            albcols = [c[0].lower() for c in cur.description]
            arrival_lines += [dict(zip(albcols, r)) for r in cur.fetchall()]
        return {
            "orders": orders, "order_lines": order_lines,
            "arrivals": arrivals, "arrival_lines": arrival_lines,
            "suppliers": suppliers, "materials": materials, "uoms": uoms,
            "currencies": currencies, "max_modifiedtime": maxmt,
        }
    finally:
        con.close()


def _chunks(seq, n):
    for i in range(0, len(seq), n):
        yield seq[i:i + n]
```
Note: dedupe `arrivals` by `pk_arriveorder` in transform (the per-chunk EXISTS query can repeat an arrival across chunks).

- [ ] **Step 6: Run config tests + commit**

Run: `cd epms-api && python -m pytest tests/test_nc_purchase_transform.py -k nc_configured -v`
Expected: PASS (both).
```bash
git add epms-api/app/core/config.py epms-api/requirements.txt epms-api/app/services/nc_purchase_sync/__init__.py epms-api/app/services/nc_purchase_sync/reader.py epms-api/tests/test_nc_purchase_transform.py
git commit -m "feat(nc): NC Oracle config + read-only reader for orders/arrivals"
```

---

## Phase 3 — Transform (pure, fully unit-tested)

### Task 3: Map NC raw rows → EPMS PO/GR payloads

**Files:**
- Create: `epms-api/app/services/nc_purchase_sync/transform.py`
- Test: `epms-api/tests/test_nc_purchase_transform.py` (add cases)

**Interfaces:**
- Consumes: `NcRaw` dict from `reader.fetch_nc` (Task 2), and `vendor_by_erp: dict[str, tuple[uuid, str]]` (erp_id → (vendor_id, vendor_name)) supplied by the writer.
- Produces: `transform(raw: dict, vendor_by_erp: dict) -> TransformResult` — a dataclass/dict with keys `orders`, `order_lines`, `grs`, `gr_lines` (lists of column-dicts ready for upsert), plus `skipped_no_vendor: list[str]` (NC order vbillcodes). Column-dicts use EXACT EPMS column names. Each PO dict includes `nc_source_pk`, `number`, `title`, `type`=1, `status`="issued", `currency`, `subtotal`, `tax_rate`, `tax_amount`, `total`, `vendor_id`, `vendor_name`, `pr_id`=None, `source`="nc", `place_order_method`="nc", `place_order_reference`=vbillcode. GR dict includes `nc_source_pk`, `number`, `po_nc_pk` (to resolve po_id in writer), `gr_type`="physical", `procurement_type`=1, `status`="confirmed", `source`="nc". Line dicts carry `nc_source_pk`, parent `nc_source_pk` ref, `po_line_nc_pk` (GR line → po_line resolution), `material_id`, `description`, qty/price/total fields.

- [ ] **Step 1: Write failing transform tests**

Add to `epms-api/tests/test_nc_purchase_transform.py`:
```python
import uuid
from decimal import Decimal
from app.services.nc_purchase_sync.transform import transform

VEND = {"0000415": (uuid.uuid4(), "Lactalis Canada")}

def _raw():
    return {
        "orders": [{"pk_order": "O1", "vbillcode": "PO-010-2105-03",
                    "dbilldate": "2026-05-01 00:00:00", "pk_supplier": "S1",
                    "corigcurrencyid": "C1", "ntotalorigmny": Decimal("100"),
                    "forderstatus": 3, "modifiedtime": "2026-05-01 09:00:00", "vmemo": None}],
        "order_lines": [{"pk_order_b": "OL1", "pk_order": "O1", "crowno": "10",
                         "pk_material": "M1", "vvendinventoryname": "Lactose",
                         "castunitid": "U1", "nastnum": Decimal("38000"),
                         "norigtaxprice": Decimal("1"), "ntaxrate": Decimal("5"),
                         "ctaxcodeid": "T1", "norigtaxmny": Decimal("100"),
                         "norigmny": Decimal("95"), "ntax": Decimal("5")}],
        "arrivals": [{"pk_arriveorder": "A1", "vbillcode": "DH2021", "dbilldate": "2026-05-03 00:00:00",
                      "pk_supplier": "S1", "fbillstatus": 3, "modifiedtime": "2026-05-03 09:00:00"}],
        "arrival_lines": [{"pk_arriveorder_b": "AL1", "pk_arriveorder": "A1", "pk_order": "O1",
                           "pk_order_b": "OL1", "crowno": "10", "pk_material": "M1",
                           "nastnum": Decimal("19000"), "norigtaxprice": Decimal("1"),
                           "norigtaxmny": Decimal("50"), "norigmny": Decimal("47.5")}],
        "suppliers": {"S1": "0000415"}, "materials": {"M1": ("CR0025", "Lactose")},
        "uoms": {"U1": "KG"}, "currencies": {"C1": "CAD"}, "max_modifiedtime": "2026-05-03 09:00:00",
    }

def test_transform_maps_order_header():
    r = transform(_raw(), VEND)
    po = r["orders"][0]
    assert po["nc_source_pk"] == "O1" and po["number"] == "PO-010-2105-03"
    assert po["type"] == 1 and po["status"] == "issued" and po["source"] == "nc"
    assert po["vendor_name"] == "Lactalis Canada" and po["currency"] == "CAD"
    assert po["pr_id"] is None and po["place_order_reference"] == "PO-010-2105-03"

def test_transform_line_uses_nc_material_code():
    r = transform(_raw(), VEND)
    ln = r["order_lines"][0]
    assert ln["material_id"] == "CR0025" and ln["qty"] == Decimal("38000")
    assert ln["unit"] == "KG" and ln["nc_source_pk"] == "OL1"

def test_transform_gr_line_links_po_line_and_uses_arrival_qty():
    r = transform(_raw(), VEND)
    gl = r["gr_lines"][0]
    assert gl["po_line_nc_pk"] == "OL1" and gl["qty_received"] == Decimal("19000")
    assert gl["line_total"] == Decimal("50")

def test_transform_skips_order_without_vendor():
    raw = _raw(); raw["suppliers"] = {"S1": "9999999"}   # not in VEND
    r = transform(raw, VEND)
    assert r["orders"] == [] and "PO-010-2105-03" in r["skipped_no_vendor"]

def test_transform_received_qty_rolled_up_to_po_line():
    r = transform(_raw(), VEND)
    assert r["order_lines"][0]["received_qty"] == Decimal("19000")

def test_transform_splits_arrival_spanning_two_orders_into_two_grs():
    raw = _raw()
    raw["orders"].append({**raw["orders"][0], "pk_order": "O2", "vbillcode": "PO-011"})
    raw["order_lines"].append({**raw["order_lines"][0], "pk_order_b": "OL2", "pk_order": "O2"})
    raw["arrival_lines"].append({**raw["arrival_lines"][0], "pk_arriveorder_b": "AL2",
                                 "pk_order": "O2", "pk_order_b": "OL2"})
    r = transform(raw, VEND)
    grs_for_a1 = [g for g in r["grs"] if g["nc_source_pk"].startswith("A1")]
    assert len(grs_for_a1) == 2  # one GR per (arrival, order)
```

- [ ] **Step 2: Run to verify failure**

Run: `cd epms-api && python -m pytest tests/test_nc_purchase_transform.py -k transform -v`
Expected: FAIL (no `transform`).

- [ ] **Step 3: Implement transform**

Create `epms-api/app/services/nc_purchase_sync/transform.py`:
```python
"""Pure mapping: NC raw rows -> EPMS PO/GR upsert payloads."""
from collections import defaultdict
from decimal import Decimal


def _num(v):
    return Decimal(str(v)) if v is not None else Decimal("0")


def transform(raw: dict, vendor_by_erp: dict) -> dict:
    sup, mat, uom, ccy = raw["suppliers"], raw["materials"], raw["uoms"], raw["currencies"]

    # dedupe arrivals (per-chunk EXISTS may repeat)
    arrivals = {a["pk_arriveorder"]: a for a in raw["arrivals"]}

    # received qty per order line
    recv = defaultdict(lambda: Decimal("0"))
    for al in raw["arrival_lines"]:
        recv[al["pk_order_b"]] += _num(al["nastnum"])

    orders, order_lines, skipped = [], [], []
    kept_order_pks = set()
    for o in raw["orders"]:
        code = sup.get(o["pk_supplier"])
        vend = vendor_by_erp.get(code) if code else None
        if not vend:
            skipped.append(o["vbillcode"])
            continue
        kept_order_pks.add(o["pk_order"])
        orders.append({
            "nc_source_pk": o["pk_order"], "number": o["vbillcode"], "title": o["vbillcode"],
            "type": 1, "status": "issued", "source": "nc",
            "currency": ccy.get(o["corigcurrencyid"], "CAD"),
            "total": _num(o["ntotalorigmny"]), "subtotal": Decimal("0"),
            "tax_rate": Decimal("0"), "tax_amount": Decimal("0"),
            "vendor_id": vend[0], "vendor_name": vend[1],
            "pr_id": None, "place_order_method": "nc",
            "place_order_reference": o["vbillcode"], "notes": o.get("vmemo"),
        })

    for ln in raw["order_lines"]:
        if ln["pk_order"] not in kept_order_pks:
            continue
        mcode, mname = mat.get(ln["pk_material"], (None, ln.get("vvendinventoryname") or ""))
        order_lines.append({
            "nc_source_pk": ln["pk_order_b"], "po_nc_pk": ln["pk_order"],
            "material_id": mcode, "description": mname or (ln.get("vvendinventoryname") or mcode or ""),
            "qty": _num(ln["nastnum"]), "unit": uom.get(ln["castunitid"], "EA"),
            "unit_price": _num(ln["norigtaxprice"]), "line_total": _num(ln["norigtaxmny"]),
            "received_qty": recv.get(ln["pk_order_b"], Decimal("0")),
            "sort_order": int(ln["crowno"]) if str(ln.get("crowno") or "").isdigit() else 0,
        })

    # GR: split each arrival by order → one GR per (arrival, order)
    grp = defaultdict(list)
    for al in raw["arrival_lines"]:
        if al["pk_order"] not in kept_order_pks:
            continue
        grp[(al["pk_arriveorder"], al["pk_order"])].append(al)

    # per-arrival order sets → suffix ONLY when THIS arrival spans >1 order
    orders_per_arrival = defaultdict(set)
    for (arr_pk, ord_pk) in grp:
        orders_per_arrival[arr_pk].add(ord_pk)

    grs, gr_lines = [], []
    for (arr_pk, ord_pk), lines in grp.items():
        ah = arrivals.get(arr_pk)
        if not ah:
            continue
        gr_pk = f"{arr_pk}:{ord_pk}"          # composite, unique per split GR
        ords = orders_per_arrival[arr_pk]
        if len(ords) > 1:
            idx = sorted(ords).index(ord_pk) + 1   # stable across re-syncs, short
            number = f"{ah['vbillcode']}-{idx}"
        else:
            number = ah["vbillcode"]
        grs.append({
            "nc_source_pk": gr_pk, "po_nc_pk": ord_pk, "number": number,
            "title": ah["vbillcode"], "gr_type": "physical", "procurement_type": 1,
            "status": "confirmed", "source": "nc",
        })
        for al in lines:
            mcode, mname = mat.get(al["pk_material"], (None, ""))
            gr_lines.append({
                "nc_source_pk": al["pk_arriveorder_b"], "gr_nc_pk": gr_pk,
                "po_line_nc_pk": al["pk_order_b"], "material_id": mcode,
                "description": mname or mcode or "",
                "qty_ordered": Decimal("0"), "qty_received": _num(al["nastnum"]),
                "unit_price": _num(al["norigtaxprice"]), "line_total": _num(al["norigtaxmny"]),
                "sort_order": int(al["crowno"]) if str(al.get("crowno") or "").isdigit() else 0,
            })
    return {"orders": orders, "order_lines": order_lines, "grs": grs,
            "gr_lines": gr_lines, "skipped_no_vendor": skipped}
```
Note the multi-order GR-number suffix keeps `goods_receipts.number` unique (String(30), unique).

- [ ] **Step 4: Run tests to pass**

Run: `cd epms-api && python -m pytest tests/test_nc_purchase_transform.py -k transform -v`
Expected: all PASS.

- [ ] **Step 5: Commit**
```bash
git add epms-api/app/services/nc_purchase_sync/transform.py epms-api/tests/test_nc_purchase_transform.py
git commit -m "feat(nc): pure transform NC orders/arrivals -> EPMS PO/GR payloads"
```

---

## Phase 4 — Writer + orchestration

### Task 4: Idempotent bulk writer with run registry + single-flight

**Files:**
- Create: `epms-api/app/services/nc_purchase_sync/writer.py`
- Create: `epms-api/app/services/nc_purchase_sync/service.py`
- Test: `epms-api/tests/test_nc_purchase_writer.py` (add cases)

**Interfaces:**
- Consumes: `transform` output (Task 3), a psycopg2 connection DSN.
- Produces:
  - `writer.load_vendor_map(cur) -> dict[str, tuple[uuid, str]]` — erp_id → (id, name) from `business_partners` where `is_supplier`.
  - `writer.upsert(cur, payload: dict, system_user_id) -> dict` counts; resolves po_id/po_line_id/gr_id via `nc_source_pk`; skips consumed-doc destructive updates.
  - `service.nc_configured()`, `service.FULL_CONFIRM="FULL RELOAD"`, `service.start_run(mode, user_id, *, fetch, pg_dsn, run_worker=False) -> uuid` (single-flight + registry insert; raises `SyncAlreadyRunning`), `service._run_worker(run_id, mode, fetch, pg_dsn)`, `service.latest_watermark(cur) -> str|None`, `service.ensure_system_user_sync(cur) -> uuid`.

- [ ] **Step 1: Write failing writer tests (DB-backed)**

Add to `epms-api/tests/test_nc_purchase_writer.py`:
```python
import uuid, psycopg2
from decimal import Decimal
from app.services.nc_purchase_sync import writer

# fixture `pg_cur` = psycopg2 cursor on epms_test with a seeded vendor + system user.
# (build in conftest: insert business_partners row erp_id='0000415', and a users row.)

def test_upsert_inserts_po_and_gr(pg_cur, seeded_vendor, system_user_id):
    payload = _mini_payload(seeded_vendor)     # helper mirrors transform output
    counts = writer.upsert(pg_cur, payload, system_user_id)
    assert counts["pos_upserted"] == 1 and counts["grs_upserted"] == 1
    pg_cur.execute("select status, type, source from purchase_orders where nc_source_pk='O1'")
    assert pg_cur.fetchone() == ("issued", 1, "nc")

def test_upsert_is_idempotent(pg_cur, seeded_vendor, system_user_id):
    payload = _mini_payload(seeded_vendor)
    writer.upsert(pg_cur, payload, system_user_id)
    writer.upsert(pg_cur, payload, system_user_id)     # second run
    pg_cur.execute("select count(*) from purchase_orders where nc_source_pk='O1'")
    assert pg_cur.fetchone()[0] == 1

def test_gr_line_resolves_po_line_id(pg_cur, seeded_vendor, system_user_id):
    writer.upsert(pg_cur, _mini_payload(seeded_vendor), system_user_id)
    pg_cur.execute("select l.po_line_id from gr_line_items l "
                   "join goods_receipts g on g.id=l.gr_id where g.nc_source_pk='A1:O1'")
    assert pg_cur.fetchone()[0] is not None

def test_upsert_skips_consumed_po(pg_cur, seeded_vendor, system_user_id):
    writer.upsert(pg_cur, _mini_payload(seeded_vendor), system_user_id)
    # simulate consumption: attach a matched invoice to the PO
    _attach_matched_invoice(pg_cur, nc_pk="O1")
    payload = _mini_payload(seeded_vendor); payload["orders"][0]["total"] = Decimal("999")
    counts = writer.upsert(pg_cur, payload, system_user_id)
    pg_cur.execute("select total from purchase_orders where nc_source_pk='O1'")
    assert pg_cur.fetchone()[0] != Decimal("999")      # not overwritten
    assert counts["skipped_consumed"] >= 1
```
(Add `conftest` fixtures `pg_cur`, `seeded_vendor`, `system_user_id`, and helpers `_mini_payload`, `_attach_matched_invoice`. `_mini_payload` returns a transform-shaped dict for order O1 + line OL1 + GR A1:O1 + gr line AL1.)

- [ ] **Step 2: Run to verify failure**

Run: `cd epms-api && <test env> python -m pytest tests/test_nc_purchase_writer.py::test_upsert_inserts_po_and_gr -v`
Expected: FAIL (no `writer.upsert`).

- [ ] **Step 3: Implement writer**

Create `epms-api/app/services/nc_purchase_sync/writer.py`. Core logic (system-user ensure mirrors `scripts/import_pms/load.py:205-218`; consumed-doc guard uses the gate query shape from `crud/po.py:533-540`):
```python
"""psycopg2 idempotent writer for mirrored NC orders/arrivals."""
import secrets
import uuid


def load_vendor_map(cur) -> dict:
    cur.execute("select erp_id, id, name from business_partners "
                "where erp_id is not null and is_supplier is true")
    return {r[0]: (r[1], r[2]) for r in cur.fetchall()}


def ensure_system_user_sync(cur) -> uuid.UUID:
    cur.execute("select id from users where email=%s", ("nc-sync@epms.local",))
    row = cur.fetchone()
    if row:
        return row[0]
    from app.core.security import hash_password   # same helper the importer uses
    uid = uuid.uuid4()
    cur.execute(
        "insert into users (id, email, hashed_password, full_name, role, is_active, "
        "must_change_password, created_at, updated_at) "
        "values (%s,%s,%s,%s,'system_admin',true,true,now(),now())",
        (uid, "nc-sync@epms.local", hash_password(secrets.token_urlsafe(24)), "NC Sync"))
    return uid


def _po_consumed(cur, po_id) -> bool:
    cur.execute("select 1 from invoices where po_id=%s and status='matched' "
                "and gr_id is not null limit 1", (po_id,))
    return cur.fetchone() is not None


def upsert(cur, payload: dict, system_user_id) -> dict:
    counts = dict(pos_upserted=0, po_lines_upserted=0, grs_upserted=0,
                  gr_lines_upserted=0, skipped_consumed=0)
    po_id_by_ncpk, po_line_id_by_ncpk, gr_id_by_ncpk = {}, {}, {}

    for po in payload["orders"]:
        cur.execute("select id from purchase_orders where nc_source_pk=%s and source='nc'",
                    (po["nc_source_pk"],))
        row = cur.fetchone()
        if row and _po_consumed(cur, row[0]):
            po_id_by_ncpk[po["nc_source_pk"]] = row[0]
            counts["skipped_consumed"] += 1
            continue
        if row:
            pid = row[0]
            cur.execute("update purchase_orders set number=%s,title=%s,status=%s,currency=%s,"
                        "total=%s,vendor_id=%s,vendor_name=%s,notes=%s,updated_at=now() where id=%s",
                        (po["number"], po["title"], po["status"], po["currency"], po["total"],
                         po["vendor_id"], po["vendor_name"], po["notes"], pid))
        else:
            pid = uuid.uuid4()
            cur.execute(
                "insert into purchase_orders (id,number,title,type,status,currency,subtotal,"
                "tax_rate,tax_amount,total,vendor_id,vendor_name,is_prepaid,approval_step_idx,"
                "pr_id,created_by,place_order_method,place_order_reference,source,nc_source_pk,"
                "notes,created_at,updated_at) values (%s,%s,%s,1,%s,%s,0,0,0,%s,%s,%s,false,0,"
                "NULL,%s,'nc',%s,'nc',%s,%s,now(),now())",
                (pid, po["number"], po["title"], po["status"], po["currency"], po["total"],
                 po["vendor_id"], po["vendor_name"], system_user_id,
                 po["place_order_reference"], po["nc_source_pk"], po["notes"]))
        po_id_by_ncpk[po["nc_source_pk"]] = pid
        counts["pos_upserted"] += 1

    for ln in payload["order_lines"]:
        pid = po_id_by_ncpk.get(ln["po_nc_pk"])
        if pid is None:      # parent skipped (consumed or no-vendor)
            continue
        cur.execute("select id from po_line_items where nc_source_pk=%s", (ln["nc_source_pk"],))
        row = cur.fetchone()
        if row:
            lid = row[0]
            cur.execute("update po_line_items set description=%s,material_id=%s,qty=%s,unit=%s,"
                        "unit_price=%s,line_total=%s,received_qty=%s,sort_order=%s where id=%s",
                        (ln["description"], ln["material_id"], ln["qty"], ln["unit"],
                         ln["unit_price"], ln["line_total"], ln["received_qty"], ln["sort_order"], lid))
        else:
            lid = uuid.uuid4()
            cur.execute(
                "insert into po_line_items (id,po_id,description,material_id,qty,unit,unit_price,"
                "line_total,received_qty,sort_order,nc_source_pk) "
                "values (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                (lid, pid, ln["description"], ln["material_id"], ln["qty"], ln["unit"],
                 ln["unit_price"], ln["line_total"], ln["received_qty"], ln["sort_order"],
                 ln["nc_source_pk"]))
        po_line_id_by_ncpk[ln["nc_source_pk"]] = lid
        counts["po_lines_upserted"] += 1

    for gr in payload["grs"]:
        pid = po_id_by_ncpk.get(gr["po_nc_pk"])
        if pid is None:
            continue
        cur.execute("select id from goods_receipts where nc_source_pk=%s and source='nc'",
                    (gr["nc_source_pk"],))
        row = cur.fetchone()
        if row:
            gid = row[0]
        else:
            gid = uuid.uuid4()
            cur.execute("select number, vendor_id, vendor_name, currency from purchase_orders where id=%s", (pid,))
            _num, vid, vname, ccy = cur.fetchone()
            cur.execute(
                "insert into goods_receipts (id,number,title,po_id,po_number,pr_id,vendor_id,"
                "vendor_name,gr_type,procurement_type,currency,status,created_by,source,nc_source_pk,"
                "created_at,updated_at) values (%s,%s,%s,%s,%s,NULL,%s,%s,'physical',1,%s,%s,%s,'nc',%s,now(),now())",
                (gid, gr["number"], gr["title"], pid, _num, vid, vname, ccy, gr["status"],
                 system_user_id, gr["nc_source_pk"]))
            counts["grs_upserted"] += 1
        gr_id_by_ncpk[gr["nc_source_pk"]] = gid

    for gl in payload["gr_lines"]:
        gid = gr_id_by_ncpk.get(gl["gr_nc_pk"])
        if gid is None:
            continue
        cur.execute("select id from gr_line_items where nc_source_pk=%s", (gl["nc_source_pk"],))
        if cur.fetchone():
            continue
        plid = po_line_id_by_ncpk.get(gl["po_line_nc_pk"])
        cur.execute(
            "insert into gr_line_items (id,gr_id,po_line_id,description,material_id,qty_ordered,"
            "qty_received,unit,unit_price,line_total,condition,sort_order,nc_source_pk) "
            "values (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,'good',%s,%s)",
            (uuid.uuid4(), gid, plid, gl["description"], gl["material_id"], gl["qty_ordered"],
             gl["qty_received"], "EA", gl["unit_price"], gl["line_total"], gl["sort_order"],
             gl["nc_source_pk"]))
        counts["gr_lines_upserted"] += 1
    return counts
```
(GR line `unit` uses `"EA"`; if a matching po_line exists you may copy its `unit` — optional refinement. Keep `line_total` present on both PO-line insert and update.)

- [ ] **Step 4: Run writer tests to pass**

Run: `cd epms-api && <test env> python -m pytest tests/test_nc_purchase_writer.py -v`
Expected: all PASS.

- [ ] **Step 5: Implement service orchestration**

Create `epms-api/app/services/nc_purchase_sync/service.py` — mirror `finance-api/app/services/nc_sync.py` harness (single-flight `threading.Lock`, `STALE_AFTER=30min`, `start_run` inserts a `nc_purchase_sync_runs` row and raises `SyncAlreadyRunning` if one is live, `_run_worker` calls `reader.fetch_nc` → `transform` → per-run psycopg2 txn calling `writer.upsert`, then `_mark_terminal` sets success + `watermark_to=raw["max_modifiedtime"]`). `latest_watermark(cur)` = `select watermark_to from nc_purchase_sync_runs where status='success' and watermark_to is not null order by started_at desc limit 1`. Constants: `FULL_CONFIRM="FULL RELOAD"`, `SyncAlreadyRunning(Exception)`. For `mode=='full'`, before upsert: `delete from goods_receipts where source='nc'; delete from purchase_orders where source='nc'` (CASCADE removes lines) — but FIRST guard: skip/abort deletion of any NC PO that is consumed (has a matched invoice) — collect those and exclude, count into `skipped_consumed`.

- [ ] **Step 6: Write service single-flight test**

```python
def test_start_run_rejects_concurrent(pg_conn, system_user_id):
    from app.services.nc_purchase_sync import service
    rid = service.start_run("incremental", system_user_id,
                            fetch=lambda *a, **k: _empty_raw(), pg_dsn=TEST_DSN, run_worker=False)
    import pytest
    with pytest.raises(service.SyncAlreadyRunning):
        service.start_run("incremental", system_user_id,
                          fetch=lambda *a, **k: _empty_raw(), pg_dsn=TEST_DSN, run_worker=False)
```

- [ ] **Step 7: Run + commit**

Run: `cd epms-api && <test env> python -m pytest tests/test_nc_purchase_writer.py -v`
Expected: PASS.
```bash
git add epms-api/app/services/nc_purchase_sync/writer.py epms-api/app/services/nc_purchase_sync/service.py epms-api/tests/test_nc_purchase_writer.py epms-api/tests/conftest.py
git commit -m "feat(nc): idempotent writer + sync orchestration with single-flight + watermark"
```

---

## Phase 5 — API

### Task 5: NC purchase sync endpoints

**Files:**
- Create: `epms-api/app/api/v1/nc_purchase_sync.py`
- Modify: `epms-api/app/api/v1/__init__.py` (register router)
- Test: `epms-api/tests/test_nc_purchase_sync_api.py`

**Interfaces:**
- Consumes: `service.nc_configured/start_run/FULL_CONFIRM/SyncAlreadyRunning`, `reader.fetch_nc`.
- Produces: `GET /api/v1/admin/nc-purchase-sync/status` → `{configured, can_sync, cutover, current_run, last_run}`; `POST /api/v1/admin/nc-purchase-sync` body `{mode: "full"|"incremental", confirm?: str}` → 202 `{run_id}`. Gates: `system_admin` (403), not configured (503), full without confirm (422), already running (409).

- [ ] **Step 1: Write failing API tests**

`epms-api/tests/test_nc_purchase_sync_api.py`:
```python
def test_status_requires_system_admin(client, clerk_token):
    r = client.get("/api/v1/admin/nc-purchase-sync/status",
                   headers={"Authorization": f"Bearer {clerk_token}"})
    assert r.json()["can_sync"] is False

def test_trigger_403_for_non_admin(client, clerk_token):
    r = client.post("/api/v1/admin/nc-purchase-sync",
                    json={"mode": "incremental"},
                    headers={"Authorization": f"Bearer {clerk_token}"})
    assert r.status_code == 403

def test_trigger_full_requires_confirm(client, admin_token, monkeypatch):
    monkeypatch.setattr("app.services.nc_purchase_sync.service.nc_configured", lambda: True)
    r = client.post("/api/v1/admin/nc-purchase-sync",
                    json={"mode": "full"},
                    headers={"Authorization": f"Bearer {admin_token}"})
    assert r.status_code == 422

def test_trigger_503_when_not_configured(client, admin_token, monkeypatch):
    monkeypatch.setattr("app.services.nc_purchase_sync.service.nc_configured", lambda: False)
    r = client.post("/api/v1/admin/nc-purchase-sync",
                    json={"mode": "incremental"},
                    headers={"Authorization": f"Bearer {admin_token}"})
    assert r.status_code == 503
```

- [ ] **Step 2: Run to verify failure**

Run: `cd epms-api && <test env> python -m pytest tests/test_nc_purchase_sync_api.py -v`
Expected: FAIL (404 — route missing).

- [ ] **Step 3: Implement the router**

Create `epms-api/app/api/v1/nc_purchase_sync.py` — mirror `finance-api/app/api/v1/nc_sync.py:17,43-85`, adjusting prefix to `/admin/nc-purchase-sync`, using this repo's auth dependency (match how other epms `admin` endpoints read the current user / enforce `system_admin`, e.g. `app/api/v1/admin.py`). `cutover` comes from `settings` (add `nc_cutover_date: str = "2026-08-01 00:00:00"` to config) and is echoed in status. The trigger passes `fetch=lambda wm: reader.fetch_nc(settings.nc_cutover_date, wm)` into `service.start_run` and launches `service._run_worker` via `loop.run_in_executor`.

- [ ] **Step 4: Register the router**

In `epms-api/app/api/v1/__init__.py`, import and `include_router(nc_purchase_sync.router)` following the existing registration pattern.

- [ ] **Step 5: Run tests + commit**

Run: `cd epms-api && <test env> python -m pytest tests/test_nc_purchase_sync_api.py -v`
Expected: all PASS.
```bash
git add epms-api/app/api/v1/nc_purchase_sync.py epms-api/app/api/v1/__init__.py epms-api/app/core/config.py epms-api/tests/test_nc_purchase_sync_api.py
git commit -m "feat(nc): admin API to trigger + monitor NC purchase sync"
```

---

## Phase 6 — Frontend

### Task 6: Portal admin "NC Purchase Sync" section

**Files:**
- Create: `portal/src/pages/admin/NcPurchaseSyncSection.tsx`
- Modify: `portal/src/pages/admin/AdminPanel.tsx:2091-2102` (SECTIONS), `:2174-2183` (content switch)
- Test: manual (browse) — see Step 4.

**Interfaces:**
- Consumes: `epmsApi.get('/admin/nc-purchase-sync/status')`, `epmsApi.post('/admin/nc-purchase-sync', {mode, confirm})`.
- Produces: a new admin section `nc_purchase`.

- [ ] **Step 1: Build the section component**

Create `portal/src/pages/admin/NcPurchaseSyncSection.tsx` — model on `finance/src/pages/finance/NcSyncModal.tsx` (mode radio incremental|full, FULL RELOAD confirm input for full, poll status via `refetchInterval`, show current/last run counts). Use `epmsApi` from `@/lib/api`. Show `cutover` from status. Disable the trigger button when `!configured` or a run is `running`.

- [ ] **Step 2: Register in AdminPanel**

`AdminPanel.tsx` SECTIONS array (`:2091-2102`) add: `{ key: 'nc_purchase', label: 'NC Purchase Sync', icon: DatabaseZap }` (import `DatabaseZap` from `lucide-react`). Content switch (`:2174-2183`) add: `{section === 'nc_purchase' && <NcPurchaseSyncSection />}` and import the component.

- [ ] **Step 3: Typecheck**

Run: `cd portal && npx tsc -p tsconfig.app.json --noEmit`
Expected: no NEW errors vs baseline (portal baseline is 0 per `reference_uniops_frontend_tsc6`; do not add the `--ignoreDeprecations` flag to epms).

- [ ] **Step 4: Manual browse verification**

Use the `browse` skill against the dev portal admin page: confirm the "NC Purchase Sync" section renders, shows `configured` state and the cutover date, and the Full mode requires typing FULL RELOAD. Screenshot before/after.

- [ ] **Step 5: Commit**
```bash
git add portal/src/pages/admin/NcPurchaseSyncSection.tsx portal/src/pages/admin/AdminPanel.tsx
git commit -m "feat(nc): portal admin NC Purchase Sync section"
```

### Task 7: Read-only NC context on invoice/PA/PO detail

**Files:**
- Modify: PO detail (`epms/src/pages/po/PoDetailPage.tsx`), PA detail, invoice detail/match pages — add a small "NC 来源" badge when `source === 'nc'` and render the linked NC order/arrival numbers read-only.
- Modify: `epms/src/components/pr/ProcurementTypeSelector.tsx:12` — keep type-1 `disabled` for manual creation, but ensure type-1 **label** renders for display of NC-source POs (display path must not blank out a disabled type).
- Test: manual (browse).

**Interfaces:**
- Consumes: PO/GR/invoice payloads now carrying `source`. Confirm the read serializers expose `source` (add to the Pydantic response schema if missing — `epms-api/app/schemas/po.py`, `gr.py`).

- [ ] **Step 1: Expose `source` in read schemas**

In `epms-api/app/schemas/po.py` (and `gr.py`) add `source: str | None = None` to the read/response model. Add a schema test asserting a mirrored PO serializes `source='nc'`.

- [ ] **Step 2: Run schema test**

Run: `cd epms-api && <test env> python -m pytest tests/test_nc_purchase_sync_api.py -k source -v`
Expected: PASS.

- [ ] **Step 3: Frontend badge + type-1 display**

Add a `source === 'nc'` badge (reuse app-level `StatusBadge` per `project_uniops_status_badge_unify`; do NOT write inline badge colors). Ensure `ProcurementTypeSelector` display mapping returns the type-1 label even though the option is `disabled` for creation.

- [ ] **Step 4: Typecheck (epms)**

Run: `cd epms && npx tsc -p tsconfig.app.json --noEmit`
Expected: error count == baseline 59 (per `project_uniops_pa_chain_attachments`); no NEW errors.

- [ ] **Step 5: Manual browse + commit**

Browse a mirrored PO/PA detail; confirm the NC badge + linked arrival numbers show. Screenshot.
```bash
git add epms-api/app/schemas/po.py epms-api/app/schemas/gr.py epms/src/pages epms/src/components/pr/ProcurementTypeSelector.tsx
git commit -m "feat(nc): read-only NC-source context on PO/PA/invoice detail"
```

---

## Phase 7 — End-to-end verification

### Task 8: Mirror → upload invoice → match → PA gate integration test

**Files:**
- Test: `epms-api/tests/test_nc_mirror_pipeline.py`

**Interfaces:**
- Consumes: everything above.

- [ ] **Step 1: Write the end-to-end test**

`epms-api/tests/test_nc_mirror_pipeline.py`:
```python
def test_mirrored_po_and_gr_pass_three_way_gate(db, seeded_vendor, system_user_id):
    # 1. mirror one order + one arrival via writer.upsert
    from app.services.nc_purchase_sync import writer
    writer.upsert(raw_cur(db), _mini_payload(seeded_vendor), system_user_id)
    # 2. resolve mirrored po + po_line + gr ids
    po = _get_po_by_nc(db, "O1")
    # 3. upload an invoice against the PO, create an allocation on the po_line, match with gr_ids
    inv = _upload_and_match_invoice(db, po, gr_nc_pk="A1:O1")
    assert inv.status == "matched" and inv.gr_id is not None
    # 4. gate passes
    from app.crud.po import po_has_three_way_matched_invoice
    assert await po_has_three_way_matched_invoice(db, po.id) is True
```
(Use the existing invoice-upload + match crud/endpoints — do NOT hand-set statuses. This proves the mirrored GR's `po_line_id` + `line_total` make the real match/gate pass.)

- [ ] **Step 2: Run**

Run: `cd epms-api && <test env> python -m pytest tests/test_nc_mirror_pipeline.py -v`
Expected: PASS.

- [ ] **Step 3: Full NC suite (regression) + baseline compare**

Run the NC tests together:
`cd epms-api && <test env> python -m pytest tests/test_nc_purchase_transform.py tests/test_nc_purchase_writer.py tests/test_nc_purchase_sync_api.py tests/test_nc_mirror_pipeline.py -v`
Expected: all PASS. Then run the broader suite once and confirm no NEW failures beyond the known ~72 baseline.

- [ ] **Step 4: Commit**
```bash
git add epms-api/tests/test_nc_mirror_pipeline.py
git commit -m "test(nc): end-to-end mirror -> match -> 3-way gate integration"
```

### Task 9: Manual NC-prod dry-run verification (no code)

- [ ] **Step 1: Confirm NC status semantics**

Using the read-only helper (`c:/Project/nc65_introspect/conn.py`), spot-check `forderstatus` 0/2 and `fbillstatus` 0 rows are not active-payable orders being wrongly excluded. Document counts in the run report.

- [ ] **Step 2: Verify app-server → NC connectivity**

Confirm (give the user a command list; app server 10.10.50.65 has no SSH for us) that `10.10.50.65 → 10.10.95.67:1521` is reachable before enabling `NC_*` in prod compose. Add `oracledb` to the epms-api image build.

- [ ] **Step 3: Staged incremental run**

On a staging/dev epms DB with `NC_*` pointed at NC prod (read-only), trigger one Incremental sync; verify PO/GR row counts, vendor resolution rate, and `skipped_no_vendor` report. Import any missing vendors via existing `import-from-erp`, re-run.

---

## Self-Review

**Spec coverage:** ✅ mirror orders+arrivals (Tasks 3-4), provenance/idempotency (Task 1), sync mechanism + watermark + single-flight (Task 4), API + FULL RELOAD (Task 5), admin button (Task 6), read-only NC context + type-1 display (Task 7), receipt gate reuse via real GRs (Tasks 3/4/8), cutover filter (Task 2/5 config), vendor resolution + skip-report (Tasks 3-4), material free-text (Task 3), multi-order arrival split (Task 3), consumed-doc guard (Task 4), status filter=3 (Task 2 reader). Open items (NC status 0/2, oracledb + connectivity) → Task 9.

**Placeholder scan:** Backend core (schema, reader SQL, transform, writer, gate reuse, API gates) is fully written. Harness scaffolding (service single-flight, admin modal, router auth) references the proven finance `nc_sync`/`NcSyncModal` templates by exact file:line — the established-pattern exception, with all NC-specific deltas spelled out.

**Type consistency:** `nc_source_pk` String(20) everywhere; GR composite key `"{arr_pk}:{ord_pk}"` used identically in transform + writer + tests; PO status `"issued"`, GR status `"confirmed"`, type `1` consistent; vendor map shape `erp_id -> (id, name)` consistent across `load_vendor_map`/`transform`.
