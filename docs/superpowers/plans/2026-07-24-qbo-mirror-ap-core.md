# QBO Mirror — AP Core Pipeline Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Mirror QuickBooks Online's AP core (accounts, vendors, bills, bill
payments, vendor credits) into typed `qbo_*` tables in finance-api, with a
full + manual-incremental import engine, so the AP data is loaded, queryable, and
reconcilable inside UniOps.

**Architecture:** A self-contained subsystem in finance-api. Real payloads are
extracted to JSON, then loaded via a declarative entity registry into typed
header tables + line child tables (each with a `raw` JSONB fallback). Read-only
mirror — no association, no posting. This plan is phase 1 of the spec
(`docs/superpowers/specs/2026-07-24-qbo-mirror-design.md`); AR, the long tail,
attachments, the API, and the UI are separate follow-up plans that reuse this
engine.

**Tech Stack:** Python 3.12, async SQLAlchemy 2.0, Postgres (asyncpg), Alembic,
httpx, pytest / pytest-asyncio.

---

## Scope

**In this plan:** `qbo_sync_runs`; entity registry + shared mappers; models +
load for `Account`, `Vendor`, `Bill` (+lines), `BillPayment` (+lines),
`VendorCredit` (+lines); `extract.py` (paged full + incremental); `run_import.py`
CLI with full/incremental orchestration and soft-delete diff; unit tests with
synthetic fixtures.

**Not in this plan (later phases):** AR (Invoice/Payment/CreditMemo), long-tail
`qbo_raw` entities, `Purchase`/`Deposit`/`Transfer`/`JournalEntry`, attachments,
finance-api API endpoints, Finance UI.

## Conventions (read once)

- Models base classes come from `app.db.base`: `Base`, `UUIDPrimaryKey`,
  `TimestampMixin`. Async engine already configured there.
- New models live in `finance-api/app/models/qbo.py`.
- Import engine lives in `finance-api/scripts/qbo_import/` alongside the existing
  `client.py` / `authorize.py` / `sample_extract.py`.
- Latest alembic head is `0026_jv_lines_nc_cc_code`; the new migration chains off
  it. (Verify with `grep -h "^revision" alembic/versions/0026_jv_lines_nc_cc_code.py`.)
- Tests build the schema by running real alembic migrations against `finance_test`
  (see `tests/conftest.py`). Run tests inside the environment that reaches the
  local `uniops_postgres` container; env overrides `TEST_PG_*` if needed.
- **Fixtures use SYNTHETIC payloads** (real structure, fake values) — real sample
  data in `data/qbo_samples/` is gitignored because it holds real vendor/amount
  data and must never be committed.
- Amounts are `Numeric`. Amounts from QBO arrive as JSON numbers.
- `qbo_id` is the upsert key everywhere (`DocNumber` is nullable and non-unique).
- Every model carries a `raw` JSONB column; unmapped fields (Canadian tax flags,
  `TxnTaxDetail`) live there and are promoted to columns only when needed.

---

### Task 1: `qbo_sync_runs` model + migration

**Files:**
- Create: `finance-api/app/models/qbo.py`
- Create: `finance-api/alembic/versions/0027_qbo_mirror_ap_core.py`
- Test: `finance-api/tests/test_qbo_sync_runs.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_qbo_sync_runs.py
import pytest
from datetime import datetime, timezone
from sqlalchemy import select
from app.models.qbo import QboSyncRun


@pytest.mark.asyncio
async def test_sync_run_roundtrips(db_session):
    run = QboSyncRun(
        mode="full",
        status="running",
        started_at=datetime(2026, 7, 24, tzinfo=timezone.utc),
        counters={"Vendor": 0},
        watermarks={},
    )
    db_session.add(run)
    await db_session.commit()

    got = (await db_session.execute(select(QboSyncRun))).scalar_one()
    assert got.mode == "full"
    assert got.status == "running"
    assert got.counters == {"Vendor": 0}
    assert got.watermarks == {}
    assert got.error is None
```

Note: `db_session` is the existing async-session fixture from `tests/conftest.py`.
Confirm its name (`grep "def db_session\|async def db_session" tests/conftest.py`);
use the actual fixture name if it differs.

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_qbo_sync_runs.py -v`
Expected: FAIL with `ImportError: cannot import name 'QboSyncRun'`

- [ ] **Step 3: Write the model**

```python
# app/models/qbo.py
"""QuickBooks Online mirror tables (finance owns the schema; migration 0027).

Read-only mirror of the QBO company — no association, no posting. Columns were
defined from real production payloads; anything unmapped stays in `raw`.
"""
import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy import DateTime, ForeignKey, Integer, Numeric, String, Text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin, UUIDPrimaryKey

RUNNING = "running"
SUCCESS = "success"
FAILED = "failed"


class QboSyncRun(UUIDPrimaryKey, TimestampMixin, Base):
    """One row per UI/CLI-triggered import; doubles as the live progress feed."""
    __tablename__ = "qbo_sync_runs"

    mode: Mapped[str] = mapped_column(String(15), nullable=False)          # full | incremental
    status: Mapped[str] = mapped_column(String(10), nullable=False, default=RUNNING, index=True)
    started_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # {entity: {inserted, updated, deleted}} accumulated as the run progresses.
    counters: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict, server_default="{}")
    # {entity: max LastUpdatedTime seen} — the incremental watermark for next run.
    watermarks: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict, server_default="{}")
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
```

- [ ] **Step 4: Write the migration**

```python
# alembic/versions/0027_qbo_mirror_ap_core.py
"""qbo mirror AP core tables

Revision ID: 0027_qbo_mirror_ap_core
Revises: 0026_jv_lines_nc_cc_code
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision = "0027_qbo_mirror_ap_core"
down_revision = "0026_jv_lines_nc_cc_code"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "qbo_sync_runs",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("mode", sa.String(15), nullable=False),
        sa.Column("status", sa.String(10), nullable=False, server_default="running"),
        sa.Column("started_by", UUID(as_uuid=True), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("counters", JSONB, nullable=False, server_default="{}"),
        sa.Column("watermarks", JSONB, nullable=False, server_default="{}"),
        sa.Column("error", sa.Text, nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_qbo_sync_runs_status", "qbo_sync_runs", ["status"])
    # Entity tables are added in later steps of this same migration (Tasks 3-7).


def downgrade() -> None:
    op.drop_index("ix_qbo_sync_runs_status", table_name="qbo_sync_runs")
    op.drop_table("qbo_sync_runs")
```

- [ ] **Step 5: Run test to verify it passes**

Run: `pytest tests/test_qbo_sync_runs.py -v`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add app/models/qbo.py alembic/versions/0027_qbo_mirror_ap_core.py tests/test_qbo_sync_runs.py
git commit -m "feat(qbo): qbo_sync_runs model + migration 0027"
```

---

### Task 2: Entity registry + shared header/line mappers

The registry is the DRY core: each entity declares its model, optional line model,
and pure mapper functions from a raw QBO dict to column dicts. `load.py` and
`extract.py` both read it, so no per-entity load logic is duplicated.

**Files:**
- Create: `finance-api/scripts/qbo_import/mappers.py`
- Create: `finance-api/scripts/qbo_import/registry.py`
- Test: `finance-api/tests/test_qbo_mappers.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_qbo_mappers.py
from scripts.qbo_import.mappers import ref_id, ref_name, txn_header, txn_line, to_dt

# Synthetic payload — real STRUCTURE, fake values (never commit real QBO data).
SAMPLE_BILL = {
    "Id": "1645",
    "SyncToken": "3",
    "DocNumber": "INV-001",
    "TxnDate": "2019-05-01",
    "DueDate": "2019-06-01",
    "CurrencyRef": {"value": "CAD", "name": "Canadian Dollar"},
    "ExchangeRate": 1,
    "TotalAmt": 38040.0,
    "Balance": 0,
    "HomeBalance": 0,
    "GlobalTaxCalculation": "TaxExcluded",
    "PrivateNote": "note",
    "VendorRef": {"value": "64", "name": "Acme Inc"},
    "MetaData": {"LastUpdatedTime": "2019-05-02T10:00:00-07:00"},
    "Line": [
        {
            "Id": "1", "LineNum": 1, "Amount": 100.0,
            "Description": "widgets",
            "DetailType": "AccountBasedExpenseLineDetail",
            "AccountBasedExpenseLineDetail": {
                "AccountRef": {"value": "141", "name": "Construction"},
                "TaxCodeRef": {"value": "2"},
            },
        }
    ],
}


def test_ref_helpers():
    assert ref_id(SAMPLE_BILL, "VendorRef") == "64"
    assert ref_name(SAMPLE_BILL, "VendorRef") == "Acme Inc"
    assert ref_id(SAMPLE_BILL, "MissingRef") is None


def test_to_dt_is_tz_aware():
    dt = to_dt("2019-05-02T10:00:00-07:00")
    assert dt.utcoffset() is not None  # tz preserved, not naive


def test_txn_header_maps_amounts_and_currency():
    h = txn_header(SAMPLE_BILL, counterparty="VendorRef")
    assert h["qbo_id"] == "1645"
    assert h["sync_token"] == "3"
    assert h["doc_number"] == "INV-001"
    assert h["currency"] == "CAD"
    assert h["exchange_rate"] == 1
    assert h["total_amt"] == 38040.0
    assert h["counterparty_id"] == "64"
    assert h["counterparty_name"] == "Acme Inc"
    assert h["global_tax_calc"] == "TaxExcluded"
    assert h["last_updated_time"].utcoffset() is not None
    assert h["raw"] == SAMPLE_BILL


def test_txn_line_maps_account_and_tax():
    line = txn_line(SAMPLE_BILL["Line"][0], parent_qbo_id="1645")
    assert line["parent_qbo_id"] == "1645"
    assert line["line_num"] == 1
    assert line["amount"] == 100.0
    assert line["detail_type"] == "AccountBasedExpenseLineDetail"
    assert line["account_id"] == "141"
    assert line["account_name"] == "Construction"
    assert line["tax_code_ref"] == "2"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_qbo_mappers.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'scripts.qbo_import.mappers'`

- [ ] **Step 3: Write the mappers**

```python
# scripts/qbo_import/mappers.py
"""Pure functions from a raw QBO entity dict to qbo_* column dicts.

No DB or network here — trivially unit-testable against synthetic payloads.
The `*_DETAIL` key that holds a line's account/tax varies by DetailType; we scan
the known detail keys rather than hardcode one.
"""
from datetime import datetime

# DetailType -> the key holding its AccountRef/TaxCodeRef.
_LINE_DETAIL_KEYS = (
    "AccountBasedExpenseLineDetail",
    "ItemBasedExpenseLineDetail",
    "JournalEntryLineDetail",
    "DepositLineDetail",
    "SalesItemLineDetail",
)


def ref_id(obj: dict, key: str) -> str | None:
    ref = obj.get(key)
    return ref.get("value") if isinstance(ref, dict) else None


def ref_name(obj: dict, key: str) -> str | None:
    ref = obj.get(key)
    return ref.get("name") if isinstance(ref, dict) else None


def to_dt(s: str | None) -> datetime | None:
    """Parse a QBO timestamp preserving its timezone offset (never naive)."""
    if not s:
        return None
    # QBO uses ISO-8601 with an offset, e.g. 2019-05-02T10:00:00-07:00.
    return datetime.fromisoformat(s)


def last_updated(obj: dict) -> datetime | None:
    return to_dt((obj.get("MetaData") or {}).get("LastUpdatedTime"))


def txn_header(obj: dict, counterparty: str) -> dict:
    """Common transaction-header columns. `counterparty` is VendorRef/CustomerRef."""
    return {
        "qbo_id": obj["Id"],
        "sync_token": obj.get("SyncToken"),
        "doc_number": obj.get("DocNumber"),
        "txn_date": obj.get("TxnDate"),
        "due_date": obj.get("DueDate"),
        "currency": ref_id(obj, "CurrencyRef"),
        "exchange_rate": obj.get("ExchangeRate"),
        "total_amt": obj.get("TotalAmt"),
        "home_total_amt": obj.get("HomeTotalAmt"),
        "balance": obj.get("Balance"),
        "home_balance": obj.get("HomeBalance"),
        "global_tax_calc": obj.get("GlobalTaxCalculation"),
        "private_note": obj.get("PrivateNote"),
        "counterparty_id": ref_id(obj, counterparty),
        "counterparty_name": ref_name(obj, counterparty),
        "last_updated_time": last_updated(obj),
        "raw": obj,
    }


def _line_detail(line: dict) -> dict:
    for k in _LINE_DETAIL_KEYS:
        if isinstance(line.get(k), dict):
            return line[k]
    return {}


def txn_line(line: dict, parent_qbo_id: str) -> dict:
    """Common line columns. `linked_txn_*` are filled for payment-type lines."""
    detail = _line_detail(line)
    linked = (line.get("LinkedTxn") or [{}])[0] if line.get("LinkedTxn") else {}
    return {
        "parent_qbo_id": parent_qbo_id,
        "line_num": line.get("LineNum"),
        "amount": line.get("Amount"),
        "detail_type": line.get("DetailType"),
        "account_id": ref_id(detail, "AccountRef"),
        "account_name": ref_name(detail, "AccountRef"),
        "tax_code_ref": ref_id(detail, "TaxCodeRef"),
        "description": line.get("Description"),
        "linked_txn_id": linked.get("TxnId"),
        "linked_txn_type": linked.get("TxnType"),
        "raw": line,
    }
```

- [ ] **Step 4: Write the registry (models filled in as Tasks 3-7 add them)**

```python
# scripts/qbo_import/registry.py
"""Declarative entity registry — the single source both extract and load read.

Each Entity says: the QBO entity name, its header model, optional line model, and
how to build header/line column dicts. Adding an entity is one registry line plus
its model — no new load code.
"""
from dataclasses import dataclass
from typing import Callable

from app.models import qbo as m
from scripts.qbo_import import mappers


@dataclass(frozen=True)
class Entity:
    name: str                       # QBO entity name, e.g. "Bill"
    model: type                     # header SQLAlchemy model
    header: Callable[[dict], dict]  # raw -> header column dict
    line_model: type | None = None  # line SQLAlchemy model, if any
    line: Callable[[dict, str], dict] | None = None  # (raw_line, parent_qbo_id) -> line dict


# Populated incrementally by Tasks 3-7.
REGISTRY: list[Entity] = []


def by_name(name: str) -> Entity:
    for e in REGISTRY:
        if e.name == name:
            return e
    raise KeyError(f"unknown QBO entity: {name}")
```

- [ ] **Step 5: Run test to verify it passes**

Run: `pytest tests/test_qbo_mappers.py -v`
Expected: PASS (registry import is exercised in later tasks)

- [ ] **Step 6: Commit**

```bash
git add scripts/qbo_import/mappers.py scripts/qbo_import/registry.py tests/test_qbo_mappers.py
git commit -m "feat(qbo): entity registry + pure header/line mappers"
```

---

### Task 3: `Account` mirror (simplest master — no lines, no currency mapping)

**Files:**
- Modify: `finance-api/app/models/qbo.py` (add `QboAccount`)
- Modify: `finance-api/alembic/versions/0027_qbo_mirror_ap_core.py` (add table)
- Modify: `finance-api/scripts/qbo_import/registry.py` (register)
- Create: `finance-api/scripts/qbo_import/load.py`
- Test: `finance-api/tests/test_qbo_load.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_qbo_load.py
import pytest
from sqlalchemy import select
from app.models.qbo import QboAccount
from scripts.qbo_import.load import load_entity

ACCOUNTS = [
    {"Id": "58", "SyncToken": "0", "Name": "Accounts Payable",
     "AcctNum": "2000", "AccountType": "Accounts Payable",
     "AccountSubType": "AccountsPayable", "Active": True,
     "CurrentBalance": 1234.5, "CurrencyRef": {"value": "CAD"},
     "MetaData": {"LastUpdatedTime": "2019-05-02T10:00:00-07:00"}},
]


@pytest.mark.asyncio
async def test_load_accounts_inserts_then_updates(db_session):
    n = await load_entity(db_session, "Account", ACCOUNTS)
    assert n["inserted"] == 1
    row = (await db_session.execute(select(QboAccount))).scalar_one()
    assert row.qbo_id == "58"
    assert row.name == "Accounts Payable"
    assert row.account_type == "Accounts Payable"

    # Re-load same id with a changed name → update, not duplicate.
    ACCOUNTS[0]["Name"] = "A/P"
    n2 = await load_entity(db_session, "Account", ACCOUNTS)
    assert n2["updated"] == 1
    rows = (await db_session.execute(select(QboAccount))).scalars().all()
    assert len(rows) == 1
    assert rows[0].name == "A/P"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_qbo_load.py -v`
Expected: FAIL — `cannot import name 'QboAccount'` / `load_entity`

- [ ] **Step 3: Add the `QboAccount` model**

```python
# append to app/models/qbo.py
class QboAccount(TimestampMixin, Base):
    __tablename__ = "qbo_accounts"

    qbo_id: Mapped[str] = mapped_column(String(20), primary_key=True)
    sync_token: Mapped[str | None] = mapped_column(String(10))
    name: Mapped[str | None] = mapped_column(String(255))
    acct_num: Mapped[str | None] = mapped_column(String(50))
    account_type: Mapped[str | None] = mapped_column(String(64))
    account_sub_type: Mapped[str | None] = mapped_column(String(64))
    currency: Mapped[str | None] = mapped_column(String(10))
    current_balance: Mapped[Decimal | None] = mapped_column(Numeric(20, 2))
    active: Mapped[bool | None] = mapped_column()
    last_updated_time: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    raw: Mapped[dict] = mapped_column(JSONB, nullable=False)
```

Note: `qbo_id` is the natural primary key (no separate UUID) for mirror rows.

- [ ] **Step 4: Add the account mapper + register it**

```python
# append to scripts/qbo_import/mappers.py
def account_header(obj: dict) -> dict:
    return {
        "qbo_id": obj["Id"],
        "sync_token": obj.get("SyncToken"),
        "name": obj.get("Name"),
        "acct_num": obj.get("AcctNum"),
        "account_type": obj.get("AccountType"),
        "account_sub_type": obj.get("AccountSubType"),
        "currency": ref_id(obj, "CurrencyRef"),
        "current_balance": obj.get("CurrentBalance"),
        "active": obj.get("Active"),
        "last_updated_time": last_updated(obj),
        "raw": obj,
    }
```

```python
# append to REGISTRY in scripts/qbo_import/registry.py
REGISTRY.append(Entity(name="Account", model=m.QboAccount, header=mappers.account_header))
```

- [ ] **Step 5: Write `load.py` with the generic upsert**

```python
# scripts/qbo_import/load.py
"""Generic, idempotent loader driven by the registry.

Header rows upsert by qbo_id (on_conflict_do_update). Line rows are
delete-then-insert per parent to avoid line drift across reloads.
"""
from sqlalchemy import delete, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from scripts.qbo_import.registry import by_name


async def _upsert(db: AsyncSession, model, rows: list[dict]) -> dict:
    if not rows:
        return {"inserted": 0, "updated": 0}
    pk = "qbo_id"
    existing = set(
        (await db.execute(select(getattr(model, pk)))).scalars().all()
    )
    ins = updated = 0
    for r in rows:
        stmt = insert(model).values(**r)
        update_cols = {k: stmt.excluded[k] for k in r if k != pk}
        stmt = stmt.on_conflict_do_update(index_elements=[pk], set_=update_cols)
        await db.execute(stmt)
        if r[pk] in existing:
            updated += 1
        else:
            ins += 1
    return {"inserted": ins, "updated": updated}


async def load_entity(db: AsyncSession, name: str, objects: list[dict]) -> dict:
    """Load one entity's raw objects. Returns {inserted, updated}."""
    ent = by_name(name)
    headers = [ent.header(o) for o in objects]
    counts = await _upsert(db, ent.model, headers)

    if ent.line_model and ent.line:
        parent_ids = [h["qbo_id"] for h in headers]
        # Delete-then-insert lines for the parents in this batch.
        await db.execute(
            delete(ent.line_model).where(ent.line_model.parent_qbo_id.in_(parent_ids))
        )
        line_rows = [
            ent.line(ln, obj["Id"])
            for obj in objects
            for ln in (obj.get("Line") or [])
        ]
        for lr in line_rows:
            await db.execute(insert(ent.line_model).values(**lr))
    await db.commit()
    return counts
```

- [ ] **Step 6: Add `qbo_accounts` to the migration**

```python
# inside upgrade(), after qbo_sync_runs, in 0027_qbo_mirror_ap_core.py
op.create_table(
    "qbo_accounts",
    sa.Column("qbo_id", sa.String(20), primary_key=True),
    sa.Column("sync_token", sa.String(10)),
    sa.Column("name", sa.String(255)),
    sa.Column("acct_num", sa.String(50)),
    sa.Column("account_type", sa.String(64)),
    sa.Column("account_sub_type", sa.String(64)),
    sa.Column("currency", sa.String(10)),
    sa.Column("current_balance", sa.Numeric(20, 2)),
    sa.Column("active", sa.Boolean),
    sa.Column("last_updated_time", sa.DateTime(timezone=True)),
    sa.Column("deleted_at", sa.DateTime(timezone=True)),
    sa.Column("raw", JSONB, nullable=False),
    sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
)
```

Add the matching `op.drop_table("qbo_accounts")` to `downgrade()` (before dropping
`qbo_sync_runs`).

- [ ] **Step 7: Run test to verify it passes**

Run: `pytest tests/test_qbo_load.py -v`
Expected: PASS (both inserted and updated assertions)

- [ ] **Step 8: Commit**

```bash
git add app/models/qbo.py alembic/versions/0027_qbo_mirror_ap_core.py \
        scripts/qbo_import/mappers.py scripts/qbo_import/registry.py \
        scripts/qbo_import/load.py tests/test_qbo_load.py
git commit -m "feat(qbo): Account mirror + generic idempotent loader"
```

---

### Task 4: `Vendor` mirror (currency + Canadian flags in raw)

**Files:**
- Modify: `app/models/qbo.py` (add `QboVendor`)
- Modify: `alembic/versions/0027_qbo_mirror_ap_core.py`
- Modify: `scripts/qbo_import/mappers.py`, `registry.py`
- Test: `tests/test_qbo_load.py` (add case)

- [ ] **Step 1: Write the failing test**

```python
# add to tests/test_qbo_load.py
from app.models.qbo import QboVendor

VENDORS = [
    {"Id": "64", "SyncToken": "1", "DisplayName": "Acme Inc",
     "PrintOnCheckName": "Acme Inc", "Active": True,
     "Balance": 500.0, "CurrencyRef": {"value": "CAD"},
     "T4AEligible": False, "T5018Eligible": True,
     "PrimaryEmailAddr": {"Address": "ap@acme.test"},
     "MetaData": {"LastUpdatedTime": "2019-05-02T10:00:00-07:00"}},
]


@pytest.mark.asyncio
async def test_load_vendor_keeps_canadian_flags_in_raw(db_session):
    await load_entity(db_session, "Vendor", VENDORS)
    v = (await db_session.execute(select(QboVendor))).scalar_one()
    assert v.qbo_id == "64"
    assert v.display_name == "Acme Inc"
    assert v.currency == "CAD"
    assert v.email == "ap@acme.test"
    # Canadian slip flags are not columns — they live in raw.
    assert v.raw["T5018Eligible"] is True
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_qbo_load.py::test_load_vendor_keeps_canadian_flags_in_raw -v`
Expected: FAIL — `cannot import name 'QboVendor'`

- [ ] **Step 3: Model + mapper + registry + migration**

```python
# append to app/models/qbo.py
class QboVendor(TimestampMixin, Base):
    __tablename__ = "qbo_vendors"

    qbo_id: Mapped[str] = mapped_column(String(20), primary_key=True)
    sync_token: Mapped[str | None] = mapped_column(String(10))
    display_name: Mapped[str | None] = mapped_column(String(255))
    print_on_check_name: Mapped[str | None] = mapped_column(String(255))
    currency: Mapped[str | None] = mapped_column(String(10))
    balance: Mapped[Decimal | None] = mapped_column(Numeric(20, 2))
    email: Mapped[str | None] = mapped_column(String(255))
    active: Mapped[bool | None] = mapped_column()
    last_updated_time: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    raw: Mapped[dict] = mapped_column(JSONB, nullable=False)
```

```python
# append to scripts/qbo_import/mappers.py
def vendor_header(obj: dict) -> dict:
    email = (obj.get("PrimaryEmailAddr") or {}).get("Address")
    return {
        "qbo_id": obj["Id"],
        "sync_token": obj.get("SyncToken"),
        "display_name": obj.get("DisplayName"),
        "print_on_check_name": obj.get("PrintOnCheckName"),
        "currency": ref_id(obj, "CurrencyRef"),
        "balance": obj.get("Balance"),
        "email": email,
        "active": obj.get("Active"),
        "last_updated_time": last_updated(obj),
        "raw": obj,
    }
```

```python
# append to REGISTRY
REGISTRY.append(Entity(name="Vendor", model=m.QboVendor, header=mappers.vendor_header))
```

```python
# add table to migration upgrade() (drop in downgrade())
op.create_table(
    "qbo_vendors",
    sa.Column("qbo_id", sa.String(20), primary_key=True),
    sa.Column("sync_token", sa.String(10)),
    sa.Column("display_name", sa.String(255)),
    sa.Column("print_on_check_name", sa.String(255)),
    sa.Column("currency", sa.String(10)),
    sa.Column("balance", sa.Numeric(20, 2)),
    sa.Column("email", sa.String(255)),
    sa.Column("active", sa.Boolean),
    sa.Column("last_updated_time", sa.DateTime(timezone=True)),
    sa.Column("deleted_at", sa.DateTime(timezone=True)),
    sa.Column("raw", JSONB, nullable=False),
    sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_qbo_load.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add app/models/qbo.py alembic/versions/0027_qbo_mirror_ap_core.py \
        scripts/qbo_import/mappers.py scripts/qbo_import/registry.py tests/test_qbo_load.py
git commit -m "feat(qbo): Vendor mirror"
```

---

### Task 5: `Bill` mirror header + `qbo_bill_lines` (header+line pattern, multicurrency)

**Files:**
- Modify: `app/models/qbo.py` (add `QboBill`, `QboBillLine`)
- Modify: `alembic/versions/0027_qbo_mirror_ap_core.py`
- Modify: `scripts/qbo_import/registry.py`
- Test: `tests/test_qbo_load.py`

- [ ] **Step 1: Write the failing test**

```python
# add to tests/test_qbo_load.py
from app.models.qbo import QboBill, QboBillLine

BILLS = [
    {"Id": "1645", "SyncToken": "0", "DocNumber": "INV-001",
     "TxnDate": "2019-05-01", "DueDate": "2019-06-01",
     "CurrencyRef": {"value": "USD"}, "ExchangeRate": 1.35,
     "TotalAmt": 100.0, "HomeTotalAmt": 135.0, "Balance": 100.0, "HomeBalance": 135.0,
     "GlobalTaxCalculation": "TaxExcluded",
     "VendorRef": {"value": "64", "name": "Acme Inc"},
     "MetaData": {"LastUpdatedTime": "2019-05-02T10:00:00-07:00"},
     "Line": [
        {"Id": "1", "LineNum": 1, "Amount": 100.0, "Description": "widgets",
         "DetailType": "AccountBasedExpenseLineDetail",
         "AccountBasedExpenseLineDetail": {
            "AccountRef": {"value": "141", "name": "COGS"}, "TaxCodeRef": {"value": "2"}}},
     ]},
]


@pytest.mark.asyncio
async def test_load_bill_header_and_lines_multicurrency(db_session):
    await load_entity(db_session, "Bill", BILLS)
    b = (await db_session.execute(select(QboBill))).scalar_one()
    assert b.qbo_id == "1645"
    assert b.currency == "USD"
    assert float(b.exchange_rate) == 1.35
    assert float(b.total_amt) == 100.0
    assert float(b.home_total_amt) == 135.0
    assert b.counterparty_id == "64"

    lines = (await db_session.execute(select(QboBillLine))).scalars().all()
    assert len(lines) == 1
    assert lines[0].account_id == "141"
    assert lines[0].tax_code_ref == "2"


@pytest.mark.asyncio
async def test_reload_bill_replaces_lines_not_duplicates(db_session):
    await load_entity(db_session, "Bill", BILLS)
    await load_entity(db_session, "Bill", BILLS)  # second load
    lines = (await db_session.execute(select(QboBillLine))).scalars().all()
    assert len(lines) == 1  # delete-then-insert, no dup
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_qbo_load.py -k bill -v`
Expected: FAIL — `cannot import name 'QboBill'`

- [ ] **Step 3: Add models**

```python
# append to app/models/qbo.py

class _TxnHeaderMixin:
    """Shared transaction-header columns (Bill/BillPayment/VendorCredit/...)."""
    qbo_id: Mapped[str] = mapped_column(String(20), primary_key=True)
    sync_token: Mapped[str | None] = mapped_column(String(10))
    doc_number: Mapped[str | None] = mapped_column(String(64))
    txn_date: Mapped[str | None] = mapped_column(String(10))
    due_date: Mapped[str | None] = mapped_column(String(10))
    currency: Mapped[str | None] = mapped_column(String(10))
    exchange_rate: Mapped[Decimal | None] = mapped_column(Numeric(20, 8))
    total_amt: Mapped[Decimal | None] = mapped_column(Numeric(20, 2))
    home_total_amt: Mapped[Decimal | None] = mapped_column(Numeric(20, 2))
    balance: Mapped[Decimal | None] = mapped_column(Numeric(20, 2))
    home_balance: Mapped[Decimal | None] = mapped_column(Numeric(20, 2))
    global_tax_calc: Mapped[str | None] = mapped_column(String(20))
    private_note: Mapped[str | None] = mapped_column(Text)
    counterparty_id: Mapped[str | None] = mapped_column(String(20))
    counterparty_name: Mapped[str | None] = mapped_column(String(255))
    last_updated_time: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    raw: Mapped[dict] = mapped_column(JSONB, nullable=False)


class _TxnLineMixin:
    """Shared line columns."""
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    parent_qbo_id: Mapped[str] = mapped_column(String(20), index=True, nullable=False)
    line_num: Mapped[int | None] = mapped_column(Integer)
    amount: Mapped[Decimal | None] = mapped_column(Numeric(20, 2))
    detail_type: Mapped[str | None] = mapped_column(String(48))
    account_id: Mapped[str | None] = mapped_column(String(20))
    account_name: Mapped[str | None] = mapped_column(String(255))
    tax_code_ref: Mapped[str | None] = mapped_column(String(20))
    description: Mapped[str | None] = mapped_column(Text)
    linked_txn_id: Mapped[str | None] = mapped_column(String(20))
    linked_txn_type: Mapped[str | None] = mapped_column(String(32))
    raw: Mapped[dict] = mapped_column(JSONB, nullable=False)


class QboBill(_TxnHeaderMixin, TimestampMixin, Base):
    __tablename__ = "qbo_bills"


class QboBillLine(_TxnLineMixin, Base):
    __tablename__ = "qbo_bill_lines"
```

- [ ] **Step 4: Register Bill with its line model**

```python
# append to REGISTRY
REGISTRY.append(Entity(
    name="Bill", model=m.QboBill,
    header=lambda o: mappers.txn_header(o, counterparty="VendorRef"),
    line_model=m.QboBillLine, line=mappers.txn_line,
))
```

- [ ] **Step 5: Add tables to migration**

```python
# upgrade() — qbo_bills then qbo_bill_lines (drop in reverse in downgrade())
op.create_table(
    "qbo_bills",
    sa.Column("qbo_id", sa.String(20), primary_key=True),
    sa.Column("sync_token", sa.String(10)),
    sa.Column("doc_number", sa.String(64)),
    sa.Column("txn_date", sa.String(10)),
    sa.Column("due_date", sa.String(10)),
    sa.Column("currency", sa.String(10)),
    sa.Column("exchange_rate", sa.Numeric(20, 8)),
    sa.Column("total_amt", sa.Numeric(20, 2)),
    sa.Column("home_total_amt", sa.Numeric(20, 2)),
    sa.Column("balance", sa.Numeric(20, 2)),
    sa.Column("home_balance", sa.Numeric(20, 2)),
    sa.Column("global_tax_calc", sa.String(20)),
    sa.Column("private_note", sa.Text),
    sa.Column("counterparty_id", sa.String(20)),
    sa.Column("counterparty_name", sa.String(255)),
    sa.Column("last_updated_time", sa.DateTime(timezone=True)),
    sa.Column("deleted_at", sa.DateTime(timezone=True)),
    sa.Column("raw", JSONB, nullable=False),
    sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
)
op.create_index("ix_qbo_bills_txn_date", "qbo_bills", ["txn_date"])
op.create_index("ix_qbo_bills_counterparty_id", "qbo_bills", ["counterparty_id"])
op.create_index("ix_qbo_bills_doc_number", "qbo_bills", ["doc_number"])
op.create_table(
    "qbo_bill_lines",
    sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
    sa.Column("parent_qbo_id", sa.String(20), nullable=False),
    sa.Column("line_num", sa.Integer),
    sa.Column("amount", sa.Numeric(20, 2)),
    sa.Column("detail_type", sa.String(48)),
    sa.Column("account_id", sa.String(20)),
    sa.Column("account_name", sa.String(255)),
    sa.Column("tax_code_ref", sa.String(20)),
    sa.Column("description", sa.Text),
    sa.Column("linked_txn_id", sa.String(20)),
    sa.Column("linked_txn_type", sa.String(32)),
    sa.Column("raw", JSONB, nullable=False),
)
op.create_index("ix_qbo_bill_lines_parent", "qbo_bill_lines", ["parent_qbo_id"])
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `pytest tests/test_qbo_load.py -k bill -v`
Expected: PASS (header, lines, and reload-replaces-lines)

- [ ] **Step 7: Commit**

```bash
git add app/models/qbo.py alembic/versions/0027_qbo_mirror_ap_core.py \
        scripts/qbo_import/registry.py tests/test_qbo_load.py
git commit -m "feat(qbo): Bill mirror header + lines (multicurrency, delete-then-insert)"
```

---

### Task 6: `BillPayment` mirror + lines (reconciliation via LinkedTxn)

**Files:**
- Modify: `app/models/qbo.py` (add `QboBillPayment`, `QboBillPaymentLine`)
- Modify: `alembic/versions/0027_qbo_mirror_ap_core.py`
- Modify: `scripts/qbo_import/registry.py`
- Test: `tests/test_qbo_load.py`

- [ ] **Step 1: Write the failing test**

```python
# add to tests/test_qbo_load.py
from app.models.qbo import QboBillPayment, QboBillPaymentLine

BILLPAYMENTS = [
    {"Id": "22", "SyncToken": "0", "DocNumber": "CHK-1",
     "TxnDate": "2019-05-26", "PayType": "Check",
     "CurrencyRef": {"value": "CAD"}, "ExchangeRate": 1,
     "TotalAmt": 100.0, "VendorRef": {"value": "64", "name": "Acme Inc"},
     "MetaData": {"LastUpdatedTime": "2019-05-27T10:00:00-07:00"},
     "Line": [
        {"Amount": 100.0, "LinkedTxn": [{"TxnId": "1645", "TxnType": "Bill"}]},
     ]},
]


@pytest.mark.asyncio
async def test_billpayment_line_captures_reconciliation(db_session):
    await load_entity(db_session, "BillPayment", BILLPAYMENTS)
    p = (await db_session.execute(select(QboBillPayment))).scalar_one()
    assert p.counterparty_id == "64"
    assert p.pay_type == "Check"

    line = (await db_session.execute(select(QboBillPaymentLine))).scalar_one()
    # The payment→bill reconciliation link:
    assert line.linked_txn_id == "1645"
    assert line.linked_txn_type == "Bill"
    assert float(line.amount) == 100.0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_qbo_load.py -k billpayment -v`
Expected: FAIL — `cannot import name 'QboBillPayment'`

- [ ] **Step 3: Add models (header adds `pay_type`)**

```python
# append to app/models/qbo.py
class QboBillPayment(_TxnHeaderMixin, TimestampMixin, Base):
    __tablename__ = "qbo_bill_payments"
    pay_type: Mapped[str | None] = mapped_column(String(20))


class QboBillPaymentLine(_TxnLineMixin, Base):
    __tablename__ = "qbo_bill_payment_lines"
```

- [ ] **Step 4: Add a pay-type-aware header mapper + register**

```python
# append to scripts/qbo_import/mappers.py
def billpayment_header(obj: dict) -> dict:
    h = txn_header(obj, counterparty="VendorRef")
    h["pay_type"] = obj.get("PayType")
    return h
```

```python
# append to REGISTRY
REGISTRY.append(Entity(
    name="BillPayment", model=m.QboBillPayment,
    header=mappers.billpayment_header,
    line_model=m.QboBillPaymentLine, line=mappers.txn_line,
))
```

- [ ] **Step 5: Add tables to migration**

Mirror the Task 5 table pattern for `qbo_bill_payments` (all `_TxnHeaderMixin`
columns **plus** `sa.Column("pay_type", sa.String(20))`) and
`qbo_bill_payment_lines` (identical to `qbo_bill_lines`). Add the
`ix_qbo_bill_payment_lines_parent` index and drops in `downgrade()`.

- [ ] **Step 6: Run test to verify it passes**

Run: `pytest tests/test_qbo_load.py -k billpayment -v`
Expected: PASS

- [ ] **Step 7: Commit**

```bash
git add app/models/qbo.py alembic/versions/0027_qbo_mirror_ap_core.py \
        scripts/qbo_import/mappers.py scripts/qbo_import/registry.py tests/test_qbo_load.py
git commit -m "feat(qbo): BillPayment mirror + reconciliation lines"
```

---

### Task 7: `VendorCredit` mirror + lines

**Files:** same set as Task 5.

- [ ] **Step 1: Write the failing test**

```python
# add to tests/test_qbo_load.py
from app.models.qbo import QboVendorCredit, QboVendorCreditLine

VENDORCREDITS = [
    {"Id": "900", "SyncToken": "0", "DocNumber": "VC-1", "TxnDate": "2019-07-01",
     "CurrencyRef": {"value": "CAD"}, "ExchangeRate": 1, "TotalAmt": 50.0,
     "VendorRef": {"value": "64", "name": "Acme Inc"},
     "MetaData": {"LastUpdatedTime": "2019-07-02T10:00:00-07:00"},
     "Line": [
        {"Id": "1", "LineNum": 1, "Amount": 50.0,
         "DetailType": "AccountBasedExpenseLineDetail",
         "AccountBasedExpenseLineDetail": {"AccountRef": {"value": "141", "name": "COGS"}}},
     ]},
]


@pytest.mark.asyncio
async def test_load_vendor_credit(db_session):
    await load_entity(db_session, "VendorCredit", VENDORCREDITS)
    vc = (await db_session.execute(select(QboVendorCredit))).scalar_one()
    assert vc.qbo_id == "900"
    assert vc.counterparty_id == "64"
    line = (await db_session.execute(select(QboVendorCreditLine))).scalar_one()
    assert line.account_id == "141"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_qbo_load.py -k vendor_credit -v`
Expected: FAIL — `cannot import name 'QboVendorCredit'`

- [ ] **Step 3: Add models**

```python
# append to app/models/qbo.py
class QboVendorCredit(_TxnHeaderMixin, TimestampMixin, Base):
    __tablename__ = "qbo_vendor_credits"


class QboVendorCreditLine(_TxnLineMixin, Base):
    __tablename__ = "qbo_vendor_credit_lines"
```

- [ ] **Step 4: Register**

```python
# append to REGISTRY
REGISTRY.append(Entity(
    name="VendorCredit", model=m.QboVendorCredit,
    header=lambda o: mappers.txn_header(o, counterparty="VendorRef"),
    line_model=m.QboVendorCreditLine, line=mappers.txn_line,
))
```

- [ ] **Step 5: Add tables to migration**

Mirror the Task 5 table pattern for `qbo_vendor_credits` (all `_TxnHeaderMixin`
columns) and `qbo_vendor_credit_lines` (identical to `qbo_bill_lines`), with the
parent index and drops in `downgrade()`.

- [ ] **Step 6: Run test to verify it passes**

Run: `pytest tests/test_qbo_load.py -k vendor_credit -v`
Expected: PASS

- [ ] **Step 7: Full migration round-trip check + commit**

Run: `pytest tests/test_qbo_load.py tests/test_qbo_sync_runs.py -v`
Expected: PASS (conftest runs the full alembic upgrade, so 0027 is exercised end to end)

```bash
git add app/models/qbo.py alembic/versions/0027_qbo_mirror_ap_core.py \
        scripts/qbo_import/registry.py tests/test_qbo_load.py
git commit -m "feat(qbo): VendorCredit mirror + lines"
```

---

### Task 8: `extract.py` — paged full + incremental pull

**Files:**
- Create: `finance-api/scripts/qbo_import/extract.py`
- Test: `finance-api/tests/test_qbo_extract.py`

- [ ] **Step 1: Write the failing test (fake client, no network)**

```python
# tests/test_qbo_extract.py
from scripts.qbo_import.extract import build_where, extract_entity


class FakeClient:
    """Records the where-clauses query_all was called with; returns canned rows."""
    def __init__(self, rows):
        self._rows = rows
        self.calls = []

    def query_all(self, entity, where=""):
        self.calls.append((entity, where))
        return iter(self._rows)


def test_build_where_full_is_empty():
    assert build_where(None) == ""


def test_build_where_incremental_uses_lastupdated():
    w = build_where("2019-05-02T10:00:00-07:00")
    assert w == "MetaData.LastUpdatedTime > '2019-05-02T10:00:00-07:00'"


def test_extract_entity_full_collects_rows():
    c = FakeClient([{"Id": "1"}, {"Id": "2"}])
    rows = extract_entity(c, "Bill", since=None)
    assert [r["Id"] for r in rows] == ["1", "2"]
    assert c.calls == [("Bill", "")]


def test_extract_entity_incremental_passes_where():
    c = FakeClient([{"Id": "3"}])
    extract_entity(c, "Bill", since="2019-05-02T10:00:00-07:00")
    assert c.calls[0][1] == "MetaData.LastUpdatedTime > '2019-05-02T10:00:00-07:00'"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_qbo_extract.py -v`
Expected: FAIL — `No module named 'scripts.qbo_import.extract'`

- [ ] **Step 3: Write `extract.py`**

```python
# scripts/qbo_import/extract.py
"""Pull entities from QBO, optionally incrementally, and (optionally) cache to JSON.

`since` is a prior watermark (QBO LastUpdatedTime, tz-aware ISO string). The
extract is deliberately decoupled from load: callers can persist the returned
rows to data/qbo/<entity>.json so a load error never forces a re-fetch.
"""
import json
from pathlib import Path

OUT_DIR = Path(__file__).resolve().parents[2] / "data" / "qbo"


def build_where(since: str | None) -> str:
    if not since:
        return ""
    return f"MetaData.LastUpdatedTime > '{since}'"


def extract_entity(client, entity: str, since: str | None) -> list[dict]:
    """Return all rows for `entity` (full when since is None, else incremental)."""
    return list(client.query_all(entity, where=build_where(since)))


def cache_json(entity: str, rows: list[dict]) -> Path:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    path = OUT_DIR / f"{entity}.json"
    path.write_text(json.dumps(rows, ensure_ascii=False), encoding="utf-8")
    return path
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_qbo_extract.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add scripts/qbo_import/extract.py tests/test_qbo_extract.py
git commit -m "feat(qbo): extract with full + incremental where-clause"
```

---

### Task 9: `run_import.py` — orchestration, sync_runs, watermarks, soft-delete

**Files:**
- Create: `finance-api/scripts/qbo_import/run_import.py`
- Create: `finance-api/scripts/qbo_import/orchestrator.py`
- Test: `finance-api/tests/test_qbo_orchestrator.py`

The CLI is a thin wrapper; the testable logic (per-entity loop, watermark advance,
soft-delete diff, counter accumulation) lives in `orchestrator.py`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_qbo_orchestrator.py
import pytest
from sqlalchemy import select
from app.models.qbo import QboAccount, QboSyncRun
from scripts.qbo_import.orchestrator import run_sync


class FakeClient:
    def __init__(self, rows_by_entity):
        self.rows_by_entity = rows_by_entity

    def query_all(self, entity, where=""):
        return iter(self.rows_by_entity.get(entity, []))


@pytest.mark.asyncio
async def test_full_sync_loads_and_records_watermark(db_session):
    client = FakeClient({"Account": [
        {"Id": "58", "Name": "A/P", "AccountType": "Accounts Payable",
         "MetaData": {"LastUpdatedTime": "2019-05-02T10:00:00-07:00"}},
    ]})
    run = await run_sync(db_session, client, mode="full", entities=["Account"])
    assert run.status == "success"
    assert run.counters["Account"]["inserted"] == 1
    assert run.watermarks["Account"] == "2019-05-02T10:00:00-07:00"
    assert (await db_session.execute(select(QboAccount))).scalar_one().qbo_id == "58"


@pytest.mark.asyncio
async def test_full_sync_soft_deletes_local_extras(db_session):
    # First full pull loads two accounts.
    client1 = FakeClient({"Account": [
        {"Id": "1", "Name": "a", "MetaData": {"LastUpdatedTime": "2019-05-01T00:00:00-07:00"}},
        {"Id": "2", "Name": "b", "MetaData": {"LastUpdatedTime": "2019-05-01T00:00:00-07:00"}},
    ]})
    await run_sync(db_session, client1, mode="full", entities=["Account"])

    # Second full pull no longer returns id=2 → it must be soft-deleted, not removed.
    client2 = FakeClient({"Account": [
        {"Id": "1", "Name": "a", "MetaData": {"LastUpdatedTime": "2019-05-03T00:00:00-07:00"}},
    ]})
    await run_sync(db_session, client2, mode="full", entities=["Account"])

    rows = {r.qbo_id: r for r in (await db_session.execute(select(QboAccount))).scalars().all()}
    assert rows["2"].deleted_at is not None
    assert rows["1"].deleted_at is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_qbo_orchestrator.py -v`
Expected: FAIL — `No module named 'scripts.qbo_import.orchestrator'`

- [ ] **Step 3: Write `orchestrator.py`**

```python
# scripts/qbo_import/orchestrator.py
"""Drive a full/incremental sync across entities, recording progress + watermarks.

Soft-delete (full mode only): any local, not-already-deleted qbo_id absent from
QBO's returned set is stamped deleted_at — the record is preserved, never removed.
"""
from datetime import datetime, timezone

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.qbo import QboSyncRun
from scripts.qbo_import.extract import extract_entity
from scripts.qbo_import.load import load_entity
from scripts.qbo_import.mappers import last_updated
from scripts.qbo_import.registry import by_name

# AP-core default order (masters before transactions).
DEFAULT_ENTITIES = ["Account", "Vendor", "Bill", "BillPayment", "VendorCredit"]


def _max_watermark(objects: list[dict]) -> str | None:
    best = None
    for o in objects:
        lu = (o.get("MetaData") or {}).get("LastUpdatedTime")
        if lu and (best is None or lu > best):
            best = lu
    return best


async def _soft_delete_extras(db: AsyncSession, name: str, seen_ids: set[str]) -> int:
    ent = by_name(name)
    model = ent.model
    local = set((await db.execute(select(model.qbo_id).where(model.deleted_at.is_(None)))).scalars().all())
    gone = local - seen_ids
    if gone:
        await db.execute(
            update(model).where(model.qbo_id.in_(gone)).values(deleted_at=datetime.now(timezone.utc))
        )
        await db.commit()
    return len(gone)


async def run_sync(db: AsyncSession, client, mode: str, entities: list[str] | None = None,
                   started_by=None) -> QboSyncRun:
    entities = entities or DEFAULT_ENTITIES
    run = QboSyncRun(mode=mode, status="running",
                     started_at=datetime.now(timezone.utc), counters={}, watermarks={})
    db.add(run)
    await db.commit()
    try:
        prior = {}
        if mode == "incremental":
            last = (await db.execute(
                select(QboSyncRun).where(QboSyncRun.status == "success")
                .order_by(QboSyncRun.started_at.desc()).limit(1)
            )).scalars().first()
            prior = (last.watermarks if last else {}) or {}

        counters, watermarks = {}, {}
        for name in entities:
            since = prior.get(name) if mode == "incremental" else None
            objects = extract_entity(client, name, since=since)
            counts = await load_entity(db, name, objects)
            if mode == "full":
                counts["deleted"] = await _soft_delete_extras(
                    db, name, {o["Id"] for o in objects}
                )
            counters[name] = counts
            wm = _max_watermark(objects)
            # Keep the prior watermark if this run saw nothing newer.
            watermarks[name] = wm or prior.get(name)

        run.status = "success"
        run.counters = counters
        run.watermarks = watermarks
    except Exception as exc:  # noqa: BLE001 — record failure on the run row
        run.status = "failed"
        run.error = str(exc)[:2000]
        raise
    finally:
        run.finished_at = datetime.now(timezone.utc)
        await db.commit()
    return run
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_qbo_orchestrator.py -v`
Expected: PASS (watermark recorded; id=2 soft-deleted on the second full pull)

- [ ] **Step 5: Write the CLI wrapper**

```python
# scripts/qbo_import/run_import.py
"""CLI entry point for the QBO AP-core import.

Usage (run INSIDE the finance-api container, or with an explicit DATABASE_URL —
the host .env points at the production DB):
    python scripts/qbo_import/run_import.py --full
    python scripts/qbo_import/run_import.py --incremental
    python scripts/qbo_import/run_import.py --full --entities Account,Vendor
"""
import argparse
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from app.db.base import AsyncSessionLocal  # noqa: E402
from scripts.qbo_import.client import QboClient  # noqa: E402
from scripts.qbo_import.orchestrator import DEFAULT_ENTITIES, run_sync  # noqa: E402


async def _main(mode: str, entities: list[str]) -> int:
    client = QboClient()
    async with AsyncSessionLocal() as db:
        run = await run_sync(db, client, mode=mode, entities=entities)
    print(f"{mode} sync {run.status}")
    for name, c in (run.counters or {}).items():
        print(f"  {name}: {c}")
    if run.error:
        print(f"  error: {run.error}")
        return 1
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--full", action="store_true")
    g.add_argument("--incremental", action="store_true")
    ap.add_argument("--entities", help="comma-separated subset (default: AP core)")
    args = ap.parse_args()
    mode = "full" if args.full else "incremental"
    entities = args.entities.split(",") if args.entities else DEFAULT_ENTITIES
    return asyncio.run(_main(mode, entities))


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 6: Commit**

```bash
git add scripts/qbo_import/orchestrator.py scripts/qbo_import/run_import.py \
        tests/test_qbo_orchestrator.py
git commit -m "feat(qbo): full/incremental orchestrator + soft-delete + CLI"
```

---

### Task 10: End-to-end smoke against sandbox (success path)

Confirms the whole pipeline runs against a live QBO company before touching
production. Uses the sandbox company already authorized earlier (or re-point
`qbo_conn.env` to sandbox). This task has no automated test — it is a manual
verification step.

- [ ] **Step 1: Point env at sandbox, run a scoped full pull**

With `qbo_conn.env` set to a sandbox realm (`QBO_ENV=sandbox`), run inside the
finance-api container (or with `DATABASE_URL` overridden to a local DB — never the
production DB):

Run: `python scripts/qbo_import/run_import.py --full --entities Account`
Expected: prints `full sync success` and `Account: {'inserted': N, 'updated': 0, 'deleted': 0}`
with N matching the sandbox Account count.

- [ ] **Step 2: Verify rows landed**

Run: `python -c "import asyncio; from sqlalchemy import select, func; from app.db.base import AsyncSessionLocal; from app.models.qbo import QboAccount;
async def c():
  async with AsyncSessionLocal() as db: print((await db.execute(select(func.count()).select_from(QboAccount))).scalar())
asyncio.run(c())"`
Expected: the same N.

- [ ] **Step 3: Run an incremental immediately (should be a no-op)**

Run: `python scripts/qbo_import/run_import.py --incremental --entities Account`
Expected: `Account: {'inserted': 0, 'updated': 0}` (watermark from step 1 filters everything out).

- [ ] **Step 4: Record the smoke result**

No commit (no code change). Note the counts in the run/PR description as evidence
the success path works end to end.

---

## Self-review notes

- **Spec coverage (AP-core slice):** sync log (Task 1), hybrid typed tables +
  line tables (Tasks 3-7), raw fallback column (every model), multicurrency
  amounts (Task 5), reconciliation links (Task 6), full+incremental engine
  (Tasks 8-9), soft-delete diff (Task 9), tz-aware watermark (mappers.to_dt +
  orchestrator), idempotent reload (Tasks 3/5 tests), CLI + container/DATABASE_URL
  safety (Task 9), sandbox smoke (Task 10). AR, long tail, Purchase/Deposit/
  Transfer/JournalEntry, attachments, API, and UI are explicitly deferred to
  later plans per the spec's phasing.
- **No placeholders:** every model, mapper, migration column, and test body is
  spelled out. Tasks 6-7 reference the Task 5 table shape rather than re-pasting
  ~30 identical column lines; the exact delta (BillPayment adds `pay_type`) is
  stated so it is unambiguous.
- **Type consistency:** `load_entity(db, name, objects)`, `run_sync(db, client,
  mode, entities)`, `txn_header(obj, counterparty)`, `txn_line(line,
  parent_qbo_id)`, `by_name(name)` are used identically across tasks. Header key
  `counterparty_id/_name` matches the column names in `_TxnHeaderMixin`. `qbo_id`
  is the upsert key in `load._upsert` and the PK in every header model.
