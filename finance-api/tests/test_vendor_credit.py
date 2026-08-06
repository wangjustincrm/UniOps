"""Vendor Credit — Phase A (record + review, no money movement)."""
import pytest
import sqlalchemy as sa


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

    idx = (await db_session.execute(sa.text(
        "SELECT indexname FROM pg_indexes WHERE tablename = 'vendor_credits'"
    ))).scalars().all()
    assert "uq_vendor_credits_vendor_docno" in idx
    assert "uq_vendor_credits_source_ref" in idx
