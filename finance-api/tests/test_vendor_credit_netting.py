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
