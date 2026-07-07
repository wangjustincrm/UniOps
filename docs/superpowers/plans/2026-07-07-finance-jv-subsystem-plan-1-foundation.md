# JV Subsystem — Plan 1: Data Model + Generation (Implementation Plan)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add the journal-voucher (总账凭证) tables and auto-generate a `draft` JV from every finance posting event, inside the same transaction.

**Architecture:** New `journal_vouchers` + `journal_voucher_lines` + `jv_line_dimensions` tables (finance-api owned). A new `services/journal_voucher.py::generate_from_event()` copies a just-emitted `posting_event` + its `posting_lines` (+ long-tail dimensions) into a balanced `draft` JV, keyed 1:1 on `posting_event_id` (idempotent). `emit_event()` gains a `prepared_by` param and calls the generator at the end. Dual currency: original amounts come from the posting line; local (CAD) = `orig × fx_rate`. Quantity fields stay null for business events (they are populated only by the separate NC-import path).

**Tech Stack:** Python 3.12, FastAPI, SQLAlchemy 2 (async), Alembic, Postgres, pytest/pytest-asyncio. All paths under `finance-api/`.

**Spec:** `docs/superpowers/specs/2026-07-07-finance-jv-subsystem-design.md` (§2 data model, §3 generation). This plan covers §2 + §3 only; lifecycle/GL-migration/UI are later plans.

---

## File Structure

- Create `finance-api/alembic/versions/0017_journal_vouchers.py` — the three tables (down_revision `0016_payment_account_kind`, the current head).
- Create `finance-api/app/models/journal_voucher.py` — `JournalVoucher`, `JournalVoucherLine`, `JvLineDimension` ORM models.
- Create `finance-api/app/services/journal_voucher.py` — `next_jv_number()`, `build_summary()`, `generate_from_event()`.
- Modify `finance-api/app/services/posting.py` — add `prepared_by` param to `emit_event`; call `generate_from_event` before returning.
- Create `finance-api/tests/test_journal_voucher.py` — unit tests for the generator.

Run tests with: `cd finance-api && python -m pytest tests/test_journal_voucher.py -v` (needs local `finance_test` DB per `tests/conftest.py`; see [[feedback_uniops_admin_test_db_env]]).

---

### Task 1: Migration — create the three JV tables

**Files:**
- Create: `finance-api/alembic/versions/0017_journal_vouchers.py`

- [ ] **Step 1: Write the migration**

```python
"""create journal_vouchers + journal_voucher_lines + jv_line_dimensions

Revision ID: 0017_journal_vouchers
Revises: 0016_payment_account_kind
Create Date: 2026-07-07
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

revision = "0017_journal_vouchers"
down_revision = "0016_payment_account_kind"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "journal_vouchers",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("jv_number", sa.String(40), nullable=False),
        sa.Column("voucher_word", sa.String(10), nullable=False, server_default="JV"),
        sa.Column("voucher_date", sa.Date(), nullable=False),
        sa.Column("fiscal_period", sa.String(7), nullable=False),
        sa.Column("summary", sa.String(255), nullable=True),
        sa.Column("status", sa.String(10), nullable=False, server_default="draft"),
        sa.Column("posting_event_id", UUID(as_uuid=True),
                  sa.ForeignKey("posting_events.id", ondelete="SET NULL"), nullable=True),
        sa.Column("source_service", sa.String(20), nullable=True),
        sa.Column("source_doc_type", sa.String(30), nullable=True),
        sa.Column("source_doc_id", UUID(as_uuid=True), nullable=True),
        sa.Column("source_doc_number", sa.String(40), nullable=True),
        sa.Column("prepared_by", UUID(as_uuid=True), nullable=True),
        sa.Column("prepared_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("reviewed_by", UUID(as_uuid=True), nullable=True),
        sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("posted_by", UUID(as_uuid=True), nullable=True),
        sa.Column("posted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("reverses_jv_id", UUID(as_uuid=True), nullable=True),
        sa.Column("reversed_by_jv_id", UUID(as_uuid=True), nullable=True),
        sa.Column("total_debit", sa.Numeric(15, 2), nullable=False, server_default="0"),
        sa.Column("total_credit", sa.Numeric(15, 2), nullable=False, server_default="0"),
        sa.Column("total_local_debit", sa.Numeric(15, 2), nullable=False, server_default="0"),
        sa.Column("total_local_credit", sa.Numeric(15, 2), nullable=False, server_default="0"),
        sa.Column("entity_id", UUID(as_uuid=True), nullable=True),
        sa.Column("nc_source_pk", sa.String(40), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("posting_event_id", name="uq_journal_vouchers_event"),
        sa.CheckConstraint("status in ('draft','reviewed','posted','reversed')",
                           name="ck_journal_vouchers_status"),
    )
    op.create_index("ix_journal_vouchers_period", "journal_vouchers", ["fiscal_period"])
    op.create_index("ix_journal_vouchers_status", "journal_vouchers", ["status"])
    op.create_index("ix_journal_vouchers_doc", "journal_vouchers",
                    ["source_doc_type", "source_doc_id"])

    op.create_table(
        "journal_voucher_lines",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("jv_id", UUID(as_uuid=True),
                  sa.ForeignKey("journal_vouchers.id", ondelete="CASCADE"), nullable=False),
        sa.Column("line_no", sa.Integer(), nullable=False),
        sa.Column("account_code", sa.String(40), nullable=True),
        sa.Column("summary", sa.String(255), nullable=True),
        sa.Column("orig_debit", sa.Numeric(15, 2), nullable=False, server_default="0"),
        sa.Column("orig_credit", sa.Numeric(15, 2), nullable=False, server_default="0"),
        sa.Column("local_debit", sa.Numeric(15, 2), nullable=False, server_default="0"),
        sa.Column("local_credit", sa.Numeric(15, 2), nullable=False, server_default="0"),
        sa.Column("currency", sa.String(10), nullable=False, server_default="CAD"),
        sa.Column("fx_rate", sa.Numeric(12, 6), nullable=False, server_default="1"),
        sa.Column("quantity", sa.Numeric(18, 4), nullable=True),
        sa.Column("unit", sa.String(30), nullable=True),
        sa.Column("price", sa.Numeric(18, 6), nullable=True),
        sa.Column("cost_center_id", UUID(as_uuid=True), nullable=True),
        sa.Column("department_id", UUID(as_uuid=True), nullable=True),
        sa.Column("partner_id", UUID(as_uuid=True), nullable=True),
        sa.Column("partner_name", sa.String(255), nullable=True),
        sa.Column("tax_code", sa.String(20), nullable=True),
        sa.Column("project_id", UUID(as_uuid=True), nullable=True),
        sa.Column("item_id", UUID(as_uuid=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint("NOT (orig_debit > 0 AND orig_credit > 0)",
                           name="ck_jv_lines_one_side"),
    )
    op.create_index("ix_jv_lines_jv_id", "journal_voucher_lines", ["jv_id"])
    op.create_index("ix_jv_lines_account", "journal_voucher_lines", ["account_code"])

    op.create_table(
        "jv_line_dimensions",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("jv_line_id", UUID(as_uuid=True),
                  sa.ForeignKey("journal_voucher_lines.id", ondelete="CASCADE"), nullable=False),
        sa.Column("dim_code", sa.String(40), nullable=False),
        sa.Column("value_id", UUID(as_uuid=True), nullable=True),
        sa.Column("value_text", sa.String(255), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_jv_line_dimensions_line", "jv_line_dimensions", ["jv_line_id"])


def downgrade():
    op.drop_table("jv_line_dimensions")
    op.drop_table("journal_voucher_lines")
    op.drop_table("journal_vouchers")
```

- [ ] **Step 2: Verify the migration applies cleanly**

Run: `cd finance-api && python -m pytest tests/test_ap_accrual.py -v`
Expected: PASS — the `db_session` fixture runs `alembic upgrade head`, which now includes `0017`. If `0017` had a schema error the fixture setup would fail. (Any existing passing test exercises the fixture.)

- [ ] **Step 3: Commit**

```bash
git add finance-api/alembic/versions/0017_journal_vouchers.py
git commit -m "feat(finance): migration for journal_vouchers + lines + dimensions"
```

---

### Task 2: ORM models

**Files:**
- Create: `finance-api/app/models/journal_voucher.py`
- Test: `finance-api/tests/test_journal_voucher.py`

- [ ] **Step 1: Write the failing test**

```python
"""JV subsystem — data model + generation (Plan 1)."""
import uuid
from datetime import date, datetime, timezone
from decimal import Decimal

import pytest
import pytest_asyncio
from sqlalchemy import select

from app.models.journal_voucher import JournalVoucher, JournalVoucherLine, JvLineDimension


async def test_can_insert_jv_with_lines_and_dims(db_session):
    jv = JournalVoucher(
        jv_number="JV-202607-0001", voucher_word="JV",
        voucher_date=date(2026, 7, 1), fiscal_period="2026-07",
        summary="test", status="draft",
        total_debit=Decimal("100.00"), total_credit=Decimal("100.00"),
        total_local_debit=Decimal("100.00"), total_local_credit=Decimal("100.00"),
    )
    db_session.add(jv)
    await db_session.flush()
    line = JournalVoucherLine(
        jv_id=jv.id, line_no=1, account_code="5000",
        orig_debit=Decimal("100.00"), local_debit=Decimal("100.00"),
        currency="CAD", fx_rate=Decimal("1"),
    )
    db_session.add(line)
    await db_session.flush()
    db_session.add(JvLineDimension(jv_line_id=line.id, dim_code="cost_center",
                                   value_text="CC-1"))
    await db_session.flush()

    got = (await db_session.execute(
        select(JournalVoucher).where(JournalVoucher.id == jv.id))).scalar_one()
    assert got.status == "draft"
    assert got.jv_number == "JV-202607-0001"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd finance-api && python -m pytest tests/test_journal_voucher.py::test_can_insert_jv_with_lines_and_dims -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'app.models.journal_voucher'`

- [ ] **Step 3: Write the models**

```python
"""Journal vouchers (总账凭证) — formal GL vouchers over the posting spine.

One posting_event → one draft JV (generated in the same transaction). JV becomes
the GL source of truth once posted. Holds dual-currency (orig + local/CAD) and
quantity so it also contains imported NC65 history.
"""
import uuid
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import (
    CheckConstraint, Date, DateTime, ForeignKey, Integer, Numeric, String,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin, UUIDPrimaryKey

DRAFT = "draft"
REVIEWED = "reviewed"
POSTED = "posted"
REVERSED = "reversed"


class JournalVoucher(UUIDPrimaryKey, TimestampMixin, Base):
    __tablename__ = "journal_vouchers"
    __table_args__ = (
        UniqueConstraint("posting_event_id", name="uq_journal_vouchers_event"),
        CheckConstraint("status in ('draft','reviewed','posted','reversed')",
                        name="ck_journal_vouchers_status"),
    )

    jv_number: Mapped[str] = mapped_column(String(40), nullable=False)
    voucher_word: Mapped[str] = mapped_column(String(10), nullable=False, default="JV")
    voucher_date: Mapped[date] = mapped_column(Date, nullable=False)
    fiscal_period: Mapped[str] = mapped_column(String(7), nullable=False, index=True)
    summary: Mapped[str | None] = mapped_column(String(255), nullable=True)
    status: Mapped[str] = mapped_column(String(10), nullable=False, default=DRAFT, index=True)

    posting_event_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("posting_events.id", ondelete="SET NULL"), nullable=True)
    source_service: Mapped[str | None] = mapped_column(String(20), nullable=True)
    source_doc_type: Mapped[str | None] = mapped_column(String(30), nullable=True)
    source_doc_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    source_doc_number: Mapped[str | None] = mapped_column(String(40), nullable=True)

    prepared_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    prepared_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    reviewed_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    posted_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    posted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    reverses_jv_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    reversed_by_jv_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)

    total_debit: Mapped[Decimal] = mapped_column(Numeric(15, 2), nullable=False, default=Decimal("0"))
    total_credit: Mapped[Decimal] = mapped_column(Numeric(15, 2), nullable=False, default=Decimal("0"))
    total_local_debit: Mapped[Decimal] = mapped_column(Numeric(15, 2), nullable=False, default=Decimal("0"))
    total_local_credit: Mapped[Decimal] = mapped_column(Numeric(15, 2), nullable=False, default=Decimal("0"))

    entity_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    nc_source_pk: Mapped[str | None] = mapped_column(String(40), nullable=True)


class JournalVoucherLine(UUIDPrimaryKey, TimestampMixin, Base):
    __tablename__ = "journal_voucher_lines"
    __table_args__ = (
        CheckConstraint("NOT (orig_debit > 0 AND orig_credit > 0)", name="ck_jv_lines_one_side"),
    )

    jv_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("journal_vouchers.id", ondelete="CASCADE"),
        nullable=False, index=True)
    line_no: Mapped[int] = mapped_column(Integer, nullable=False)
    account_code: Mapped[str | None] = mapped_column(String(40), nullable=True, index=True)
    summary: Mapped[str | None] = mapped_column(String(255), nullable=True)

    orig_debit: Mapped[Decimal] = mapped_column(Numeric(15, 2), nullable=False, default=Decimal("0"))
    orig_credit: Mapped[Decimal] = mapped_column(Numeric(15, 2), nullable=False, default=Decimal("0"))
    local_debit: Mapped[Decimal] = mapped_column(Numeric(15, 2), nullable=False, default=Decimal("0"))
    local_credit: Mapped[Decimal] = mapped_column(Numeric(15, 2), nullable=False, default=Decimal("0"))
    currency: Mapped[str] = mapped_column(String(10), nullable=False, default="CAD")
    fx_rate: Mapped[Decimal] = mapped_column(Numeric(12, 6), nullable=False, default=Decimal("1"))

    quantity: Mapped[Decimal | None] = mapped_column(Numeric(18, 4), nullable=True)
    unit: Mapped[str | None] = mapped_column(String(30), nullable=True)
    price: Mapped[Decimal | None] = mapped_column(Numeric(18, 6), nullable=True)

    cost_center_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    department_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    partner_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    partner_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    tax_code: Mapped[str | None] = mapped_column(String(20), nullable=True)
    project_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    item_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)


class JvLineDimension(UUIDPrimaryKey, TimestampMixin, Base):
    __tablename__ = "jv_line_dimensions"

    jv_line_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("journal_voucher_lines.id", ondelete="CASCADE"),
        nullable=False, index=True)
    dim_code: Mapped[str] = mapped_column(String(40), nullable=False)
    value_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    value_text: Mapped[str | None] = mapped_column(String(255), nullable=True)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd finance-api && python -m pytest tests/test_journal_voucher.py::test_can_insert_jv_with_lines_and_dims -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add finance-api/app/models/journal_voucher.py finance-api/tests/test_journal_voucher.py
git commit -m "feat(finance): JournalVoucher/Line/Dimension ORM models"
```

---

### Task 3: Number + summary helpers

**Files:**
- Create: `finance-api/app/services/journal_voucher.py`
- Test: `finance-api/tests/test_journal_voucher.py` (append)

- [ ] **Step 1: Write the failing test (append to test file)**

```python
async def test_next_jv_number_increments_per_period(db_session):
    from app.services.journal_voucher import next_jv_number
    n1 = await next_jv_number(db_session, "2026-07")
    assert n1 == "JV-202607-0001"
    db_session.add(JournalVoucher(
        jv_number=n1, voucher_word="JV", voucher_date=date(2026, 7, 1),
        fiscal_period="2026-07", status="draft"))
    await db_session.flush()
    n2 = await next_jv_number(db_session, "2026-07")
    assert n2 == "JV-202607-0002"
    # different period resets
    assert await next_jv_number(db_session, "2026-08") == "JV-202608-0001"


def test_build_summary_templates():
    from app.services.journal_voucher import build_summary
    assert build_summary("ap_invoice", "accrual", "AP-1", "ACME") == "应付计提 · AP-1 · ACME"
    assert build_summary("pa", "payment", "PA-9", "ACME") == "付款 · PA-9 · ACME"
    # unknown event falls back to doc number
    assert build_summary("gl_opening", "opening", "OB-1", None) == "OB-1"
```

- [ ] **Step 2: Run to verify it fails**

Run: `cd finance-api && python -m pytest tests/test_journal_voucher.py -k "number or summary" -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'app.services.journal_voucher'`

- [ ] **Step 3: Implement helpers**

```python
"""Journal voucher generation service.

generate_from_event() turns one just-emitted posting_event (+ its lines and
long-tail dimensions) into a balanced `draft` JournalVoucher, 1:1 idempotent on
posting_event_id. Called at the tail of emit_event() inside the same txn.
"""
import uuid
from datetime import datetime, timezone
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.journal_voucher import DRAFT, JournalVoucher, JournalVoucherLine, JvLineDimension
from app.models.posting import PostingEvent, PostingLine, PostingLineDimension

_ZERO = Decimal("0")

# event_type/source_doc_type → summary template (Chinese; user-facing UI strings
# are handled elsewhere — this is a stored memo, editable later).
_SUMMARY = {
    ("ap_invoice", "accrual"): "应付计提",
    ("pa", "payment"): "付款",
    ("pa_dir", "payment"): "付款",
    ("exp", "expense_paid"): "报销付款",
    ("trv", "expense_paid"): "报销付款",
    ("cfm", "expense_paid"): "报销付款",
    ("mil", "expense_paid"): "报销付款",
}


def _q(v) -> Decimal:
    return Decimal(str(v)).quantize(Decimal("0.01"))


def build_summary(source_doc_type: str, event_type: str,
                  doc_number: str | None, partner_name: str | None) -> str:
    prefix = _SUMMARY.get((source_doc_type, event_type))
    if not prefix:
        return doc_number or ""
    parts = [prefix, doc_number or ""]
    if partner_name:
        parts.append(partner_name)
    return " · ".join(p for p in parts if p)


async def next_jv_number(db: AsyncSession, fiscal_period: str, voucher_word: str = "JV") -> str:
    yyyymm = fiscal_period.replace("-", "")
    like = f"{voucher_word}-{yyyymm}-%"
    n = (await db.execute(
        select(func.count()).select_from(JournalVoucher).where(
            JournalVoucher.jv_number.like(like))
    )).scalar_one()
    return f"{voucher_word}-{yyyymm}-{n + 1:04d}"
```

- [ ] **Step 4: Run to verify it passes**

Run: `cd finance-api && python -m pytest tests/test_journal_voucher.py -k "number or summary" -v`
Expected: PASS (2 passed)

- [ ] **Step 5: Commit**

```bash
git add finance-api/app/services/journal_voucher.py finance-api/tests/test_journal_voucher.py
git commit -m "feat(finance): JV number + summary helpers"
```

---

### Task 4: `generate_from_event()` — posting event → draft JV

**Files:**
- Modify: `finance-api/app/services/journal_voucher.py`
- Test: `finance-api/tests/test_journal_voucher.py` (append)

- [ ] **Step 1: Write the failing test (append)**

```python
async def _make_event(db, *, currency="CAD", fx="1", debit="100.00", credit="0"):
    from app.services.posting import emit_event
    ev_id = await emit_event(
        db, source_service="finance", source_doc_type="ap_invoice",
        source_doc_id=uuid.uuid4(), source_doc_number="AP-1", event_type="accrual",
        lines=[
            {"line_role": "purchase_expense", "account_code": "5000",
             "debit": Decimal(debit), "currency": currency, "fx_rate": Decimal(fx),
             "partner_name": "ACME"},
            {"line_role": "accounts_payable", "account_code": "2000",
             "credit": Decimal("100.00"), "currency": currency, "fx_rate": Decimal(fx),
             "partner_name": "ACME"},
        ],
    )
    return ev_id


async def test_generate_from_event_creates_balanced_draft_jv(db_session):
    from app.services.journal_voucher import generate_from_event
    prepared = uuid.uuid4()
    ev_id = await _make_event(db_session)
    jv = await generate_from_event(db_session, ev_id, prepared)
    assert jv is not None
    assert jv.status == "draft"
    assert jv.jv_number.startswith("JV-")
    assert jv.prepared_by == prepared
    assert jv.summary == "应付计提 · AP-1 · ACME"
    assert jv.total_debit == Decimal("100.00")
    assert jv.total_credit == Decimal("100.00")
    lines = (await db_session.execute(
        select(JournalVoucherLine).where(JournalVoucherLine.jv_id == jv.id)
        .order_by(JournalVoucherLine.line_no))).scalars().all()
    assert len(lines) == 2
    assert lines[0].account_code == "5000"
    assert lines[0].orig_debit == Decimal("100.00")
    assert lines[0].local_debit == Decimal("100.00")   # CAD, fx 1


async def test_generate_computes_local_from_fx(db_session):
    from app.services.journal_voucher import generate_from_event
    ev_id = await _make_event(db_session, currency="USD", fx="1.35", debit="100.00")
    jv = await generate_from_event(db_session, ev_id, uuid.uuid4())
    line = (await db_session.execute(
        select(JournalVoucherLine).where(JournalVoucherLine.jv_id == jv.id,
                                          JournalVoucherLine.orig_debit > 0))).scalar_one()
    assert line.currency == "USD"
    assert line.orig_debit == Decimal("100.00")
    assert line.local_debit == Decimal("135.00")       # 100 * 1.35
    assert jv.total_local_debit == Decimal("135.00")


async def test_generate_is_idempotent_per_event(db_session):
    from app.services.journal_voucher import generate_from_event
    ev_id = await _make_event(db_session)
    jv1 = await generate_from_event(db_session, ev_id, uuid.uuid4())
    jv2 = await generate_from_event(db_session, ev_id, uuid.uuid4())
    assert jv2.id == jv1.id                              # returns existing, no dup
    all_jv = (await db_session.execute(
        select(JournalVoucher).where(JournalVoucher.posting_event_id == ev_id))).scalars().all()
    assert len(all_jv) == 1
```

- [ ] **Step 2: Run to verify it fails**

Run: `cd finance-api && python -m pytest tests/test_journal_voucher.py -k generate -v`
Expected: FAIL with `ImportError: cannot import name 'generate_from_event'`

- [ ] **Step 3: Implement `generate_from_event` (append to service)**

```python
async def generate_from_event(db: AsyncSession, event_id: uuid.UUID,
                              prepared_by: uuid.UUID | None) -> JournalVoucher | None:
    """Create a draft JV from a posting_event. Idempotent on posting_event_id —
    if a JV already exists for this event, returns it without creating a second."""
    existing = (await db.execute(
        select(JournalVoucher).where(JournalVoucher.posting_event_id == event_id)
    )).scalar_one_or_none()
    if existing is not None:
        return existing

    ev = (await db.execute(
        select(PostingEvent).where(PostingEvent.id == event_id)
    )).scalar_one_or_none()
    if ev is None:
        return None

    plines = (await db.execute(
        select(PostingLine).where(PostingLine.event_id == event_id)
        .order_by(PostingLine.line_no)
    )).scalars().all()

    jv = JournalVoucher(
        jv_number=await next_jv_number(db, ev.fiscal_period or ev.occurred_at.strftime("%Y-%m")),
        voucher_word="JV",
        voucher_date=ev.occurred_at.date(),
        fiscal_period=ev.fiscal_period or ev.occurred_at.strftime("%Y-%m"),
        summary=build_summary(ev.source_doc_type, ev.event_type, ev.source_doc_number,
                              next((p.partner_name for p in plines if p.partner_name), None)),
        status=DRAFT,
        posting_event_id=ev.id,
        source_service=ev.source_service, source_doc_type=ev.source_doc_type,
        source_doc_id=ev.source_doc_id, source_doc_number=ev.source_doc_number,
        prepared_by=prepared_by, prepared_at=datetime.now(timezone.utc),
        entity_id=ev.entity_id,
    )
    db.add(jv)
    await db.flush()

    tot_d = tot_c = tot_ld = tot_lc = _ZERO
    line_map: list[tuple[JournalVoucherLine, uuid.UUID]] = []
    for pl in plines:
        rate = pl.fx_rate if pl.fx_rate is not None else Decimal("1")
        ld = _q(pl.debit * rate)
        lc = _q(pl.credit * rate)
        jl = JournalVoucherLine(
            jv_id=jv.id, line_no=pl.line_no, account_code=pl.account_code,
            summary=pl.memo,
            orig_debit=pl.debit, orig_credit=pl.credit,
            local_debit=ld, local_credit=lc,
            currency=pl.currency, fx_rate=rate,
            cost_center_id=pl.cost_center_id, department_id=pl.department_id,
            partner_id=pl.partner_id, partner_name=pl.partner_name,
            tax_code=pl.tax_code, project_id=pl.project_id, item_id=pl.item_id,
        )
        db.add(jl)
        tot_d += pl.debit; tot_c += pl.credit; tot_ld += ld; tot_lc += lc
        line_map.append((jl, pl.id))
    await db.flush()

    # copy long-tail dimensions
    for jl, pl_id in line_map:
        dims = (await db.execute(
            select(PostingLineDimension).where(PostingLineDimension.posting_line_id == pl_id)
        )).scalars().all()
        for d in dims:
            db.add(JvLineDimension(jv_line_id=jl.id, dim_code=d.dim_code,
                                   value_id=d.value_id, value_text=d.value_text))

    jv.total_debit = _q(tot_d); jv.total_credit = _q(tot_c)
    jv.total_local_debit = _q(tot_ld); jv.total_local_credit = _q(tot_lc)
    await db.flush()
    return jv
```

- [ ] **Step 4: Run to verify it passes**

Run: `cd finance-api && python -m pytest tests/test_journal_voucher.py -k generate -v`
Expected: PASS (3 passed)

- [ ] **Step 5: Commit**

```bash
git add finance-api/app/services/journal_voucher.py finance-api/tests/test_journal_voucher.py
git commit -m "feat(finance): generate draft JV from posting event (dual-ccy, idempotent)"
```

---

### Task 5: Wire `emit_event` to generate the JV

**Files:**
- Modify: `finance-api/app/services/posting.py`
- Test: `finance-api/tests/test_journal_voucher.py` (append)

- [ ] **Step 1: Write the failing test (append)**

```python
async def test_emit_event_auto_generates_jv(db_session):
    from app.services.posting import emit_event
    ev_id = await emit_event(
        db_session, source_service="finance", source_doc_type="pa",
        source_doc_id=uuid.uuid4(), source_doc_number="PA-9", event_type="payment",
        prepared_by=uuid.uuid4(),
        lines=[
            {"line_role": "accounts_payable", "account_code": "2000",
             "debit": Decimal("50.00"), "currency": "CAD"},
            {"line_role": "bank", "account_code": "1000",
             "credit": Decimal("50.00"), "currency": "CAD"},
        ],
    )
    jv = (await db_session.execute(
        select(JournalVoucher).where(JournalVoucher.posting_event_id == ev_id))).scalar_one()
    assert jv.status == "draft"
    assert jv.summary == "付款 · PA-9 · "  # no partner_name on these lines
    assert jv.total_debit == Decimal("50.00")


async def test_emit_event_idempotent_skip_makes_no_jv(db_session):
    from app.services.posting import emit_event
    doc_id = uuid.uuid4()
    kw = dict(source_service="finance", source_doc_type="pa", source_doc_id=doc_id,
              source_doc_number="PA-1", event_type="payment",
              lines=[{"line_role": "bank", "credit": Decimal("1.00"), "currency": "CAD"},
                     {"line_role": "accounts_payable", "debit": Decimal("1.00"), "currency": "CAD"}])
    await emit_event(db_session, **kw)
    second = await emit_event(db_session, **kw)   # ON CONFLICT DO NOTHING → None
    assert second is None
    jvs = (await db_session.execute(select(JournalVoucher).where(
        JournalVoucher.source_doc_id == doc_id))).scalars().all()
    assert len(jvs) == 1
```

- [ ] **Step 2: Run to verify it fails**

Run: `cd finance-api && python -m pytest tests/test_journal_voucher.py -k emit_event -v`
Expected: FAIL — `emit_event()` got an unexpected keyword argument `prepared_by`.

- [ ] **Step 3: Modify `emit_event`**

In `finance-api/app/services/posting.py`, change the signature to add `prepared_by` and call the generator before returning. Update the signature:

```python
async def emit_event(
    db: AsyncSession,
    *,
    source_service: str,
    source_doc_type: str,
    source_doc_id: uuid.UUID,
    source_doc_number: str,
    event_type: str,
    lines: list[dict],
    occurred_at: datetime | None = None,
    prepared_by: uuid.UUID | None = None,
) -> uuid.UUID | None:
```

Then replace the final `await db.flush()` / `return event_id` tail (currently the last two lines of the function) with:

```python
    await db.flush()

    from app.services.journal_voucher import generate_from_event
    await generate_from_event(db, event_id, prepared_by)
    return event_id
```

(The import is function-local to avoid a circular import: `journal_voucher.py` imports from `posting` models, and `emit_event` now needs the generator.)

- [ ] **Step 4: Run to verify it passes**

Run: `cd finance-api && python -m pytest tests/test_journal_voucher.py -k emit_event -v`
Expected: PASS (2 passed)

- [ ] **Step 5: Run the full finance suite (regression — every emit_event caller now also writes a JV)**

Run: `cd finance-api && python -m pytest -q`
Expected: PASS — all pre-existing tests still green (AP accrual, AR, payment, gl). If a test that counts rows in an unrelated table breaks, investigate; JV rows are additive and should not affect existing assertions.

- [ ] **Step 6: Commit**

```bash
git add finance-api/app/services/posting.py finance-api/tests/test_journal_voucher.py
git commit -m "feat(finance): auto-generate draft JV on every posting event"
```

---

## Self-Review

**Spec coverage (§2 + §3 of the JV spec):**
- `journal_vouchers` / `journal_voucher_lines` / `jv_line_dimensions` tables + models — Tasks 1–2. ✓
- Dual currency (orig + local, fx) — Task 4 (`local = orig × fx_rate`). ✓
- Quantity/unit/price columns — Task 1/2 (nullable; populated by NC import path in a later plan, null for business events). ✓
- JV number `JV-YYYYMM-NNNN` per-period — Task 3. ✓
- Summary templates (editable later) — Task 3. ✓
- Inline generation wrapping `emit_event`, `prepared_by`, same-txn atomic, idempotent on `posting_event_id` — Tasks 4–5. ✓
- `posting_event_id` unique + nullable (import/manual later) — Task 1 (`uq_journal_vouchers_event`, nullable). ✓
- Balance totals on header — Task 4. ✓

**Not in this plan (later plans, by design):** lifecycle 审核/过账/弃审/红冲 + SoD (Plan 2); GL read-layer migration + backfill (Plan 3); 凭证中心 API/UI (Plan 4); NC-import path that populates quantity/local/nc_source_pk directly (NC migration plan).

**Placeholder scan:** none — every step has complete code/commands.

**Type consistency:** `generate_from_event(db, event_id, prepared_by) -> JournalVoucher | None`, `next_jv_number(db, fiscal_period, voucher_word='JV')`, `build_summary(source_doc_type, event_type, doc_number, partner_name)` used consistently across Tasks 3–5. `emit_event` gains `prepared_by` keyword used in Task 5 tests. Model field names (`orig_debit`, `local_debit`, `posting_event_id`, `jv_number`) consistent across migration, model, service, tests.
