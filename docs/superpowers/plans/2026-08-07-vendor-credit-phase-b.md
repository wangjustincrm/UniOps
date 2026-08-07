# Vendor Credit — Phase B Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Automatically net approved vendor credits off the next payment to that vendor, inside the same transaction as the payment record, with the general ledger and the remittance advice both telling the truth about what was paid.

**Architecture:** All work is in `finance-api`, plus one page in the `finance` frontend. A new `vendor_credit_applications` table records which credit paid down which document and by how much. `payment_execute.execute()` — the single executor both the one-off payment route and the batch runner already funnel through — selects available credits FIFO under a row lock, reduces the cash paid, and posts a three-line voucher so the bank leg equals the cash that actually left. The AP-editable preview rides on the batch page, the only surface from which payments are actually executed today.

**Tech Stack:** FastAPI · SQLAlchemy 2 async · Alembic · Pydantic v2 · pytest/pytest-asyncio (`asyncio_mode = auto`) · Postgres row locking (`FOR UPDATE`, `FOR UPDATE SKIP LOCKED`) · React 18 + TypeScript + TanStack Query

**Spec:** `docs/superpowers/specs/2026-08-06-vendor-credit-management-design.md` — Phase B is §6 (payment application) plus the §6.6 decision.

**Builds on:** Phase A, complete at `4ff9187`. `vendor_credits` exists with `applied_amount` / `remaining_amount` / `status` and the CHECK trio; nothing writes to `applied_amount` yet.

## Global Constraints

- **This phase moves real money.** Every arithmetic change needs a test that pins the resulting numbers, not just that the call succeeded.
- Every monetary column in `vendor_credits` stays POSITIVE. `abs()` remains only in `crud/vendor_credit.py` `_positive()`. Netting subtracts positives; it never stores a negative.
- **Currency must match exactly. No FX conversion.** A CAD credit never applies to a USD payment.
- Credits apply to `doc_kind` `pa` and `pa_dir` only — both are vendor payments against `payment_applications`. **`expense_claim` is never eligible**: it pays an employee, and it has no `vendor_id`. Enforce this explicitly rather than letting it fall out of a join.
- `net` floors at zero. A credit larger than the payment is partially consumed; the payment never goes negative.
- When `net` is zero the payment still writes a `PaymentRecord` with `amount = 0` and the PA still reaches `processed` with `paid_at` set — PA already has a zero-cash settlement precedent (`epms-api/app/models/pa.py:48`).
- `credit_ids` is three-valued: `None` = apply the FIFO default, `[]` = apply nothing this run, `[ids]` = apply only these. **Test `is None`, never truthiness** — `[]` is falsy and would silently become "apply the default", which is the opposite of what the caller asked for.
- New alembic revision chains off `0030_vendor_credits`, verified as finance-api's single head on 2026-08-07.
- Identifiers and user-facing strings are English. Code comments may be Chinese.
- Work happens in worktree `c:/Project/uniops-vendor-credit` on branch `feature/vendor-credit`.

## Environment (verified — do not re-investigate)

- **Run every command in the FOREGROUND.** Never background a command; the Bash tool auto-backgrounds anything over its timeout and a stranded agent is the single most expensive failure mode this project has hit.
- **Run only the targeted test file. NEVER the full finance-api suite** — it takes 45 minutes and concurrent runs corrupt the shared test database.
- finance-api test command (the password override is mandatory; conftest's default is wrong on this machine):
  ```
  cd finance-api && TEST_PG_PASSWORD=7c0a03bb8c2afef690d1852f8dc3a0195932db5f0f1670e9 TEST_FINANCE_DB=finance_test_vc_b<N> python -m pytest tests/test_vendor_credit_netting.py -v
  ```
  Use a distinct `TEST_FINANCE_DB` per task to avoid racing other runs.
- **Never run `alembic` by hand.** `finance-api/.env` points at the PRODUCTION database (10.10.50.20). The pytest fixture overrides `DATABASE_URL` to the local test DB, which is safe; manual invocation is not.
- finance frontend gate: `cd finance && npx tsc --noEmit 2>&1 | grep -c "error TS"`. **Measure the baseline before changing anything** and match it afterwards — do not copy a number from anywhere. If `node_modules` is missing run `npm ci` first, or tsc prints an install prompt and the grep count is a false zero.
- **Never `git stash`.**

### Verified facts for the executor tests (do not re-derive)

- `_PAY_ROLES` in `app/crud/payment_execute.py:36` is `{"ap_clerk", "finance_manager", "finance_bp", "system_admin"}`, and `_check_can_pay` returns immediately on a match — so a test user dict of `{"sub": <uuid>, "role": "system_admin"}` clears the payment gate with no seeded rows.
- `_check_period_open` treats a missing `fiscal_period` row as OPEN ("No row means open"). A freshly migrated test DB has none, so today's period is open and `payment_date=None` is safe.
- `_check_self_payment` only fires when a matching `sod_rules` row is enabled; a fresh test DB has none, so the self-payment gate is inert in tests.
- `payment_records.pa_id` carries a real FK to `payment_applications.id`, so every test that creates a payment record must first create and flush a real `PaymentApplication` — a bare `uuid4()` violates the constraint.

---

## File Structure

| File | Responsibility |
|---|---|
| `finance-api/alembic/versions/0031_vendor_credit_applications.py` | **Create.** `vendor_credit_applications` table + `payment_records.credit_applied`. |
| `finance-api/app/models/vendor_credit.py` | **Modify.** Add `VendorCreditApplication`. |
| `finance-api/app/models/payment.py` | **Modify.** Add `credit_applied`. |
| `finance-api/app/crud/vendor_credit.py` | **Modify.** FIFO selection + the apply mutation. All netting rules live here. |
| `finance-api/app/schemas/vendor_credit.py` | **Modify.** Preview contracts. |
| `finance-api/app/api/v1/vendor_credits.py` | **Modify.** `GET /vendor-credits/suggest`. |
| `finance-api/app/schemas/payment_execute.py` | **Modify.** `credit_ids`. |
| `finance-api/app/crud/payment_execute.py` | **Modify.** Netting + three-line voucher + the §6.6 base fix. |
| `finance-api/app/api/v1/payments.py` | **Modify.** `credit_ids_by_doc` on the batch execute body. |
| `finance-api/app/crud/payment_batch.py` | **Modify.** Thread per-line `credit_ids` through. |
| `finance-api/app/crud/remittance.py` | **Modify.** Carry gross + credit_applied onto the group line. |
| `finance-api/app/services/remittance_template.py` | **Modify.** Render Gross / Credits applied / Net. |
| `finance-api/tests/test_vendor_credit_netting.py` | **Create.** Every Phase B test. Keeps Phase A's file untouched. |
| `finance/src/pages/finance/PaymentBatchPage.tsx` | **Modify.** Preview + per-line deselect. |

---
## Task 1: `vendor_credit_applications` table and `payment_records.credit_applied`

**Files:**
- Create: `finance-api/alembic/versions/0031_vendor_credit_applications.py`
- Modify: `finance-api/app/models/vendor_credit.py`
- Modify: `finance-api/app/models/payment.py`
- Test: `finance-api/tests/test_vendor_credit_netting.py`

**Interfaces:**
- Consumes: `VendorCredit` from Phase A.
- Produces: `VendorCreditApplication` ORM class; `PaymentRecord.credit_applied`.

`payment_records` is finance-owned (`app/models/payment.py`), so adding a column is this service's own migration — no cross-service coordination.

- [ ] **Step 1: Write the failing model test**

Create `finance-api/tests/test_vendor_credit_netting.py`:

```python
"""Vendor Credit — Phase B (payment netting, GL, remittance)."""
import uuid
from datetime import date, datetime, timezone
from decimal import Decimal

import pytest
import sqlalchemy as sa


def test_application_model_mapped():
    from app.models.vendor_credit import VendorCreditApplication
    cols = {c.name for c in VendorCreditApplication.__table__.columns}
    assert {"credit_id", "payment_record_id", "batch_id",
            "doc_kind", "doc_id", "doc_number",
            "applied_amount", "applied_at", "applied_by"} <= cols
    assert VendorCreditApplication.__tablename__ == "vendor_credit_applications"


def test_payment_record_has_credit_applied():
    from app.models.payment import PaymentRecord
    assert "credit_applied" in {c.name for c in PaymentRecord.__table__.columns}
```

- [ ] **Step 2: Run it and watch it fail**

Run: `cd finance-api && TEST_PG_PASSWORD=7c0a03bb8c2afef690d1852f8dc3a0195932db5f0f1670e9 TEST_FINANCE_DB=finance_test_vc_b1 python -m pytest tests/test_vendor_credit_netting.py -v`

Expected: FAIL — `ImportError: cannot import name 'VendorCreditApplication'`

- [ ] **Step 3: Add the application model**

Append to `finance-api/app/models/vendor_credit.py`:

```python
class VendorCreditApplication(UUIDPrimaryKey, TimestampMixin, Base):
    """One row per (credit, payment) application — the audit trail for why a
    payment was short. Written by app/crud/vendor_credit.py inside the same
    transaction as the PaymentRecord it references, never separately.
    """
    __tablename__ = "vendor_credit_applications"

    credit_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("vendor_credits.id", ondelete="RESTRICT"),
        nullable=False, index=True,
    )
    payment_record_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), nullable=False, index=True,
    )
    batch_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)

    # The document that was paid: pa | pa_dir. Never expense_claim.
    doc_kind: Mapped[str] = mapped_column(String(20), nullable=False)
    doc_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False, index=True)
    doc_number: Mapped[str | None] = mapped_column(String(40), nullable=True)

    applied_amount: Mapped[Decimal] = mapped_column(Numeric(15, 2), nullable=False)
    applied_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    applied_by: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)

    __table_args__ = (
        CheckConstraint("applied_amount > 0", name="ck_vendor_credit_applications_positive"),
    )
```

Add `ForeignKey` to that module's `sqlalchemy` import line if it is not already there.

- [ ] **Step 4: Add the payment_records column**

In `finance-api/app/models/payment.py`, inside `PaymentRecord`, immediately after the `amount` column:

```python
    # Vendor credit netted off this payment. `amount` above is the NET cash that
    # actually left the bank; gross = amount + credit_applied.
    credit_applied: Mapped[Decimal] = mapped_column(Numeric(15, 2), nullable=False, default=Decimal("0"))
```

- [ ] **Step 5: Write the migration**

Create `finance-api/alembic/versions/0031_vendor_credit_applications.py`:

```python
"""vendor credit applications + payment_records.credit_applied — Phase B

Revision ID: 0031_vc_applications
Revises: 0030_vendor_credits
Create Date: 2026-08-07
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

revision = "0031_vc_applications"
down_revision = "0030_vendor_credits"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "vendor_credit_applications",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("credit_id", UUID(as_uuid=True),
                  sa.ForeignKey("vendor_credits.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("payment_record_id", UUID(as_uuid=True), nullable=False),
        sa.Column("batch_id", UUID(as_uuid=True), nullable=True),
        sa.Column("doc_kind", sa.String(20), nullable=False),
        sa.Column("doc_id", UUID(as_uuid=True), nullable=False),
        sa.Column("doc_number", sa.String(40), nullable=True),
        sa.Column("applied_amount", sa.Numeric(15, 2), nullable=False),
        sa.Column("applied_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("applied_by", UUID(as_uuid=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.CheckConstraint("applied_amount > 0", name="ck_vendor_credit_applications_positive"),
    )
    op.create_index("ix_vca_credit_id", "vendor_credit_applications", ["credit_id"])
    op.create_index("ix_vca_payment_record_id", "vendor_credit_applications", ["payment_record_id"])
    op.create_index("ix_vca_doc_id", "vendor_credit_applications", ["doc_id"])

    op.add_column("payment_records",
                  sa.Column("credit_applied", sa.Numeric(15, 2), nullable=False, server_default="0"))


def downgrade() -> None:
    op.drop_column("payment_records", "credit_applied")
    op.drop_table("vendor_credit_applications")
```

- [ ] **Step 6: Verify the chain has exactly one head**

Run this from `finance-api/` — write it to a scratch file and execute it, do not inline it as a nested heredoc:

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

Expected: `HEADS: ['0031_vc_applications']` — exactly one. Two means the chain forked; fix `down_revision` before continuing.

- [ ] **Step 7: Add behavioural DB tests**

Append to `finance-api/tests/test_vendor_credit_netting.py`:

```python
def _credit(**over):
    """A minimal available credit row. Overrides win."""
    from app.models.vendor_credit import VendorCredit
    kw = dict(
        credit_number=f"VC-TEST-{uuid.uuid4().hex[:8]}", vendor_id=uuid.uuid4(),
        vendor_name="ULINE", vendor_credit_number=uuid.uuid4().hex[:12],
        credit_date=date(2026, 7, 1), currency="CAD",
        amount=Decimal("100.00"), tax_amount=Decimal("0"),
        total_amount=Decimal("100.00"), applied_amount=Decimal("0"),
        remaining_amount=Decimal("100.00"), status="available",
        line_items=[], uploaded_by=uuid.uuid4(),
        uploaded_at=datetime.now(timezone.utc),
    )
    kw.update(over)
    return VendorCredit(**kw)


@pytest.mark.anyio
async def test_migration_builds_applications_table(db_session):
    cols = (await db_session.execute(sa.text(
        "SELECT column_name FROM information_schema.columns "
        "WHERE table_name = 'vendor_credit_applications'"
    ))).scalars().all()
    assert {"credit_id", "payment_record_id", "applied_amount", "applied_by"} <= set(cols)

    pr = (await db_session.execute(sa.text(
        "SELECT column_name FROM information_schema.columns "
        "WHERE table_name = 'payment_records' AND column_name = 'credit_applied'"
    ))).scalars().all()
    assert pr == ["credit_applied"]


@pytest.mark.anyio
async def test_application_rejects_non_positive_amount(db_session):
    """A zero or negative application is meaningless and must not be storable."""
    from sqlalchemy.exc import IntegrityError
    from app.models.vendor_credit import VendorCreditApplication

    vc = _credit()
    db_session.add(vc)
    await db_session.flush()

    db_session.add(VendorCreditApplication(
        credit_id=vc.id, payment_record_id=uuid.uuid4(),
        doc_kind="pa", doc_id=uuid.uuid4(), doc_number="PA-1",
        applied_amount=Decimal("0"), applied_at=datetime.now(timezone.utc),
        applied_by=uuid.uuid4(),
    ))
    with pytest.raises(IntegrityError) as exc:
        await db_session.flush()
    assert "ck_vendor_credit_applications_positive" in str(exc.value)
    await db_session.rollback()
```

- [ ] **Step 8: Run the tests**

Run: `cd finance-api && TEST_PG_PASSWORD=7c0a03bb8c2afef690d1852f8dc3a0195932db5f0f1670e9 TEST_FINANCE_DB=finance_test_vc_b1 python -m pytest tests/test_vendor_credit_netting.py -v`

Expected: 4 passed. The `db_session` fixture rebuilds the schema by running alembic, so a broken chain surfaces here as a migration error.

- [ ] **Step 9: Commit**

```bash
git add finance-api/app/models/vendor_credit.py finance-api/app/models/payment.py finance-api/alembic/versions/0031_vendor_credit_applications.py finance-api/tests/test_vendor_credit_netting.py
git commit -m "feat(finance): vendor_credit_applications table + payment_records.credit_applied"
```

---

## Task 2: FIFO credit selection

**Files:**
- Modify: `finance-api/app/crud/vendor_credit.py`
- Test: `finance-api/tests/test_vendor_credit_netting.py`

**Interfaces:**
- Consumes: `VendorCredit`, `AVAILABLE` from Phase A.
- Produces:
  - `class CreditUnavailable(Exception)` — an explicitly requested credit is not applicable.
  - `async def select_credits_for_payment(db, *, vendor_id: uuid.UUID, currency: str, base: Decimal, credit_ids: list[uuid.UUID] | None = None, lock: bool = True) -> list[tuple[VendorCredit, Decimal]]`
    Returns `(credit, amount_to_apply)` pairs, FIFO by `credit_date` then `created_at`, cumulative amount never exceeding `base`. Pairs with a zero apply amount are never returned.

**The two locking modes are deliberate and different — do not unify them:**

- `credit_ids is None` (the automatic default) uses `FOR UPDATE SKIP LOCKED`. Two payment batches paying the same vendor concurrently then take disjoint credits: no double-spend, no blocking.
- `credit_ids` given explicitly uses plain `FOR UPDATE` (blocking), and **any** requested id that is not applicable raises `CreditUnavailable`, aborting the whole payment. Silently applying less than the operator's preview showed would destroy trust in the preview.
- `lock=False` is for the read-only preview endpoint. A preview must never hold row locks across a human's think time.

- [ ] **Step 1: Write the failing tests**

Append to `finance-api/tests/test_vendor_credit_netting.py`:

```python
@pytest.mark.anyio
async def test_fifo_takes_oldest_first_and_stops_at_base(db_session):
    from app.crud import vendor_credit as crud
    vid = uuid.uuid4()
    older = _credit(vendor_id=vid, credit_date=date(2026, 5, 1),
                    amount=Decimal("30.00"), total_amount=Decimal("30.00"),
                    remaining_amount=Decimal("30.00"))
    newer = _credit(vendor_id=vid, credit_date=date(2026, 6, 1),
                    amount=Decimal("50.00"), total_amount=Decimal("50.00"),
                    remaining_amount=Decimal("50.00"))
    db_session.add_all([newer, older])
    await db_session.flush()

    picks = await crud.select_credits_for_payment(
        db_session, vendor_id=vid, currency="CAD", base=Decimal("40.00"))

    assert [p[0].id for p in picks] == [older.id, newer.id]
    assert [p[1] for p in picks] == [Decimal("30.00"), Decimal("10.00")]


@pytest.mark.anyio
async def test_selection_never_exceeds_base(db_session):
    from app.crud import vendor_credit as crud
    vid = uuid.uuid4()
    db_session.add(_credit(vendor_id=vid, amount=Decimal("500.00"),
                           total_amount=Decimal("500.00"),
                           remaining_amount=Decimal("500.00")))
    await db_session.flush()
    picks = await crud.select_credits_for_payment(
        db_session, vendor_id=vid, currency="CAD", base=Decimal("120.00"))
    assert [p[1] for p in picks] == [Decimal("120.00")]


@pytest.mark.anyio
async def test_currency_must_match_exactly(db_session):
    from app.crud import vendor_credit as crud
    vid = uuid.uuid4()
    db_session.add(_credit(vendor_id=vid, currency="CAD"))
    await db_session.flush()
    picks = await crud.select_credits_for_payment(
        db_session, vendor_id=vid, currency="USD", base=Decimal("50.00"))
    assert picks == []


@pytest.mark.anyio
async def test_only_available_credits_are_selected(db_session):
    from app.crud import vendor_credit as crud
    vid = uuid.uuid4()
    db_session.add_all([
        _credit(vendor_id=vid, status="pending_review"),
        _credit(vendor_id=vid, status="void"),
        _credit(vendor_id=vid, status="exhausted",
                applied_amount=Decimal("100.00"), remaining_amount=Decimal("0")),
    ])
    await db_session.flush()
    picks = await crud.select_credits_for_payment(
        db_session, vendor_id=vid, currency="CAD", base=Decimal("50.00"))
    assert picks == []


@pytest.mark.anyio
async def test_zero_or_negative_base_selects_nothing(db_session):
    from app.crud import vendor_credit as crud
    vid = uuid.uuid4()
    db_session.add(_credit(vendor_id=vid))
    await db_session.flush()
    for base in (Decimal("0"), Decimal("-5.00")):
        assert await crud.select_credits_for_payment(
            db_session, vendor_id=vid, currency="CAD", base=base) == []


@pytest.mark.anyio
async def test_empty_credit_ids_selects_nothing(db_session):
    """[] means 'apply nothing this run' — NOT the same as None."""
    from app.crud import vendor_credit as crud
    vid = uuid.uuid4()
    db_session.add(_credit(vendor_id=vid))
    await db_session.flush()
    assert await crud.select_credits_for_payment(
        db_session, vendor_id=vid, currency="CAD",
        base=Decimal("50.00"), credit_ids=[]) == []


@pytest.mark.anyio
async def test_explicit_ids_select_only_those(db_session):
    from app.crud import vendor_credit as crud
    vid = uuid.uuid4()
    a = _credit(vendor_id=vid, credit_date=date(2026, 5, 1),
                amount=Decimal("20.00"), total_amount=Decimal("20.00"),
                remaining_amount=Decimal("20.00"))
    b = _credit(vendor_id=vid, credit_date=date(2026, 6, 1),
                amount=Decimal("20.00"), total_amount=Decimal("20.00"),
                remaining_amount=Decimal("20.00"))
    db_session.add_all([a, b])
    await db_session.flush()
    picks = await crud.select_credits_for_payment(
        db_session, vendor_id=vid, currency="CAD",
        base=Decimal("100.00"), credit_ids=[b.id])
    assert [p[0].id for p in picks] == [b.id]


@pytest.mark.anyio
async def test_explicit_id_that_is_not_applicable_aborts(db_session):
    """The operator's preview must match what executes, or nothing executes."""
    from app.crud import vendor_credit as crud
    vid = uuid.uuid4()
    voided = _credit(vendor_id=vid, status="void")
    db_session.add(voided)
    await db_session.flush()
    with pytest.raises(crud.CreditUnavailable):
        await crud.select_credits_for_payment(
            db_session, vendor_id=vid, currency="CAD",
            base=Decimal("50.00"), credit_ids=[voided.id])


@pytest.mark.anyio
async def test_explicit_id_belonging_to_another_vendor_aborts(db_session):
    from app.crud import vendor_credit as crud
    other = _credit(vendor_id=uuid.uuid4())
    db_session.add(other)
    await db_session.flush()
    with pytest.raises(crud.CreditUnavailable):
        await crud.select_credits_for_payment(
            db_session, vendor_id=uuid.uuid4(), currency="CAD",
            base=Decimal("50.00"), credit_ids=[other.id])
```

- [ ] **Step 2: Run and watch them fail**

Run: `cd finance-api && TEST_PG_PASSWORD=7c0a03bb8c2afef690d1852f8dc3a0195932db5f0f1670e9 TEST_FINANCE_DB=finance_test_vc_b2 python -m pytest tests/test_vendor_credit_netting.py -v`

Expected: FAIL — `AttributeError: module 'app.crud.vendor_credit' has no attribute 'select_credits_for_payment'`

- [ ] **Step 3: Implement the selection**

Append to `finance-api/app/crud/vendor_credit.py`:

```python
class CreditUnavailable(Exception):
    """An explicitly requested credit is not applicable to this payment."""


async def select_credits_for_payment(
    db: AsyncSession, *, vendor_id: uuid.UUID, currency: str, base: Decimal,
    credit_ids: list[uuid.UUID] | None = None, lock: bool = True,
) -> list[tuple[VendorCredit, Decimal]]:
    """Pick credits to net off a payment of `base`, oldest first.

    credit_ids is THREE-VALUED and must be compared with `is None`:
      None  -> automatic FIFO over every applicable credit
      []    -> apply nothing this run
      [ids] -> apply only these, and fail loudly if any is not applicable

    Locking differs by mode on purpose. The automatic path uses SKIP LOCKED so
    two concurrent batches paying the same vendor take disjoint credits instead
    of blocking each other. The explicit path blocks, because the operator was
    shown these exact credits and applying a subset silently would make the
    preview a lie. lock=False is for the read-only preview, which must not hold
    locks across a human's think time.
    """
    if credit_ids is not None and len(credit_ids) == 0:
        return []
    if base <= _ZERO:
        return []

    q = select(VendorCredit).where(
        VendorCredit.vendor_id == vendor_id,
        VendorCredit.currency == currency,
        VendorCredit.status == AVAILABLE,
        VendorCredit.remaining_amount > _ZERO,
    ).order_by(VendorCredit.credit_date, VendorCredit.created_at)

    if credit_ids is not None:
        q = q.where(VendorCredit.id.in_(credit_ids))
        if lock:
            q = q.with_for_update()
    elif lock:
        q = q.with_for_update(skip_locked=True)

    rows = list((await db.execute(q)).scalars().all())

    if credit_ids is not None:
        found = {r.id for r in rows}
        missing = [str(cid) for cid in credit_ids if cid not in found]
        if missing:
            raise CreditUnavailable(
                "These credits are no longer available for this payment: "
                + ", ".join(missing)
            )

    picks: list[tuple[VendorCredit, Decimal]] = []
    remaining_base = base
    for credit in rows:
        if remaining_base <= _ZERO:
            break
        take = min(credit.remaining_amount, remaining_base)
        if take <= _ZERO:
            continue
        picks.append((credit, take))
        remaining_base -= take
    return picks
```

- [ ] **Step 4: Run the tests**

Run: `cd finance-api && TEST_PG_PASSWORD=7c0a03bb8c2afef690d1852f8dc3a0195932db5f0f1670e9 TEST_FINANCE_DB=finance_test_vc_b2 python -m pytest tests/test_vendor_credit_netting.py -v`

Expected: all pass (4 from Task 1 + 9 here).

- [ ] **Step 5: Commit**

```bash
git add finance-api/app/crud/vendor_credit.py finance-api/tests/test_vendor_credit_netting.py
git commit -m "feat(finance): FIFO vendor-credit selection with mode-specific row locking"
```

---

## Task 3: Apply the selected credits

**Files:**
- Modify: `finance-api/app/crud/vendor_credit.py`
- Test: `finance-api/tests/test_vendor_credit_netting.py`

**Interfaces:**
- Consumes: `select_credits_for_payment` from Task 2; `VendorCreditApplication` from Task 1.
- Produces:
  `async def apply_credits(db, picks: list[tuple[VendorCredit, Decimal]], *, payment_record_id: uuid.UUID, batch_id: uuid.UUID | None, doc_kind: str, doc_id: uuid.UUID, doc_number: str | None, applied_by: uuid.UUID) -> Decimal`
  Writes one `VendorCreditApplication` per pick, decrements each credit, flips a fully-consumed credit to `exhausted`, and returns the total applied.

This must run **after** the `PaymentRecord` has been flushed, because it stores that record's id.

- [ ] **Step 1: Write the failing tests**

Append to `finance-api/tests/test_vendor_credit_netting.py`:

```python
@pytest.mark.anyio
async def test_apply_writes_rows_and_decrements(db_session):
    from app.crud import vendor_credit as crud
    from app.models.vendor_credit import VendorCreditApplication
    vid, prid, actor = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    c = _credit(vendor_id=vid, amount=Decimal("80.00"),
                total_amount=Decimal("80.00"), remaining_amount=Decimal("80.00"))
    db_session.add(c)
    await db_session.flush()

    picks = await crud.select_credits_for_payment(
        db_session, vendor_id=vid, currency="CAD", base=Decimal("30.00"))
    total = await crud.apply_credits(
        db_session, picks, payment_record_id=prid, batch_id=None,
        doc_kind="pa", doc_id=uuid.uuid4(), doc_number="PA-1", applied_by=actor)
    await db_session.flush()

    assert total == Decimal("30.00")
    assert c.applied_amount == Decimal("30.00")
    assert c.remaining_amount == Decimal("50.00")
    assert c.status == "available"          # partially consumed, still usable

    rows = (await db_session.execute(
        sa.select(VendorCreditApplication).where(
            VendorCreditApplication.payment_record_id == prid))).scalars().all()
    assert len(rows) == 1
    assert rows[0].applied_amount == Decimal("30.00")
    assert rows[0].applied_by == actor


@pytest.mark.anyio
async def test_fully_consumed_credit_becomes_exhausted(db_session):
    from app.crud import vendor_credit as crud
    vid = uuid.uuid4()
    c = _credit(vendor_id=vid, amount=Decimal("25.00"),
                total_amount=Decimal("25.00"), remaining_amount=Decimal("25.00"))
    db_session.add(c)
    await db_session.flush()

    picks = await crud.select_credits_for_payment(
        db_session, vendor_id=vid, currency="CAD", base=Decimal("25.00"))
    await crud.apply_credits(
        db_session, picks, payment_record_id=uuid.uuid4(), batch_id=None,
        doc_kind="pa", doc_id=uuid.uuid4(), doc_number="PA-2",
        applied_by=uuid.uuid4())
    await db_session.flush()

    assert c.remaining_amount == Decimal("0.00")
    assert c.status == "exhausted"


@pytest.mark.anyio
async def test_apply_keeps_the_balance_check_satisfied(db_session):
    """applied + remaining must still equal total, or the DB CHECK rejects it."""
    from app.crud import vendor_credit as crud
    vid = uuid.uuid4()
    c = _credit(vendor_id=vid, amount=Decimal("60.00"),
                total_amount=Decimal("60.00"), remaining_amount=Decimal("60.00"))
    db_session.add(c)
    await db_session.flush()

    picks = await crud.select_credits_for_payment(
        db_session, vendor_id=vid, currency="CAD", base=Decimal("15.00"))
    await crud.apply_credits(
        db_session, picks, payment_record_id=uuid.uuid4(), batch_id=None,
        doc_kind="pa", doc_id=uuid.uuid4(), doc_number="PA-3",
        applied_by=uuid.uuid4())
    await db_session.flush()   # would raise if ck_vendor_credits_balance broke

    assert c.applied_amount + c.remaining_amount == c.total_amount


@pytest.mark.anyio
async def test_apply_of_nothing_is_a_noop(db_session):
    from app.crud import vendor_credit as crud
    total = await crud.apply_credits(
        db_session, [], payment_record_id=uuid.uuid4(), batch_id=None,
        doc_kind="pa", doc_id=uuid.uuid4(), doc_number="PA-4",
        applied_by=uuid.uuid4())
    assert total == Decimal("0")


@pytest.mark.anyio
async def test_one_credit_spanning_two_payments(db_session):
    from app.crud import vendor_credit as crud
    vid = uuid.uuid4()
    c = _credit(vendor_id=vid, amount=Decimal("100.00"),
                total_amount=Decimal("100.00"), remaining_amount=Decimal("100.00"))
    db_session.add(c)
    await db_session.flush()

    for amount in (Decimal("40.00"), Decimal("60.00")):
        picks = await crud.select_credits_for_payment(
            db_session, vendor_id=vid, currency="CAD", base=amount)
        await crud.apply_credits(
            db_session, picks, payment_record_id=uuid.uuid4(), batch_id=None,
            doc_kind="pa", doc_id=uuid.uuid4(), doc_number="PA-x",
            applied_by=uuid.uuid4())
        await db_session.flush()

    assert c.applied_amount == Decimal("100.00")
    assert c.remaining_amount == Decimal("0.00")
    assert c.status == "exhausted"
```

- [ ] **Step 2: Run and watch them fail**

Run: `cd finance-api && TEST_PG_PASSWORD=7c0a03bb8c2afef690d1852f8dc3a0195932db5f0f1670e9 TEST_FINANCE_DB=finance_test_vc_b3 python -m pytest tests/test_vendor_credit_netting.py -v`

Expected: FAIL — `AttributeError: module 'app.crud.vendor_credit' has no attribute 'apply_credits'`

- [ ] **Step 3: Implement it**

Append to `finance-api/app/crud/vendor_credit.py`:

```python
async def apply_credits(
    db: AsyncSession, picks: list[tuple[VendorCredit, Decimal]], *,
    payment_record_id: uuid.UUID, batch_id: uuid.UUID | None,
    doc_kind: str, doc_id: uuid.UUID, doc_number: str | None,
    applied_by: uuid.UUID,
) -> Decimal:
    """Consume `picks` against one payment and return the total applied.

    Call this only AFTER the PaymentRecord has been flushed — its id is stored
    on every application row. Runs in the caller's transaction so the payment
    and the credit decrements commit or roll back together; a partial outcome
    here is money that exists in one place and not the other.
    """
    from app.models.vendor_credit import VendorCreditApplication

    total = _ZERO
    now = datetime.now(timezone.utc)
    for credit, take in picks:
        credit.applied_amount = credit.applied_amount + take
        credit.remaining_amount = credit.remaining_amount - take
        if credit.remaining_amount <= _ZERO:
            credit.status = EXHAUSTED
        db.add(VendorCreditApplication(
            credit_id=credit.id,
            payment_record_id=payment_record_id,
            batch_id=batch_id,
            doc_kind=doc_kind,
            doc_id=doc_id,
            doc_number=doc_number,
            applied_amount=take,
            applied_at=now,
            applied_by=applied_by,
        ))
        total += take
    return total
```

`EXHAUSTED` is already defined in `app/models/vendor_credit.py`; add it to this module's existing import from there if it is not already imported.

- [ ] **Step 4: Run the tests**

Run: `cd finance-api && TEST_PG_PASSWORD=7c0a03bb8c2afef690d1852f8dc3a0195932db5f0f1670e9 TEST_FINANCE_DB=finance_test_vc_b3 python -m pytest tests/test_vendor_credit_netting.py -v`

Expected: all pass (18 by now).

- [ ] **Step 5: Commit**

```bash
git add finance-api/app/crud/vendor_credit.py finance-api/tests/test_vendor_credit_netting.py
git commit -m "feat(finance): apply vendor credits to a payment and record the trail"
```

---

## Task 4: Net credits inside the payment executor

**Files:**
- Modify: `finance-api/app/schemas/payment_execute.py`
- Modify: `finance-api/app/crud/payment_execute.py`
- Test: `finance-api/tests/test_vendor_credit_netting.py`

**Interfaces:**
- Consumes: `select_credits_for_payment`, `CreditUnavailable`, `apply_credits`.
- Produces: `PaymentExecuteRequest.credit_ids: list[uuid.UUID] | None = None`; a `PaymentRecord` whose `amount` is net cash and whose `credit_applied` is the netted total.

**Where:** the `pa` / `pa_dir` branch of `execute()`, after `amount` is resolved (currently `app/crud/payment_execute.py:309`) and before the `PaymentRecord` is constructed. Both the one-off route (`app/api/v1/payments.py:47`) and the batch runner (`app/crud/payment_batch.py:214`) funnel through this function, so one change covers both.

**Arithmetic — implement exactly this:**

```
base           = req.amount_paid if req.amount_paid is not None else pa.payment_amount
picks          = select_credits_for_payment(vendor_id=pa.vendor_id, currency=pa.currency,
                                            base=base, credit_ids=req.credit_ids)
credit_applied = sum(take for _, take in picks)
net            = base - credit_applied        # picks never exceed base, so net >= 0
```

`expense_claim` must never reach this code — it is a different branch of `execute()` and has no `vendor_id`. Do not add credit handling there.

- [ ] **Step 1: Write the failing tests**

Append to `finance-api/tests/test_vendor_credit_netting.py`. These need a real `PaymentApplication` row because `payment_records.pa_id` carries a genuine FK:

```python
def _pa(**over):
    from app.models.pa import PaymentApplication
    kw = dict(
        pa_number=f"PA-{uuid.uuid4().hex[:8]}", title="Test PA",
        vendor_id=uuid.uuid4(), vendor_name="ULINE",
        invoice_ids=[], gr_ids=[], pa_type="regular",
        subtotal=Decimal("100.00"), tax_amount=Decimal("0"),
        shipping_amount=Decimal("0"), other_charges=Decimal("0"),
        payment_amount=Decimal("100.00"), currency="CAD",
        status="approved", approval_step_idx=0,
    )
    kw.update(over)
    return PaymentApplication(**kw)


async def _run_execute(db, pa, *, user_id, credit_ids=None, amount_paid=None):
    from app.crud import payment_execute
    from app.schemas.payment_execute import PaymentExecuteRequest
    return await payment_execute.execute(
        db,
        PaymentExecuteRequest(
            doc_kind="pa", doc_id=pa.id, payment_method="bank_transfer",
            credit_ids=credit_ids, amount_paid=amount_paid,
        ),
        {"sub": str(user_id), "role": "system_admin"},
    )


@pytest.mark.anyio
async def test_payment_is_reduced_by_available_credit(db_session):
    from app.models.payment import PaymentRecord
    actor = uuid.uuid4()
    pa = _pa(payment_amount=Decimal("100.00"))
    db_session.add(pa)
    db_session.add(_credit(vendor_id=pa.vendor_id, amount=Decimal("30.00"),
                           total_amount=Decimal("30.00"),
                           remaining_amount=Decimal("30.00")))
    await db_session.flush()

    res = await _run_execute(db_session, pa, user_id=actor)

    rec = (await db_session.execute(sa.select(PaymentRecord).where(
        PaymentRecord.id == res.payment_record_id))).scalar_one()
    assert rec.amount == Decimal("70.00")
    assert rec.credit_applied == Decimal("30.00")
    assert res.new_status == "processed"


@pytest.mark.anyio
async def test_credit_larger_than_payment_pays_zero_cash(db_session):
    """Zero-cash settlement: the record and the PA still complete."""
    from app.models.payment import PaymentRecord
    pa = _pa(payment_amount=Decimal("40.00"))
    db_session.add(pa)
    c = _credit(vendor_id=pa.vendor_id, amount=Decimal("100.00"),
                total_amount=Decimal("100.00"), remaining_amount=Decimal("100.00"))
    db_session.add(c)
    await db_session.flush()

    res = await _run_execute(db_session, pa, user_id=uuid.uuid4())

    rec = (await db_session.execute(sa.select(PaymentRecord).where(
        PaymentRecord.id == res.payment_record_id))).scalar_one()
    assert rec.amount == Decimal("0.00")
    assert rec.credit_applied == Decimal("40.00")
    assert c.remaining_amount == Decimal("60.00")
    assert c.status == "available"
    assert pa.status == "processed"
    assert pa.paid_at is not None


@pytest.mark.anyio
async def test_empty_credit_ids_pays_in_full(db_session):
    """[] must mean 'do not apply', not 'apply the default'."""
    from app.models.payment import PaymentRecord
    pa = _pa(payment_amount=Decimal("100.00"))
    db_session.add(pa)
    db_session.add(_credit(vendor_id=pa.vendor_id))
    await db_session.flush()

    res = await _run_execute(db_session, pa, user_id=uuid.uuid4(), credit_ids=[])

    rec = (await db_session.execute(sa.select(PaymentRecord).where(
        PaymentRecord.id == res.payment_record_id))).scalar_one()
    assert rec.amount == Decimal("100.00")
    assert rec.credit_applied == Decimal("0.00")


@pytest.mark.anyio
async def test_credit_of_another_currency_is_not_applied(db_session):
    from app.models.payment import PaymentRecord
    pa = _pa(payment_amount=Decimal("100.00"), currency="USD")
    db_session.add(pa)
    db_session.add(_credit(vendor_id=pa.vendor_id, currency="CAD"))
    await db_session.flush()

    res = await _run_execute(db_session, pa, user_id=uuid.uuid4())
    rec = (await db_session.execute(sa.select(PaymentRecord).where(
        PaymentRecord.id == res.payment_record_id))).scalar_one()
    assert rec.amount == Decimal("100.00")
    assert rec.credit_applied == Decimal("0.00")


@pytest.mark.anyio
async def test_unavailable_explicit_credit_aborts_the_payment(db_session):
    """The whole execution rolls back — no partial payment, no partial apply."""
    from app.crud.vendor_credit import CreditUnavailable
    pa = _pa(payment_amount=Decimal("100.00"))
    db_session.add(pa)
    voided = _credit(vendor_id=pa.vendor_id, status="void")
    db_session.add(voided)
    await db_session.flush()

    with pytest.raises(CreditUnavailable):
        await _run_execute(db_session, pa, user_id=uuid.uuid4(),
                           credit_ids=[voided.id])


@pytest.mark.anyio
async def test_partial_payment_nets_against_the_amount_actually_paid(db_session):
    from app.models.payment import PaymentRecord
    pa = _pa(payment_amount=Decimal("100.00"))
    db_session.add(pa)
    db_session.add(_credit(vendor_id=pa.vendor_id, amount=Decimal("90.00"),
                           total_amount=Decimal("90.00"),
                           remaining_amount=Decimal("90.00")))
    await db_session.flush()

    res = await _run_execute(db_session, pa, user_id=uuid.uuid4(),
                             amount_paid=Decimal("50.00"))
    rec = (await db_session.execute(sa.select(PaymentRecord).where(
        PaymentRecord.id == res.payment_record_id))).scalar_one()
    assert rec.credit_applied == Decimal("50.00")
    assert rec.amount == Decimal("0.00")
```

- [ ] **Step 2: Run and watch them fail**

Run: `cd finance-api && TEST_PG_PASSWORD=7c0a03bb8c2afef690d1852f8dc3a0195932db5f0f1670e9 TEST_FINANCE_DB=finance_test_vc_b4 python -m pytest tests/test_vendor_credit_netting.py -v`

Expected: FAIL — `PaymentExecuteRequest` rejects the unknown field `credit_ids`.

- [ ] **Step 3: Add the request field**

In `finance-api/app/schemas/payment_execute.py`, inside `PaymentExecuteRequest`, after `bank_account_id`:

```python
    # Vendor credits to net off this payment. THREE-VALUED — compare with `is None`:
    #   None  -> apply the automatic FIFO default
    #   []    -> apply nothing this run
    #   [ids] -> apply only these; any that is no longer applicable aborts the payment
    credit_ids: list[uuid.UUID] | None = None
```

- [ ] **Step 4: Net inside the executor**

In `finance-api/app/crud/payment_execute.py`, add near the other crud imports:

```python
from app.crud import vendor_credit as vendor_credit_crud
```

In the `pa` / `pa_dir` branch, replace the single line that computes `amount` with the block below, and change the `PaymentRecord(...)` construction so `amount=net` and it also passes `credit_applied=credit_applied`:

```python
        bank = await _resolve_bank(db, req.bank_account_id, pa.currency)
        base = req.amount_paid if req.amount_paid is not None else pa.payment_amount

        # Vendor credits reduce the cash that leaves the bank. Selection takes row
        # locks, so it must happen inside this transaction, immediately before the
        # PaymentRecord — a credit consumed without its payment (or the reverse)
        # is money that exists in one place and not the other.
        picks = await vendor_credit_crud.select_credits_for_payment(
            db, vendor_id=pa.vendor_id, currency=pa.currency,
            base=base, credit_ids=req.credit_ids,
        )
        credit_applied = sum((take for _, take in picks), Decimal("0"))
        net = base - credit_applied

        record = PaymentRecord(
            doc_kind=doc_kind, doc_id=pa.id, doc_number=pa.pa_number,
            pa_id=pa.id, pa_number=pa.pa_number,
            vendor_id=pa.vendor_id, vendor_name=pa.vendor_name,
            payment_date=pay_date, payment_method=req.payment_method,
            reference=req.reference, amount=net, credit_applied=credit_applied,
            currency=pa.currency,
            recorded_by=recorded_by, notes=req.notes, batch_id=batch_id,
            bank_account_id=req.bank_account_id,
        )
        db.add(record)
        await db.flush()

        if picks:
            await vendor_credit_crud.apply_credits(
                db, picks, payment_record_id=record.id, batch_id=batch_id,
                doc_kind=doc_kind, doc_id=pa.id, doc_number=pa.pa_number,
                applied_by=recorded_by,
            )
```

Leave the `expense_claim` branch untouched — it pays an employee and has no vendor.

- [ ] **Step 5: Run the tests**

Run: `cd finance-api && TEST_PG_PASSWORD=7c0a03bb8c2afef690d1852f8dc3a0195932db5f0f1670e9 TEST_FINANCE_DB=finance_test_vc_b4 python -m pytest tests/test_vendor_credit_netting.py -v`

Expected: all pass (24 by now).

- [ ] **Step 6: Confirm Phase A still passes**

Run: `cd finance-api && TEST_PG_PASSWORD=7c0a03bb8c2afef690d1852f8dc3a0195932db5f0f1670e9 TEST_FINANCE_DB=finance_test_vc_b4 python -m pytest tests/test_vendor_credit.py -q`

Expected: 41 passed, unchanged. Also run `tests/test_payment_execute.py` and `tests/test_payment_batch.py` the same way and record their counts — this task changed the executor both of them exercise.

- [ ] **Step 7: Commit**

```bash
git add finance-api/app/schemas/payment_execute.py finance-api/app/crud/payment_execute.py finance-api/tests/test_vendor_credit_netting.py
git commit -m "feat(finance): net vendor credits off PA payments in the unified executor"
```

---

## Task 5: The three-line voucher and the `base` consistency fix

**Files:**
- Modify: `finance-api/app/crud/payment_execute.py`
- Test: `finance-api/tests/test_vendor_credit_netting.py`

**Interfaces:**
- Consumes: `base`, `net`, `credit_applied` from Task 4.
- Produces: a posting event whose bank leg equals the cash that actually left, and a new `line_role` `vendor_credit_clearing`.

**Why:** without this the GL bank account drifts from the bank statement by the applied amount on every netted payment, and this company reconciles against real statements.

**Also fixes spec §6.6.** Today `record.amount` uses `req.amount_paid ?? pa.payment_amount` while the posting lines use `pa.payment_amount` unconditionally, so **partial payments already post a voucher that disagrees with the cash paid**. That predates this feature; credits would make it fire far more often. Both now use `base`.

**This changes existing partial-payment posting behaviour.** Finance must be told before deploy — if they have been manually adjusting for the old figure, those adjustments must stop.

- [ ] **Step 1: Write the failing tests**

Append to `finance-api/tests/test_vendor_credit_netting.py`:

```python
async def _posting_lines(db, event_id):
    rows = (await db.execute(sa.text(
        "SELECT line_role, debit, credit FROM posting_lines "
        "WHERE event_id = :e ORDER BY line_role"
    ), {"e": str(event_id)})).all()
    return {r[0]: (r[1], r[2]) for r in rows}


@pytest.mark.anyio
async def test_voucher_splits_the_credit_side(db_session):
    pa = _pa(payment_amount=Decimal("100.00"))
    db_session.add(pa)
    db_session.add(_credit(vendor_id=pa.vendor_id, amount=Decimal("30.00"),
                           total_amount=Decimal("30.00"),
                           remaining_amount=Decimal("30.00")))
    await db_session.flush()

    res = await _run_execute(db_session, pa, user_id=uuid.uuid4())
    lines = await _posting_lines(db_session, res.posting_event_id)

    assert lines["accounts_payable"][0] == Decimal("100.00")   # debit gross
    assert lines["bank"][1] == Decimal("70.00")                # credit net cash
    assert lines["vendor_credit_clearing"][1] == Decimal("30.00")
    debits = sum(d or Decimal("0") for d, _ in lines.values())
    credits = sum(c or Decimal("0") for _, c in lines.values())
    assert debits == credits


@pytest.mark.anyio
async def test_voucher_has_no_clearing_line_without_credits(db_session):
    """An ordinary payment's voucher shape is unchanged."""
    pa = _pa(payment_amount=Decimal("100.00"))
    db_session.add(pa)
    await db_session.flush()

    res = await _run_execute(db_session, pa, user_id=uuid.uuid4())
    lines = await _posting_lines(db_session, res.posting_event_id)

    assert set(lines) == {"accounts_payable", "bank"}
    assert lines["accounts_payable"][0] == Decimal("100.00")
    assert lines["bank"][1] == Decimal("100.00")


@pytest.mark.anyio
async def test_partial_payment_posts_the_amount_actually_paid(db_session):
    """Spec 6.6: the voucher used pa.payment_amount while the record used
    amount_paid. Both must now be the amount actually paid."""
    pa = _pa(payment_amount=Decimal("100.00"))
    db_session.add(pa)
    await db_session.flush()

    res = await _run_execute(db_session, pa, user_id=uuid.uuid4(),
                             amount_paid=Decimal("40.00"))
    lines = await _posting_lines(db_session, res.posting_event_id)

    assert lines["accounts_payable"][0] == Decimal("40.00")
    assert lines["bank"][1] == Decimal("40.00")
```

`posting_lines` is the correct physical table (`app/models/posting.py:41`), and it carries a
`CHECK (NOT (debit > 0 AND credit > 0))`, so a line must sit on exactly one side.

- [ ] **Step 2: Run and watch them fail**

Run: `cd finance-api && TEST_PG_PASSWORD=7c0a03bb8c2afef690d1852f8dc3a0195932db5f0f1670e9 TEST_FINANCE_DB=finance_test_vc_b5 python -m pytest tests/test_vendor_credit_netting.py -v -k voucher`

Expected: FAIL — no `vendor_credit_clearing` line exists, and the AP debit is still `pa.payment_amount`.

- [ ] **Step 3: Build the lines from base / net / credit_applied**

In `finance-api/app/crud/payment_execute.py`, replace the `emit_event(...)` lines list in the PA branch:

```python
        posting_lines = [
            {"line_role": "accounts_payable", "debit": base,
             "partner_id": pa.vendor_id, "partner_name": pa.vendor_name,
             "currency": pa.currency},
            _bank_line(net, pa.currency, bank),
        ]
        if credit_applied > Decimal("0"):
            # Bank is credited only with the cash that left; the netted portion
            # parks in a clearing account so the GL bank balance still ties to
            # the bank statement.
            posting_lines.append({
                "line_role": "vendor_credit_clearing", "credit": credit_applied,
                "partner_id": pa.vendor_id, "partner_name": pa.vendor_name,
                "currency": pa.currency,
            })

        event_id = await emit_event(
            db,
            source_service="finance",
            source_doc_type=doc_kind,
            source_doc_id=pa.id,
            source_doc_number=pa.pa_number,
            event_type="payment",
            lines=await _stamp_fx(db, await _stamp_account_codes(db, posting_lines), pay_date),
        )
```

`_stamp_account_codes` resolves `line_role` to a COA code from `account_mappings`; a missing mapping leaves `account_code` NULL and **does not block payment** (see its docstring), so the new role can be configured after deploy without risk.

- [ ] **Step 4: Run the tests**

Run: `cd finance-api && TEST_PG_PASSWORD=7c0a03bb8c2afef690d1852f8dc3a0195932db5f0f1670e9 TEST_FINANCE_DB=finance_test_vc_b5 python -m pytest tests/test_vendor_credit_netting.py -v`

Expected: all pass (27 by now).

- [ ] **Step 5: Re-check the executor's own suites**

Run `tests/test_payment_execute.py` and `tests/test_payment_batch.py` with the same env. Compare against the counts recorded in Task 4 Step 6. If a partial-payment test now fails because it asserted the old `pa.payment_amount` posting, that test was encoding the §6.6 bug — update it and say so explicitly in your report. Do not update any test that is failing for another reason.

- [ ] **Step 6: Commit**

```bash
git add finance-api/app/crud/payment_execute.py finance-api/tests/test_vendor_credit_netting.py
git commit -m "feat(finance): split the payment voucher credit side into bank + vendor credit clearing

Also fixes the pre-existing inconsistency where the posting used
pa.payment_amount while the payment record used amount_paid, so partial
payments posted a voucher that disagreed with the cash actually paid."
```

---

## Task 6: The preview endpoint

**Files:**
- Modify: `finance-api/app/schemas/vendor_credit.py`
- Modify: `finance-api/app/api/v1/vendor_credits.py`
- Test: `finance-api/tests/test_vendor_credit_netting.py`

**Interfaces:**
- Consumes: `select_credits_for_payment` with `lock=False`.
- Produces: `GET /finance/v1/vendor-credits/suggest?doc_kind=&doc_id=` returning
  `{gross, suggested: [{credit_id, credit_number, credit_date, remaining, apply}], credit_applied, net}`.

**Read-only and unlocked on purpose.** A preview that took row locks would hold them across the operator's think time and block the very payment run it is previewing.

**Route ordering matters:** `/vendor-credits/{credit_id}` already exists and `{credit_id}` is a UUID path param. Declare `/suggest` **before** the `/{credit_id}` route, or FastAPI will try to parse the literal string `suggest` as a UUID and return 422.

- [ ] **Step 1: Write the failing tests**

Append to `finance-api/tests/test_vendor_credit_netting.py`:

```python
@pytest.mark.anyio
async def test_suggest_returns_the_fifo_plan(client, db_session):
    pa = _pa(payment_amount=Decimal("100.00"))
    db_session.add(pa)
    db_session.add(_credit(vendor_id=pa.vendor_id, amount=Decimal("30.00"),
                           total_amount=Decimal("30.00"),
                           remaining_amount=Decimal("30.00")))
    await db_session.flush()

    r = await client.get("/finance/v1/vendor-credits/suggest",
                         params={"doc_kind": "pa", "doc_id": str(pa.id)},
                         headers=_h())
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["gross"] == "100.00"
    assert body["credit_applied"] == "30.00"
    assert body["net"] == "70.00"
    assert len(body["suggested"]) == 1
    assert body["suggested"][0]["apply"] == "30.00"


@pytest.mark.anyio
async def test_suggest_with_no_credits_returns_full_gross(client, db_session):
    pa = _pa(payment_amount=Decimal("55.00"))
    db_session.add(pa)
    await db_session.flush()

    r = await client.get("/finance/v1/vendor-credits/suggest",
                         params={"doc_kind": "pa", "doc_id": str(pa.id)},
                         headers=_h())
    body = r.json()
    assert body["suggested"] == []
    assert body["credit_applied"] == "0.00"
    assert body["net"] == "55.00"


@pytest.mark.anyio
async def test_suggest_rejects_expense_claim(client):
    r = await client.get("/finance/v1/vendor-credits/suggest",
                         params={"doc_kind": "expense_claim",
                                 "doc_id": str(uuid.uuid4())},
                         headers=_h())
    assert r.status_code == 422


@pytest.mark.anyio
async def test_suggest_404s_for_an_unknown_document(client):
    r = await client.get("/finance/v1/vendor-credits/suggest",
                         params={"doc_kind": "pa", "doc_id": str(uuid.uuid4())},
                         headers=_h())
    assert r.status_code == 404
```

Copy the `client` fixture and the `_h()` JWT helper from `tests/test_vendor_credit.py` — they are already written there and this file needs its own copies.

- [ ] **Step 2: Run and watch them fail**

Run: `cd finance-api && TEST_PG_PASSWORD=7c0a03bb8c2afef690d1852f8dc3a0195932db5f0f1670e9 TEST_FINANCE_DB=finance_test_vc_b6 python -m pytest tests/test_vendor_credit_netting.py -v -k suggest`

Expected: FAIL — 422 or 404 from the `/{credit_id}` route catching `suggest`.

- [ ] **Step 3: Add the response schemas**

Append to `finance-api/app/schemas/vendor_credit.py`:

```python
class CreditSuggestion(BaseModel):
    credit_id: uuid.UUID
    credit_number: str
    credit_date: date
    remaining: Decimal
    apply: Decimal


class CreditSuggestResponse(BaseModel):
    gross: Decimal
    suggested: list[CreditSuggestion]
    credit_applied: Decimal
    net: Decimal
```

- [ ] **Step 4: Add the route**

In `finance-api/app/api/v1/vendor_credits.py`, add this **above** the existing `@router.get("/{credit_id}")` route:

```python
@router.get("/suggest", response_model=CreditSuggestResponse)
async def suggest_credits(user: CurrentUser,
                          doc_kind: str = Query(...),
                          doc_id: uuid.UUID = Query(...),
                          db: AsyncSession = Depends(get_db)):
    """Preview which credits would be netted off a payment, without locking.

    Declared before /{credit_id} so FastAPI does not try to parse the literal
    "suggest" as a UUID.
    """
    if doc_kind not in ("pa", "pa_dir"):
        raise HTTPException(
            status_code=422,
            detail="Vendor credits apply to vendor payments only (pa, pa_dir)")

    pa = (await db.execute(
        select(PaymentApplication).where(PaymentApplication.id == doc_id)
    )).scalar_one_or_none()
    if pa is None:
        raise HTTPException(status_code=404, detail="Payment application not found")

    picks = await crud.select_credits_for_payment(
        db, vendor_id=pa.vendor_id, currency=pa.currency,
        base=pa.payment_amount, credit_ids=None, lock=False,
    )
    applied = sum((take for _, take in picks), Decimal("0"))
    return CreditSuggestResponse(
        gross=pa.payment_amount,
        suggested=[
            CreditSuggestion(
                credit_id=c.id, credit_number=c.credit_number,
                credit_date=c.credit_date, remaining=c.remaining_amount, apply=take,
            )
            for c, take in picks
        ],
        credit_applied=applied,
        net=pa.payment_amount - applied,
    )
```

Add the imports this needs: `Query` from `fastapi`, `select` from `sqlalchemy`, `Decimal` from `decimal`, `PaymentApplication` from `app.models.pa`, and the two new schemas.

- [ ] **Step 5: Run the tests**

Run: `cd finance-api && TEST_PG_PASSWORD=7c0a03bb8c2afef690d1852f8dc3a0195932db5f0f1670e9 TEST_FINANCE_DB=finance_test_vc_b6 python -m pytest tests/test_vendor_credit_netting.py -v`

Expected: all pass (31 by now). Also re-run `tests/test_vendor_credit.py` — adding a route above `/{credit_id}` must not change Phase A's behaviour.

- [ ] **Step 6: Commit**

```bash
git add finance-api/app/schemas/vendor_credit.py finance-api/app/api/v1/vendor_credits.py finance-api/tests/test_vendor_credit_netting.py
git commit -m "feat(finance): GET /vendor-credits/suggest — unlocked netting preview"
```

---

## Task 7: Per-line credit selection through the batch runner

**Files:**
- Modify: `finance-api/app/crud/payment_batch.py`
- Modify: `finance-api/app/api/v1/payments.py`
- Test: `finance-api/tests/test_vendor_credit_netting.py`

**Interfaces:**
- Consumes: `PaymentExecuteRequest.credit_ids`.
- Produces: `ExecuteBatchRequest.credit_ids_by_doc: dict[uuid.UUID, list[uuid.UUID]] | None = None`, keyed by `doc_id`; and `execute_batch(..., credit_ids_by_doc=None)`.

Omitting the map entirely, or omitting one document from it, leaves that line on the automatic FIFO default. An explicit empty list for a document means "pay this one in full".

- [ ] **Step 1: Write the failing tests**

Append to `finance-api/tests/test_vendor_credit_netting.py`:

```python
@pytest.mark.anyio
async def test_batch_line_defaults_to_automatic_netting(db_session):
    from app.crud import payment_batch
    from app.models.payment import PaymentRecord
    from app.models.payment_batch import PaymentBatch, PaymentBatchLine

    pa = _pa(payment_amount=Decimal("100.00"))
    db_session.add(pa)
    db_session.add(_credit(vendor_id=pa.vendor_id, amount=Decimal("25.00"),
                           total_amount=Decimal("25.00"),
                           remaining_amount=Decimal("25.00")))
    batch = PaymentBatch(batch_number=f"B-{uuid.uuid4().hex[:6]}",
                         batch_date=date(2026, 8, 7), status="draft",
                         currency="CAD", total=Decimal("100.00"),
                         payment_method="bank_transfer", created_by=uuid.uuid4())
    db_session.add(batch)
    await db_session.flush()
    db_session.add(PaymentBatchLine(batch_id=batch.id, doc_kind="pa",
                                    doc_id=pa.id, doc_number=pa.pa_number,
                                    amount=Decimal("100.00"), status="pending"))
    await db_session.flush()

    await payment_batch.execute_batch(
        db_session, batch, {"sub": str(uuid.uuid4()), "role": "system_admin"}, None)
    await db_session.flush()

    rec = (await db_session.execute(sa.select(PaymentRecord).where(
        PaymentRecord.doc_id == pa.id))).scalar_one()
    assert rec.credit_applied == Decimal("25.00")
    assert rec.amount == Decimal("75.00")


@pytest.mark.anyio
async def test_batch_line_can_opt_out_of_netting(db_session):
    from app.crud import payment_batch
    from app.models.payment import PaymentRecord
    from app.models.payment_batch import PaymentBatch, PaymentBatchLine

    pa = _pa(payment_amount=Decimal("100.00"))
    db_session.add(pa)
    db_session.add(_credit(vendor_id=pa.vendor_id))
    batch = PaymentBatch(batch_number=f"B-{uuid.uuid4().hex[:6]}",
                         batch_date=date(2026, 8, 7), status="draft",
                         currency="CAD", total=Decimal("100.00"),
                         payment_method="bank_transfer", created_by=uuid.uuid4())
    db_session.add(batch)
    await db_session.flush()
    db_session.add(PaymentBatchLine(batch_id=batch.id, doc_kind="pa",
                                    doc_id=pa.id, doc_number=pa.pa_number,
                                    amount=Decimal("100.00"), status="pending"))
    await db_session.flush()

    await payment_batch.execute_batch(
        db_session, batch, {"sub": str(uuid.uuid4()), "role": "system_admin"},
        None, credit_ids_by_doc={pa.id: []})
    await db_session.flush()

    rec = (await db_session.execute(sa.select(PaymentRecord).where(
        PaymentRecord.doc_id == pa.id))).scalar_one()
    assert rec.credit_applied == Decimal("0.00")
    assert rec.amount == Decimal("100.00")
```

- [ ] **Step 2: Run and watch them fail**

Run: `cd finance-api && TEST_PG_PASSWORD=7c0a03bb8c2afef690d1852f8dc3a0195932db5f0f1670e9 TEST_FINANCE_DB=finance_test_vc_b7 python -m pytest tests/test_vendor_credit_netting.py -v -k batch`

Expected: FAIL — `execute_batch()` got an unexpected keyword argument `credit_ids_by_doc`.

- [ ] **Step 3: Thread the map through the runner**

In `finance-api/app/crud/payment_batch.py`, extend the signature:

```python
async def execute_batch(db: AsyncSession, batch: PaymentBatch, user: dict,
                        bearer_token: str | None,
                        bank_account_id: uuid.UUID | None = None,
                        credit_ids_by_doc: dict[uuid.UUID, list[uuid.UUID]] | None = None,
                        ) -> PaymentBatch:
```

and inside the per-line loop, pass the entry for that document. A document absent from the map keeps `None` — the automatic default — while an explicit `[]` opts that line out:

```python
                        credit_ids=(credit_ids_by_doc or {}).get(ln.doc_id),
```

added to the `PaymentExecuteRequest(...)` construction.

- [ ] **Step 4: Accept the map on the route**

In `finance-api/app/api/v1/payments.py`, extend the request model:

```python
class ExecuteBatchRequest(BaseModel):
    bank_account_id: uuid.UUID | None = None
    # doc_id -> credit ids to apply. A document absent from the map uses the
    # automatic FIFO default; an explicit empty list pays that line in full.
    credit_ids_by_doc: dict[uuid.UUID, list[uuid.UUID]] | None = None
```

and forward it at the `execute_batch(...)` call site in that route.

- [ ] **Step 5: Run the tests**

Run: `cd finance-api && TEST_PG_PASSWORD=7c0a03bb8c2afef690d1852f8dc3a0195932db5f0f1670e9 TEST_FINANCE_DB=finance_test_vc_b7 python -m pytest tests/test_vendor_credit_netting.py -v`

Expected: all pass (33 by now). Re-run `tests/test_payment_batch.py` and compare with the count recorded in Task 4.

- [ ] **Step 6: Commit**

```bash
git add finance-api/app/crud/payment_batch.py finance-api/app/api/v1/payments.py finance-api/tests/test_vendor_credit_netting.py
git commit -m "feat(finance): per-line credit selection through the payment batch runner"
```

---

## Task 8: Remittance advice shows Gross / Credits applied / Net

**Files:**
- Modify: `finance-api/app/crud/remittance.py`
- Modify: `finance-api/app/services/remittance_template.py`
- Test: `finance-api/tests/test_vendor_credit_netting.py`

**Interfaces:**
- Consumes: `PaymentRecord.credit_applied`.
- Produces: `GroupLine.credit_applied` and `GroupLine.gross`; a remittance body that shows all three figures.

**Why this is not cosmetic:** the vendor receives less cash than their invoice. With no explanation on the advice they will call AP, and AP will have no document to point at.

- [ ] **Step 1: Write the failing test**

Append to `finance-api/tests/test_vendor_credit_netting.py`:

```python
@pytest.mark.anyio
async def test_remittance_line_carries_gross_and_credit(db_session):
    from app.crud import remittance
    from app.models.payment import PaymentRecord

    pa = _pa(payment_amount=Decimal("100.00"))
    db_session.add(pa)
    db_session.add(_credit(vendor_id=pa.vendor_id, amount=Decimal("30.00"),
                           total_amount=Decimal("30.00"),
                           remaining_amount=Decimal("30.00")))
    await db_session.flush()

    res = await _run_execute(db_session, pa, user_id=uuid.uuid4())
    await db_session.flush()

    rec = (await db_session.execute(sa.select(PaymentRecord).where(
        PaymentRecord.id == res.payment_record_id))).scalar_one()

    line = remittance.GroupLine(
        vendor_inv_no="INV-1", doc_number=rec.pa_number,
        payment_date=rec.payment_date, amount=rec.amount,
        credit_applied=rec.credit_applied, gross=rec.amount + rec.credit_applied,
    )
    assert line.amount == Decimal("70.00")
    assert line.credit_applied == Decimal("30.00")
    assert line.gross == Decimal("100.00")
```

- [ ] **Step 2: Run and watch it fail**

Run: `cd finance-api && TEST_PG_PASSWORD=7c0a03bb8c2afef690d1852f8dc3a0195932db5f0f1670e9 TEST_FINANCE_DB=finance_test_vc_b8 python -m pytest tests/test_vendor_credit_netting.py -v -k remittance`

Expected: FAIL — `GroupLine.__init__() got an unexpected keyword argument 'credit_applied'`

- [ ] **Step 3: Widen the dataclass**

In `finance-api/app/crud/remittance.py`, extend `GroupLine` with defaults so existing construction sites keep working:

```python
@dataclass
class GroupLine:
    vendor_inv_no: str
    doc_number: str
    payment_date: date
    amount: Decimal                       # net cash paid
    credit_applied: Decimal = Decimal("0")
    gross: Decimal = Decimal("0")
```

Then, at both `GroupLine(...)` construction sites in that module (near lines 206 and 252), pass the new fields:

```python
                                  credit_applied=r.credit_applied,
                                  gross=r.amount + r.credit_applied))
```

- [ ] **Step 4: Show the figures in the rendered advice**

In `finance-api/app/services/remittance_template.py`, in the per-line row builder, add the two figures beside the existing amount cell — render `Gross`, `Credits applied` and `Net paid` for any line where `credit_applied` is non-zero, and keep the current single-amount layout when it is zero, so ordinary remittances look exactly as they do today. Use the existing `_money(...)` helper for every figure so currency formatting stays consistent.

- [ ] **Step 5: Run the tests**

Run: `cd finance-api && TEST_PG_PASSWORD=7c0a03bb8c2afef690d1852f8dc3a0195932db5f0f1670e9 TEST_FINANCE_DB=finance_test_vc_b8 python -m pytest tests/test_vendor_credit_netting.py -v`

Expected: all pass (34 by now). Then run `tests/test_remittance.py` with the same env and compare against `origin/main` — widening a dataclass with defaults should change nothing there.

- [ ] **Step 6: Commit**

```bash
git add finance-api/app/crud/remittance.py finance-api/app/services/remittance_template.py finance-api/tests/test_vendor_credit_netting.py
git commit -m "feat(finance): remittance advice explains a short payment (gross/credits/net)"
```

---

## Task 9: Batch execution preview with per-line deselect

**Files:**
- Modify: `finance/src/pages/finance/PaymentBatchPage.tsx`
- Modify: whichever module in `finance/src/services/` holds the payments client (find it with `grep -rn "payments/batches" finance/src/services finance/src/pages`)

**Interfaces:**
- Consumes: `GET /finance/v1/vendor-credits/suggest`; `POST /finance/v1/payments/batches/{id}/execute` now accepting `credit_ids_by_doc`.

**Why here:** this is the only surface from which payments are executed today — nothing in either frontend calls `/payments/execute` directly (verified by search on 2026-08-07).

**Money fields arrive as Decimal-serialised strings.** Type them `string` and put every arithmetic operation through `Number()`.

- [ ] **Step 1: Measure the frontend baseline before touching anything**

Run:
```bash
cd finance && npx tsc --noEmit 2>&1 | grep -c "error TS"
```
Record the number and put it in your report. If it prints `0`, check the raw output for real diagnostics — a missing `node_modules` makes tsc print an install prompt instead of errors, which greps to zero. Run `npm ci` in `finance/` if so, then measure again. **Do not copy a baseline number from anywhere; measure it.**

- [ ] **Step 2: Add the suggest client call**

In the payments service module, add:

```ts
export interface CreditSuggestion {
  credit_id: string
  credit_number: string
  credit_date: string
  /** Decimal-as-string. Number() before arithmetic. */
  remaining: string
  apply: string
}

export interface CreditSuggestResponse {
  gross: string
  suggested: CreditSuggestion[]
  credit_applied: string
  net: string
}

export const suggestCredits = (docKind: string, docId: string) =>
  financeApi.get<CreditSuggestResponse>(
    `/vendor-credits/suggest?doc_kind=${docKind}&doc_id=${docId}`)
```

- [ ] **Step 3: Fetch a suggestion per batch line when the execute dialog opens**

In `PaymentBatchPage.tsx`'s batch-detail view, when the execute confirmation opens, fetch one suggestion per line in parallel and hold them keyed by `doc_id`:

```tsx
const [suggestions, setSuggestions] = useState<Record<string, CreditSuggestResponse>>({})
const [deselected, setDeselected] = useState<Record<string, Set<string>>>({})

useEffect(() => {
  if (!showExecute) return
  let cancelled = false
  void Promise.all(
    lines.map(async (ln) => [ln.doc_id, await suggestCredits(ln.doc_kind, ln.doc_id)] as const),
  ).then((entries) => {
    if (!cancelled) setSuggestions(Object.fromEntries(entries))
  })
  return () => { cancelled = true }
}, [showExecute, lines])
```

- [ ] **Step 4: Render Gross / Credits / Net per line, with a checkbox per credit**

Each line shows `Gross`, `Credits applied`, `Net` computed from the suggestion minus anything the operator deselected:

```tsx
const appliedFor = (docId: string) => {
  const s = suggestions[docId]
  if (!s) return 0
  const off = deselected[docId] ?? new Set<string>()
  return s.suggested
    .filter((c) => !off.has(c.credit_id))
    .reduce((sum, c) => sum + Number(c.apply), 0)
}
```

Render each suggested credit as a checked checkbox labelled with its `credit_number`, `credit_date` and `apply` amount; unchecking adds it to `deselected[docId]`.

- [ ] **Step 5: Send the operator's choices on execute**

Build the map only for documents the operator actually changed. A document left untouched must be **absent** from the map so the server applies its automatic default — sending its full id list would work today but would silently diverge if a new credit arrived between preview and execute:

```tsx
const creditIdsByDoc: Record<string, string[]> = {}
for (const ln of lines) {
  const off = deselected[ln.doc_id]
  if (!off || off.size === 0) continue
  const s = suggestions[ln.doc_id]
  creditIdsByDoc[ln.doc_id] = (s?.suggested ?? [])
    .filter((c) => !off.has(c.credit_id))
    .map((c) => c.credit_id)
}
```

and pass `credit_ids_by_doc: creditIdsByDoc` in the execute mutation body alongside `bank_account_id`.

- [ ] **Step 6: Surface an execute failure**

If the execute call rejects, show the server's `detail` text — a credit that became unavailable between preview and execute returns a message naming it, which is exactly what the operator needs. Follow whatever error idiom this page already uses; do not invent a new one.

- [ ] **Step 7: Type-check**

Run: `cd finance && npx tsc --noEmit 2>&1 | grep -c "error TS"`

Expected: the baseline you recorded in Step 1, unchanged.

- [ ] **Step 8: Commit**

```bash
git add finance/src/pages/finance/PaymentBatchPage.tsx finance/src/services
git commit -m "feat(finance-ui): show and edit vendor credit netting before executing a batch"
```

---

## Phase B Done Criteria

- [ ] `tests/test_vendor_credit_netting.py` passes in full
- [ ] `tests/test_vendor_credit.py` still passes at 41 — Phase A untouched
- [ ] `tests/test_payment_execute.py`, `tests/test_payment_batch.py`, `tests/test_remittance.py` compared against the counts recorded in Task 4 Step 6, with any change explained
- [ ] `finance` tsc equals the baseline measured in Task 9 Step 1
- [ ] One uncontended full finance-api suite run at the end of the phase (45 minutes; run it alone)
- [ ] `expense_claim` payments still take no credits — confirm no diff touches that branch

## Deploy notes for this phase

- Add an `account_mappings` row: `mapping_type='line_role'`, `source_code='vendor_credit_clearing'`, `account_code=<the COA clearing account>`. A missing mapping does not block payment; the line simply posts with a NULL account code and shows up in posting queries.
- **Tell Finance before deploying:** partial payments now post a voucher for the amount actually paid rather than the full PA amount. If they have been manually adjusting for the old behaviour, those adjustments must stop.
- Phase A's deploy step still applies if not yet done: run `seed_phase2_keys.py` in the identity container and tick `epms.vendor_credit.manage` in Portal → Access Control.
