"""Vendor Credit — Phase A (record + review, no money movement)."""
import uuid
from datetime import date, datetime, timezone
from decimal import Decimal

import pytest
import sqlalchemy as sa
from sqlalchemy.exc import IntegrityError


def _row(**overrides):
    """A minimal valid vendor_credits row. Callers override just the columns
    relevant to the constraint under test; every NOT-NULL column without a
    Column-level default is filled in here so a Core insert() can be issued
    directly against VendorCredit.__table__ without going through the ORM's
    add()/flush() machinery."""
    base = dict(
        credit_number=f"VC-{uuid.uuid4().hex[:12]}",
        vendor_id=uuid.uuid4(),
        vendor_name="Test Vendor",
        vendor_credit_number=f"VCN-{uuid.uuid4().hex[:8]}",
        credit_date=date(2026, 8, 1),
        amount=Decimal("100.00"),
        tax_amount=Decimal("0.00"),
        total_amount=Decimal("100.00"),
        applied_amount=Decimal("0.00"),
        remaining_amount=Decimal("100.00"),
        uploaded_by=uuid.uuid4(),
        uploaded_at=datetime.now(timezone.utc),
    )
    base.update(overrides)
    return base


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


@pytest.mark.anyio
async def test_migration_creates_table_with_checks(db_session):
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
    assert "ck_vendor_credits_nonneg" in checks

    idx = (await db_session.execute(sa.text(
        "SELECT indexname FROM pg_indexes WHERE tablename = 'vendor_credits'"
    ))).scalars().all()
    assert "uq_vendor_credits_vendor_docno" in idx
    assert "uq_vendor_credits_source_ref" in idx
    assert "ix_vendor_credits_available" in idx


@pytest.mark.anyio
async def test_check_total_positive_rejects_zero_and_negative(db_session):
    """ck_vendor_credits_total_positive: total_amount must be > 0.

    The zero case is set up so applied_amount + remaining_amount = 0 = total,
    which satisfies ck_vendor_credits_balance and ck_vendor_credits_nonneg —
    isolating the failure to ck_vendor_credits_total_positive alone.
    """
    from app.models.vendor_credit import VendorCredit

    with pytest.raises(IntegrityError) as exc:
        await db_session.execute(sa.insert(VendorCredit).values(
            **_row(total_amount=Decimal("0.00"), applied_amount=Decimal("0.00"),
                   remaining_amount=Decimal("0.00"))))
    assert "ck_vendor_credits_total_positive" in str(exc.value)
    await db_session.rollback()

    # A negative total_amount is rejected too. It necessarily also breaks
    # ck_vendor_credits_nonneg or ck_vendor_credits_balance (nonneg
    # applied/remaining can never sum to a negative total), so only the
    # error type is asserted here — not which constraint fires first.
    with pytest.raises(IntegrityError):
        await db_session.execute(sa.insert(VendorCredit).values(
            **_row(total_amount=Decimal("-5.00"), applied_amount=Decimal("0.00"),
                   remaining_amount=Decimal("-5.00"))))
    await db_session.rollback()


@pytest.mark.anyio
async def test_check_balance_rejects_mismatch(db_session):
    """ck_vendor_credits_balance: applied_amount + remaining_amount must equal
    total_amount. 50 + 40 != 100, with every other constraint satisfied."""
    from app.models.vendor_credit import VendorCredit

    with pytest.raises(IntegrityError) as exc:
        await db_session.execute(sa.insert(VendorCredit).values(
            **_row(total_amount=Decimal("100.00"), applied_amount=Decimal("50.00"),
                   remaining_amount=Decimal("40.00"))))
    assert "ck_vendor_credits_balance" in str(exc.value)
    await db_session.rollback()


@pytest.mark.anyio
async def test_check_nonneg_rejects_negative_applied(db_session):
    """ck_vendor_credits_nonneg: applied_amount must be >= 0. remaining_amount
    is set to keep the balance constraint satisfied (-10 + 110 = 100), so only
    ck_vendor_credits_nonneg is broken."""
    from app.models.vendor_credit import VendorCredit

    with pytest.raises(IntegrityError) as exc:
        await db_session.execute(sa.insert(VendorCredit).values(
            **_row(total_amount=Decimal("100.00"), applied_amount=Decimal("-10.00"),
                   remaining_amount=Decimal("110.00"))))
    assert "ck_vendor_credits_nonneg" in str(exc.value)
    await db_session.rollback()


@pytest.mark.anyio
async def test_unique_vendor_docno_blocks_duplicate_upload(db_session):
    """uq_vendor_credits_vendor_docno: two non-void 'upload' rows for the same
    (vendor_id, vendor_credit_number) must collide."""
    from app.models.vendor_credit import PENDING_REVIEW, SOURCE_UPLOAD, VendorCredit

    vendor_id = uuid.uuid4()
    docno = f"VCN-{uuid.uuid4().hex[:8]}"
    await db_session.execute(sa.insert(VendorCredit).values(
        **_row(vendor_id=vendor_id, vendor_credit_number=docno,
               source=SOURCE_UPLOAD, status=PENDING_REVIEW)))

    with pytest.raises(IntegrityError) as exc:
        await db_session.execute(sa.insert(VendorCredit).values(
            **_row(vendor_id=vendor_id, vendor_credit_number=docno,
                   source=SOURCE_UPLOAD, status=PENDING_REVIEW)))
    assert "uq_vendor_credits_vendor_docno" in str(exc.value)
    await db_session.rollback()


@pytest.mark.anyio
async def test_unique_vendor_docno_allows_reupload_after_void(db_session):
    """The re-upload-after-rejection rule: if the first row for a
    (vendor_id, vendor_credit_number) is void, a second row with the same
    pair MUST succeed — the partial unique index only guards non-void rows."""
    from app.models.vendor_credit import PENDING_REVIEW, SOURCE_UPLOAD, VOID, VendorCredit

    vendor_id = uuid.uuid4()
    docno = f"VCN-{uuid.uuid4().hex[:8]}"
    await db_session.execute(sa.insert(VendorCredit).values(
        **_row(vendor_id=vendor_id, vendor_credit_number=docno,
               source=SOURCE_UPLOAD, status=VOID)))

    # Must not raise.
    await db_session.execute(sa.insert(VendorCredit).values(
        **_row(vendor_id=vendor_id, vendor_credit_number=docno,
               source=SOURCE_UPLOAD, status=PENDING_REVIEW)))


@pytest.mark.anyio
async def test_unique_source_ref_blocks_duplicate_but_allows_null(db_session):
    """uq_vendor_credits_source_ref: two rows sharing (source, source_ref)
    collide when source_ref is set, but rows with source_ref IS NULL never
    collide with each other (the partial index excludes NULLs)."""
    from app.models.vendor_credit import SOURCE_QBO_IMPORT, VendorCredit

    ref = f"REF-{uuid.uuid4().hex[:8]}"
    await db_session.execute(sa.insert(VendorCredit).values(
        **_row(source=SOURCE_QBO_IMPORT, source_ref=ref)))

    with pytest.raises(IntegrityError) as exc:
        await db_session.execute(sa.insert(VendorCredit).values(
            **_row(source=SOURCE_QBO_IMPORT, source_ref=ref)))
    assert "uq_vendor_credits_source_ref" in str(exc.value)
    await db_session.rollback()

    # Two NULL source_ref rows must NOT collide.
    await db_session.execute(sa.insert(VendorCredit).values(
        **_row(source=SOURCE_QBO_IMPORT, source_ref=None)))
    await db_session.execute(sa.insert(VendorCredit).values(
        **_row(source=SOURCE_QBO_IMPORT, source_ref=None)))
