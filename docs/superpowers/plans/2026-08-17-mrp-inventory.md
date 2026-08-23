# MRP Inventory Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give MRP an Inventory section — lot search, shelf-life aging warnings at 180/60/30 days, and per-material supply that includes what is on order but not yet received, with its expected arrival date.

**Architecture:** One new column fed by the existing NC purchase sync carries the ERP's per-line planned arrival date into `po_line_items`. mrp-api reads open POs through a narrow read-only mirror (both services share one Postgres database) and serves three new read endpoints off the existing WMS lot mirror. The frontend adds one nav item with three tabs.

**Tech Stack:** FastAPI + SQLAlchemy 2.x async + Alembic; React 19 + TS + Vite + TanStack Query v5 + `@uniops/shell`; python-oracledb for the NC read.

**Spec:** `docs/superpowers/specs/2026-08-17-mrp-inventory-design.md`

## Global Constraints

- **Branch:** `feature/mrp-inventory` (stacked on `feature/mrp-1c-prereqs`). Commit per logical unit; **never push without the user's consent**.
- **Baselines, measured on this branch before any change:** mrp-api `tests/` — measure with the command in Task 0 and record it; mrp frontend `tsc -b --force` — **1 pre-existing error** (`src/components/matrixGrid/verify.ts:159`, also on `origin/main`); epms-api `tests/` — measure before touching it.
- **UI copy is English.** Comments may be Chinese; user-facing strings never are.
- **Decimals cross the wire as JSON strings.** Type them `string` at the boundary and convert with `Number()` at the call site.
- **Date-only values never go through `new Date()`** or any timezone conversion. `DPLANARRVDATE` is `CHAR` holding `'YYYY-MM-DD HH24:MI:SS'`; take `value[:10]`.
- **Every query gets an explicit error surface.** A silently empty table is this project's most-repeated bug; "no rows" and "the request failed" must never render identically.
- **Filters and paging happen in the database**, before the page is cut.
- **New alembic migration:** check `alembic heads` first and chain onto the real tail. epms-api's head at time of writing is `ag09_receipt_amounts_nullable`; re-verify, do not trust this line.
- **mrp-api needs no migration.** Every read is over existing tables.
- **In-transit definition (used verbatim in Tasks 3, 4, 6):**
  `purchase_orders.type = 1` AND `purchase_orders.status IN ('issued','partially_received')` AND `po_line_items.qty > po_line_items.received_qty` AND `po_line_items.material_id IS NOT NULL`.
  `nc_milk` is excluded because its receipts are recorded outside UniOps and net to **−2,695,743** (CR0180 −1,648,350; CR0010 −1,525,432).
- **Expected numbers on the dev snapshot** (a production copy), for verifying end to end: in transit **87 lines / 71 materials / 902,779**; raw-material aging **90 lots expired, 11 under 30 days, 11 at 30–60, 97 at 60–180, 347 over 180**; WMS mirror **3,532 lots / 200 materials**.

---

### Task 0: Record the baselines

No production code. This exists because every later task's "did I break something" answer is a comparison against these numbers, and this repo's baselines drift.

**Files:** none (record the output in the task's commit message or the session log).

- [ ] **Step 1: mrp-api**

```bash
cd mrp-api
PGPW=$(docker inspect uniops_postgres --format '{{range .Config.Env}}{{println .}}{{end}}' \
  | grep '^POSTGRES_PASSWORD=' | cut -d= -f2)
JWT_SECRET_KEY=test-secret TEST_PG_PASSWORD="$PGPW" \
  ALLOWED_ORIGINS='["http://localhost:5179"]' \
  python -m pytest tests -q 2>&1 | tail -5
```

Expected: a `N passed, M failed` line. **Record N and M.** Do not assume 0 failures — record whatever it says. Full run takes roughly an hour; if that is too slow, run per-file and record per-file counts instead, but never substitute a guess.

- [ ] **Step 2: epms-api** — its NC sync tests are the ones Task 2 touches

```bash
cd epms-api
JWT_SECRET_KEY=test-secret POSTGRES_DB=epms_test POSTGRES_HOST=localhost \
  POSTGRES_USER=epms POSTGRES_PASSWORD="$PGPW" \
  python -m pytest tests/test_nc_purchase_sync*.py -q 2>&1 | tail -5
```

Record the count. If the file names differ, list `tests/` and use the NC sync ones.

- [ ] **Step 3: mrp frontend**

```bash
cd mrp && ./node_modules/.bin/tsc --version && ./node_modules/.bin/tsc -b --force
```

Expected: TypeScript 6.0.3 and exactly one error, in `src/components/matrixGrid/verify.ts`. **Confirm the version line printed** — a missing local typescript silently falls back to a global one that reports nothing.

---

### Task 1: `planned_arrival_date` column + migration (epms-api)

**Files:**
- Modify: `epms-api/app/models/po.py` (add the column to `PoLineItem`)
- Create: `epms-api/alembic/versions/<rev>_po_line_planned_arrival_date.py`

**Interfaces:**
- Produces: `PoLineItem.planned_arrival_date: Mapped[date | None]` — read by Tasks 2, 4, 5.

- [ ] **Step 1: Confirm the real migration head**

```bash
cd epms-api && docker exec uniops_epms_api alembic heads
```

Expected: exactly one head. Use it as `down_revision`. Two heads means stop and ask — a guessed parent splits the chain and aborts the migration in production.

- [ ] **Step 2: Add the column to the model**

```python
    received_qty: Mapped[Decimal] = mapped_column(Numeric(15, 4), nullable=False, default=Decimal("0"))
    # ERP's per-line planned arrival date (NCSC.PO_ORDER_B.DPLANARRVDATE),
    # brought across by the NC purchase sync. LINE level, not header: NC lets
    # each line have its own date and real orders do (PO-009-2603-01's two
    # lines differ), which is why this is not the header's expected_delivery.
    # NULL for UniOps-native POs, where the hand-entered header date is the
    # only date there is.
    planned_arrival_date: Mapped[date | None] = mapped_column(Date, nullable=True)
```

- [ ] **Step 3: Write the migration**

```python
"""po_line_items.planned_arrival_date

Revision ID: <rev>
Revises: <the head from Step 1>
"""
import sqlalchemy as sa
from alembic import op

revision = "<rev>"
down_revision = "<head from step 1>"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "po_line_items",
        sa.Column("planned_arrival_date", sa.Date(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("po_line_items", "planned_arrival_date")
```

- [ ] **Step 4: Apply it against the dev database and verify the column exists**

```bash
docker exec uniops_epms_api alembic upgrade head
docker exec uniops_postgres psql -U epms -d epms -c \
  "select column_name, data_type, is_nullable from information_schema.columns
   where table_name='po_line_items' and column_name='planned_arrival_date';"
```

Expected: one row, `date`, `YES`. An empty result means the migration did not run — positive evidence, not absence of errors.

- [ ] **Step 5: Commit**

```bash
git add epms-api/app/models/po.py epms-api/alembic/versions/
git commit -m "feat(epms): po_line_items.planned_arrival_date, the ERP's per-line arrival date"
```

---

### Task 2: Read `DPLANARRVDATE` in the NC sync (epms-api)

**Files:**
- Modify: `epms-api/app/services/nc_purchase_sync/reader.py` (the `PO_ORDER_B` select)
- Modify: `epms-api/app/services/nc_purchase_sync/transform.py` (parse + carry on the line dict)
- Modify: `epms-api/app/services/nc_purchase_sync/writer.py` (persist on insert and update)
- Test: `epms-api/tests/test_nc_purchase_sync_transform.py` (or the existing transform test file — check `tests/` first and extend rather than duplicate)

**Interfaces:**
- Consumes: `PoLineItem.planned_arrival_date` from Task 1.
- Produces: `nc_purchase_sync.transform._planned_arrival(raw: str | None) -> date | None`, and a `"planned_arrival_date"` key on every transformed PO line dict.

- [ ] **Step 1: Write the failing test for the date parser**

```python
from datetime import date

from app.services.nc_purchase_sync.transform import _planned_arrival


def test_planned_arrival_takes_the_date_half_only():
    """NC stores this as CHAR 'YYYY-MM-DD HH24:MI:SS'. The time is the stamp of
    whenever the value was last edited and has no business meaning."""
    assert _planned_arrival("2026-02-02 10:01:25") == date(2026, 2, 2)


def test_planned_arrival_does_not_shift_across_a_day_boundary():
    """The trap this guards: putting a date-only value through a timezone
    conversion moves it a day in either direction. A late-evening stamp is the
    case that exposes it, and a New Year's Eve one exposes the wrong YEAR."""
    assert _planned_arrival("2026-12-31 23:59:59") == date(2026, 12, 31)
    assert _planned_arrival("2026-01-01 00:00:00") == date(2026, 1, 1)


def test_planned_arrival_of_nothing_is_none_not_today():
    for empty in (None, "", "   ", "not a date", "0000-00-00 00:00:00"):
        assert _planned_arrival(empty) is None, empty
```

- [ ] **Step 2: Run it and watch it fail**

```bash
cd epms-api
JWT_SECRET_KEY=test-secret python -m pytest tests/test_nc_purchase_sync_transform.py -q -k planned_arrival
```

Expected: `ImportError` / `AttributeError` — `_planned_arrival` does not exist.

- [ ] **Step 3: Implement the parser**

```python
def _planned_arrival(raw: str | None) -> date | None:
    """NCSC.PO_ORDER_B.DPLANARRVDATE -> a plain date.

    The column is CHAR holding 'YYYY-MM-DD HH24:MI:SS'. Only the date half is
    the planned arrival; the time is when somebody last set it.

    ★ Parsed by slicing the first 10 characters, deliberately. Nothing here
    constructs a datetime and nothing converts a timezone: a date-only value
    put through a UTC conversion lands a day early or late (and on Dec 31, in
    the wrong year), which is a bug this codebase has already shipped once.
    """
    if not raw or not raw.strip():
        return None
    try:
        return date.fromisoformat(raw.strip()[:10])
    except ValueError:
        return None
```

- [ ] **Step 4: Run it and watch it pass**

```bash
JWT_SECRET_KEY=test-secret python -m pytest tests/test_nc_purchase_sync_transform.py -q -k planned_arrival
```

Expected: 3 passed.

- [ ] **Step 5: Add the column to the reader's query**

In `reader.py`, the `PO_ORDER_B` select currently reads:

```python
                "select pk_order_b, pk_order, crowno, pk_material, vvendinventoryname, "
                "castunitid, nastnum, norigtaxprice, ntaxrate, ctaxcodeid, norigtaxmny, norigmny, ntax, "
                "bpayclose, binvoiceclose "
                f"from NCSC.PO_ORDER_B where pk_order in ({ph})", b)
```

Add `dplanarrvdate` to the column list. Verified present on `NCSC.PO_ORDER_B` (`all_tab_columns`, type CHAR) and populated on 4,890 of 4,890 approved order lines.

- [ ] **Step 6: Carry it on the transformed line and write a test that it survives the transform**

Find where `transform.py` builds each PO-line dict (the dict containing `"nc_source_pk": l["pk_order_b"]`) and add:

```python
            "planned_arrival_date": _planned_arrival(l.get("dplanarrvdate")),
```

Then a test using the file's existing raw-payload fixture shape:

```python
def test_transform_carries_the_planned_arrival_date_onto_the_line():
    """Reading the column is useless if the transform drops it -- the failure
    looks exactly like the ERP not having the value."""
    raw = _minimal_raw_payload()   # reuse this file's existing fixture helper
    raw["order_lines"][0]["dplanarrvdate"] = "2026-05-05 09:31:37"
    result = transform(raw, ...)   # match the existing tests' call signature
    assert result.po_lines[0]["planned_arrival_date"] == date(2026, 5, 5)
```

- [ ] **Step 7: Persist it in the writer**

In `writer.py`, add `planned_arrival_date` wherever the other line columns are written, on **both** the insert and the update path. A sync that only sets it on insert leaves every already-synced line permanently null — and the whole point is the 87 lines that already exist.

- [ ] **Step 8: Run the NC sync suite against the Task 0 baseline**

```bash
JWT_SECRET_KEY=test-secret POSTGRES_DB=epms_test POSTGRES_HOST=localhost \
  POSTGRES_USER=epms POSTGRES_PASSWORD="$PGPW" \
  python -m pytest tests/test_nc_purchase_sync*.py -q 2>&1 | tail -5
```

Expected: Task 0's count plus the new tests, with no new failures.

- [ ] **Step 9: Commit**

```bash
git add epms-api/app/services/nc_purchase_sync/ epms-api/tests/
git commit -m "feat(epms): bring NC's per-line planned arrival date into the PO sync"
```

---

### Task 3: The in-transit query, as a pure-ish service (mrp-api)

**Files:**
- Create: `mrp-api/app/models/epms_po_mirror.py`
- Create: `mrp-api/app/services/in_transit.py`
- Test: `mrp-api/tests/test_in_transit.py`
- Test: `mrp-api/tests/test_epms_po_mirror_columns.py`

**Interfaces:**
- Produces:
  - `EpmsPurchaseOrder` / `EpmsPoLineItem` — read-only mirror models (`__tablename__` `purchase_orders` / `po_line_items`), mapping only: PO `id, number, type, status, vendor_name, expected_delivery`; line `id, po_id, material_id, qty, received_qty, unit, planned_arrival_date`.
  - `in_transit_by_material(db) -> dict[str, InTransit]` where
    `InTransit = dataclass(qty: Decimal, earliest_arrival: date | None, open_lines: int)`.
  - `open_lines_for_material(db, material_code) -> list[OpenPoLine]` for the drill-down.
- Consumed by: Task 4's `/inventory/materials`.

- [ ] **Step 1: Write the mirror-column guard test first**

```python
"""EPMS owns purchase_orders/po_line_items; mrp-api only reads them.

This suite is the tripwire for that arrangement. Without it, EPMS renaming or
retyping a column makes the in-transit column silently ZERO -- a number that
looks like "nothing is on order" and is indistinguishable from the truth. The
same failure has bitten mirror models in this repo three times, which is why
the assertion is against information_schema rather than against the ORM.
"""
import pytest
from sqlalchemy import text

EXPECTED = {
    "purchase_orders": {
        "id": "uuid", "number": "character varying", "type": "integer",
        "status": "character varying", "vendor_name": "character varying",
        "expected_delivery": "date",
    },
    "po_line_items": {
        "id": "uuid", "po_id": "uuid", "material_id": "character varying",
        "qty": "numeric", "received_qty": "numeric", "unit": "character varying",
        "planned_arrival_date": "date",
    },
}


@pytest.mark.anyio
@pytest.mark.parametrize("table", sorted(EXPECTED))
async def test_mirrored_columns_still_exist_with_the_expected_type(db_session, table):
    rows = (await db_session.execute(text(
        "select column_name, data_type from information_schema.columns "
        "where table_name = :t"
    ), {"t": table})).all()
    actual = {name: dtype for name, dtype in rows}
    assert actual, f"{table} does not exist in the test database at all"
    for column, dtype in EXPECTED[table].items():
        assert column in actual, f"EPMS dropped or renamed {table}.{column}"
        assert actual[column] == dtype, (
            f"{table}.{column} is now {actual[column]}, expected {dtype}")
```

**Note for the implementer:** mrp-api's test database is built with `create_all()` from mrp-api's own metadata (see `tests/conftest.py`), so these tables only exist there because the mirror models are registered. Registering them is part of Step 2; if this test reports "does not exist at all", the models are not imported where conftest can see them.

- [ ] **Step 2: Write the mirror models**

```python
"""Read-only mirror of EPMS's purchase orders — the "what is on order" half
of MRP's inventory picture.

★ EPMS OWNS THESE TABLES. mrp-api never writes them, never migrates them, and
maps only the columns it reads. Both services share one Postgres database, so
this is a direct read rather than an HTTP call to epms-api: the alternative is
an N+1 over ~90 rows against endpoints that carry user-level department
scoping which means nothing for a service-to-service aggregate.

The arrangement is only safe because tests/test_epms_po_mirror_columns.py
asserts every column below still exists with the same type. Without that, an
EPMS rename turns in-transit silently into zero.
"""
import uuid
from datetime import date
from decimal import Decimal

from sqlalchemy import Date, ForeignKey, Integer, Numeric, String
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class EpmsPurchaseOrder(Base):
    __tablename__ = "purchase_orders"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    number: Mapped[str] = mapped_column(String(40))
    # 1=Raw Materials (which is also where packaging lives), 2=Consumables,
    # 3=Spare Parts, 4=Service, 5=Fixed Assets, 6=Software.
    type: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(30))
    vendor_name: Mapped[str] = mapped_column(String(255))
    expected_delivery: Mapped[date | None] = mapped_column(Date)


class EpmsPoLineItem(Base):
    __tablename__ = "po_line_items"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    po_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("purchase_orders.id"))
    material_id: Mapped[str | None] = mapped_column(String(50))
    qty: Mapped[Decimal] = mapped_column(Numeric(15, 4))
    received_qty: Mapped[Decimal] = mapped_column(Numeric(15, 4))
    unit: Mapped[str] = mapped_column(String(30))
    planned_arrival_date: Mapped[date | None] = mapped_column(Date)
```

Register the module wherever mrp-api's other models are imported for metadata (check `app/models/__init__.py` and `tests/conftest.py` — follow whichever mechanism `wms_inventory` uses).

- [ ] **Step 3: Run the guard test**

```bash
cd mrp-api
JWT_SECRET_KEY=test-secret TEST_PG_PASSWORD="$PGPW" \
  ALLOWED_ORIGINS='["http://localhost:5179"]' \
  python -m pytest tests/test_epms_po_mirror_columns.py -q
```

Expected: 2 passed. `planned_arrival_date` only exists because of Task 1 — if it fails on that column, Task 1's migration has not reached the test database.

- [ ] **Step 4: Write the failing in-transit tests**

```python
"""The three exclusions, each with the number that justifies it."""
import uuid
from datetime import date
from decimal import Decimal

import pytest

from app.models.epms_po_mirror import EpmsPoLineItem, EpmsPurchaseOrder
from app.services.in_transit import in_transit_by_material


async def _po(db, *, status="issued", type_=1, number=None, eta=None):
    po = EpmsPurchaseOrder(
        id=uuid.uuid4(), number=number or f"PO-{uuid.uuid4().hex[:8]}",
        type=type_, status=status, vendor_name="Test Vendor", expected_delivery=eta,
    )
    db.add(po)
    await db.commit()
    return po


async def _line(db, po, code, qty, received, arrival=None):
    db.add(EpmsPoLineItem(
        id=uuid.uuid4(), po_id=po.id, material_id=code, qty=Decimal(qty),
        received_qty=Decimal(received), unit="KGM", planned_arrival_date=arrival))
    await db.commit()


@pytest.mark.anyio
async def test_only_the_unreceived_remainder_is_in_transit(db_session):
    po = await _po(db_session)
    await _line(db_session, po, "CR0025", "1000", "400")
    result = await in_transit_by_material(db_session)
    assert result["CR0025"].qty == Decimal("600")
    assert result["CR0025"].open_lines == 1


@pytest.mark.anyio
async def test_raw_milk_pos_are_excluded(db_session):
    """nc_milk receipts are recorded in NC, not UniOps, so received_qty
    routinely EXCEEDS qty: CR0180 nets -1,648,350 and CR0010 -1,525,432, and
    all 238 such lines net -2,695,743. Counting them reports negative stock
    on order for the plant's highest-volume materials."""
    po = await _po(db_session, status="nc_milk")
    await _line(db_session, po, "CR0180", "1000", "2648350")
    assert "CR0180" not in await in_transit_by_material(db_session)


@pytest.mark.anyio
async def test_pos_not_yet_placed_are_not_in_transit(db_session):
    for status in ("draft", "in_review", "approved", "rejected", "cancelled"):
        po = await _po(db_session, status=status)
        await _line(db_session, po, f"CR-{status}", "100", "0")
    result = await in_transit_by_material(db_session)
    assert result == {}, f"nothing unplaced is in transit, got {sorted(result)}"


@pytest.mark.anyio
async def test_non_material_po_types_are_excluded(db_session):
    """Types 2-6 are consumables/spares/service/assets/software. 893 of the
    open lines company-wide are these, and none carries a material code."""
    for type_ in (2, 3, 4, 5, 6):
        po = await _po(db_session, type_=type_)
        await _line(db_session, po, "CR0025", "100", "0")
    assert await in_transit_by_material(db_session) == {}


@pytest.mark.anyio
async def test_fully_received_lines_do_not_linger(db_session):
    po = await _po(db_session)
    await _line(db_session, po, "CR0025", "500", "500")
    assert "CR0025" not in await in_transit_by_material(db_session)


@pytest.mark.anyio
async def test_earliest_arrival_wins_and_the_line_date_beats_the_header(db_session):
    """A planner asks "when does the next of it land", so the roll-up is the
    MINIMUM. The line's own NC date wins over the hand-entered header date."""
    late = await _po(db_session, eta=date(2026, 1, 1))
    await _line(db_session, late, "CR0025", "100", "0", arrival=date(2026, 9, 30))
    soon = await _po(db_session, eta=date(2026, 12, 1))
    await _line(db_session, soon, "CR0025", "100", "0", arrival=date(2026, 8, 20))
    assert (await in_transit_by_material(db_session))["CR0025"].earliest_arrival == date(2026, 8, 20)


@pytest.mark.anyio
async def test_header_date_is_the_fallback_when_the_line_has_none(db_session):
    """UniOps-native POs have no NC line date; the hand-entered header date is
    the only one there is, and dropping it would report "not stated" for a PO
    that states it."""
    po = await _po(db_session, eta=date(2026, 7, 15))
    await _line(db_session, po, "CR0025", "100", "0", arrival=None)
    assert (await in_transit_by_material(db_session))["CR0025"].earliest_arrival == date(2026, 7, 15)


@pytest.mark.anyio
async def test_no_date_anywhere_is_none_not_today(db_session):
    po = await _po(db_session, eta=None)
    await _line(db_session, po, "CR0025", "100", "0", arrival=None)
    assert (await in_transit_by_material(db_session))["CR0025"].earliest_arrival is None
```

- [ ] **Step 5: Run them and watch them fail**

```bash
JWT_SECRET_KEY=test-secret TEST_PG_PASSWORD="$PGPW" \
  ALLOWED_ORIGINS='["http://localhost:5179"]' \
  python -m pytest tests/test_in_transit.py -q
```

Expected: collection error — `app.services.in_transit` does not exist.

- [ ] **Step 6: Implement the service**

```python
"""What is on order and not yet in the warehouse.

Three exclusions, each load-bearing and each with its number in the tests:

- **PO type must be 1** (raw material / packaging). Types 2-6 are
  consumables, spare parts, service, fixed assets and software; 893 of the
  open lines company-wide are those and not one carries a material code.
- **nc_milk is excluded.** Raw-milk receipt and stock-in happen in NC, not
  UniOps, so `received_qty` is loosely backfilled and regularly EXCEEDS the
  ordered quantity -- CR0180 nets -1,648,350, CR0010 -1,525,432, all 238
  lines -2,695,743. Including them reports negative quantities on order for
  the plant's biggest materials.
- **Only placed orders count.** draft/in_review/approved/rejected/cancelled
  are not on their way anywhere.

Expected arrival prefers the line's own NC date over the PO header's
hand-entered one, and is None when neither exists -- never "today", never a
guess from a lead time.
"""
from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from sqlalchemy import func, select

from app.models.epms_po_mirror import EpmsPoLineItem, EpmsPurchaseOrder

RAW_MATERIAL_PO_TYPE = 1
PLACED_STATUSES = ("issued", "partially_received")


@dataclass(frozen=True)
class InTransit:
    qty: Decimal
    earliest_arrival: date | None
    open_lines: int


def _open_line_filters():
    return (
        EpmsPurchaseOrder.type == RAW_MATERIAL_PO_TYPE,
        EpmsPurchaseOrder.status.in_(PLACED_STATUSES),
        EpmsPoLineItem.material_id.is_not(None),
        EpmsPoLineItem.qty > EpmsPoLineItem.received_qty,
    )


async def in_transit_by_material(db) -> dict[str, InTransit]:
    arrival = func.coalesce(
        EpmsPoLineItem.planned_arrival_date, EpmsPurchaseOrder.expected_delivery)
    stmt = (
        select(
            EpmsPoLineItem.material_id,
            func.sum(EpmsPoLineItem.qty - EpmsPoLineItem.received_qty),
            func.min(arrival),
            func.count(),
        )
        .join(EpmsPurchaseOrder, EpmsPurchaseOrder.id == EpmsPoLineItem.po_id)
        .where(*_open_line_filters())
        .group_by(EpmsPoLineItem.material_id)
    )
    return {
        code: InTransit(qty=qty, earliest_arrival=arrival_date, open_lines=lines)
        for code, qty, arrival_date, lines in (await db.execute(stmt)).all()
    }
```

Also implement `open_lines_for_material(db, material_code) -> list[OpenPoLine]` returning PO number, vendor name, qty, received, remaining, unit and expected arrival for the drill-down, reusing `_open_line_filters()` so the two can never disagree about what "open" means.

- [ ] **Step 7: Run the tests and watch them pass**

```bash
JWT_SECRET_KEY=test-secret TEST_PG_PASSWORD="$PGPW" \
  ALLOWED_ORIGINS='["http://localhost:5179"]' \
  python -m pytest tests/test_in_transit.py tests/test_epms_po_mirror_columns.py -q
```

Expected: 10 passed.

- [ ] **Step 8: Commit**

```bash
git add mrp-api/app/models/epms_po_mirror.py mrp-api/app/services/in_transit.py mrp-api/tests/
git commit -m "feat(mrp): read what is on order from EPMS, minus the raw-milk trap"
```

---

### Task 4: Aging buckets, as a pure function (mrp-api)

**Files:**
- Create: `mrp-api/app/services/inventory_aging.py`
- Test: `mrp-api/tests/test_inventory_aging.py`

**Interfaces:**
- Produces:
  - `AGING_BUCKETS: tuple[str, ...] = ("expired", "under_30", "30_to_60", "60_to_180", "over_180")`
  - `bucket_for(expiry: date | None, today: date) -> str | None` — `None` when there is no expiry date, which is not a bucket.
  - `summarise(lots, today) -> AgingSummary` with per-bucket lot count and quantity, plus `no_expiry_lots` / `no_expiry_qty`.

Kept free of the database because every trap here is arithmetic on boundaries.

- [ ] **Step 1: Write the failing boundary tests**

```python
from datetime import date, timedelta
from decimal import Decimal

from app.services.inventory_aging import bucket_for, summarise

TODAY = date(2026, 8, 17)


def test_a_lot_expiring_today_is_expired_not_nearly_expired():
    """The boundary that decides whether somebody is allowed to use it. "Today"
    belongs on the expired side: shelf life ran out at the start of the day."""
    assert bucket_for(TODAY, TODAY) == "expired"
    assert bucket_for(TODAY - timedelta(days=1), TODAY) == "expired"
    assert bucket_for(TODAY + timedelta(days=1), TODAY) == "under_30"


def test_exact_bucket_boundaries():
    """Each threshold named in the brief, at exactly the day it names. 30 days
    out is in the 30-60 bucket, not under_30 -- the buckets are half-open so no
    lot can land in two."""
    assert bucket_for(TODAY + timedelta(days=29), TODAY) == "under_30"
    assert bucket_for(TODAY + timedelta(days=30), TODAY) == "30_to_60"
    assert bucket_for(TODAY + timedelta(days=59), TODAY) == "30_to_60"
    assert bucket_for(TODAY + timedelta(days=60), TODAY) == "60_to_180"
    assert bucket_for(TODAY + timedelta(days=179), TODAY) == "60_to_180"
    assert bucket_for(TODAY + timedelta(days=180), TODAY) == "over_180"


def test_no_expiry_date_is_not_a_bucket():
    """1,640 packaging lots have no expiry because packaging does not expire.
    Bucketing them as expired would drown the warning that matters in noise."""
    assert bucket_for(None, TODAY) is None


def test_summary_counts_lots_without_an_expiry_separately():
    """Reported, not dropped: "excluded" and "none" must not look the same."""
    lots = [
        _lot("CR0025", TODAY - timedelta(days=5), "100"),
        _lot("CP0133", None, "5000"),
    ]
    summary = summarise(lots, TODAY)
    assert summary.buckets["expired"].qty == Decimal("100")
    assert summary.no_expiry_lots == 1
    assert summary.no_expiry_qty == Decimal("5000")
```

`summarise` consumes any iterable of objects exposing `material_code`, `expiry_date` and `qty` — which the `WmsInventoryLot` ORM rows already do, so the endpoint passes them straight in and the test uses a three-field `NamedTuple`:

```python
class _L(NamedTuple):
    material_code: str
    expiry_date: date | None
    qty: Decimal

def _lot(code, expiry, qty):
    return _L(code, expiry, Decimal(qty))
```

The function must never take a DB session: every trap in it is arithmetic, and arithmetic tests that need fixtures do not get written.

- [ ] **Step 2: Run and watch fail** — `python -m pytest tests/test_inventory_aging.py -q`, expected collection error.

- [ ] **Step 3: Implement, with the half-open boundaries documented**

```python
"""Shelf-life bucketing for WMS lots.

Thresholds come from the plant: 180 / 60 / 30 days. "Already expired" is a
FOURTH bucket rather than part of "under 30", because 90 raw-material lots
holding 32 tonnes are already past date today and a countdown that stops at
zero never shows them.

Buckets are half-open -- `[today, today+30)` is under_30, `[+30, +60)` is
30_to_60 -- so no lot can fall in two and none can fall in none. A lot
expiring TODAY is expired: its shelf life ran out at the start of the day.

A lot with no expiry date gets NO bucket. Packaging (1,640 of the mirror's
3,532 lots) has none because it does not expire, and calling that "expired"
would bury the real warnings. Callers report the no-expiry count alongside
the buckets so that "excluded" never reads as "none".
"""
```

Implement `bucket_for` and `summarise` to satisfy the tests.

- [ ] **Step 4: Run and watch pass** — expected 4 passed.

- [ ] **Step 5: Commit** — `feat(mrp): shelf-life aging buckets, with expired as its own bucket`

---

### Task 5: The three inventory endpoints (mrp-api)

**Files:**
- Modify: `mrp-api/app/api/v1/inventory.py`
- Test: `mrp-api/tests/test_inventory_endpoint.py` (extend the existing file)
- Test: `mrp-api/tests/test_permission_gates.py` (one gate test for the new endpoints)

**Interfaces:**
- Consumes: `in_transit_by_material`, `open_lines_for_material` (Task 3); `bucket_for`, `summarise`, `AGING_BUCKETS` (Task 4); `resolve_material_names` from `app/services/mdm_client.py` (already used by `forecast.py` — one batched call per request, never per row, and it never raises).
- Produces: `GET /inventory/lots` (extended), `GET /inventory/aging`, `GET /inventory/materials`.

- [ ] **Step 1: Write the failing endpoint tests**

Cover, one test each:

```python
# /inventory/lots
async def test_lots_search_matches_code_name_lot_and_supplier_batch(...)
async def test_lots_search_and_filters_are_applied_before_paging(...)   # total describes the FILTERED set
async def test_lots_expiry_window_filter(...)
async def test_lots_material_names_come_from_one_batched_mdm_call(...)  # monkeypatch, assert call count == 1
async def test_lots_degrade_to_null_names_when_mdm_is_unreachable(...)  # numbers must not depend on mdm

# /inventory/aging
async def test_aging_returns_every_bucket_even_when_empty(...)          # a missing key reads as "no data"
async def test_aging_reports_lots_without_an_expiry_date_separately(...)
async def test_aging_prefix_filter_defaults_to_nothing_and_is_explicit(...)

# /inventory/materials
async def test_materials_combines_on_hand_and_in_transit(...)
async def test_materials_available_excludes_held_and_expired_lots(...)  # available = qty - qty_onhold, mapped_status='available'
async def test_materials_shows_a_material_that_is_only_on_order(...)    # in transit with zero stock must still appear
async def test_materials_in_transit_is_zero_not_null_when_nothing_is_on_order(...)
```

The "only on order" case is the one an inner join would silently lose, and it is exactly the case a planner cares about: nothing in the warehouse, something arriving.

- [ ] **Step 2: Run and watch them fail** — expected 404s / KeyErrors.

- [ ] **Step 3: Implement `GET /inventory/lots`**

Add to the existing endpoint, keeping its current parameters working (`net_requirement.py` and Phase 1C call it):

- `search` — ILIKE over the three columns that live in this database: `material_code`, `lot_no`, `supplier_batch`. **Material NAME is deliberately not searchable here** and the docstring says so: names come from mdm-api one batched call per request, so matching on them would mean either filtering after the page is cut (short pages, a total that counts rows it never shows — the bug fixed on the Outlooks list this same week) or pulling the whole 2,567-row material master into every request. The Materials tab is where you look something up by name; this tab is by code, lot or supplier batch. Saying that in the placeholder text is part of the task — a search box that silently ignores what you typed is worse than one that says what it covers.
- `warehouse_id`, `mapped_status`, `expiring_before`, `expiring_after` — all applied in the database.
- `sort` — a whitelist of `material_code`, `expiry_date`, `qty`, `lot_no`, each with a direction. Never interpolate the parameter into SQL.

- [ ] **Step 4: Implement `GET /inventory/aging`**

Returns every bucket in `AGING_BUCKETS` even when empty, `no_expiry_lots`/`no_expiry_qty` alongside, and the lots behind a requested bucket. `today` is computed once per request and passed into `bucket_for`, never called per row — two lots must not land in different buckets because midnight passed mid-request.

- [ ] **Step 5: Implement `GET /inventory/materials`**

Per material: `on_hand`, `available`, `allocated`, `on_hold`, `expired_qty`, `next_expiry`, `in_transit`, `earliest_arrival`, `open_po_lines`, `name`. Built as a **full outer** combination of the WMS aggregate and the in-transit map — a material with stock and no orders, and a material with orders and no stock, must both appear.

- [ ] **Step 6: Add the permission gate test**

```python
@pytest.mark.anyio
@pytest.mark.parametrize("path", ["/api/v1/inventory/lots", "/api/v1/inventory/aging",
                                  "/api/v1/inventory/materials"])
async def test_inventory_read_gates_403_non_permitted_role(client, non_admin_token, monkeypatch, path):
    _deny_everything(monkeypatch)
    r = await client.get(path, headers={"Authorization": f"Bearer {non_admin_token}"})
    assert r.status_code == 403
```

- [ ] **Step 7: Run the suites** — `tests/test_inventory_endpoint.py tests/test_permission_gates.py tests/test_in_transit.py tests/test_inventory_aging.py`, all passing, and `tests/test_net_requirement.py` unchanged from Task 0 (it reads `/inventory/lots`).

- [ ] **Step 8: Commit** — `feat(mrp): inventory search, aging and per-material supply endpoints`

---

### Task 6: The Inventory page (mrp frontend)

**Files:**
- Create: `mrp/src/pages/inventory/inventoryApi.ts`
- Create: `mrp/src/pages/inventory/InventoryPage.tsx` (tab shell + shared filter state)
- Create: `mrp/src/pages/inventory/LotsTab.tsx`
- Create: `mrp/src/pages/inventory/AgingTab.tsx`
- Create: `mrp/src/pages/inventory/MaterialsTab.tsx`
- Modify: `mrp/src/app/routes.tsx`, `mrp/src/components/layout/AppLayout.tsx`

**Interfaces:**
- Consumes the three endpoints from Task 5. Every Decimal field is typed `string` and converted with `Number()` at the point of display.

- [ ] **Step 1: Write the API client**

Paged list calls take `(page, pageSize, filters)` and build their query with `URLSearchParams`. Follow `mrp/src/pages/forecast/forecastApi.ts` for the shape.

- [ ] **Step 2: Build the tab shell**

`/inventory` with tabs Lots / Aging / Materials. The material filter lives in `InventoryPage` and is passed down, so switching tabs keeps it — a filter that resets on every tab change makes the tabs feel like unrelated pages. Reflect the active tab in the URL (`?tab=aging`) so a tab can be linked and the multi-tab shell restores it.

- [ ] **Step 3: Lots tab**

Search box (debounced 300ms), warehouse and status filters, sortable columns, server-side paging with `keepPreviousData`, `1–10 of N` plus a pager. An `role="alert"` error surface on the query. Expired lots tinted, `mapped_status` as a badge via the shared `StatusBadge` convention rather than a hand-rolled colour map.

- [ ] **Step 4: Aging tab**

Five bucket cards (expired / <30 / 30–60 / 60–180 />180) showing lot count and quantity, clickable to filter the table below. Defaults to raw materials with expired and <30 selected. A line stating how many lots have no expiry date and that they are excluded — with the reason (packaging does not expire), because an unexplained exclusion reads as a bug.

- [ ] **Step 5: Materials tab**

One row per material: on hand, available, on hold, in transit, earliest arrival, next expiry. In transit is a link/expander showing the open PO lines behind it (PO number, vendor, remaining qty, expected arrival). Where an arrival date is unknown, the cell says "not stated" — never blank, which reads as zero, and never a guess.

- [ ] **Step 6: Wire the route and nav**

```tsx
{ path: '/inventory', element: <InventoryPage />, tab: { title: 'Inventory', icon: 'Boxes', keyStrategy: 'static' } },
```

and the matching `NAV` entry with the `Boxes` icon from `lucide-react`. Place it directly after Consignment Stock — both are stock views.

- [ ] **Step 7: Type-check against the Task 0 baseline**

```bash
cd mrp && ./node_modules/.bin/tsc -b --force
```

Expected: the one pre-existing `verify.ts` error and nothing else.

- [ ] **Step 8: Commit** — `feat(mrp-web): Inventory — lot search, shelf-life aging, supply per material`

---

### Task 7: End-to-end verification against real data

**Files:** none (verification only). Record the numbers in the commit message.

- [ ] **Step 1: Merge into the local test branch and confirm the served source is yours**

```bash
cd /c/Project/uniops && git merge --no-edit feature/mrp-inventory
curl -s http://localhost:5179/src/pages/inventory/InventoryPage.tsx | head -5
```

A 404 or stale content means the container is not serving what you just wrote — fix that before believing anything else on this list.

- [ ] **Step 2: Confirm the live API surface**

```bash
curl -s http://localhost:8011/openapi.json | python -c "
import json,sys; d=json.load(sys.stdin)
for p in sorted(k for k in d['paths'] if 'inventory' in k): print(p, sorted(d['paths'][p]))"
```

Expected: `/api/v1/inventory/lots`, `/aging`, `/materials`.

- [ ] **Step 3: Run the NC sync in full mode and confirm the arrival dates landed**

```bash
docker exec uniops_postgres psql -U epms -d epms -c "
select count(*) open_lines, count(planned_arrival_date) with_line_eta
from po_line_items l join purchase_orders po on po.id=l.po_id
where po.type=1 and po.status in ('issued','partially_received')
  and l.material_id is not null and l.qty > l.received_qty;"
```

Expected **before** the sync: 87 open lines, 0 with a date. **After**: 87 and 87. A count that stays at 0 means the writer's update path was missed (Task 2 Step 7).

- [ ] **Step 4: Cross-check in-transit against SQL**

The `/inventory/materials` totals must equal:

```sql
select count(*) lines, count(distinct material_id) mats,
       sum(qty - received_qty)::numeric(18,0) in_transit
from po_line_items l join purchase_orders po on po.id = l.po_id
where po.type = 1 and po.status in ('issued','partially_received')
  and l.material_id is not null and l.qty > l.received_qty;
```

Expected on the dev snapshot: **87 / 71 / 902,779**. A different number means the endpoint and the definition have diverged.

- [ ] **Step 5: Cross-check aging against SQL**

Raw-material buckets must match: **90 expired, 11 under 30, 11 at 30–60, 97 at 60–180, 347 over 180**. Watch the boundary lots specifically — an off-by-one moves a lot between adjacent buckets and the totals still add up.

- [ ] **Step 6: Full mrp-api suite against the Task 0 baseline**

Compare the **set** of failing tests, not just the count: three tests in this suite are known flaky, so an equal count can still hide a new failure.

- [ ] **Step 7: Commit the verification record**

```bash
git commit --allow-empty -m "test(mrp): inventory verified end to end against the dev snapshot"
```
