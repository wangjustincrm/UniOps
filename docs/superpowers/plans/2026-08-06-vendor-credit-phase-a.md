# Vendor Credit — Phase A Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let AP record vendor Credit Notes end-to-end — detect them at upload, confirm the document type, store them as a finance-owned `vendor_credits` ledger, and review them on a dedicated EPMS page. **No money moves in this phase.**

**Architecture:** `finance-api` owns the table, the schemas, the CRUD and the API (`/finance/v1/vendor-credits`). The EPMS frontend calls finance-api directly through the existing `financeApi` client. `expense-api` gains a `document_type` field on its OCR response and accepts `"credit"` as an attachment source. Payment netting and QBO import are **out of scope** — they are Phases B and C.

**Tech Stack:** FastAPI · SQLAlchemy 2 async · Alembic · Pydantic v2 · pytest/pytest-asyncio (`asyncio_mode = auto`) · React 18 + TypeScript + TanStack Query + Tailwind

**Spec:** `docs/superpowers/specs/2026-08-06-vendor-credit-management-design.md`

## Global Constraints

- **All monetary columns in `vendor_credits` are positive.** `abs()` is applied in exactly one place — the finance-api write layer — backed by `CHECK (total_amount > 0)`. Never in the frontend, never in OCR.
- **Do not modify `expense-api`'s `_normalize_negative_quantities()`.** It already flips sign once; a second flip desyncs `qty × unit_price` from the line amount.
- **Do not modify `epms-api`'s `invoices` table or its `amount = Field(gt=0)` constraint.** It is retained deliberately as a backstop against a mis-classified credit reaching the payment queue.
- **UI copy is English only.** Code comments may be Chinese.
- New alembic revision must chain off `0029_qbo_mirror_phase2` — verified as finance-api's single head on 2026-08-06.
- Only one test suite may run against the shared test Postgres at a time.
- finance-api tests need: `TEST_FINANCE_DB=finance_test`, `TEST_PG_HOST`, `TEST_PG_USER`, `TEST_PG_PASSWORD`, `TEST_PG_PORT` (defaults: `localhost` / `epms` / `epms_dev` / `5432`).
- Work happens in worktree `c:/Project/uniops-vendor-credit` on branch `feature/vendor-credit`.

---

## File Structure

| File | Responsibility |
|---|---|
| `finance-api/app/models/vendor_credit.py` | **Create.** ORM model + status constants. |
| `finance-api/alembic/versions/0030_vendor_credits.py` | **Create.** Table, checks, indexes. |
| `finance-api/app/schemas/vendor_credit.py` | **Create.** Pydantic in/out contracts. |
| `finance-api/app/crud/vendor_credit.py` | **Create.** create / list / get / approve / reject / void. All business rules live here. |
| `finance-api/app/api/v1/vendor_credits.py` | **Create.** HTTP surface + permission gates only. |
| `finance-api/app/api/v1/__init__.py` | **Modify.** Register the router. |
| `finance-api/tests/test_vendor_credit.py` | **Create.** CRUD + API tests. |
| `identity-api/scripts/seed_phase2_keys.py` | **Modify.** Register `epms.vendor_credit.manage`. |
| `expense-api/app/services/ocr_service.py` | **Modify.** Add `document_type` to prompt + scalar fields. |
| `expense-api/tests/test_ocr_service.py` | **Modify.** Cover `document_type`. |
| `expense-api/app/api/v1/invoice_attachments.py` | **Modify.** Accept `invoice_source="credit"`. |
| `epms/src/lib/api.ts` | **Modify.** `financeApi` gains `post`. |
| `epms/src/services/vendorCredits.ts` | **Create.** Typed client for the new endpoints. |
| `epms/src/hooks/useVendorCredits.ts` | **Create.** TanStack Query hooks. |
| `epms/src/lib/invoice-parser.ts` | **Modify.** Surface `documentType`. |
| `epms/src/pages/invoices/InvoiceListPage.tsx` | **Modify.** Document-type switch + credit branch in the upload modal. |
| `epms/src/pages/vendorcredits/VendorCreditsPage.tsx` | **Create.** Review page. |
| `epms/src/app/routes.tsx` | **Modify.** Register `/vendor-credits`. |
| `epms/src/components/layout/Sidebar.tsx` | **Modify.** Nav entry. |
| `epms/src/components/layout/Header.tsx` | **Modify.** Page title map. |

---

## Task 1: `vendor_credits` model and migration

**Files:**
- Create: `finance-api/app/models/vendor_credit.py`
- Create: `finance-api/alembic/versions/0030_vendor_credits.py`
- Test: `finance-api/tests/test_vendor_credit.py`

**Interfaces:**
- Consumes: `app.db.base.Base`, `UUIDPrimaryKey`, `TimestampMixin` (already used by `app/models/payment.py`)
- Produces: `VendorCredit` ORM class; constants `PENDING_REVIEW`, `AVAILABLE`, `EXHAUSTED`, `VOID`

**Note on column scope:** all `vendor_credits` columns ship in this one migration, including `source` / `source_ref` / `opening_balance` / `imported_from_sync_run_id`, which only Phase C populates. They are included now because the partial unique index below depends on `source`, and splitting the table across two migrations buys nothing. The `vendor_credit_applications` table and `payment_records.credit_applied` are **not** created here — they belong to Phase B.

**Note on `vendor_id`:** plain `UUID` with no FK, matching `PaymentRecord.vendor_id` (`app/models/payment.py:22`). `business_partners` is an mdm-owned mirror in this service; adding a real FK from a finance-owned table to a mirror would make this migration depend on another service's schema.

- [ ] **Step 1: Write the failing model test**

Create `finance-api/tests/test_vendor_credit.py`:

```python
"""Vendor Credit — Phase A (record + review, no money movement)."""
import uuid
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from jose import jwt

from app.core.config import settings
from app.db.base import get_db
from app.main import app


def test_vendor_credit_model_mapped():
    from app.models.vendor_credit import VendorCredit
    cols = {c.name for c in VendorCredit.__table__.columns}
    assert {"credit_number", "vendor_id", "vendor_name", "vendor_credit_number",
            "credit_date", "currency", "amount", "tax_amount", "total_amount",
            "applied_amount", "remaining_amount", "status",
            "po_id", "po_number", "line_items", "file_name", "notes",
            "source", "source_ref", "opening_balance",
            "imported_from_sync_run_id",
            "uploaded_by", "uploaded_by_name", "uploaded_at",
            "reviewed_by", "reviewed_by_name", "reviewed_at",
            "review_note"} <= cols
    assert VendorCredit.__tablename__ == "vendor_credits"


def test_vendor_credit_status_constants():
    from app.models.vendor_credit import AVAILABLE, EXHAUSTED, PENDING_REVIEW, VOID
    assert (PENDING_REVIEW, AVAILABLE, EXHAUSTED, VOID) == (
        "pending_review", "available", "exhausted", "void")
```

- [ ] **Step 2: Run it and watch it fail**

Run: `cd finance-api && python -m pytest tests/test_vendor_credit.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.models.vendor_credit'`

- [ ] **Step 3: Write the model**

Create `finance-api/app/models/vendor_credit.py`:

```python
"""Vendor Credit — finance-owned credit-note ledger.

A vendor Credit Note reduces what we owe a vendor. It is NOT an invoice: it
never enters 3-way match and never becomes a PA. It accrues into a per-vendor
balance that Phase B nets off the next payment to that vendor.

Sign convention: every monetary column here is POSITIVE. A vendor document
printed as "-0.04" is stored as 0.04. "This is a credit" is expressed by the
table, not by a sign — mixed signs are a repeated source of defects in this
codebase (see the PO/GR/Invoice negative-unit-price series). abs() is applied
in app/crud/vendor_credit.py and nowhere else; CHECK (total_amount > 0) is the
backstop.
"""
import uuid
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import Boolean, CheckConstraint, Date, DateTime, Index, Numeric, String, Text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin, UUIDPrimaryKey

PENDING_REVIEW = "pending_review"
AVAILABLE = "available"
EXHAUSTED = "exhausted"
VOID = "void"

SOURCE_UPLOAD = "upload"
SOURCE_QBO_IMPORT = "qbo_import"


class VendorCredit(UUIDPrimaryKey, TimestampMixin, Base):
    __tablename__ = "vendor_credits"

    credit_number: Mapped[str] = mapped_column(String(30), unique=True, index=True, nullable=False)

    # No FK: business_partners is an mdm-owned mirror in this service, same as
    # PaymentRecord.vendor_id (app/models/payment.py:22).
    vendor_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False, index=True)
    vendor_name: Mapped[str] = mapped_column(String(255), nullable=False)

    # The vendor's own credit-note number, e.g. "11DJ-MFHX-N4JG".
    vendor_credit_number: Mapped[str] = mapped_column(String(100), nullable=False)

    credit_date: Mapped[date] = mapped_column(Date, nullable=False)
    currency: Mapped[str] = mapped_column(String(10), nullable=False, default="CAD")

    amount: Mapped[Decimal] = mapped_column(Numeric(15, 2), nullable=False)          # pre-tax, positive
    tax_amount: Mapped[Decimal] = mapped_column(Numeric(15, 2), nullable=False, default=Decimal("0"))
    total_amount: Mapped[Decimal] = mapped_column(Numeric(15, 2), nullable=False)    # = amount + tax_amount

    applied_amount: Mapped[Decimal] = mapped_column(Numeric(15, 2), nullable=False, default=Decimal("0"))
    remaining_amount: Mapped[Decimal] = mapped_column(Numeric(15, 2), nullable=False)

    status: Mapped[str] = mapped_column(String(20), nullable=False, default=PENDING_REVIEW, index=True)

    # Traceability only — a credit is never 3-way matched against this PO.
    po_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    po_number: Mapped[str | None] = mapped_column(String(40), nullable=True)

    line_items: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    file_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Provenance. Phase C populates the qbo_import variants.
    source: Mapped[str] = mapped_column(String(20), nullable=False, default=SOURCE_UPLOAD)
    source_ref: Mapped[str | None] = mapped_column(String(20), nullable=True)
    opening_balance: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    imported_from_sync_run_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)

    uploaded_by: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    uploaded_by_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    uploaded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    reviewed_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    reviewed_by_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    review_note: Mapped[str | None] = mapped_column(Text, nullable=True)

    __table_args__ = (
        CheckConstraint("total_amount > 0", name="ck_vendor_credits_total_positive"),
        CheckConstraint("applied_amount >= 0 AND remaining_amount >= 0",
                        name="ck_vendor_credits_nonneg"),
        CheckConstraint("applied_amount + remaining_amount = total_amount",
                        name="ck_vendor_credits_balance"),
        # A rejected/voided upload must not block re-uploading a corrected one.
        Index("uq_vendor_credits_vendor_docno",
              "vendor_id", "vendor_credit_number",
              unique=True,
              postgresql_where=text("source = 'upload' AND status <> 'void'")),
        Index("uq_vendor_credits_source_ref",
              "source", "source_ref",
              unique=True,
              postgresql_where=text("source_ref IS NOT NULL")),
        # Serves the Phase B FIFO lookup.
        Index("ix_vendor_credits_available",
              "vendor_id", "currency", "credit_date",
              postgresql_where=text("status = 'available' AND remaining_amount > 0")),
    )
```

Add `from sqlalchemy import text` to the import block — the partial indexes need it.

- [ ] **Step 4: Run the model tests**

Run: `cd finance-api && python -m pytest tests/test_vendor_credit.py -v`
Expected: both tests PASS (they only inspect the mapping, no DB needed)

- [ ] **Step 5: Write the migration**

Create `finance-api/alembic/versions/0030_vendor_credits.py`:

```python
"""vendor credits — Phase A ledger (record + review)

Revision ID: 0030_vendor_credits
Revises: 0029_qbo_mirror_phase2
Create Date: 2026-08-06

All vendor_credits columns land here, including the source/opening-balance
columns that only Phase C populates: the partial unique index below keys off
`source`, so the column must exist now. vendor_credit_applications and
payment_records.credit_applied belong to Phase B.
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision = "0030_vendor_credits"
down_revision = "0029_qbo_mirror_phase2"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "vendor_credits",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("credit_number", sa.String(30), nullable=False),
        sa.Column("vendor_id", UUID(as_uuid=True), nullable=False),
        sa.Column("vendor_name", sa.String(255), nullable=False),
        sa.Column("vendor_credit_number", sa.String(100), nullable=False),
        sa.Column("credit_date", sa.Date(), nullable=False),
        sa.Column("currency", sa.String(10), nullable=False, server_default="CAD"),
        sa.Column("amount", sa.Numeric(15, 2), nullable=False),
        sa.Column("tax_amount", sa.Numeric(15, 2), nullable=False, server_default="0"),
        sa.Column("total_amount", sa.Numeric(15, 2), nullable=False),
        sa.Column("applied_amount", sa.Numeric(15, 2), nullable=False, server_default="0"),
        sa.Column("remaining_amount", sa.Numeric(15, 2), nullable=False),
        sa.Column("status", sa.String(20), nullable=False, server_default="pending_review"),
        sa.Column("po_id", UUID(as_uuid=True), nullable=True),
        sa.Column("po_number", sa.String(40), nullable=True),
        sa.Column("line_items", JSONB, nullable=False, server_default="[]"),
        sa.Column("file_name", sa.String(255), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("source", sa.String(20), nullable=False, server_default="upload"),
        sa.Column("source_ref", sa.String(20), nullable=True),
        sa.Column("opening_balance", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("imported_from_sync_run_id", UUID(as_uuid=True), nullable=True),
        sa.Column("uploaded_by", UUID(as_uuid=True), nullable=False),
        sa.Column("uploaded_by_name", sa.String(255), nullable=True),
        sa.Column("uploaded_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("reviewed_by", UUID(as_uuid=True), nullable=True),
        sa.Column("reviewed_by_name", sa.String(255), nullable=True),
        sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("review_note", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.CheckConstraint("total_amount > 0", name="ck_vendor_credits_total_positive"),
        sa.CheckConstraint("applied_amount >= 0 AND remaining_amount >= 0",
                           name="ck_vendor_credits_nonneg"),
        sa.CheckConstraint("applied_amount + remaining_amount = total_amount",
                           name="ck_vendor_credits_balance"),
    )
    op.create_index("ix_vendor_credits_credit_number", "vendor_credits", ["credit_number"], unique=True)
    op.create_index("ix_vendor_credits_vendor_id", "vendor_credits", ["vendor_id"])
    op.create_index("ix_vendor_credits_status", "vendor_credits", ["status"])
    op.create_index(
        "uq_vendor_credits_vendor_docno", "vendor_credits",
        ["vendor_id", "vendor_credit_number"], unique=True,
        postgresql_where=sa.text("source = 'upload' AND status <> 'void'"),
    )
    op.create_index(
        "uq_vendor_credits_source_ref", "vendor_credits",
        ["source", "source_ref"], unique=True,
        postgresql_where=sa.text("source_ref IS NOT NULL"),
    )
    op.create_index(
        "ix_vendor_credits_available", "vendor_credits",
        ["vendor_id", "currency", "credit_date"],
        postgresql_where=sa.text("status = 'available' AND remaining_amount > 0"),
    )


def downgrade() -> None:
    op.drop_table("vendor_credits")
```

- [ ] **Step 6: Verify the migration chain has exactly one head**

Run:
```bash
cd finance-api && python - <<'EOF'
import re, glob, os
revs, parents = {}, set()
for f in glob.glob("alembic/versions/*.py"):
    s = open(f, encoding="utf-8").read()
    r = re.search(r'^revision\s*=\s*["\']([^"\']+)', s, re.M)
    d = re.search(r'^down_revision\s*=\s*["\']([^"\']+)', s, re.M)
    if r: revs[r.group(1)] = os.path.basename(f)
    if d: parents.add(d.group(1))
print("HEADS:", [k for k in revs if k not in parents])
EOF
```
Expected: `HEADS: ['0030_vendor_credits']` — exactly one entry. Two entries means the chain forked; fix `down_revision` before continuing.

- [ ] **Step 7: Write a test that the migration actually builds the table**

Append to `finance-api/tests/test_vendor_credit.py`:

```python
@pytest.mark.anyio
async def test_migration_creates_table_with_checks(db_session):
    import sqlalchemy as sa
    cols = (await db_session.execute(sa.text(
        "SELECT column_name FROM information_schema.columns "
        "WHERE table_name = 'vendor_credits'"
    ))).scalars().all()
    assert {"credit_number", "total_amount", "remaining_amount", "source"} <= set(cols)

    checks = (await db_session.execute(sa.text(
        "SELECT conname FROM pg_constraint WHERE conrelid = 'vendor_credits'::regclass "
        "AND contype = 'c'"
    ))).scalars().all()
    assert "ck_vendor_credits_total_positive" in checks
    assert "ck_vendor_credits_balance" in checks

    idx = (await db_session.execute(sa.text(
        "SELECT indexname FROM pg_indexes WHERE tablename = 'vendor_credits'"
    ))).scalars().all()
    assert "uq_vendor_credits_vendor_docno" in idx
    assert "uq_vendor_credits_source_ref" in idx
```

- [ ] **Step 8: Run the DB test**

Run: `cd finance-api && python -m pytest tests/test_vendor_credit.py -v`
Expected: all 3 PASS. The `db_session` fixture drops and rebuilds the schema by running alembic, so a chain error shows up here as a migration failure.

- [ ] **Step 9: Commit**

```bash
git add finance-api/app/models/vendor_credit.py finance-api/alembic/versions/0030_vendor_credits.py finance-api/tests/test_vendor_credit.py
git commit -m "feat(finance): vendor_credits table — positive-amount credit-note ledger"
```

---

## Task 2: Create a vendor credit (normalisation, numbering, duplicates)

**Files:**
- Create: `finance-api/app/schemas/vendor_credit.py`
- Create: `finance-api/app/crud/vendor_credit.py`
- Test: `finance-api/tests/test_vendor_credit.py` (append)

**Interfaces:**
- Consumes: `VendorCredit`, `PENDING_REVIEW`, `SOURCE_UPLOAD` from Task 1; `app.crud._numbering.next_number(db, column, prefix, width) -> str`
- Produces:
  - `VendorCreditCreate` (Pydantic): `vendor_id: UUID`, `vendor_name: str`, `vendor_credit_number: str`, `credit_date: date`, `currency: str = "CAD"`, `amount: Decimal`, `tax_amount: Decimal = 0`, `po_id: UUID | None`, `po_number: str | None`, `line_items: list[dict] = []`, `file_name: str | None`, `notes: str | None`
  - `async def create(db, *, payload: VendorCreditCreate, uploaded_by: UUID, uploaded_by_name: str | None) -> VendorCredit`
  - `class DuplicateCredit(Exception)` with attribute `existing: VendorCredit`

- [ ] **Step 1: Write the failing tests**

Append to `finance-api/tests/test_vendor_credit.py`:

```python
def _create_payload(**over):
    from app.schemas.vendor_credit import VendorCreditCreate
    p = dict(
        vendor_id=uuid.uuid4(), vendor_name="Amazon Business",
        vendor_credit_number="11DJ-MFHX-N4JG",
        credit_date=date(2026, 7, 1), currency="CAD",
        amount=Decimal("-0.04"), tax_amount=Decimal("0"),
        po_id=None, po_number="PO-089-2605-23",
        line_items=[{"description": "Export Fee", "quantity": 1,
                     "unit_price": -0.04, "line_total": -0.04}],
        file_name="AmazonBusiness_CreditNote_11DJ-MFHX-N4JG.pdf", notes=None,
    )
    p.update(over)
    return VendorCreditCreate(**p)


@pytest.mark.anyio
async def test_create_stores_negative_input_as_positive(db_session):
    from app.crud import vendor_credit as crud
    uid = uuid.uuid4()
    vc = await crud.create(db_session, payload=_create_payload(),
                           uploaded_by=uid, uploaded_by_name="AP Clerk")
    await db_session.commit()
    assert vc.amount == Decimal("0.04")
    assert vc.tax_amount == Decimal("0.00")
    assert vc.total_amount == Decimal("0.04")
    assert vc.remaining_amount == Decimal("0.04")
    assert vc.applied_amount == Decimal("0.00")
    assert vc.status == "pending_review"
    assert vc.source == "upload"
    assert vc.opening_balance is False
    assert vc.credit_number.startswith("VC-")
    assert vc.po_number == "PO-089-2605-23"


@pytest.mark.anyio
async def test_create_normalises_negative_tax_too(db_session):
    from app.crud import vendor_credit as crud
    vc = await crud.create(
        db_session,
        payload=_create_payload(amount=Decimal("-100.00"), tax_amount=Decimal("-13.00")),
        uploaded_by=uuid.uuid4(), uploaded_by_name="AP Clerk")
    await db_session.commit()
    assert vc.amount == Decimal("100.00")
    assert vc.tax_amount == Decimal("13.00")
    assert vc.total_amount == Decimal("113.00")


@pytest.mark.anyio
async def test_create_rejects_zero_total(db_session):
    from app.crud import vendor_credit as crud
    with pytest.raises(ValueError, match="must not be zero"):
        await crud.create(
            db_session,
            payload=_create_payload(amount=Decimal("0"), tax_amount=Decimal("0")),
            uploaded_by=uuid.uuid4(), uploaded_by_name="AP Clerk")


@pytest.mark.anyio
async def test_create_allocates_sequential_numbers(db_session):
    from app.crud import vendor_credit as crud
    vid = uuid.uuid4()
    a = await crud.create(db_session, payload=_create_payload(vendor_id=vid, vendor_credit_number="CN-1"),
                          uploaded_by=uuid.uuid4(), uploaded_by_name="AP")
    b = await crud.create(db_session, payload=_create_payload(vendor_id=vid, vendor_credit_number="CN-2"),
                          uploaded_by=uuid.uuid4(), uploaded_by_name="AP")
    await db_session.commit()
    prefix = f"VC-{date.today():%Y%m%d}-"
    assert a.credit_number == f"{prefix}0001"
    assert b.credit_number == f"{prefix}0002"


@pytest.mark.anyio
async def test_create_rejects_same_vendor_same_document_number(db_session):
    from app.crud import vendor_credit as crud
    vid = uuid.uuid4()
    first = await crud.create(db_session, payload=_create_payload(vendor_id=vid),
                              uploaded_by=uuid.uuid4(), uploaded_by_name="AP")
    await db_session.commit()
    with pytest.raises(crud.DuplicateCredit) as exc:
        await crud.create(db_session, payload=_create_payload(vendor_id=vid),
                          uploaded_by=uuid.uuid4(), uploaded_by_name="AP")
    assert exc.value.existing.id == first.id


@pytest.mark.anyio
async def test_same_document_number_allowed_for_a_different_vendor(db_session):
    from app.crud import vendor_credit as crud
    await crud.create(db_session, payload=_create_payload(vendor_id=uuid.uuid4()),
                      uploaded_by=uuid.uuid4(), uploaded_by_name="AP")
    await crud.create(db_session, payload=_create_payload(vendor_id=uuid.uuid4()),
                      uploaded_by=uuid.uuid4(), uploaded_by_name="AP")
    await db_session.commit()
```

- [ ] **Step 2: Run and watch them fail**

Run: `cd finance-api && python -m pytest tests/test_vendor_credit.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.schemas.vendor_credit'`

- [ ] **Step 3: Write the schema module**

Create `finance-api/app/schemas/vendor_credit.py`:

```python
"""Pydantic contracts for Vendor Credit.

Note the deliberate absence of a positivity constraint on `amount`: callers
legitimately submit what the vendor printed, which is usually negative. The
CRUD layer is the single place that normalises sign (see app/crud/vendor_credit.py).
"""
import uuid
from datetime import date, datetime
from decimal import Decimal

from pydantic import BaseModel, Field


class VendorCreditCreate(BaseModel):
    vendor_id: uuid.UUID
    vendor_name: str = Field(min_length=1, max_length=255)
    vendor_credit_number: str = Field(min_length=1, max_length=100)
    credit_date: date
    currency: str = Field(default="CAD", max_length=10)
    amount: Decimal                                   # pre-tax, sign-agnostic
    tax_amount: Decimal = Decimal("0")                # sign-agnostic
    po_id: uuid.UUID | None = None
    po_number: str | None = Field(default=None, max_length=40)
    line_items: list[dict] = Field(default_factory=list)
    file_name: str | None = Field(default=None, max_length=255)
    notes: str | None = None


class VendorCreditReview(BaseModel):
    note: str | None = None


class VendorCreditReject(BaseModel):
    note: str = Field(min_length=1)


class VendorCreditResponse(BaseModel):
    id: uuid.UUID
    credit_number: str
    vendor_id: uuid.UUID
    vendor_name: str
    vendor_credit_number: str
    credit_date: date
    currency: str
    amount: Decimal
    tax_amount: Decimal
    total_amount: Decimal
    applied_amount: Decimal
    remaining_amount: Decimal
    status: str
    po_id: uuid.UUID | None
    po_number: str | None
    line_items: list
    file_name: str | None
    notes: str | None
    source: str
    opening_balance: bool
    uploaded_by: uuid.UUID
    uploaded_by_name: str | None
    uploaded_at: datetime
    reviewed_by: uuid.UUID | None
    reviewed_by_name: str | None
    reviewed_at: datetime | None
    review_note: str | None
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class VendorCreditListResponse(BaseModel):
    items: list[VendorCreditResponse]
    total: int
```

- [ ] **Step 4: Write the CRUD create path**

Create `finance-api/app/crud/vendor_credit.py`:

```python
"""Vendor Credit business rules.

This module is the ONLY place that normalises the sign of a credit amount.
Vendors print credit notes as negatives; the ledger stores positives. Doing it
here (rather than in the API layer, the frontend, or OCR) means every entry
path — manual upload today, QBO import in Phase C — gets the same treatment
and the CHECK constraint can be trusted.
"""
import uuid
from datetime import datetime, timezone
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.crud._numbering import next_number
from app.models.vendor_credit import (
    AVAILABLE, PENDING_REVIEW, SOURCE_UPLOAD, VOID, VendorCredit,
)
from app.schemas.vendor_credit import VendorCreditCreate

_ZERO = Decimal("0")
_CENT = Decimal("0.01")


class DuplicateCredit(Exception):
    """Same vendor already has a live credit with this document number."""

    def __init__(self, existing: VendorCredit):
        self.existing = existing
        super().__init__(
            f"Credit note {existing.vendor_credit_number} already recorded "
            f"as {existing.credit_number}"
        )


def _positive(v: Decimal) -> Decimal:
    return abs(Decimal(v)).quantize(_CENT)


async def _find_duplicate(db: AsyncSession, vendor_id: uuid.UUID,
                          doc_number: str) -> VendorCredit | None:
    return (await db.execute(
        select(VendorCredit).where(
            VendorCredit.vendor_id == vendor_id,
            VendorCredit.vendor_credit_number == doc_number,
            VendorCredit.source == SOURCE_UPLOAD,
            VendorCredit.status != VOID,
        )
    )).scalars().first()


async def create(db: AsyncSession, *, payload: VendorCreditCreate,
                 uploaded_by: uuid.UUID,
                 uploaded_by_name: str | None) -> VendorCredit:
    amount = _positive(payload.amount)
    tax = _positive(payload.tax_amount)
    total = amount + tax
    if total == _ZERO:
        raise ValueError("Credit total must not be zero")

    dup = await _find_duplicate(db, payload.vendor_id, payload.vendor_credit_number)
    if dup is not None:
        raise DuplicateCredit(dup)

    now = datetime.now(timezone.utc)
    number = await next_number(
        db, VendorCredit.credit_number, f"VC-{now.date():%Y%m%d}-", 4)

    vc = VendorCredit(
        credit_number=number,
        vendor_id=payload.vendor_id,
        vendor_name=payload.vendor_name,
        vendor_credit_number=payload.vendor_credit_number,
        credit_date=payload.credit_date,
        currency=payload.currency,
        amount=amount,
        tax_amount=tax,
        total_amount=total,
        applied_amount=_ZERO,
        remaining_amount=total,
        status=PENDING_REVIEW,
        po_id=payload.po_id,
        po_number=payload.po_number,
        line_items=payload.line_items,
        file_name=payload.file_name,
        notes=payload.notes,
        source=SOURCE_UPLOAD,
        opening_balance=False,
        uploaded_by=uploaded_by,
        uploaded_by_name=uploaded_by_name,
        uploaded_at=now,
    )
    db.add(vc)
    await db.flush()
    return vc
```

- [ ] **Step 5: Run the create tests**

Run: `cd finance-api && python -m pytest tests/test_vendor_credit.py -v`
Expected: all PASS

- [ ] **Step 6: Commit**

```bash
git add finance-api/app/schemas/vendor_credit.py finance-api/app/crud/vendor_credit.py finance-api/tests/test_vendor_credit.py
git commit -m "feat(finance): create vendor credits — sign normalisation, numbering, duplicate guard"
```

---

## Task 3: Review transitions (approve / reject / void)

**Files:**
- Modify: `finance-api/app/crud/vendor_credit.py`
- Test: `finance-api/tests/test_vendor_credit.py` (append)

**Interfaces:**
- Produces:
  - `async def approve(db, credit, *, reviewed_by: UUID, reviewed_by_name: str | None, note: str | None) -> VendorCredit`
  - `async def reject(db, credit, *, reviewed_by: UUID, reviewed_by_name: str | None, note: str) -> VendorCredit`
  - `async def void(db, credit, *, reviewed_by: UUID, reviewed_by_name: str | None, note: str) -> VendorCredit`
  - `class InvalidTransition(Exception)`

**Business rules** (spec §4.6): `approve` only from `pending_review`. `reject` only from `pending_review`. `void` only from `available`, and only while `applied_amount == 0`. Self-review is explicitly allowed — do **not** compare `reviewed_by` with `uploaded_by`.

- [ ] **Step 1: Write the failing tests**

Append to `finance-api/tests/test_vendor_credit.py`:

```python
@pytest_asyncio.fixture
async def pending_credit(db_session):
    from app.crud import vendor_credit as crud
    vc = await crud.create(db_session, payload=_create_payload(),
                           uploaded_by=uuid.uuid4(), uploaded_by_name="AP")
    await db_session.commit()
    return vc


@pytest.mark.anyio
async def test_approve_makes_it_available(db_session, pending_credit):
    from app.crud import vendor_credit as crud
    reviewer = uuid.uuid4()
    vc = await crud.approve(db_session, pending_credit, reviewed_by=reviewer,
                            reviewed_by_name="Finance Manager", note=None)
    await db_session.commit()
    assert vc.status == "available"
    assert vc.reviewed_by == reviewer
    assert vc.reviewed_at is not None


@pytest.mark.anyio
async def test_uploader_may_approve_their_own_credit(db_session):
    """Self-review is allowed by design — no SoD gate in Phase A."""
    from app.crud import vendor_credit as crud
    me = uuid.uuid4()
    vc = await crud.create(db_session, payload=_create_payload(),
                           uploaded_by=me, uploaded_by_name="AP")
    await db_session.commit()
    vc = await crud.approve(db_session, vc, reviewed_by=me,
                            reviewed_by_name="AP", note=None)
    await db_session.commit()
    assert vc.status == "available"


@pytest.mark.anyio
async def test_reject_voids_with_a_note(db_session, pending_credit):
    from app.crud import vendor_credit as crud
    vc = await crud.reject(db_session, pending_credit, reviewed_by=uuid.uuid4(),
                           reviewed_by_name="FM", note="Duplicate of CN-9")
    await db_session.commit()
    assert vc.status == "void"
    assert vc.review_note == "Duplicate of CN-9"


@pytest.mark.anyio
async def test_cannot_approve_twice(db_session, pending_credit):
    from app.crud import vendor_credit as crud
    await crud.approve(db_session, pending_credit, reviewed_by=uuid.uuid4(),
                       reviewed_by_name="FM", note=None)
    await db_session.commit()
    with pytest.raises(crud.InvalidTransition):
        await crud.approve(db_session, pending_credit, reviewed_by=uuid.uuid4(),
                           reviewed_by_name="FM", note=None)


@pytest.mark.anyio
async def test_void_requires_available_and_untouched(db_session, pending_credit):
    from app.crud import vendor_credit as crud
    # Not yet available → refuse.
    with pytest.raises(crud.InvalidTransition):
        await crud.void(db_session, pending_credit, reviewed_by=uuid.uuid4(),
                        reviewed_by_name="FM", note="oops")

    await crud.approve(db_session, pending_credit, reviewed_by=uuid.uuid4(),
                       reviewed_by_name="FM", note=None)
    await db_session.commit()

    # Simulate a Phase-B application having consumed part of it.
    pending_credit.applied_amount = Decimal("0.02")
    pending_credit.remaining_amount = Decimal("0.02")
    await db_session.flush()
    with pytest.raises(crud.InvalidTransition, match="already been applied"):
        await crud.void(db_session, pending_credit, reviewed_by=uuid.uuid4(),
                        reviewed_by_name="FM", note="oops")


@pytest.mark.anyio
async def test_rejected_document_number_can_be_re_uploaded(db_session, pending_credit):
    """The partial unique index excludes voided rows, so a corrected
    re-upload of the same vendor document number must succeed."""
    from app.crud import vendor_credit as crud
    await crud.reject(db_session, pending_credit, reviewed_by=uuid.uuid4(),
                      reviewed_by_name="FM", note="wrong amount")
    await db_session.commit()
    again = await crud.create(
        db_session,
        payload=_create_payload(vendor_id=pending_credit.vendor_id),
        uploaded_by=uuid.uuid4(), uploaded_by_name="AP")
    await db_session.commit()
    assert again.status == "pending_review"
```

- [ ] **Step 2: Run and watch them fail**

Run: `cd finance-api && python -m pytest tests/test_vendor_credit.py -k "approve or reject or void or re_upload" -v`
Expected: FAIL — `AttributeError: module 'app.crud.vendor_credit' has no attribute 'approve'`

- [ ] **Step 3: Implement the transitions**

Append to `finance-api/app/crud/vendor_credit.py`:

```python
class InvalidTransition(Exception):
    """The requested review action is not legal from the current status."""


def _stamp_review(credit: VendorCredit, reviewed_by: uuid.UUID,
                  reviewed_by_name: str | None, note: str | None) -> None:
    credit.reviewed_by = reviewed_by
    credit.reviewed_by_name = reviewed_by_name
    credit.reviewed_at = datetime.now(timezone.utc)
    if note is not None:
        credit.review_note = note


async def approve(db: AsyncSession, credit: VendorCredit, *,
                  reviewed_by: uuid.UUID, reviewed_by_name: str | None,
                  note: str | None) -> VendorCredit:
    # Self-review is deliberately permitted: the AP team is small enough that a
    # segregation-of-duties gate would deadlock the queue. See spec §8.
    if credit.status != PENDING_REVIEW:
        raise InvalidTransition(
            f"Only a pending_review credit can be approved (is {credit.status})")
    credit.status = AVAILABLE
    _stamp_review(credit, reviewed_by, reviewed_by_name, note)
    await db.flush()
    return credit


async def reject(db: AsyncSession, credit: VendorCredit, *,
                 reviewed_by: uuid.UUID, reviewed_by_name: str | None,
                 note: str) -> VendorCredit:
    if credit.status != PENDING_REVIEW:
        raise InvalidTransition(
            f"Only a pending_review credit can be rejected (is {credit.status})")
    credit.status = VOID
    _stamp_review(credit, reviewed_by, reviewed_by_name, note)
    await db.flush()
    return credit


async def void(db: AsyncSession, credit: VendorCredit, *,
               reviewed_by: uuid.UUID, reviewed_by_name: str | None,
               note: str) -> VendorCredit:
    if credit.status != AVAILABLE:
        raise InvalidTransition(
            f"Only an available credit can be voided (is {credit.status})")
    if credit.applied_amount > _ZERO:
        raise InvalidTransition(
            "Credit has already been applied to a payment and cannot be voided")
    credit.status = VOID
    _stamp_review(credit, reviewed_by, reviewed_by_name, note)
    await db.flush()
    return credit
```

- [ ] **Step 4: Run the full file**

Run: `cd finance-api && python -m pytest tests/test_vendor_credit.py -v`
Expected: all PASS

- [ ] **Step 5: Commit**

```bash
git add finance-api/app/crud/vendor_credit.py finance-api/tests/test_vendor_credit.py
git commit -m "feat(finance): vendor credit review transitions (approve/reject/void)"
```

---

## Task 4: HTTP surface + permission gates

**Files:**
- Modify: `finance-api/app/crud/vendor_credit.py` (add `get_by_id`, `get_all`)
- Create: `finance-api/app/api/v1/vendor_credits.py`
- Modify: `finance-api/app/api/v1/__init__.py`
- Test: `finance-api/tests/test_vendor_credit.py` (append)

**Interfaces:**
- Consumes: everything from Tasks 2–3; `app.core.authz.require_permission`; `app.core.deps.CurrentUser`; `app.db.base.get_db`
- Produces these routes under the `/finance/v1` prefix:
  - `POST /vendor-credits` → 201 `VendorCreditResponse`; 409 on duplicate
  - `GET /vendor-credits` (query `status`, `vendor_id`, `limit`, `offset`) → `VendorCreditListResponse`
  - `GET /vendor-credits/{id}` → `VendorCreditResponse`; 404
  - `POST /vendor-credits/{id}/approve` → `VendorCreditResponse`; 409 on illegal transition
  - `POST /vendor-credits/{id}/reject` (body `{note}`) → `VendorCreditResponse`
  - `POST /vendor-credits/{id}/void` (body `{note}`) → `VendorCreditResponse`

**Gating (spec §8):** `POST /vendor-credits`, `GET` list and `GET` detail require only a valid token (`CurrentUser`) — "if you can upload an invoice you can upload a credit note", and a freshly created credit is `pending_review`, which grants nothing until approved. `approve` / `reject` / `void` require `epms.vendor_credit.manage`.

- [ ] **Step 1: Write the failing API tests**

Append to `finance-api/tests/test_vendor_credit.py`:

```python
def _h(role="ap_clerk", sub=None):
    token = jwt.encode({"sub": str(sub or uuid.uuid4()), "role": role,
                        "exp": datetime.now(timezone.utc) + timedelta(hours=1)},
                       settings.jwt_secret_key, algorithm=settings.jwt_algorithm)
    return {"Authorization": f"Bearer {token}"}


@pytest_asyncio.fixture
async def client(db_session):
    async def _override_get_db():
        yield db_session
    app.dependency_overrides[get_db] = _override_get_db
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        yield c
    app.dependency_overrides.clear()


def _api_body(**over):
    b = dict(
        vendor_id=str(uuid.uuid4()), vendor_name="Amazon Business",
        vendor_credit_number="11DJ-MFHX-N4JG", credit_date="2026-07-01",
        currency="CAD", amount="-0.04", tax_amount="0",
        po_number="PO-089-2605-23", line_items=[], file_name="cn.pdf",
    )
    b.update(over)
    return b


@pytest.mark.anyio
async def test_post_creates_pending_credit_with_positive_amount(client):
    r = await client.post("/finance/v1/vendor-credits", json=_api_body(), headers=_h())
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["status"] == "pending_review"
    assert body["total_amount"] == "0.04"
    assert body["credit_number"].startswith("VC-")


@pytest.mark.anyio
async def test_post_requires_a_token(client):
    r = await client.post("/finance/v1/vendor-credits", json=_api_body())
    assert r.status_code == 403


@pytest.mark.anyio
async def test_post_duplicate_returns_409_with_existing_number(client):
    vid = str(uuid.uuid4())
    first = await client.post("/finance/v1/vendor-credits",
                              json=_api_body(vendor_id=vid), headers=_h())
    assert first.status_code == 201
    dup = await client.post("/finance/v1/vendor-credits",
                            json=_api_body(vendor_id=vid), headers=_h())
    assert dup.status_code == 409
    assert first.json()["credit_number"] in dup.json()["detail"]


@pytest.mark.anyio
async def test_list_filters_by_status(client):
    await client.post("/finance/v1/vendor-credits", json=_api_body(), headers=_h())
    r = await client.get("/finance/v1/vendor-credits",
                         params={"status": "pending_review"}, headers=_h())
    assert r.status_code == 200
    assert r.json()["total"] >= 1
    assert all(i["status"] == "pending_review" for i in r.json()["items"])

    empty = await client.get("/finance/v1/vendor-credits",
                             params={"status": "available"}, headers=_h())
    assert empty.json()["total"] == 0


@pytest.mark.anyio
async def test_approve_flips_to_available(client):
    created = (await client.post("/finance/v1/vendor-credits",
                                 json=_api_body(), headers=_h())).json()
    r = await client.post(f"/finance/v1/vendor-credits/{created['id']}/approve",
                          json={}, headers=_h("system_admin"))
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "available"


@pytest.mark.anyio
async def test_approving_twice_returns_409(client):
    created = (await client.post("/finance/v1/vendor-credits",
                                 json=_api_body(), headers=_h())).json()
    await client.post(f"/finance/v1/vendor-credits/{created['id']}/approve",
                      json={}, headers=_h("system_admin"))
    again = await client.post(f"/finance/v1/vendor-credits/{created['id']}/approve",
                              json={}, headers=_h("system_admin"))
    assert again.status_code == 409


@pytest.mark.anyio
async def test_reject_requires_a_note(client):
    created = (await client.post("/finance/v1/vendor-credits",
                                 json=_api_body(), headers=_h())).json()
    r = await client.post(f"/finance/v1/vendor-credits/{created['id']}/reject",
                          json={"note": ""}, headers=_h("system_admin"))
    assert r.status_code == 422


@pytest.mark.anyio
async def test_detail_404_for_unknown_id(client):
    r = await client.get(f"/finance/v1/vendor-credits/{uuid.uuid4()}", headers=_h())
    assert r.status_code == 404
```

- [ ] **Step 2: Run and watch them fail**

Run: `cd finance-api && python -m pytest tests/test_vendor_credit.py -k "post_ or list_ or approve or reject or detail" -v`
Expected: FAIL — 404 on every route (the router is not registered yet)

- [ ] **Step 3: Add the read helpers to CRUD**

Append to `finance-api/app/crud/vendor_credit.py`:

```python
async def get_by_id(db: AsyncSession, credit_id: uuid.UUID) -> VendorCredit | None:
    return (await db.execute(
        select(VendorCredit).where(VendorCredit.id == credit_id)
    )).scalars().first()


async def get_all(db: AsyncSession, *, status: str | None = None,
                  vendor_id: uuid.UUID | None = None,
                  limit: int = 100, offset: int = 0) -> tuple[list[VendorCredit], int]:
    q = select(VendorCredit)
    c = select(func.count()).select_from(VendorCredit)
    if status:
        q = q.where(VendorCredit.status == status)
        c = c.where(VendorCredit.status == status)
    if vendor_id:
        q = q.where(VendorCredit.vendor_id == vendor_id)
        c = c.where(VendorCredit.vendor_id == vendor_id)
    total = (await db.execute(c)).scalar_one()
    rows = (await db.execute(
        q.order_by(VendorCredit.created_at.desc()).limit(limit).offset(offset)
    )).scalars().all()
    return list(rows), total
```

- [ ] **Step 4: Write the router**

Create `finance-api/app/api/v1/vendor_credits.py`:

```python
"""Vendor Credit API — record and review vendor credit notes.

Gating rationale (spec §8): creating and reading need only a valid token,
matching "if you can upload an invoice you can upload a credit note". A created
credit is `pending_review` and confers nothing until approved, so the real gate
sits on the review actions, which require epms.vendor_credit.manage.
"""
import uuid

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.authz import require_permission
from app.core.deps import CurrentUser
from app.crud import vendor_credit as crud
from app.db.base import get_db
from app.schemas.vendor_credit import (
    VendorCreditCreate, VendorCreditListResponse, VendorCreditReject,
    VendorCreditResponse, VendorCreditReview,
)

router = APIRouter(prefix="/vendor-credits", tags=["vendor-credits"])

_MANAGE_KEY = "epms.vendor_credit.manage"


def _actor(user: dict) -> tuple[uuid.UUID, str | None]:
    try:
        uid = uuid.UUID(str(user.get("sub", "")))
    except ValueError:
        raise HTTPException(status_code=401, detail="Token has no usable subject")
    return uid, user.get("full_name") or user.get("email")


async def _load(db: AsyncSession, credit_id: uuid.UUID):
    credit = await crud.get_by_id(db, credit_id)
    if credit is None:
        raise HTTPException(status_code=404, detail="Vendor credit not found")
    return credit


@router.post("", response_model=VendorCreditResponse, status_code=201)
async def create_vendor_credit(payload: VendorCreditCreate, user: CurrentUser,
                               db: AsyncSession = Depends(get_db)):
    uid, name = _actor(user)
    try:
        credit = await crud.create(db, payload=payload, uploaded_by=uid,
                                   uploaded_by_name=name)
    except crud.DuplicateCredit as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    await db.commit()
    await db.refresh(credit)
    return credit


@router.get("", response_model=VendorCreditListResponse)
async def list_vendor_credits(user: CurrentUser,
                              status: str | None = Query(default=None),
                              vendor_id: uuid.UUID | None = Query(default=None),
                              limit: int = Query(default=100, le=500),
                              offset: int = Query(default=0, ge=0),
                              db: AsyncSession = Depends(get_db)):
    items, total = await crud.get_all(db, status=status, vendor_id=vendor_id,
                                      limit=limit, offset=offset)
    return VendorCreditListResponse(items=items, total=total)


@router.get("/{credit_id}", response_model=VendorCreditResponse)
async def get_vendor_credit(credit_id: uuid.UUID, user: CurrentUser,
                            db: AsyncSession = Depends(get_db)):
    return await _load(db, credit_id)


@router.post("/{credit_id}/approve", response_model=VendorCreditResponse)
async def approve_vendor_credit(credit_id: uuid.UUID, body: VendorCreditReview,
                                user: dict = Depends(require_permission(_MANAGE_KEY)),
                                db: AsyncSession = Depends(get_db)):
    credit = await _load(db, credit_id)
    uid, name = _actor(user)
    try:
        credit = await crud.approve(db, credit, reviewed_by=uid,
                                    reviewed_by_name=name, note=body.note)
    except crud.InvalidTransition as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    await db.commit()
    await db.refresh(credit)
    return credit


@router.post("/{credit_id}/reject", response_model=VendorCreditResponse)
async def reject_vendor_credit(credit_id: uuid.UUID, body: VendorCreditReject,
                               user: dict = Depends(require_permission(_MANAGE_KEY)),
                               db: AsyncSession = Depends(get_db)):
    credit = await _load(db, credit_id)
    uid, name = _actor(user)
    try:
        credit = await crud.reject(db, credit, reviewed_by=uid,
                                   reviewed_by_name=name, note=body.note)
    except crud.InvalidTransition as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    await db.commit()
    await db.refresh(credit)
    return credit


@router.post("/{credit_id}/void", response_model=VendorCreditResponse)
async def void_vendor_credit(credit_id: uuid.UUID, body: VendorCreditReject,
                             user: dict = Depends(require_permission(_MANAGE_KEY)),
                             db: AsyncSession = Depends(get_db)):
    credit = await _load(db, credit_id)
    uid, name = _actor(user)
    try:
        credit = await crud.void(db, credit, reviewed_by=uid,
                                 reviewed_by_name=name, note=body.note)
    except crud.InvalidTransition as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    await db.commit()
    await db.refresh(credit)
    return credit
```

- [ ] **Step 5: Register the router**

In `finance-api/app/api/v1/__init__.py`, add the import alongside the others:

```python
from app.api.v1.vendor_credits import router as vendor_credits_router
```

and the registration at the end of the `api_router.include_router(...)` block:

```python
api_router.include_router(vendor_credits_router)
```

- [ ] **Step 6: Run the whole vendor-credit suite**

Run: `cd finance-api && python -m pytest tests/test_vendor_credit.py -v`
Expected: all PASS

- [ ] **Step 7: Run the full finance-api suite and compare against baseline**

Run: `cd finance-api && python -m pytest -q 2>&1 | tail -5`

Record the failed count. Then compare with the same command on a clean checkout of `origin/main`. **The count must not increase.** A bare "no new errors" claim without both numbers is not acceptable evidence — pre-existing failures exist in this suite and the point is the delta.

- [ ] **Step 8: Commit**

```bash
git add finance-api/app/api/v1/vendor_credits.py finance-api/app/api/v1/__init__.py finance-api/app/crud/vendor_credit.py finance-api/tests/test_vendor_credit.py
git commit -m "feat(finance): vendor credit API — create, list, review"
```

---

## Task 5: Register the `epms.vendor_credit.manage` permission

**Files:**
- Modify: `identity-api/scripts/seed_phase2_keys.py`

**Interfaces:**
- Consumes: the key string `epms.vendor_credit.manage` used by Task 4
- Produces: a seedable permission key with default roles

**Do NOT touch `identity-api/scripts/verify_gate_parity.py`.** The design spec
originally said to mirror the key there; that instruction is wrong and is
corrected here. That script's own docstring states it is a one-shot acceptance
tool holding a **frozen, hand-typed snapshot** of the 12 phase-2 keys as they
stood at cutover, deliberately *not* imported from the seed script — "importing
it would make both sides read the same dict, and a script that compares a dict
to itself always says OK". Adding a 13th key would corrupt the snapshot it
exists to preserve, and the script already documents that post-cutover matrix
changes legitimately produce DIFF output.

- [ ] **Step 1: Add the key to the catalog**

In `identity-api/scripts/seed_phase2_keys.py`, inside `PHASE2_KEYS`, after the `"epms.gr.receive"` line:

```python
    "epms.vendor_credit.manage": ("epms", "Manage Vendor Credits", 104),
```

- [ ] **Step 2: Add the default role set**

In the same file, inside `PHASE2_DEFAULTS`, after the `"epms.gr.receive"` line:

```python
    "epms.vendor_credit.manage": ("system_admin", "ap_clerk", "finance_manager", "finance_bp"),
```

- [ ] **Step 3: Verify the key is registered exactly twice, and the parity snapshot is untouched**

Run:
```bash
cd identity-api && python - <<'EOF'
seed = open("scripts/seed_phase2_keys.py", encoding="utf-8").read()
parity = open("scripts/verify_gate_parity.py", encoding="utf-8").read()
key = "epms.vendor_credit.manage"
assert seed.count(key) == 2, f"expected the key twice in the seed script, saw {seed.count(key)}"
assert key not in parity, "verify_gate_parity.py is a frozen cutover snapshot — do not add keys to it"
print("OK: key registered in the seed script; parity snapshot untouched")
EOF
```
Expected: `OK: key registered in the seed script; parity snapshot untouched`

- [ ] **Step 4: Commit**

```bash
git add identity-api/scripts/seed_phase2_keys.py
git commit -m "feat(identity): register epms.vendor_credit.manage permission key"
```

> **Deploy note for the release checklist:** after deploying, run
> `docker exec uniops_identity_api python -m scripts.seed_phase2_keys`, then tick
> the new key for the intended roles in Portal → Access Control. Until then only
> `system_admin` can approve credits (it short-circuits every gate).

---

## Task 6: OCR returns `document_type`

**Files:**
- Modify: `expense-api/app/services/ocr_service.py`
- Test: `expense-api/tests/test_ocr_service.py`

**Interfaces:**
- Produces: `extract_invoice()` result gains `document_type: "invoice" | "credit_note"`, defaulting to `"invoice"` when the model omits or garbles it.

**Constraint:** do not touch `_normalize_negative_quantities()` or `_reconcile_line_amounts()`. This task only adds a field.

- [ ] **Step 1: Write the failing test**

Append to `expense-api/tests/test_ocr_service.py`:

```python
def test_document_type_is_extracted_when_present():
    from app.services import ocr_service
    parsed = {
        "vendor_name": {"value": "Amazon Business", "confidence": 1.0},
        "document_type": {"value": "credit_note", "confidence": 0.95},
        "subtotal": {"value": -0.04, "confidence": 1.0},
        "line_items": [],
    }
    result = ocr_service._assemble_invoice_result(parsed)
    assert result["document_type"] == "credit_note"


def test_document_type_defaults_to_invoice_when_absent():
    from app.services import ocr_service
    result = ocr_service._assemble_invoice_result(
        {"vendor_name": {"value": "ULINE", "confidence": 1.0}, "line_items": []})
    assert result["document_type"] == "invoice"


def test_document_type_falls_back_on_an_unexpected_value():
    from app.services import ocr_service
    result = ocr_service._assemble_invoice_result(
        {"document_type": {"value": "receipt", "confidence": 0.4}, "line_items": []})
    assert result["document_type"] == "invoice"
```

- [ ] **Step 2: Run and watch it fail**

Run: `cd expense-api && python -m pytest tests/test_ocr_service.py -k document_type -v`
Expected: FAIL — `AttributeError: module 'app.services.ocr_service' has no attribute '_assemble_invoice_result'`

- [ ] **Step 3: Extract the result assembly into a testable function**

In `expense-api/app/services/ocr_service.py`, the block inside `extract_invoice` that currently starts at the `# Flatten extracted values and calculate confidence` comment (around line 226) and ends with `return result` moves verbatim into a new module-level function. Add above `extract_invoice`:

```python
_VALID_DOCUMENT_TYPES = ("invoice", "credit_note")


def _assemble_invoice_result(parsed: dict) -> dict:
    """Flatten the model's {value, confidence} envelope into a flat result.

    Split out of extract_invoice so the mapping is unit-testable without an
    Anthropic API call.
    """
    scalar_fields = ["vendor_name", "invoice_number", "po_number",
                     "invoice_date", "due_date", "payment_terms_net_days",
                     "currency", "subtotal", "tax_amount", "total_amount"]

    result: dict = {}
    confidences: list[float] = []
    low_confidence: list[str] = []

    for field in scalar_fields:
        item = parsed.get(field, {})
        value = item.get("value") if isinstance(item, dict) else None
        conf = float(item.get("confidence", 0.0)) if isinstance(item, dict) else 0.0
        result[field] = value
        if value is not None:
            confidences.append(conf)
            if conf < CONFIDENCE_THRESHOLD:
                low_confidence.append(field)

    # Document type steers the EPMS upload form down the credit-note branch.
    # An unrecognised or missing value falls back to "invoice": the frontend
    # still catches a negative total, and mis-labelling a real invoice as a
    # credit would be the more damaging error.
    dt_item = parsed.get("document_type", {})
    dt = dt_item.get("value") if isinstance(dt_item, dict) else None
    result["document_type"] = dt if dt in _VALID_DOCUMENT_TYPES else "invoice"

    raw_lines = parsed.get("line_items", []) or []
    lines = []
    for i, li in enumerate(raw_lines[:100]):
        lines.append({
            "line_number": i + 1,
            "description": str(li.get("description", "")),
            "quantity": float(li.get("quantity", 1) or 1),
            "unit_price": float(li.get("unit_price", 0) or 0),
            "amount": float(li.get("amount", 0) or 0),
            "tax_amount": float(li.get("tax_amount", 0) or 0),
        })

    def _num(v: object) -> float | None:
        try:
            return float(v) if v is not None else None
        except (TypeError, ValueError):
            return None

    lines = _normalize_negative_quantities(lines)
    lines = _reconcile_line_amounts(
        lines, _num(result.get("subtotal")), _num(result.get("tax_amount")),
        _num(result.get("total_amount")),
    )

    result["line_items"] = lines
    result["ocr_confidence"] = (
        round(sum(confidences) / len(confidences), 4) if confidences else 0.0)
    result["low_confidence_fields"] = low_confidence
    result["ocr_raw"] = parsed
    return result
```

Then replace that whole block inside `extract_invoice` with:

```python
    return _assemble_invoice_result(parsed)
```

- [ ] **Step 4: Teach the prompt to emit the field**

In `_INVOICE_PROMPT`, add this line to the JSON structure immediately after the opening `{`:

```
  "document_type": {"value": "invoice" or "credit_note", "confidence": 0.0-1.0},
```

and add these bullets to the `Rules:` section:

```
- "document_type": "credit_note" when the document is a credit note / credit memo /
  credit invoice / adjustment note / avoir / 贷项通知单, or when the payable total is
  negative. Otherwise "invoice".
- Report amounts exactly as printed, including minus signs. Do NOT flip signs to
  make a credit note look like an invoice.
```

- [ ] **Step 5: Run the OCR tests**

Run: `cd expense-api && python -m pytest tests/test_ocr_service.py -v`
Expected: all PASS, including the pre-existing tests — the extraction refactor must not change any other behaviour.

- [ ] **Step 6: Commit**

```bash
git add expense-api/app/services/ocr_service.py expense-api/tests/test_ocr_service.py
git commit -m "feat(ocr): classify invoice vs credit_note in extract_invoice"
```

---

## Task 7: Accept `"credit"` as an attachment source

**Files:**
- Modify: `expense-api/app/api/v1/invoice_attachments.py`

**Interfaces:**
- Produces: `POST /api/v1/invoice-attachments?invoice_id=<vendor_credit_id>&invoice_source=credit` stores the original PDF for a vendor credit.

No migration: `invoice_attachments.invoice_source` is `String(10)` and `"credit"` is 6 characters. `api/v1/invoice_list.py` filters on the literal values `"epms"` and `"oa"`, so the unified invoice list is unaffected by the new value.

- [ ] **Step 1: Widen the whitelist**

In `expense-api/app/api/v1/invoice_attachments.py`, change the upload endpoint's guard (line 59) from:

```python
    if invoice_source not in ("epms", "oa"):
        raise HTTPException(status_code=400, detail="invoice_source must be 'epms' or 'oa'")
```

to:

```python
    # 'credit' = a finance-api vendor_credits row; invoice_id then carries the
    # vendor credit's id. api/v1/invoice_list.py filters on the literal 'epms' /
    # 'oa' values, so credits never leak into the unified invoice list.
    if invoice_source not in ("epms", "oa", "credit"):
        raise HTTPException(status_code=400,
                            detail="invoice_source must be 'epms', 'oa' or 'credit'")
```

Update the inline comment on the `invoice_source` query parameter (line 55) to `# 'epms' | 'oa' | 'credit'`, and the same comment on `invoice_source` in `app/models/invoice_attachment.py:17`.

- [ ] **Step 2: Write a test that the new source is accepted and the old list is untouched**

Create `expense-api/tests/test_invoice_attachments.py`. This suite's conftest
exposes **pre-authenticated** clients (`admin_client`, `finance_client`,
`requester_client`, …) rather than a `client` + `auth_headers` pair — `client`
itself is deliberately unauthenticated (`tests/conftest.py:131`).

```python
"""invoice_source whitelist on the shared invoice attachment store."""
import uuid

import pytest


@pytest.mark.anyio
async def test_credit_source_passes_the_whitelist(admin_client):
    r = await admin_client.post(
        "/api/v1/invoice-attachments",
        params={"invoice_id": str(uuid.uuid4()), "invoice_source": "credit"},
        files={"file": ("cn.pdf", b"%PDF-1.4 fake", "application/pdf")},
    )
    # 400 is the whitelist rejection specifically; any other status means the
    # guard let it through (storage may still fail without a live file-api).
    assert r.status_code != 400


@pytest.mark.anyio
async def test_unknown_source_is_still_rejected(admin_client):
    r = await admin_client.post(
        "/api/v1/invoice-attachments",
        params={"invoice_id": str(uuid.uuid4()), "invoice_source": "nonsense"},
        files={"file": ("x.pdf", b"%PDF-1.4 fake", "application/pdf")},
    )
    assert r.status_code == 400
    assert "invoice_source" in r.json()["detail"]
```

- [ ] **Step 3: Run the tests**

Run: `cd expense-api && python -m pytest tests/test_invoice_attachments.py -v`
Expected: both PASS. The first test asserts `!= 400` rather than a success code precisely because the upload path calls out to file-api; the whitelist is what is under test, not storage.

- [ ] **Step 4: Commit**

```bash
git add expense-api/app/api/v1/invoice_attachments.py expense-api/app/models/invoice_attachment.py expense-api/tests/test_invoice_attachments.py
git commit -m "feat(expense): accept 'credit' as an invoice attachment source"
```

---

## Task 8: EPMS finance client gains POST

**Files:**
- Modify: `epms/src/lib/api.ts:320-336`
- Create: `epms/src/services/vendorCredits.ts`
- Create: `epms/src/hooks/useVendorCredits.ts`

**Interfaces:**
- Consumes: `FINANCE_BASE`, `getToken` (already in `api.ts`)
- Produces:
  - `financeApi.post<T>(path: string, body?: unknown): Promise<T>`
  - `vendorCreditsService.{ list, get, create, approve, reject, void }`
  - `useVendorCredits(status?)`, `useCreateVendorCredit()`, `useReviewVendorCredit()`

- [ ] **Step 1: Extend `financeRequest` to carry a body**

In `epms/src/lib/api.ts`, replace the `financeRequest` function and the `financeApi` export with:

```ts
// ── finance-api (:8004) — bank/card accounts, vendor credits ──────────────────
async function financeRequest<T>(method: string, path: string, body?: unknown): Promise<T> {
  const token = getToken()
  const headers: Record<string, string> = {}
  if (token) headers['Authorization'] = `Bearer ${token}`
  if (body !== undefined) headers['Content-Type'] = 'application/json'
  const res = await fetch(`${FINANCE_BASE}/finance/v1${path}`, {
    method,
    headers,
    body: body !== undefined ? JSON.stringify(body) : undefined,
  })
  if (res.status === 204) return undefined as T
  if (!res.ok) {
    let detail = res.statusText
    try { const err = await res.json(); if (typeof err.detail === 'string') detail = err.detail } catch { /* ignore */ }
    const error = new Error(`finance-api: ${detail}`) as Error & { status?: number }
    error.status = res.status
    throw error
  }
  return res.json() as Promise<T>
}

export const financeApi = {
  get:  <T>(path: string)                 => financeRequest<T>('GET',  path),
  post: <T>(path: string, body?: unknown) => financeRequest<T>('POST', path, body),
}
```

The `status` property on the thrown error is what lets the upload modal single out a 409 duplicate.

- [ ] **Step 2: Write the service module**

Create `epms/src/services/vendorCredits.ts`:

```ts
import { financeApi } from '@/lib/api'

export interface VendorCredit {
  id: string
  credit_number: string
  vendor_id: string
  vendor_name: string
  vendor_credit_number: string
  credit_date: string
  currency: string
  /** Decimal-as-string — Pydantic serialises Decimal to JSON string. Always Number() before arithmetic. */
  amount: string
  tax_amount: string
  total_amount: string
  applied_amount: string
  remaining_amount: string
  status: 'pending_review' | 'available' | 'exhausted' | 'void'
  po_id: string | null
  po_number: string | null
  line_items: unknown[]
  file_name: string | null
  notes: string | null
  source: 'upload' | 'qbo_import'
  opening_balance: boolean
  uploaded_by: string
  uploaded_by_name: string | null
  uploaded_at: string
  reviewed_by: string | null
  reviewed_by_name: string | null
  reviewed_at: string | null
  review_note: string | null
}

export interface VendorCreditListResponse {
  items: VendorCredit[]
  total: number
}

export interface CreateVendorCreditBody {
  vendor_id: string
  vendor_name: string
  vendor_credit_number: string
  credit_date: string
  currency: string
  amount: number
  tax_amount: number
  po_id?: string | null
  po_number?: string | null
  line_items?: unknown[]
  file_name?: string | null
  notes?: string | null
}

const qs = (params: Record<string, string | undefined>) => {
  const sp = new URLSearchParams()
  for (const [k, v] of Object.entries(params)) if (v) sp.set(k, v)
  const s = sp.toString()
  return s ? `?${s}` : ''
}

export const vendorCreditsService = {
  list:    (status?: string) =>
    financeApi.get<VendorCreditListResponse>(`/vendor-credits${qs({ status })}`),
  get:     (id: string) =>
    financeApi.get<VendorCredit>(`/vendor-credits/${id}`),
  create:  (body: CreateVendorCreditBody) =>
    financeApi.post<VendorCredit>('/vendor-credits', body),
  approve: (id: string, note?: string) =>
    financeApi.post<VendorCredit>(`/vendor-credits/${id}/approve`, { note: note ?? null }),
  reject:  (id: string, note: string) =>
    financeApi.post<VendorCredit>(`/vendor-credits/${id}/reject`, { note }),
  void:    (id: string, note: string) =>
    financeApi.post<VendorCredit>(`/vendor-credits/${id}/void`, { note }),
}
```

- [ ] **Step 3: Write the query hooks**

Create `epms/src/hooks/useVendorCredits.ts`:

```ts
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'

import { vendorCreditsService, type CreateVendorCreditBody } from '@/services/vendorCredits'

export function useVendorCredits(status?: string) {
  return useQuery({
    queryKey: ['vendor-credits', status ?? 'all'],
    queryFn: () => vendorCreditsService.list(status),
    staleTime: 30_000,
  })
}

export function useCreateVendorCredit() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (body: CreateVendorCreditBody) => vendorCreditsService.create(body),
    onSuccess: () => { void qc.invalidateQueries({ queryKey: ['vendor-credits'] }) },
  })
}

type ReviewAction =
  | { id: string; action: 'approve'; note?: string }
  | { id: string; action: 'reject' | 'void'; note: string }

export function useReviewVendorCredit() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (v: ReviewAction) => {
      if (v.action === 'approve') return vendorCreditsService.approve(v.id, v.note)
      if (v.action === 'reject')  return vendorCreditsService.reject(v.id, v.note)
      return vendorCreditsService.void(v.id, v.note)
    },
    onSuccess: () => { void qc.invalidateQueries({ queryKey: ['vendor-credits'] }) },
  })
}
```

- [ ] **Step 4: Type-check**

Run: `cd epms && npx tsc -p tsconfig.app.json --noEmit 2>&1 | grep -c "error TS"`
Expected: `59` — the established baseline. A higher number means this task introduced errors.

If `node_modules` is missing in this worktree, run `npm ci` first. Skipping that produces a false pass: `npx tsc` prints an install prompt rather than compiler errors, and the grep then counts zero.

- [ ] **Step 5: Commit**

```bash
git add epms/src/lib/api.ts epms/src/services/vendorCredits.ts epms/src/hooks/useVendorCredits.ts
git commit -m "feat(epms): vendor credit client, hooks, and finance POST support"
```

---

## Task 9: Document-type switch in the upload modal

**Files:**
- Modify: `epms/src/lib/invoice-parser.ts`
- Modify: `epms/src/pages/invoices/InvoiceListPage.tsx`

**Interfaces:**
- Consumes: `useCreateVendorCredit` (Task 8); OCR `document_type` (Task 6)
- Produces: the upload modal can submit either an invoice (unchanged path) or a vendor credit

- [ ] **Step 1: Surface `document_type` from the parser**

In `epms/src/lib/invoice-parser.ts`, add to `OcrInvoiceResponse`:

```ts
  document_type?: 'invoice' | 'credit_note' | null
```

add to `ParsedInvoiceFields`:

```ts
  documentType: 'invoice' | 'credit_note'
```

and in the `fields` object literal:

```ts
      documentType:        r.document_type === 'credit_note' ? 'credit_note' : 'invoice',
```

- [ ] **Step 2: Add document-type state to the upload modal**

In `epms/src/pages/invoices/InvoiceListPage.tsx`, next to the existing `const [amount, setAmount] = useState('')` (line 128):

```tsx
  const [docType, setDocType] = useState<'invoice' | 'credit_note'>('invoice')
  const [docTypeAutoDetected, setDocTypeAutoDetected] = useState(false)
```

- [ ] **Step 3: Set it from OCR and from a negative total**

Inside `handleFile`, immediately after `const { fields } = result` (line 161):

```tsx
      // Two independent signals, OR'd: what the model called it, and the
      // structural fact of a negative total. Either one flips the form.
      const looksNegative =
        (fields.amount !== null && fields.amount < 0) ||
        (fields.lineItems?.some((l) => l.line_total < 0) ?? false)
      const detected = fields.documentType === 'credit_note' || looksNegative
      setDocType(detected ? 'credit_note' : 'invoice')
      setDocTypeAutoDetected(detected)
```

and change the amount prefill on line 193 so a credit note shows a positive figure:

```tsx
      if (fields.amount    !== null)  { setAmount(String(detected ? Math.abs(fields.amount) : fields.amount)); filled.add('amount') }
      if (fields.taxAmount !== null)  { setTaxAmount(String(detected ? Math.abs(fields.taxAmount) : fields.taxAmount)); filled.add('taxAmount') }
```

Also reset the flags at the top of `handleFile`, alongside `setLineItems([])`:

```tsx
    setDocType('invoice')
    setDocTypeAutoDetected(false)
```

- [ ] **Step 4: Render the switch**

Immediately above the Vendor field in the modal body, insert:

```tsx
{docTypeAutoDetected && docType === 'credit_note' ? (
  <div className="mb-4 rounded-lg border border-warning-300 bg-warning-50 p-3">
    <p className="text-sm font-medium text-warning-800">
      This looks like a Credit Note — the document total is negative.
    </p>
    <div className="mt-2 flex gap-4 text-sm">
      <label className="flex items-center gap-1.5">
        <input type="radio" checked={docType === 'invoice'}
               onChange={() => setDocType('invoice')} />
        Regular Invoice
      </label>
      <label className="flex items-center gap-1.5">
        <input type="radio" checked={docType === 'credit_note'}
               onChange={() => setDocType('credit_note')} />
        Credit Note
      </label>
    </div>
  </div>
) : (
  <p className="mb-4 text-xs text-neutral-500">
    Document type: {docType === 'credit_note' ? 'Credit Note' : 'Invoice'}
    {' · '}
    <button type="button" className="text-primary-600 underline"
            onClick={() => setDocType(docType === 'invoice' ? 'credit_note' : 'invoice')}>
      Change
    </button>
  </p>
)}
```

The collapsed variant is always rendered, not only on detection: a credit note that neither says "Credit" nor carries a negative total will never be auto-detected, and the cost of that miss is money.

- [ ] **Step 5: Relabel the fields when the form is in credit mode**

- Amount label (line 672): `{docType === 'credit_note' ? 'Credit Amount (pre-tax)' : 'Amount (pre-tax)'}`
- Tax label (line 687): `{docType === 'credit_note' ? 'Credit Tax' : 'Tax Amount'}`
- Vendor invoice number label: `{docType === 'credit_note' ? 'Credit Note #' : 'Vendor Invoice #'}`
- Wrap the Due Date field in `{docType === 'invoice' && ( … )}`
- Under the PO Number field, when `docType === 'credit_note'`, render:
  ```tsx
  <p className="mt-1 text-xs text-neutral-500">
    Optional — reference only. Credit notes are not 3-way matched.
  </p>
  ```
- Submit button label: `{docType === 'credit_note' ? 'Upload Credit Note' : 'Upload Invoice'}`

- [ ] **Step 6: Branch the submit handler**

Add near the other mutations in the modal component:

```tsx
  const createVendorCredit = useCreateVendorCredit()
```

and at the very top of `handleSubmit`, immediately after the `if (isDuplicate) return` guard (line 265):

```tsx
    if (docType === 'credit_note') {
      try {
        const credit = await createVendorCredit.mutateAsync({
          vendor_id: selectedVendor.id,
          vendor_name: selectedVendor.name,
          vendor_credit_number: vendorInvoiceNumber.trim(),
          credit_date: invoiceDate,
          currency,
          amount: amtNum,
          tax_amount: taxNum,
          po_number: poNumber.trim() || null,
          line_items: lineItems,
          file_name: file?.name ?? null,
          notes: notes.trim() || undefined,
        })
        if (file?.raw) {
          const form = new FormData()
          form.append('file', file.raw)
          const token = useAuthStore.getState().token
          await fetch(
            `${EXPENSE_BASE}/api/v1/invoice-attachments?invoice_id=${credit.id}&invoice_source=credit`,
            { method: 'POST', headers: { Authorization: `Bearer ${token}` }, body: form },
          ).catch((e) => console.error('Credit note attachment upload failed:', e))
        }
        onUploaded(credit.id)
      } catch (err) {
        const status = (err as { status?: number }).status
        setSubmitError(
          status === 409
            ? `${(err as Error).message}. Open Vendor Credits to review the existing entry.`
            : err instanceof Error ? err.message : 'Upload failed',
        )
      }
      return
    }
```

Placing the branch before the invoice path means a credit note never reaches the PO auto-match block.

- [ ] **Step 7: Make the negative-amount guard explain itself**

The gate on line 264 currently drops negative amounts silently. Change it to:

```tsx
    if (!selectedVendor || !vendorInvoiceNumber.trim() || !invoiceDate || !amount) return
    if (amtNum <= 0) {
      setSubmitError(
        docType === 'credit_note'
          ? 'Credit amount must be greater than zero.'
          : 'Negative total — this looks like a Credit Note. Switch the document type above.',
      )
      return
    }
```

Note the credit branch submits `amtNum` as a positive number: the form displays and posts positives, and finance-api applies `abs()` regardless.

- [ ] **Step 8: Type-check**

Run: `cd epms && npx tsc -p tsconfig.app.json --noEmit 2>&1 | grep -c "error TS"`
Expected: `59`

- [ ] **Step 9: Commit**

```bash
git add epms/src/lib/invoice-parser.ts epms/src/pages/invoices/InvoiceListPage.tsx
git commit -m "feat(epms): detect and upload credit notes from the invoice upload modal"
```

---

## Task 10: Vendor Credits review page

**Files:**
- Create: `epms/src/pages/vendorcredits/VendorCreditsPage.tsx`
- Modify: `epms/src/app/routes.tsx`
- Modify: `epms/src/components/layout/Sidebar.tsx`
- Modify: `epms/src/components/layout/Header.tsx`

**Interfaces:**
- Consumes: `useVendorCredits`, `useReviewVendorCredit` (Task 8)
- Produces: route `/vendor-credits`

**Gating:** the sidebar entry uses `view_invoice` — anyone who can see invoices can see the credit ledger, including the AP staffer who uploaded one and wants to check its status. The Approve / Reject / Void buttons check `epms.vendor_credit.manage` from `useRolePermissions()`.

- [ ] **Step 1: Write the page**

Create `epms/src/pages/vendorcredits/VendorCreditsPage.tsx`:

```tsx
import { useState } from 'react'

import { useRolePermissions } from '@/hooks/useConfig'
import { useReviewVendorCredit, useVendorCredits } from '@/hooks/useVendorCredits'
import type { VendorCredit } from '@/services/vendorCredits'
import { useAuthStore } from '@/stores/auth.store'

const TABS = [
  { key: 'pending_review', label: 'Pending Review' },
  { key: 'available',      label: 'Available' },
  { key: 'exhausted',      label: 'Exhausted' },
  { key: 'void',           label: 'Void' },
] as const

const fmt = (v: string, ccy: string) =>
  `${ccy} ${Number(v).toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`

export function VendorCreditsPage() {
  const [tab, setTab] = useState<(typeof TABS)[number]['key']>('pending_review')
  const [noteFor, setNoteFor] = useState<{ credit: VendorCredit; action: 'reject' | 'void' } | null>(null)
  const [note, setNote] = useState('')

  const { data, isLoading } = useVendorCredits(tab)
  const review = useReviewVendorCredit()

  const user  = useAuthStore((s) => s.user)
  const perms = useRolePermissions().data?.permissions
  const canManage = user?.role === 'system_admin' || !!perms?.['epms.vendor_credit.manage']

  const rows = data?.items ?? []

  return (
    <div className="p-6">
      <h1 className="text-xl font-semibold text-neutral-900">Vendor Credits</h1>
      <p className="mt-1 text-sm text-neutral-500">
        Credit notes received from vendors. Approved credits are netted off the next payment to that vendor.
      </p>

      <div className="mt-4 flex gap-1 border-b border-neutral-200">
        {TABS.map((t) => (
          <button key={t.key} onClick={() => setTab(t.key)}
                  className={`px-4 py-2 text-sm font-medium ${
                    tab === t.key
                      ? 'border-b-2 border-primary-600 text-primary-700'
                      : 'text-neutral-500 hover:text-neutral-700'}`}>
            {t.label}
          </button>
        ))}
      </div>

      {isLoading ? (
        <p className="mt-6 text-sm text-neutral-500">Loading…</p>
      ) : rows.length === 0 ? (
        <p className="mt-6 text-sm text-neutral-500">No credits in this state.</p>
      ) : (
        <table className="mt-4 w-full text-sm">
          <thead className="text-left text-xs uppercase text-neutral-500">
            <tr>
              <th className="px-4 py-2">Credit #</th>
              <th className="px-4 py-2">Vendor</th>
              <th className="px-4 py-2">Vendor Doc #</th>
              <th className="px-4 py-2">Date</th>
              <th className="px-4 py-2 text-right">Original</th>
              <th className="px-4 py-2 text-right">Applied</th>
              <th className="px-4 py-2 text-right">Remaining</th>
              <th className="px-4 py-2">PO</th>
              <th className="px-4 py-2" />
            </tr>
          </thead>
          <tbody>
            {rows.map((c) => (
              <tr key={c.id} className="border-t border-neutral-100">
                <td className="px-4 py-3 font-mono text-xs">
                  {c.credit_number}
                  {c.opening_balance && (
                    <span className="ml-2 rounded bg-neutral-100 px-1.5 py-0.5 text-[10px] text-neutral-600">
                      Opening balance
                    </span>
                  )}
                </td>
                <td className="px-4 py-3">{c.vendor_name}</td>
                <td className="px-4 py-3 font-mono text-xs">{c.vendor_credit_number}</td>
                <td className="px-4 py-3">{c.credit_date}</td>
                <td className="px-4 py-3 text-right font-mono">{fmt(c.total_amount, c.currency)}</td>
                <td className="px-4 py-3 text-right font-mono">{fmt(c.applied_amount, c.currency)}</td>
                <td className="px-4 py-3 text-right font-mono font-semibold">{fmt(c.remaining_amount, c.currency)}</td>
                <td className="px-4 py-3 text-xs text-neutral-500">{c.po_number ?? '—'}</td>
                <td className="px-4 py-3 text-right">
                  {canManage && c.status === 'pending_review' && (
                    <>
                      <button
                        className="mr-2 rounded bg-success-600 px-2.5 py-1 text-xs font-medium text-white"
                        disabled={review.isPending}
                        onClick={() => review.mutate({ id: c.id, action: 'approve' })}>
                        Approve
                      </button>
                      <button
                        className="rounded border border-danger-300 px-2.5 py-1 text-xs font-medium text-danger-700"
                        onClick={() => { setNoteFor({ credit: c, action: 'reject' }); setNote('') }}>
                        Reject
                      </button>
                    </>
                  )}
                  {canManage && c.status === 'available' && Number(c.applied_amount) === 0 && (
                    <button
                      className="rounded border border-neutral-300 px-2.5 py-1 text-xs font-medium text-neutral-700"
                      onClick={() => { setNoteFor({ credit: c, action: 'void' }); setNote('') }}>
                      Void
                    </button>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}

      {noteFor && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40">
          <div className="w-full max-w-md rounded-lg bg-white p-5">
            <h2 className="text-base font-semibold">
              {noteFor.action === 'reject' ? 'Reject' : 'Void'} {noteFor.credit.credit_number}
            </h2>
            <textarea
              className="mt-3 w-full rounded border border-neutral-300 p-2 text-sm"
              rows={3} value={note} onChange={(e) => setNote(e.target.value)}
              placeholder="Reason (required)" />
            <div className="mt-4 flex justify-end gap-2">
              <button className="rounded border border-neutral-300 px-3 py-1.5 text-sm"
                      onClick={() => setNoteFor(null)}>
                Cancel
              </button>
              <button
                className="rounded bg-danger-600 px-3 py-1.5 text-sm text-white disabled:opacity-50"
                disabled={!note.trim() || review.isPending}
                onClick={() => {
                  review.mutate(
                    { id: noteFor.credit.id, action: noteFor.action, note: note.trim() },
                    { onSuccess: () => setNoteFor(null) },
                  )
                }}>
                Confirm
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  )
}
```

- [ ] **Step 2: Register the route**

In `epms/src/app/routes.tsx`, import the page alongside the other page imports and add after the `/invoices` entry (line 57):

```tsx
  { path: '/vendor-credits', element: <VendorCreditsPage />, tab: { title: 'Vendor Credits', icon: 'FileText', keyStrategy: 'static' } },
```

`icon` is a string resolved by the tab host against a fixed name registry.
`FileText` is used verbatim by the existing `/invoices` entry, so it is known to
resolve; do not invent a new icon name here.

- [ ] **Step 3: Add the sidebar entry**

In `epms/src/components/layout/Sidebar.tsx`, in the `PROCUREMENT` section immediately after the Invoices item (line 55):

```tsx
      { label: 'Vendor Credits', href: '/vendor-credits', icon: <FileText className="h-4 w-4" />, permission: 'view_invoice' },
```

- [ ] **Step 4: Add the header title**

In `epms/src/components/layout/Header.tsx`, next to `invoices: 'Invoices'` (line 21):

```tsx
  'vendor-credits': 'Vendor Credits',
```

- [ ] **Step 5: Type-check**

Run: `cd epms && npx tsc -p tsconfig.app.json --noEmit 2>&1 | grep -c "error TS"`
Expected: `59`

- [ ] **Step 6: Verify in the running app**

Start the dev stack, log in as an `ap_clerk`, and confirm:
1. `/vendor-credits` renders with four tabs and an empty Pending Review list
2. Uploading the Amazon credit note PDF through Invoices → Upload flips the form to Credit Note mode and shows `0.04` as a positive Credit Amount
3. After submitting, the credit appears under Pending Review with `VC-` numbering
4. Approve moves it to the Available tab

- [ ] **Step 7: Commit**

```bash
git add epms/src/pages/vendorcredits/VendorCreditsPage.tsx epms/src/app/routes.tsx epms/src/components/layout/Sidebar.tsx epms/src/components/layout/Header.tsx
git commit -m "feat(epms): Vendor Credits review page"
```

---

## Phase A Done Criteria

- [ ] `finance-api` suite failure count equals the `origin/main` baseline — both numbers recorded, not asserted
- [ ] `expense-api` OCR suite passes in full
- [ ] `epms` tsc error count is 59
- [ ] The Amazon credit note from the bug report uploads successfully end-to-end
- [ ] `invoices` table, `InvoiceCreate.amount gt=0`, and `_normalize_negative_quantities()` are all untouched — confirm with `git diff --stat origin/main` that no such file appears

## Deferred to Phase B

`vendor_credit_applications` table · `payment_records.credit_applied` · FIFO application in `payment_execute.execute()` · the `vendor_credit_clearing` posting line · the `req.amount_paid` posting inconsistency (spec §6.6) · remittance advice Gross/Credits/Net display.

## Deferred to Phase C

`qbo_vendor_map` table · the mapping workbench · opening-balance import · drift detection.
