# Vendor Credit — Phase C Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Bring the vendor credit balances that already exist inside QuickBooks Online into `vendor_credits` as opening balances, through a workbench where every vendor mapping is decided by a human and remembered.

**Architecture:** All work is in `finance-api`, plus one admin page in the `finance` frontend. A new `qbo_vendor_map` table persists AP's decision for each QBO vendor — mapped to an EPMS partner, or ignored as an employee, or ignored for another reason — so re-running the import never re-asks. The import itself reads the existing `qbo_vendor_credits` mirror, takes each credit's **unapplied `balance`** rather than its original face value, and is idempotent through the `(source, source_ref)` unique index Phase A already created.

**Tech Stack:** FastAPI · SQLAlchemy 2 async · Alembic · Pydantic v2 · pytest/pytest-asyncio (`asyncio_mode = auto`) · React 18 + TypeScript + TanStack Query

**Spec:** `docs/superpowers/specs/2026-08-06-vendor-credit-management-design.md` §7.

**Builds on:** Phase A (the `vendor_credits` ledger and its review workflow) and Phase B (payment netting). Phase C is a one-off migration and is only useful once B is live — an imported balance that nothing can spend is inert.

## Global Constraints

- **Take `qbo_vendor_credits.balance`, never `total_amt`.** `balance` is the unapplied remainder in QBO; `total_amt` is the original face value. Anything already applied inside QBO is history and must not re-enter the pool. Getting this wrong hands vendors credit they have already used.
- **Never auto-apply a vendor mapping.** Automatic matching may only *pre-fill* the control; `decision` is written solely by an explicit human action. QBO's vendor list is a superset of EPMS's and mixes in employees set up as vendors for expense reimbursement; a mis-mapped vendor spends supplier A's money against supplier B's account.
- **A QBO vendor with no confirmed mapping is not imported.** It is listed for AP to resolve. Never guess, never skip silently.
- Imported rows land as `source='qbo_import'`, `opening_balance=True`, `status='available'` — they skip review, because the data was already reconciled by accounting inside QBO. The human judgement that matters for them is the mapping, gated separately.
- Every monetary column stays POSITIVE. `abs()` remains only in `crud/vendor_credit.py` `_positive()`; the import goes through the same write layer rather than inserting rows directly.
- **The import must be idempotent.** Re-running after a later sync adds only new credits. `(source, source_ref)` is uniquely indexed for exactly this.
- **A row already imported whose QBO `balance` has since changed is never silently overwritten.** It is surfaced as drift. Overwriting could erase applications Phase B has already recorded against it.
- New alembic revision chains off `0031_vc_applications` — verify the head before writing it, do not assume.
- Identifiers and user-facing strings are English. Code comments may be Chinese.
- Work happens in worktree `c:/Project/uniops-vendor-credit` on branch `feature/vendor-credit`.

## Environment (verified — do not re-investigate)

- **Run every command in the FOREGROUND.** Never background a command, never spawn a watcher, never stop to wait for a notification. Two agents have already been stranded this way; the Bash tool auto-backgrounds anything over its timeout.
- **Run only the targeted test file. NEVER the full finance-api suite** — 45 minutes, and concurrent runs corrupt the shared test database.
- finance-api test command (the password override is mandatory; conftest's default is wrong on this machine):
  ```
  cd finance-api && TEST_PG_PASSWORD=7c0a03bb8c2afef690d1852f8dc3a0195932db5f0f1670e9 TEST_FINANCE_DB=finance_test_vc_c<N> python -m pytest tests/test_vendor_credit_import.py -v
  ```
  Use a distinct `TEST_FINANCE_DB` per task.
- **Never run `alembic` by hand.** `finance-api/.env` points at the PRODUCTION database (10.10.50.20). The pytest fixture overrides `DATABASE_URL` safely; manual invocation does not.
- finance frontend gate: `cd finance && npx tsc -p tsconfig.app.json --noEmit --ignoreDeprecations 6.0 2>&1 | grep -c "error TS"`. **Measure the baseline before changing anything**; do not copy a number. If `node_modules` is missing, run `npm ci` first, or tsc prints an install prompt and the grep count is a false zero.
- **Never `git stash`.**

## What already exists — read before designing anything new

- `qbo_vendor_credits` (`app/models/qbo.py`) inherits `_TxnHeaderMixin`: `qbo_id` (PK), `doc_number`, `txn_date`, `currency`, `total_amt`, **`balance`**, `counterparty_id`, `counterparty_name`, `deleted_at`, `raw`.
- `qbo_vendors`: `qbo_id`, `display_name`, `email`, `active`, `deleted_at`, `raw`.
- `qbo_sync_runs`: `mode` (`full` | `incremental`), `status` (`success` on completion), `finished_at`.
- `business_partners` mirror (`app/models/mirrors.py`): `code`, `name`, `contact_email`, `remittance_email`, `is_supplier`.
- `users` mirror: `email`, `full_name` — the basis for the employee signal.
- **`POST /qbo/vendor-emails/backfill` (`app/api/v1/qbo.py`) already matches QBO vendors to EPMS partners** on `lower(trim(name)) == lower(trim(display_name))`, buckets **both** sides by the normalised key, and treats a key with more than one candidate on **either** side as ambiguous rather than guessing. Phase C reuses that normalisation and that two-sided ambiguity rule. Do not invent a second matcher.

---

## File Structure

| File | Responsibility |
|---|---|
| `finance-api/alembic/versions/0032_qbo_vendor_map.py` | **Create.** The `qbo_vendor_map` table. |
| `finance-api/app/models/qbo_vendor_map.py` | **Create.** Its ORM model, kept out of `qbo.py` because that file mirrors QBO's own schema and this table is ours. |
| `finance-api/app/crud/vendor_credit_import.py` | **Create.** Candidate discovery, decision writes, import execution, drift detection. All Phase C rules live here. |
| `finance-api/app/schemas/vendor_credit_import.py` | **Create.** Workbench and import contracts. |
| `finance-api/app/api/v1/vendor_credit_import.py` | **Create.** HTTP surface. |
| `finance-api/app/api/v1/__init__.py` | **Modify.** Register the router. |
| `finance-api/tests/test_vendor_credit_import.py` | **Create.** Every Phase C test. |
| `finance/src/pages/finance/QboVendorCreditImportPage.tsx` | **Create.** The mapping workbench. |
| `finance/src/App.tsx` or the finance router file | **Modify.** Register the route next to the existing QBO mirror page. |

---

## Task 1: The `qbo_vendor_map` table

**Files:**
- Create: `finance-api/app/models/qbo_vendor_map.py`
- Create: `finance-api/alembic/versions/0032_qbo_vendor_map.py`
- Test: `finance-api/tests/test_vendor_credit_import.py`

**Interfaces:**
- Produces: `QboVendorMap` ORM class; constants `PENDING`, `MAPPED`, `IGNORED_EMPLOYEE`, `IGNORED_OTHER`.

This table is **ours**, not a QBO mirror — it records a human decision about QBO data. That is why it gets its own module rather than joining `app/models/qbo.py`, which mirrors QBO's schema shape.

- [ ] **Step 1: Verify the current alembic head**

Write this to a scratch file in `finance-api/` and run it — do not inline it as a nested heredoc:

```python
import re, glob, os
revs, parents = {}, set()
for f in glob.glob("alembic/versions/*.py"):
    s = open(f, encoding="utf-8").read()
    r = re.search(r'^revision\s*=\s*["\']([^"\']+)', s, re.M)
    d = re.search(r'^down_revision\s*=\s*["\']([^"\']+)', s, re.M)
    if r: revs[r.group(1)] = os.path.basename(f)
    if d: parents.add(d.group(1))
print("HEADS:", [k for k in revs if k not in parents])
```

Expected: exactly one head. Use whatever it prints as your `down_revision` — it should be `0031_vc_applications`, but use the real value, not this sentence.

- [ ] **Step 2: Write the failing test**

Create `finance-api/tests/test_vendor_credit_import.py`:

```python
"""Vendor Credit — Phase C (QBO opening-balance import)."""
import uuid
from datetime import date, datetime, timezone
from decimal import Decimal

import pytest
import sqlalchemy as sa


def test_map_model_mapped():
    from app.models.qbo_vendor_map import QboVendorMap
    cols = {c.name for c in QboVendorMap.__table__.columns}
    assert {"qbo_vendor_id", "qbo_display_name", "vendor_id", "decision",
            "decided_by", "decided_at", "note"} <= cols
    assert QboVendorMap.__tablename__ == "qbo_vendor_map"


def test_map_decision_constants():
    from app.models.qbo_vendor_map import (
        IGNORED_EMPLOYEE, IGNORED_OTHER, MAPPED, PENDING)
    assert (PENDING, MAPPED, IGNORED_EMPLOYEE, IGNORED_OTHER) == (
        "pending", "mapped", "ignored_employee", "ignored_other")
```

- [ ] **Step 3: Run it and watch it fail**

Run: `cd finance-api && TEST_PG_PASSWORD=7c0a03bb8c2afef690d1852f8dc3a0195932db5f0f1670e9 TEST_FINANCE_DB=finance_test_vc_c1 python -m pytest tests/test_vendor_credit_import.py -v`

Expected: FAIL — `ModuleNotFoundError: No module named 'app.models.qbo_vendor_map'`

- [ ] **Step 4: Write the model**

Create `finance-api/app/models/qbo_vendor_map.py`:

```python
"""Human decisions about QBO vendors — ours, not a QBO mirror.

QBO's vendor list is a superset of EPMS's business partners and mixes in
employees set up as vendors for expense reimbursement. Every row here is an
explicit decision someone made; nothing writes to it automatically. Persisting
the decision is what lets the opening-balance import be re-run without asking
the same questions again.
"""
import uuid
from datetime import datetime

from sqlalchemy import DateTime, String, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin

PENDING = "pending"
MAPPED = "mapped"
IGNORED_EMPLOYEE = "ignored_employee"
IGNORED_OTHER = "ignored_other"


class QboVendorMap(TimestampMixin, Base):
    __tablename__ = "qbo_vendor_map"

    qbo_vendor_id: Mapped[str] = mapped_column(String(20), primary_key=True)

    # Snapshot so the decision stays readable after a rename inside QBO.
    qbo_display_name: Mapped[str | None] = mapped_column(String(255), nullable=True)

    # No FK: business_partners is an mdm-owned mirror in this service, same as
    # VendorCredit.vendor_id.
    vendor_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)

    decision: Mapped[str] = mapped_column(String(20), nullable=False, default=PENDING, index=True)

    decided_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
```

- [ ] **Step 5: Write the migration**

Create `finance-api/alembic/versions/0032_qbo_vendor_map.py`, using the head you verified in Step 1 as `down_revision`:

```python
"""qbo_vendor_map — Phase C human mapping decisions

Revision ID: 0032_qbo_vendor_map
Revises: 0031_vc_applications
Create Date: 2026-08-07
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

revision = "0032_qbo_vendor_map"
down_revision = "0031_vc_applications"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "qbo_vendor_map",
        sa.Column("qbo_vendor_id", sa.String(20), primary_key=True),
        sa.Column("qbo_display_name", sa.String(255), nullable=True),
        sa.Column("vendor_id", UUID(as_uuid=True), nullable=True),
        sa.Column("decision", sa.String(20), nullable=False, server_default="pending"),
        sa.Column("decided_by", UUID(as_uuid=True), nullable=True),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
    )
    op.create_index("ix_qbo_vendor_map_decision", "qbo_vendor_map", ["decision"])


def downgrade() -> None:
    op.drop_table("qbo_vendor_map")
```

The index name matches what SQLAlchemy generates for `index=True` on `decision` (`ix_<table>_<column>`). Model and migration must stay byte-identical on index names — Phase B had a finding where they diverged.

- [ ] **Step 6: Add a behavioural DB test**

Append to `finance-api/tests/test_vendor_credit_import.py`:

```python
@pytest.mark.anyio
async def test_migration_builds_the_map_table(db_session):
    cols = (await db_session.execute(sa.text(
        "SELECT column_name FROM information_schema.columns "
        "WHERE table_name = 'qbo_vendor_map'"
    ))).scalars().all()
    assert {"qbo_vendor_id", "vendor_id", "decision", "decided_by"} <= set(cols)

    idx = (await db_session.execute(sa.text(
        "SELECT indexname FROM pg_indexes WHERE tablename = 'qbo_vendor_map'"
    ))).scalars().all()
    assert "ix_qbo_vendor_map_decision" in idx


@pytest.mark.anyio
async def test_map_is_keyed_by_qbo_vendor_id(db_session):
    """One decision per QBO vendor — a second row for the same vendor must fail."""
    from sqlalchemy.exc import IntegrityError
    from app.models.qbo_vendor_map import QboVendorMap

    db_session.add(QboVendorMap(qbo_vendor_id="99", qbo_display_name="ULINE",
                                decision="pending"))
    await db_session.flush()
    db_session.add(QboVendorMap(qbo_vendor_id="99", qbo_display_name="ULINE dup",
                                decision="pending"))
    with pytest.raises(IntegrityError):
        await db_session.flush()
    await db_session.rollback()
```

- [ ] **Step 7: Run the tests**

Run: `cd finance-api && TEST_PG_PASSWORD=7c0a03bb8c2afef690d1852f8dc3a0195932db5f0f1670e9 TEST_FINANCE_DB=finance_test_vc_c1 python -m pytest tests/test_vendor_credit_import.py -v`

Expected: 4 passed.

- [ ] **Step 8: Commit**

```bash
git add finance-api/app/models/qbo_vendor_map.py finance-api/alembic/versions/0032_qbo_vendor_map.py finance-api/tests/test_vendor_credit_import.py
git commit -m "feat(finance): qbo_vendor_map — persisted human mapping decisions"
```

---

## Task 2: Workbench candidates

**Files:**
- Create: `finance-api/app/crud/vendor_credit_import.py`
- Test: `finance-api/tests/test_vendor_credit_import.py`

**Interfaces:**
- Consumes: `QboVendorMap` and its constants; `QboVendorCredit`, `QboVendor`, `QboBill` from `app/models/qbo.py`; `BusinessPartner`, `User` from `app/models/mirrors.py`.
- Produces:
  - `def norm(s: str | None) -> str` — `(s or "").strip().lower()`, the same normalisation `POST /qbo/vendor-emails/backfill` already uses.
  - `async def list_candidates(db) -> list[dict]` — one entry per QBO vendor holding importable credit, each with:
    `qbo_vendor_id`, `qbo_display_name`, `credit_count`, `credit_total` (sum of `balance`), `currency`, `decision`, `vendor_id`, `suggested_vendor_id`, `suggested_vendor_name`, `tags` (list of strings).

**Only QBO vendors that actually hold importable credit appear.** A vendor with no `balance > 0` credit is not a decision anyone needs to make.

**Tags are advisory signals, never verdicts.** They exist so a human can scan a few hundred rows quickly:
- `likely_employee` — the QBO vendor's email matches a row in `users.email`, or its normalised `display_name` matches a `users.full_name`.
- `no_bills` — the vendor has no rows in `qbo_bills`, i.e. we have never bought anything from them.
- `similar` — a normalised-name match against `business_partners` exists. This populates `suggested_vendor_id` / `suggested_vendor_name` for pre-fill **only**.

**Ambiguity is two-sided, exactly as the existing backfill handles it.** If the normalised key maps to more than one `business_partners` row, OR more than one QBO vendor shares that key, there is no suggestion — emit no `suggested_vendor_id` and tag it `ambiguous`. A one-directional lookup would silently pick the first of several same-named partners.

- [ ] **Step 1: Write the failing tests**

Append to `finance-api/tests/test_vendor_credit_import.py`:

```python
def _qbo_credit(**over):
    from app.models.qbo import QboVendorCredit
    kw = dict(
        qbo_id=uuid.uuid4().hex[:10], doc_number="CN-1", txn_date="2026-05-01",
        currency="CAD", total_amt=Decimal("100.00"), balance=Decimal("100.00"),
        counterparty_id="V1", counterparty_name="ULINE", raw={},
    )
    kw.update(over)
    return QboVendorCredit(**kw)


def _qbo_vendor(**over):
    from app.models.qbo import QboVendor
    kw = dict(qbo_id="V1", display_name="ULINE", email="ap@uline.test",
              active=True, raw={})
    kw.update(over)
    return QboVendor(**kw)


def _partner(**over):
    from app.models.mirrors import BusinessPartner
    kw = dict(id=uuid.uuid4(), code=uuid.uuid4().hex[:8], name="ULINE",
              contact_email="ap@uline.test", is_supplier=True)
    kw.update(over)
    return BusinessPartner(**kw)


@pytest.mark.anyio
async def test_candidates_only_lists_vendors_holding_balance(db_session):
    from app.crud import vendor_credit_import as crud
    db_session.add_all([
        _qbo_vendor(qbo_id="V1", display_name="ULINE"),
        _qbo_vendor(qbo_id="V2", display_name="NOBALANCE CO"),
        _qbo_credit(counterparty_id="V1", balance=Decimal("100.00")),
        _qbo_credit(counterparty_id="V2", balance=Decimal("0.00")),
    ])
    await db_session.flush()

    rows = await crud.list_candidates(db_session)
    ids = {r["qbo_vendor_id"] for r in rows}
    assert "V1" in ids
    assert "V2" not in ids


@pytest.mark.anyio
async def test_candidates_sum_balance_not_face_value(db_session):
    from app.crud import vendor_credit_import as crud
    db_session.add_all([
        _qbo_vendor(qbo_id="V1"),
        _qbo_credit(counterparty_id="V1", total_amt=Decimal("500.00"),
                    balance=Decimal("30.00")),
        _qbo_credit(counterparty_id="V1", total_amt=Decimal("500.00"),
                    balance=Decimal("20.00")),
    ])
    await db_session.flush()

    row = next(r for r in await crud.list_candidates(db_session)
               if r["qbo_vendor_id"] == "V1")
    assert row["credit_total"] == Decimal("50.00")
    assert row["credit_count"] == 2


@pytest.mark.anyio
async def test_candidates_skip_deleted_credits(db_session):
    from app.crud import vendor_credit_import as crud
    db_session.add_all([
        _qbo_vendor(qbo_id="V1"),
        _qbo_credit(counterparty_id="V1", balance=Decimal("100.00"),
                    deleted_at=datetime.now(timezone.utc)),
    ])
    await db_session.flush()
    assert [r for r in await crud.list_candidates(db_session)
            if r["qbo_vendor_id"] == "V1"] == []


@pytest.mark.anyio
async def test_exact_name_match_produces_a_suggestion_only(db_session):
    from app.crud import vendor_credit_import as crud
    p = _partner(name="  uline  ")          # normalisation must handle case + spaces
    db_session.add_all([p, _qbo_vendor(qbo_id="V1", display_name="ULINE"),
                        _qbo_credit(counterparty_id="V1")])
    await db_session.flush()

    row = next(r for r in await crud.list_candidates(db_session)
               if r["qbo_vendor_id"] == "V1")
    assert row["suggested_vendor_id"] == p.id
    assert "similar" in row["tags"]
    # A suggestion is NOT a decision.
    assert row["decision"] == "pending"
    assert row["vendor_id"] is None


@pytest.mark.anyio
async def test_two_partners_with_the_same_name_yield_no_suggestion(db_session):
    from app.crud import vendor_credit_import as crud
    db_session.add_all([
        _partner(name="ACME"), _partner(name="acme "),
        _qbo_vendor(qbo_id="V1", display_name="Acme"),
        _qbo_credit(counterparty_id="V1"),
    ])
    await db_session.flush()

    row = next(r for r in await crud.list_candidates(db_session)
               if r["qbo_vendor_id"] == "V1")
    assert row["suggested_vendor_id"] is None
    assert "ambiguous" in row["tags"]


@pytest.mark.anyio
async def test_employee_signal_from_a_matching_user(db_session):
    from app.crud import vendor_credit_import as crud
    from app.models.mirrors import User
    db_session.add_all([
        User(id=uuid.uuid4(), email="jane@canadaroyalmilk.com", full_name="Jane Doe"),
        _qbo_vendor(qbo_id="V9", display_name="Jane Doe",
                    email="jane@canadaroyalmilk.com"),
        _qbo_credit(counterparty_id="V9", counterparty_name="Jane Doe"),
    ])
    await db_session.flush()

    row = next(r for r in await crud.list_candidates(db_session)
               if r["qbo_vendor_id"] == "V9")
    assert "likely_employee" in row["tags"]
    # Still only a signal — the row is pending and importable if AP says so.
    assert row["decision"] == "pending"


@pytest.mark.anyio
async def test_no_bills_signal(db_session):
    from app.crud import vendor_credit_import as crud
    db_session.add_all([_qbo_vendor(qbo_id="V1"), _qbo_credit(counterparty_id="V1")])
    await db_session.flush()
    row = next(r for r in await crud.list_candidates(db_session)
               if r["qbo_vendor_id"] == "V1")
    assert "no_bills" in row["tags"]


@pytest.mark.anyio
async def test_existing_decision_is_returned(db_session):
    from app.crud import vendor_credit_import as crud
    from app.models.qbo_vendor_map import QboVendorMap
    p = _partner(name="ULINE")
    db_session.add_all([
        p, _qbo_vendor(qbo_id="V1", display_name="ULINE"),
        _qbo_credit(counterparty_id="V1"),
        QboVendorMap(qbo_vendor_id="V1", qbo_display_name="ULINE",
                     vendor_id=p.id, decision="mapped"),
    ])
    await db_session.flush()

    row = next(r for r in await crud.list_candidates(db_session)
               if r["qbo_vendor_id"] == "V1")
    assert row["decision"] == "mapped"
    assert row["vendor_id"] == p.id
```

- [ ] **Step 2: Run and watch them fail**

Run: `cd finance-api && TEST_PG_PASSWORD=7c0a03bb8c2afef690d1852f8dc3a0195932db5f0f1670e9 TEST_FINANCE_DB=finance_test_vc_c2 python -m pytest tests/test_vendor_credit_import.py -v`

Expected: FAIL — `ModuleNotFoundError: No module named 'app.crud.vendor_credit_import'`

- [ ] **Step 3: Implement candidate discovery**

Create `finance-api/app/crud/vendor_credit_import.py`:

```python
"""QBO opening-balance import — candidate discovery, decisions, execution.

Every rule specific to importing QBO vendor credits lives here. The one rule
that governs all the others: automatic matching may only PRE-FILL a control.
`decision` is written by an explicit human action and nothing else. QBO's vendor
list is a superset of ours and includes employees set up as vendors for expense
reimbursement, so an automatic mapping would eventually spend one supplier's
money against another's account.
"""
import uuid
from collections import defaultdict
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.mirrors import BusinessPartner, User
from app.models.qbo import QboBill, QboVendor, QboVendorCredit
from app.models.qbo_vendor_map import PENDING, QboVendorMap

_ZERO = Decimal("0")


def norm(s: str | None) -> str:
    """The same normalisation POST /qbo/vendor-emails/backfill already uses."""
    return (s or "").strip().lower()


async def list_candidates(db: AsyncSession) -> list[dict]:
    credits = (await db.execute(
        select(QboVendorCredit).where(
            QboVendorCredit.deleted_at.is_(None),
            QboVendorCredit.balance > _ZERO,
        )
    )).scalars().all()
    if not credits:
        return []

    by_vendor: dict[str, list[QboVendorCredit]] = defaultdict(list)
    for c in credits:
        if c.counterparty_id:
            by_vendor[c.counterparty_id].append(c)

    vendors = {
        v.qbo_id: v for v in (await db.execute(
            select(QboVendor).where(QboVendor.qbo_id.in_(list(by_vendor)))
        )).scalars().all()
    }
    decisions = {
        m.qbo_vendor_id: m for m in (await db.execute(
            select(QboVendorMap).where(QboVendorMap.qbo_vendor_id.in_(list(by_vendor)))
        )).scalars().all()
    }

    # Bucket BOTH sides by the normalised key. A key with more than one candidate
    # on EITHER side is ambiguous and gets no suggestion — a one-directional
    # lookup would silently pick the first of several same-named partners.
    partners_by_key: dict[str, list[BusinessPartner]] = defaultdict(list)
    for p in (await db.execute(
        select(BusinessPartner).where(BusinessPartner.is_supplier.is_(True))
    )).scalars().all():
        if norm(p.name):
            partners_by_key[norm(p.name)].append(p)

    qbo_by_key: dict[str, list[str]] = defaultdict(list)
    for v in vendors.values():
        if norm(v.display_name):
            qbo_by_key[norm(v.display_name)].append(v.qbo_id)

    user_emails = {
        norm(e) for e in (await db.execute(select(User.email))).scalars().all() if e
    }
    user_names = {
        norm(n) for n in (await db.execute(select(User.full_name))).scalars().all() if n
    }
    vendors_with_bills = set((await db.execute(
        select(QboBill.counterparty_id).where(QboBill.counterparty_id.isnot(None)).distinct()
    )).scalars().all())

    rows: list[dict] = []
    for qbo_vendor_id, items in by_vendor.items():
        v = vendors.get(qbo_vendor_id)
        display = v.display_name if v else (items[0].counterparty_name or "")
        key = norm(display)

        tags: list[str] = []
        suggested = None
        matches = partners_by_key.get(key, [])
        if key and (len(matches) > 1 or len(qbo_by_key.get(key, [])) > 1):
            tags.append("ambiguous")
        elif len(matches) == 1:
            suggested = matches[0]
            tags.append("similar")

        if (v and norm(v.email) in user_emails and norm(v.email)) or (key and key in user_names):
            tags.append("likely_employee")
        if qbo_vendor_id not in vendors_with_bills:
            tags.append("no_bills")

        m = decisions.get(qbo_vendor_id)
        rows.append({
            "qbo_vendor_id": qbo_vendor_id,
            "qbo_display_name": display,
            "credit_count": len(items),
            "credit_total": sum((i.balance for i in items), _ZERO),
            "currency": items[0].currency or "CAD",
            "decision": m.decision if m else PENDING,
            "vendor_id": m.vendor_id if m else None,
            "suggested_vendor_id": suggested.id if suggested else None,
            "suggested_vendor_name": suggested.name if suggested else None,
            "tags": tags,
        })

    rows.sort(key=lambda r: (r["decision"] != PENDING, -r["credit_total"]))
    return rows
```

- [ ] **Step 4: Run the tests**

Run: `cd finance-api && TEST_PG_PASSWORD=7c0a03bb8c2afef690d1852f8dc3a0195932db5f0f1670e9 TEST_FINANCE_DB=finance_test_vc_c2 python -m pytest tests/test_vendor_credit_import.py -v`

Expected: all pass (12 by now). If `QboBill` has no `counterparty_id` attribute, check the real column on `_TxnHeaderMixin` and use that — it is the vendor reference on a bill.

- [ ] **Step 5: Commit**

```bash
git add finance-api/app/crud/vendor_credit_import.py finance-api/tests/test_vendor_credit_import.py
git commit -m "feat(finance): QBO import workbench candidates with advisory signals"
```

---

## Task 3: Recording a decision

**Files:**
- Modify: `finance-api/app/crud/vendor_credit_import.py`
- Test: `finance-api/tests/test_vendor_credit_import.py`

**Interfaces:**
- Produces:
  - `class InvalidDecision(Exception)`
  - `async def set_decision(db, *, qbo_vendor_id: str, qbo_display_name: str | None, decision: str, vendor_id: uuid.UUID | None, decided_by: uuid.UUID, note: str | None) -> QboVendorMap` — upserts one row.

**Rules:**
- `decision='mapped'` requires a `vendor_id` that exists in `business_partners` and is a supplier. Without it, raise `InvalidDecision` — a mapped row with no target would silently import nothing while looking resolved.
- The ignore decisions must carry `vendor_id = None`; if one is supplied, clear it rather than storing a contradiction.
- A decision may be changed later — this is an upsert, not insert-only. AP will correct mistakes.
- `decided_by` and `decided_at` are always stamped, including on a change.

- [ ] **Step 1: Write the failing tests**

Append to `finance-api/tests/test_vendor_credit_import.py`:

```python
@pytest.mark.anyio
async def test_mapping_a_vendor_records_the_decision(db_session):
    from app.crud import vendor_credit_import as crud
    p = _partner()
    db_session.add(p)
    await db_session.flush()
    actor = uuid.uuid4()

    m = await crud.set_decision(db_session, qbo_vendor_id="V1",
                                qbo_display_name="ULINE", decision="mapped",
                                vendor_id=p.id, decided_by=actor, note=None)
    await db_session.flush()
    assert m.decision == "mapped"
    assert m.vendor_id == p.id
    assert m.decided_by == actor
    assert m.decided_at is not None


@pytest.mark.anyio
async def test_mapped_without_a_vendor_is_rejected(db_session):
    from app.crud import vendor_credit_import as crud
    with pytest.raises(crud.InvalidDecision):
        await crud.set_decision(db_session, qbo_vendor_id="V1",
                                qbo_display_name="ULINE", decision="mapped",
                                vendor_id=None, decided_by=uuid.uuid4(), note=None)


@pytest.mark.anyio
async def test_mapped_to_an_unknown_vendor_is_rejected(db_session):
    from app.crud import vendor_credit_import as crud
    with pytest.raises(crud.InvalidDecision):
        await crud.set_decision(db_session, qbo_vendor_id="V1",
                                qbo_display_name="ULINE", decision="mapped",
                                vendor_id=uuid.uuid4(), decided_by=uuid.uuid4(),
                                note=None)


@pytest.mark.anyio
async def test_ignore_clears_any_supplied_vendor(db_session):
    from app.crud import vendor_credit_import as crud
    p = _partner()
    db_session.add(p)
    await db_session.flush()
    m = await crud.set_decision(db_session, qbo_vendor_id="V9",
                                qbo_display_name="Jane Doe",
                                decision="ignored_employee", vendor_id=p.id,
                                decided_by=uuid.uuid4(), note="staff")
    await db_session.flush()
    assert m.decision == "ignored_employee"
    assert m.vendor_id is None


@pytest.mark.anyio
async def test_a_decision_can_be_corrected(db_session):
    from app.crud import vendor_credit_import as crud
    p = _partner()
    db_session.add(p)
    await db_session.flush()

    await crud.set_decision(db_session, qbo_vendor_id="V1", qbo_display_name="X",
                            decision="ignored_other", vendor_id=None,
                            decided_by=uuid.uuid4(), note="not ours")
    await db_session.flush()
    m = await crud.set_decision(db_session, qbo_vendor_id="V1",
                                qbo_display_name="X", decision="mapped",
                                vendor_id=p.id, decided_by=uuid.uuid4(), note=None)
    await db_session.flush()
    assert m.decision == "mapped"
    assert m.vendor_id == p.id


@pytest.mark.anyio
async def test_unknown_decision_value_is_rejected(db_session):
    from app.crud import vendor_credit_import as crud
    with pytest.raises(crud.InvalidDecision):
        await crud.set_decision(db_session, qbo_vendor_id="V1",
                                qbo_display_name="X", decision="maybe",
                                vendor_id=None, decided_by=uuid.uuid4(), note=None)
```

- [ ] **Step 2: Run and watch them fail**

Run: `cd finance-api && TEST_PG_PASSWORD=7c0a03bb8c2afef690d1852f8dc3a0195932db5f0f1670e9 TEST_FINANCE_DB=finance_test_vc_c3 python -m pytest tests/test_vendor_credit_import.py -v -k decision`

Expected: FAIL — `AttributeError: module 'app.crud.vendor_credit_import' has no attribute 'set_decision'`

- [ ] **Step 3: Implement it**

Append to `finance-api/app/crud/vendor_credit_import.py`:

```python
from datetime import datetime, timezone

from app.models.qbo_vendor_map import (
    IGNORED_EMPLOYEE, IGNORED_OTHER, MAPPED,
)

_VALID_DECISIONS = (MAPPED, IGNORED_EMPLOYEE, IGNORED_OTHER, PENDING)


class InvalidDecision(Exception):
    """The requested mapping decision is not usable."""


async def set_decision(
    db: AsyncSession, *, qbo_vendor_id: str, qbo_display_name: str | None,
    decision: str, vendor_id: uuid.UUID | None, decided_by: uuid.UUID,
    note: str | None,
) -> QboVendorMap:
    if decision not in _VALID_DECISIONS:
        raise InvalidDecision(f"Unknown decision '{decision}'")

    if decision == MAPPED:
        if vendor_id is None:
            raise InvalidDecision("Mapping to a vendor requires a vendor_id")
        partner = (await db.execute(
            select(BusinessPartner).where(
                BusinessPartner.id == vendor_id,
                BusinessPartner.is_supplier.is_(True),
            )
        )).scalars().first()
        if partner is None:
            raise InvalidDecision("That vendor does not exist or is not a supplier")
    else:
        # An ignore decision must not carry a target; storing one would be a
        # contradiction the import would then have to interpret.
        vendor_id = None

    row = (await db.execute(
        select(QboVendorMap).where(QboVendorMap.qbo_vendor_id == qbo_vendor_id)
    )).scalars().first()
    if row is None:
        row = QboVendorMap(qbo_vendor_id=qbo_vendor_id)
        db.add(row)

    row.qbo_display_name = qbo_display_name
    row.decision = decision
    row.vendor_id = vendor_id
    row.decided_by = decided_by
    row.decided_at = datetime.now(timezone.utc)
    row.note = note
    await db.flush()
    return row
```

- [ ] **Step 4: Run the tests**

Run: `cd finance-api && TEST_PG_PASSWORD=7c0a03bb8c2afef690d1852f8dc3a0195932db5f0f1670e9 TEST_FINANCE_DB=finance_test_vc_c3 python -m pytest tests/test_vendor_credit_import.py -v`

Expected: all pass (18 by now).

- [ ] **Step 5: Commit**

```bash
git add finance-api/app/crud/vendor_credit_import.py finance-api/tests/test_vendor_credit_import.py
git commit -m "feat(finance): record and correct QBO vendor mapping decisions"
```

---

## Task 4: Import execution

**Files:**
- Modify: `finance-api/app/crud/vendor_credit_import.py`
- Test: `finance-api/tests/test_vendor_credit_import.py`

**Interfaces:**
- Consumes: `set_decision` / `list_candidates`; `VendorCredit` and `_positive()` from `app/crud/vendor_credit.py`; `next_number` from `app/crud/_numbering.py`.
- Produces:
  - `async def cutover_sync_run(db) -> QboSyncRun | None` — the most recent successful FULL RELOAD.
  - `async def run_import(db, *, imported_by: uuid.UUID) -> dict` — returns `{imported, skipped_unmapped, skipped_existing, drifted, total_amount}`.

**Rules that decide whether money is right:**
- `total_amount` comes from **`balance`**, the unapplied remainder. `total_amt` is retained in `notes` for reference only.
- Only credits whose QBO vendor has `decision = 'mapped'` are imported. Everything else is counted and reported, never imported.
- `deleted_at IS NOT NULL` or `balance <= 0` are skipped.
- Imported rows are `source='qbo_import'`, `source_ref=<qbo_id>`, `opening_balance=True`, `status='available'`, `applied_amount=0`, `remaining_amount=total_amount`.
- **Idempotent:** a `source_ref` already present is skipped and counted in `skipped_existing`, never re-imported and never updated.
- `imported_from_sync_run_id` is the id from `cutover_sync_run`, so every imported row records which FULL RELOAD it came from.

**The empty-DocNumber trap — this is why this task exists as its own unit.** `vendor_credits.vendor_credit_number` is `NOT NULL`, and Phase A's partial unique index on `(vendor_id, vendor_credit_number) WHERE status <> 'void'` is **not** scoped by source. QBO permits an empty `DocNumber`. Two such credits for the same vendor would therefore collide on the second insert. **Synthesise a number when `doc_number` is blank**: use `QBO-<qbo_id>`, which is unique by construction. Do not fall back to an empty string, and do not disable the index.

- [ ] **Step 1: Write the failing tests**

Append to `finance-api/tests/test_vendor_credit_import.py`:

```python
async def _map_vendor(db, qbo_vendor_id, partner):
    from app.crud import vendor_credit_import as crud
    await crud.set_decision(db, qbo_vendor_id=qbo_vendor_id,
                            qbo_display_name=partner.name, decision="mapped",
                            vendor_id=partner.id, decided_by=uuid.uuid4(), note=None)


@pytest.mark.anyio
async def test_import_takes_balance_not_face_value(db_session):
    from app.crud import vendor_credit_import as crud
    from app.models.vendor_credit import VendorCredit
    p = _partner()
    db_session.add_all([p, _qbo_vendor(qbo_id="V1"),
                        _qbo_credit(qbo_id="Q1", counterparty_id="V1",
                                    total_amt=Decimal("500.00"),
                                    balance=Decimal("30.00"))])
    await db_session.flush()
    await _map_vendor(db_session, "V1", p)

    res = await crud.run_import(db_session, imported_by=uuid.uuid4())
    await db_session.flush()

    assert res["imported"] == 1
    vc = (await db_session.execute(sa.select(VendorCredit).where(
        VendorCredit.source_ref == "Q1"))).scalar_one()
    assert vc.total_amount == Decimal("30.00")
    assert vc.remaining_amount == Decimal("30.00")
    assert vc.applied_amount == Decimal("0.00")
    assert vc.status == "available"
    assert vc.opening_balance is True
    assert vc.source == "qbo_import"
    assert vc.vendor_id == p.id


@pytest.mark.anyio
async def test_unmapped_vendors_are_not_imported(db_session):
    from app.crud import vendor_credit_import as crud
    db_session.add_all([_qbo_vendor(qbo_id="V1"),
                        _qbo_credit(qbo_id="Q1", counterparty_id="V1")])
    await db_session.flush()

    res = await crud.run_import(db_session, imported_by=uuid.uuid4())
    assert res["imported"] == 0
    assert res["skipped_unmapped"] == 1


@pytest.mark.anyio
async def test_ignored_vendors_are_not_imported(db_session):
    from app.crud import vendor_credit_import as crud
    db_session.add_all([_qbo_vendor(qbo_id="V9", display_name="Jane Doe"),
                        _qbo_credit(qbo_id="Q9", counterparty_id="V9")])
    await db_session.flush()
    await crud.set_decision(db_session, qbo_vendor_id="V9",
                            qbo_display_name="Jane Doe",
                            decision="ignored_employee", vendor_id=None,
                            decided_by=uuid.uuid4(), note=None)

    res = await crud.run_import(db_session, imported_by=uuid.uuid4())
    assert res["imported"] == 0


@pytest.mark.anyio
async def test_import_is_idempotent(db_session):
    from app.crud import vendor_credit_import as crud
    from app.models.vendor_credit import VendorCredit
    p = _partner()
    db_session.add_all([p, _qbo_vendor(qbo_id="V1"),
                        _qbo_credit(qbo_id="Q1", counterparty_id="V1")])
    await db_session.flush()
    await _map_vendor(db_session, "V1", p)

    first = await crud.run_import(db_session, imported_by=uuid.uuid4())
    await db_session.flush()
    second = await crud.run_import(db_session, imported_by=uuid.uuid4())
    await db_session.flush()

    assert first["imported"] == 1
    assert second["imported"] == 0
    assert second["skipped_existing"] == 1
    count = (await db_session.execute(sa.select(sa.func.count()).select_from(
        VendorCredit).where(VendorCredit.source_ref == "Q1"))).scalar_one()
    assert count == 1


@pytest.mark.anyio
async def test_blank_doc_numbers_do_not_collide(db_session):
    """QBO allows an empty DocNumber; two such credits for one vendor would
    otherwise collide on the (vendor_id, vendor_credit_number) unique index."""
    from app.crud import vendor_credit_import as crud
    from app.models.vendor_credit import VendorCredit
    p = _partner()
    db_session.add_all([
        p, _qbo_vendor(qbo_id="V1"),
        _qbo_credit(qbo_id="Q1", counterparty_id="V1", doc_number=None),
        _qbo_credit(qbo_id="Q2", counterparty_id="V1", doc_number=""),
    ])
    await db_session.flush()
    await _map_vendor(db_session, "V1", p)

    res = await crud.run_import(db_session, imported_by=uuid.uuid4())
    await db_session.flush()

    assert res["imported"] == 2
    numbers = set((await db_session.execute(sa.select(
        VendorCredit.vendor_credit_number).where(
        VendorCredit.source == "qbo_import"))).scalars().all())
    assert numbers == {"QBO-Q1", "QBO-Q2"}


@pytest.mark.anyio
async def test_zero_balance_and_deleted_are_skipped(db_session):
    from app.crud import vendor_credit_import as crud
    p = _partner()
    db_session.add_all([
        p, _qbo_vendor(qbo_id="V1"),
        _qbo_credit(qbo_id="Q1", counterparty_id="V1", balance=Decimal("0.00")),
        _qbo_credit(qbo_id="Q2", counterparty_id="V1",
                    deleted_at=datetime.now(timezone.utc)),
    ])
    await db_session.flush()
    await _map_vendor(db_session, "V1", p)

    res = await crud.run_import(db_session, imported_by=uuid.uuid4())
    assert res["imported"] == 0


@pytest.mark.anyio
async def test_imported_rows_record_the_cutover_run(db_session):
    from app.crud import vendor_credit_import as crud
    from app.models.qbo import QboSyncRun
    from app.models.vendor_credit import VendorCredit
    run = QboSyncRun(mode="full", status="success",
                     started_at=datetime.now(timezone.utc),
                     finished_at=datetime.now(timezone.utc),
                     counters={}, watermarks={})
    p = _partner()
    db_session.add_all([run, p, _qbo_vendor(qbo_id="V1"),
                        _qbo_credit(qbo_id="Q1", counterparty_id="V1")])
    await db_session.flush()
    await _map_vendor(db_session, "V1", p)

    await crud.run_import(db_session, imported_by=uuid.uuid4())
    await db_session.flush()

    vc = (await db_session.execute(sa.select(VendorCredit).where(
        VendorCredit.source_ref == "Q1"))).scalar_one()
    assert vc.imported_from_sync_run_id == run.id
```

- [ ] **Step 2: Run and watch them fail**

Run: `cd finance-api && TEST_PG_PASSWORD=7c0a03bb8c2afef690d1852f8dc3a0195932db5f0f1670e9 TEST_FINANCE_DB=finance_test_vc_c4 python -m pytest tests/test_vendor_credit_import.py -v -k import`

Expected: FAIL — `AttributeError: module 'app.crud.vendor_credit_import' has no attribute 'run_import'`

- [ ] **Step 3: Implement the import**

Append to `finance-api/app/crud/vendor_credit_import.py`:

```python
from app.crud._numbering import next_number
from app.crud.vendor_credit import _positive
from app.models.qbo import QboSyncRun
from app.models.vendor_credit import AVAILABLE, VendorCredit

_SOURCE_QBO = "qbo_import"


async def cutover_sync_run(db: AsyncSession) -> QboSyncRun | None:
    """The most recent successful FULL RELOAD — the opening-balance baseline.

    Derived rather than hand-entered so nobody has to remember a date; the
    imported rows each record which run they came from.
    """
    return (await db.execute(
        select(QboSyncRun)
        .where(QboSyncRun.mode == "full", QboSyncRun.status == "success")
        .order_by(QboSyncRun.finished_at.desc())
        .limit(1)
    )).scalars().first()


async def run_import(db: AsyncSession, *, imported_by: uuid.UUID) -> dict:
    run = await cutover_sync_run(db)

    credits = (await db.execute(
        select(QboVendorCredit).where(
            QboVendorCredit.deleted_at.is_(None),
            QboVendorCredit.balance > _ZERO,
        ).order_by(QboVendorCredit.txn_date, QboVendorCredit.qbo_id)
    )).scalars().all()
    if not credits:
        return {"imported": 0, "skipped_unmapped": 0, "skipped_existing": 0,
                "drifted": 0, "total_amount": _ZERO}

    decisions = {
        m.qbo_vendor_id: m for m in (await db.execute(select(QboVendorMap))).scalars().all()
    }
    already = {
        r for r in (await db.execute(
            select(VendorCredit.source_ref).where(VendorCredit.source == _SOURCE_QBO)
        )).scalars().all() if r
    }
    partners = {
        p.id: p for p in (await db.execute(select(BusinessPartner))).scalars().all()
    }

    imported = skipped_unmapped = skipped_existing = 0
    total = _ZERO
    for c in credits:
        if c.qbo_id in already:
            skipped_existing += 1
            continue
        m = decisions.get(c.counterparty_id or "")
        if m is None or m.decision != MAPPED or m.vendor_id is None:
            skipped_unmapped += 1
            continue
        partner = partners.get(m.vendor_id)
        if partner is None:
            skipped_unmapped += 1
            continue

        amount = _positive(c.balance)
        # QBO permits an empty DocNumber. vendor_credit_number is NOT NULL and
        # participates in a unique index that is NOT scoped by source, so two
        # blank-numbered credits for one vendor would collide. Synthesise from
        # the QBO id, which is unique by construction.
        doc_no = (c.doc_number or "").strip() or f"QBO-{c.qbo_id}"

        db.add(VendorCredit(
            credit_number=await next_number(
                db, VendorCredit.credit_number, f"VC-{date.today():%Y%m%d}-", 4),
            vendor_id=partner.id,
            vendor_name=partner.name,
            vendor_credit_number=doc_no[:100],
            credit_date=date.fromisoformat(c.txn_date) if c.txn_date else date.today(),
            currency=c.currency or "CAD",
            amount=amount,
            tax_amount=_ZERO,
            total_amount=amount,
            applied_amount=_ZERO,
            remaining_amount=amount,
            status=AVAILABLE,
            line_items=[],
            notes=f"Imported from QBO vendor credit {c.qbo_id}; "
                  f"original face value {c.total_amt}",
            source=_SOURCE_QBO,
            source_ref=c.qbo_id,
            opening_balance=True,
            imported_from_sync_run_id=run.id if run else None,
            uploaded_by=imported_by,
            uploaded_at=datetime.now(timezone.utc),
        ))
        imported += 1
        total += amount

    await db.flush()
    return {"imported": imported, "skipped_unmapped": skipped_unmapped,
            "skipped_existing": skipped_existing, "drifted": 0,
            "total_amount": total}
```

Add `date` to the module's `datetime` import.

- [ ] **Step 4: Run the tests**

Run: `cd finance-api && TEST_PG_PASSWORD=7c0a03bb8c2afef690d1852f8dc3a0195932db5f0f1670e9 TEST_FINANCE_DB=finance_test_vc_c4 python -m pytest tests/test_vendor_credit_import.py -v`

Expected: all pass (25 by now).

- [ ] **Step 5: Commit**

```bash
git add finance-api/app/crud/vendor_credit_import.py finance-api/tests/test_vendor_credit_import.py
git commit -m "feat(finance): import QBO vendor credits as opening balances"
```

---

## Task 5: Drift detection

**Files:**
- Modify: `finance-api/app/crud/vendor_credit_import.py`
- Test: `finance-api/tests/test_vendor_credit_import.py`

**Interfaces:**
- Produces: `async def list_drift(db) -> list[dict]` — already-imported credits whose QBO `balance` no longer equals what was imported. Each entry: `source_ref`, `credit_number`, `vendor_name`, `imported_total`, `qbo_balance`, `applied_amount`.
- `run_import`'s `drifted` count comes from this.

**Why detection and not correction.** After cutover, a vendor credit should be consumed only in EPMS. If someone applies one inside QBO as well, its `balance` there drops while ours does not — and both ledgers then believe they can spend it. Drift is how that violation becomes visible. **Never auto-correct it:** our row may already have Phase B applications recorded against it, and overwriting would erase them.

- [ ] **Step 1: Write the failing tests**

Append to `finance-api/tests/test_vendor_credit_import.py`:

```python
@pytest.mark.anyio
async def test_no_drift_when_balances_agree(db_session):
    from app.crud import vendor_credit_import as crud
    p = _partner()
    db_session.add_all([p, _qbo_vendor(qbo_id="V1"),
                        _qbo_credit(qbo_id="Q1", counterparty_id="V1",
                                    balance=Decimal("40.00"))])
    await db_session.flush()
    await _map_vendor(db_session, "V1", p)
    await crud.run_import(db_session, imported_by=uuid.uuid4())
    await db_session.flush()

    assert await crud.list_drift(db_session) == []


@pytest.mark.anyio
async def test_drift_is_reported_not_corrected(db_session):
    from app.crud import vendor_credit_import as crud
    from app.models.qbo import QboVendorCredit
    from app.models.vendor_credit import VendorCredit
    p = _partner()
    qc = _qbo_credit(qbo_id="Q1", counterparty_id="V1", balance=Decimal("40.00"))
    db_session.add_all([p, _qbo_vendor(qbo_id="V1"), qc])
    await db_session.flush()
    await _map_vendor(db_session, "V1", p)
    await crud.run_import(db_session, imported_by=uuid.uuid4())
    await db_session.flush()

    # Someone applied this credit inside QBO after cutover.
    qc.balance = Decimal("10.00")
    await db_session.flush()

    drift = await crud.list_drift(db_session)
    assert len(drift) == 1
    assert drift[0]["source_ref"] == "Q1"
    assert drift[0]["imported_total"] == Decimal("40.00")
    assert drift[0]["qbo_balance"] == Decimal("10.00")

    # Our row is untouched — correcting it could erase recorded applications.
    vc = (await db_session.execute(sa.select(VendorCredit).where(
        VendorCredit.source_ref == "Q1"))).scalar_one()
    assert vc.total_amount == Decimal("40.00")


@pytest.mark.anyio
async def test_drift_ignores_a_credit_we_consumed_ourselves(db_session):
    """Our own applications change applied/remaining, never total_amount, so a
    credit spent in EPMS must not look like drift."""
    from app.crud import vendor_credit_import as crud
    from app.models.vendor_credit import VendorCredit
    p = _partner()
    db_session.add_all([p, _qbo_vendor(qbo_id="V1"),
                        _qbo_credit(qbo_id="Q1", counterparty_id="V1",
                                    balance=Decimal("40.00"))])
    await db_session.flush()
    await _map_vendor(db_session, "V1", p)
    await crud.run_import(db_session, imported_by=uuid.uuid4())
    await db_session.flush()

    vc = (await db_session.execute(sa.select(VendorCredit).where(
        VendorCredit.source_ref == "Q1"))).scalar_one()
    vc.applied_amount = Decimal("15.00")
    vc.remaining_amount = Decimal("25.00")
    await db_session.flush()

    assert await crud.list_drift(db_session) == []
```

- [ ] **Step 2: Run and watch them fail**

Run: `cd finance-api && TEST_PG_PASSWORD=7c0a03bb8c2afef690d1852f8dc3a0195932db5f0f1670e9 TEST_FINANCE_DB=finance_test_vc_c5 python -m pytest tests/test_vendor_credit_import.py -v -k drift`

Expected: FAIL — `AttributeError: module 'app.crud.vendor_credit_import' has no attribute 'list_drift'`

- [ ] **Step 3: Implement it**

Append to `finance-api/app/crud/vendor_credit_import.py`:

```python
async def list_drift(db: AsyncSession) -> list[dict]:
    """Imported credits whose QBO balance no longer matches what we imported.

    Compares against total_amount, NOT remaining_amount: our own Phase B
    applications move applied/remaining and leave total alone, so a credit we
    spent ourselves is not drift. A divergence here means the credit was also
    applied inside QBO after cutover — the one thing the operating agreement
    says must not happen, and the reason both ledgers would otherwise believe
    they can spend it.

    Reports only. Never corrects: our row may carry applications already.
    """
    rows = (await db.execute(
        select(VendorCredit, QboVendorCredit)
        .join(QboVendorCredit, QboVendorCredit.qbo_id == VendorCredit.source_ref)
        .where(
            VendorCredit.source == _SOURCE_QBO,
            QboVendorCredit.balance != VendorCredit.total_amount,
        )
        .order_by(VendorCredit.credit_number)
    )).all()

    return [{
        "source_ref": vc.source_ref,
        "credit_number": vc.credit_number,
        "vendor_name": vc.vendor_name,
        "imported_total": vc.total_amount,
        "qbo_balance": qc.balance,
        "applied_amount": vc.applied_amount,
    } for vc, qc in rows]
```

Then change `run_import`'s return so `drifted` reports the real count:

```python
    drift = await list_drift(db)
    return {"imported": imported, "skipped_unmapped": skipped_unmapped,
            "skipped_existing": skipped_existing, "drifted": len(drift),
            "total_amount": total}
```

- [ ] **Step 4: Run the tests**

Run: `cd finance-api && TEST_PG_PASSWORD=7c0a03bb8c2afef690d1852f8dc3a0195932db5f0f1670e9 TEST_FINANCE_DB=finance_test_vc_c5 python -m pytest tests/test_vendor_credit_import.py -v`

Expected: all pass (28 by now).

- [ ] **Step 5: Commit**

```bash
git add finance-api/app/crud/vendor_credit_import.py finance-api/tests/test_vendor_credit_import.py
git commit -m "feat(finance): detect QBO opening-balance drift without correcting it"
```

---

## Task 6: HTTP surface

**Files:**
- Create: `finance-api/app/schemas/vendor_credit_import.py`
- Create: `finance-api/app/api/v1/vendor_credit_import.py`
- Modify: `finance-api/app/api/v1/__init__.py`
- Test: `finance-api/tests/test_vendor_credit_import.py`

**Interfaces:**
- Produces, all under `/finance/v1/vendor-credit-import`:
  - `GET /candidates` → the workbench rows
  - `POST /decision` (body `{qbo_vendor_id, qbo_display_name, decision, vendor_id, note}`) → the stored decision; **400** on `InvalidDecision`
  - `GET /drift` → the drift list
  - `POST /run` → the import summary
  - `GET /cutover` → `{sync_run_id, finished_at}` or nulls

**Gating:** every route requires `epms.vendor_credit.manage`, the permission Phase A registered, via `require_permission` imported from `app.core.authz`. This is a one-off operational action that creates spendable money; it is not a read anyone should have.

Copy the `client` fixture and `_h()` JWT helper from `tests/test_vendor_credit.py`. `require_permission` short-circuits `role == "system_admin"` with no DB lookup, so `_h("system_admin")` clears the gate in tests.

- [ ] **Step 1: Write the failing tests**

Append to `finance-api/tests/test_vendor_credit_import.py`:

```python
@pytest.mark.anyio
async def test_candidates_endpoint_requires_the_permission(client, db_session):
    r = await client.get("/finance/v1/vendor-credit-import/candidates",
                         headers=_h("ap_clerk"))
    assert r.status_code == 403


@pytest.mark.anyio
async def test_candidates_endpoint_returns_rows(client, db_session):
    db_session.add_all([_qbo_vendor(qbo_id="V1"), _qbo_credit(counterparty_id="V1")])
    await db_session.flush()
    r = await client.get("/finance/v1/vendor-credit-import/candidates",
                         headers=_h("system_admin"))
    assert r.status_code == 200
    assert any(row["qbo_vendor_id"] == "V1" for row in r.json())


@pytest.mark.anyio
async def test_decision_endpoint_rejects_a_mapping_without_a_vendor(client):
    r = await client.post("/finance/v1/vendor-credit-import/decision",
                          json={"qbo_vendor_id": "V1", "qbo_display_name": "X",
                                "decision": "mapped", "vendor_id": None},
                          headers=_h("system_admin"))
    assert r.status_code == 400


@pytest.mark.anyio
async def test_run_endpoint_returns_a_summary(client, db_session):
    db_session.add_all([_qbo_vendor(qbo_id="V1"), _qbo_credit(counterparty_id="V1")])
    await db_session.flush()
    r = await client.post("/finance/v1/vendor-credit-import/run",
                          headers=_h("system_admin"))
    assert r.status_code == 200
    body = r.json()
    assert set(body) >= {"imported", "skipped_unmapped", "skipped_existing",
                         "drifted", "total_amount"}
    assert body["skipped_unmapped"] == 1
```

- [ ] **Step 2: Run and watch them fail**

Run: `cd finance-api && TEST_PG_PASSWORD=7c0a03bb8c2afef690d1852f8dc3a0195932db5f0f1670e9 TEST_FINANCE_DB=finance_test_vc_c6 python -m pytest tests/test_vendor_credit_import.py -v -k endpoint`

Expected: FAIL — 404, the router is not registered.

- [ ] **Step 3: Write the schemas**

Create `finance-api/app/schemas/vendor_credit_import.py`:

```python
import uuid
from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel, Field


class ImportCandidate(BaseModel):
    qbo_vendor_id: str
    qbo_display_name: str
    credit_count: int
    credit_total: Decimal
    currency: str
    decision: str
    vendor_id: uuid.UUID | None
    suggested_vendor_id: uuid.UUID | None
    suggested_vendor_name: str | None
    tags: list[str]


class DecisionRequest(BaseModel):
    qbo_vendor_id: str = Field(min_length=1, max_length=20)
    qbo_display_name: str | None = None
    decision: str
    vendor_id: uuid.UUID | None = None
    note: str | None = None


class DriftRow(BaseModel):
    source_ref: str
    credit_number: str
    vendor_name: str
    imported_total: Decimal
    qbo_balance: Decimal
    applied_amount: Decimal


class ImportSummary(BaseModel):
    imported: int
    skipped_unmapped: int
    skipped_existing: int
    drifted: int
    total_amount: Decimal


class CutoverInfo(BaseModel):
    sync_run_id: uuid.UUID | None
    finished_at: datetime | None
```

- [ ] **Step 4: Write the router**

Create `finance-api/app/api/v1/vendor_credit_import.py`:

```python
"""QBO opening-balance import — the mapping workbench and the import run.

Every route is gated on epms.vendor_credit.manage. This is a one-off operational
action that creates spendable money; it is not a read anyone should have.
"""
import uuid

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.authz import require_permission
from app.crud import vendor_credit_import as crud
from app.db.base import get_db
from app.schemas.vendor_credit_import import (
    CutoverInfo, DecisionRequest, DriftRow, ImportCandidate, ImportSummary,
)

router = APIRouter(prefix="/vendor-credit-import", tags=["vendor-credit-import"])

_MANAGE_KEY = "epms.vendor_credit.manage"
_gate = require_permission(_MANAGE_KEY)


@router.get("/candidates", response_model=list[ImportCandidate])
async def candidates(user: dict = Depends(_gate),
                     db: AsyncSession = Depends(get_db)):
    return await crud.list_candidates(db)


@router.post("/decision")
async def decision(body: DecisionRequest, user: dict = Depends(_gate),
                   db: AsyncSession = Depends(get_db)):
    try:
        row = await crud.set_decision(
            db, qbo_vendor_id=body.qbo_vendor_id,
            qbo_display_name=body.qbo_display_name, decision=body.decision,
            vendor_id=body.vendor_id, decided_by=uuid.UUID(str(user["sub"])),
            note=body.note,
        )
    except crud.InvalidDecision as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    await db.commit()
    return {"qbo_vendor_id": row.qbo_vendor_id, "decision": row.decision,
            "vendor_id": row.vendor_id}


@router.get("/drift", response_model=list[DriftRow])
async def drift(user: dict = Depends(_gate), db: AsyncSession = Depends(get_db)):
    return await crud.list_drift(db)


@router.post("/run", response_model=ImportSummary)
async def run(user: dict = Depends(_gate), db: AsyncSession = Depends(get_db)):
    summary = await crud.run_import(db, imported_by=uuid.UUID(str(user["sub"])))
    await db.commit()
    return summary


@router.get("/cutover", response_model=CutoverInfo)
async def cutover(user: dict = Depends(_gate), db: AsyncSession = Depends(get_db)):
    run_row = await crud.cutover_sync_run(db)
    return CutoverInfo(
        sync_run_id=run_row.id if run_row else None,
        finished_at=run_row.finished_at if run_row else None,
    )
```

- [ ] **Step 5: Register the router**

In `finance-api/app/api/v1/__init__.py`, add the import alongside the others and the registration at the end of the `include_router` block:

```python
from app.api.v1.vendor_credit_import import router as vendor_credit_import_router
```
```python
api_router.include_router(vendor_credit_import_router)
```

- [ ] **Step 6: Run the tests**

Run: `cd finance-api && TEST_PG_PASSWORD=7c0a03bb8c2afef690d1852f8dc3a0195932db5f0f1670e9 TEST_FINANCE_DB=finance_test_vc_c6 python -m pytest tests/test_vendor_credit_import.py -v`

Expected: all pass (32 by now). Also re-run `tests/test_vendor_credit.py` and `tests/test_vendor_credit_netting.py` with the same env — registering a router must not disturb Phases A or B.

- [ ] **Step 7: Commit**

```bash
git add finance-api/app/schemas/vendor_credit_import.py finance-api/app/api/v1/vendor_credit_import.py finance-api/app/api/v1/__init__.py finance-api/tests/test_vendor_credit_import.py
git commit -m "feat(finance): QBO vendor credit import API — candidates, decisions, run, drift"
```

---

## Task 7: The mapping workbench page

**Files:**
- Create: `finance/src/pages/finance/QboVendorCreditImportPage.tsx`
- Modify: the finance app's route registry (find it with `grep -rn "QboMirrorPage" finance/src`)

**Interfaces:**
- Consumes the five endpoints from Task 6.

**Money fields arrive as Decimal-serialised strings.** Type them `string`; put every arithmetic operation and comparison through `Number()`.

**The page's job is to make a few hundred decisions fast without making any of them automatically.** Four sections, each showing row count and total value:

1. **Ready to import** — `decision = 'mapped'`
2. **Needs mapping** — `decision = 'pending'`, each row showing the QBO name, credit total, and its advisory tags (`likely_employee`, `no_bills`, `similar`, `ambiguous`), with actions `Map to…` (an EPMS vendor search, pre-filled with `suggested_vendor_name` when present), `Ignore — employee`, `Ignore — other`
3. **Ignored** — reversible
4. **Imported** — after a run

Plus a **Drift** panel from `GET /drift`, and a header showing the cutover run from `GET /cutover`.

**A pre-filled suggestion must still require a click.** Do not auto-submit a decision because a suggestion exists, and do not offer a "map all suggested" bulk action — that would re-introduce automatic mapping through the back door, which is the one thing this whole design exists to prevent.

- [ ] **Step 1: Measure the frontend baseline first**

Run:
```bash
cd finance && npx tsc -p tsconfig.app.json --noEmit --ignoreDeprecations 6.0 2>&1 | grep -c "error TS"
```

> **The gate command matters.** `finance/tsconfig.json` is a solution-style config
> (`"files": []` plus `references`), so a bare `npx tsc --noEmit` compiles **zero files** and
> always reports 0 — a false pass. The project must be named explicitly. TypeScript here is
> 6.0.3, which errors on the deprecated `baseUrl` unless `--ignoreDeprecations 6.0` is passed.
> With the correct command tsc compiles 64 source files. **The measured baseline is 0 errors**
> (controller-verified 2026-08-07); do not accept a 0 that came from the bare command.

Record the number in your report. If it prints `0`, check the raw output for real diagnostics — a missing `node_modules` makes tsc print an install prompt, which greps to zero. Run `npm ci` in `finance/` if so, then measure again. Do not copy a baseline from anywhere.

- [ ] **Step 2: Add the client calls**

In the finance app's services layer, next to the existing QBO calls:

```ts
export interface ImportCandidate {
  qbo_vendor_id: string
  qbo_display_name: string
  credit_count: number
  /** Decimal-as-string. Number() before arithmetic. */
  credit_total: string
  currency: string
  decision: 'pending' | 'mapped' | 'ignored_employee' | 'ignored_other'
  vendor_id: string | null
  suggested_vendor_id: string | null
  suggested_vendor_name: string | null
  tags: string[]
}

export interface ImportSummary {
  imported: number
  skipped_unmapped: number
  skipped_existing: number
  drifted: number
  total_amount: string
}

export const importApi = {
  candidates: () => financeApi.get<ImportCandidate[]>('/vendor-credit-import/candidates'),
  drift:      () => financeApi.get<DriftRow[]>('/vendor-credit-import/drift'),
  cutover:    () => financeApi.get<{ sync_run_id: string | null; finished_at: string | null }>('/vendor-credit-import/cutover'),
  decide:     (body: { qbo_vendor_id: string; qbo_display_name: string; decision: string; vendor_id: string | null; note?: string }) =>
    financeApi.post('/vendor-credit-import/decision', body),
  run:        () => financeApi.post<ImportSummary>('/vendor-credit-import/run'),
}
```

Define `DriftRow` to match the backend's `DriftRow` schema, with all four money fields typed `string`.

- [ ] **Step 3: Build the page**

Create `finance/src/pages/finance/QboVendorCreditImportPage.tsx` following the structure above. Group the candidates client-side by `decision`. Render each section as a table with a count and a summed total (`reduce` with `Number(c.credit_total)`). Show the tags as small labels beside the name.

The `Map to…` control is an EPMS vendor picker; reuse whatever vendor-search component the finance app already has rather than writing a new one — find it with `grep -rn "vendor" finance/src/components`. If none exists, a filtered `<select>` over the partner list is acceptable for a one-off admin page.

- [ ] **Step 4: Wire the actions**

Each action calls `importApi.decide(...)` and then refetches candidates. `Run import` calls `importApi.run()` and shows the returned summary — including `skipped_unmapped`, so the operator can see what was left behind rather than assuming everything went in.

Surface failures: a 400 from `decide` carries a `detail` explaining why the decision was rejected. Follow whatever error idiom the page's neighbours already use.

- [ ] **Step 5: Register the route**

Add the page next to the existing QBO mirror route, matching how that entry is declared.

- [ ] **Step 6: Type-check**

Run: `cd finance && npx tsc -p tsconfig.app.json --noEmit --ignoreDeprecations 6.0 2>&1 | grep -c "error TS"`

Expected: the baseline from Step 1, unchanged.

- [ ] **Step 7: Commit**

```bash
git add finance/src
git commit -m "feat(finance-ui): QBO vendor credit import workbench"
```

---

## Phase C Done Criteria

- [ ] `tests/test_vendor_credit_import.py` passes in full
- [ ] `tests/test_vendor_credit.py` and `tests/test_vendor_credit_netting.py` still pass at their Phase A/B counts
- [ ] `finance` tsc equals the baseline measured in Task 7 Step 1
- [ ] One uncontended full finance-api suite run at the end of the phase (45 minutes; run it alone)
- [ ] No code path writes `decision` without an explicit human action — confirm by reading every `set_decision` call site

## Operating agreement — must go on the deploy checklist

**After cutover, vendor credits are applied only in EPMS. Nobody applies one inside QBO.** Two independently-decrementing ledgers double-spend: QBO's `balance` drops while ours does not, and both then believe the credit is available. Task 5's drift panel *detects* violations; it cannot prevent them. This has to be agreed with whoever still works in QBO before the import is run.

## Deploy notes

- Run a QBO FULL RELOAD first. The import reads whatever the mirror currently holds, and stamps each row with the run it came from; importing against a stale mirror imports stale balances.
- The import is idempotent, so it is safe to run, resolve the unmapped list, and run again.
- Phase A's permission step still applies: `seed_phase2_keys.py` in the identity container, then tick `epms.vendor_credit.manage` in Portal → Access Control. Without it only `system_admin` can reach this workbench.
