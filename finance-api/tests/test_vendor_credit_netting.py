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

    # Pins the migration's explicit index names to the model's default
    # SQLAlchemy-generated names (index=True, no explicit `name=`). If these
    # ever drift apart, `alembic revision --autogenerate` will silently
    # propose dropping and recreating indexes on a live table.
    idx = (await db_session.execute(sa.text(
        "SELECT indexname FROM pg_indexes WHERE tablename = 'vendor_credit_applications'"
    ))).scalars().all()
    assert {
        "ix_vendor_credit_applications_credit_id",
        "ix_vendor_credit_applications_payment_record_id",
        "ix_vendor_credit_applications_doc_id",
    } <= set(idx)


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
