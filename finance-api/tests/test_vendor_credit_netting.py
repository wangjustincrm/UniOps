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


def _pa(**over):
    # NOTE: the brief's version of this helper passed fields (gr_ids, subtotal,
    # tax_amount, shipping_amount, other_charges, approval_step_idx) that do not
    # exist on the actual PaymentApplication model (app/models/pa.py) — it raised
    # TypeError on construction. Corrected to the model's real columns, matching
    # the existing `_pa()` helper in tests/test_payment_execute.py; semantics
    # (payment_amount / vendor_id / vendor_name / currency / status) unchanged.
    from app.models.pa import PaymentApplication
    kw = dict(
        pa_number=f"PA-{uuid.uuid4().hex[:8]}", title="Test PA", pa_type="PA-DIR",
        vendor_id=uuid.uuid4(), vendor_name="ULINE",
        invoice_ids=[], payment_amount=Decimal("100.00"), currency="CAD",
        status="approved", created_by=uuid.uuid4(),
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
    from app.models.vendor_credit import VendorCreditApplication
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

    # Pin the flush-before-apply wiring: the application row the executor
    # wrote must point at THIS payment record, and batch_id must be None for
    # a non-batch call (a wrong id would only ever surface as a NOT NULL
    # error, not a clear assertion failure).
    app_row = (await db_session.execute(sa.select(VendorCreditApplication).where(
        VendorCreditApplication.payment_record_id == rec.id))).scalar_one()
    assert app_row.payment_record_id == rec.id
    assert app_row.batch_id is None
    assert app_row.applied_amount == Decimal("30.00")


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


@pytest.mark.anyio
async def test_pa_path_with_po_id_nets_the_same_as_pa_dir(db_session):
    """Every other test's _pa() leaves po_id unset, so execute() has only ever
    routed it through the pa_dir branch (doc_kind = 'pa_dir' if po_id is None
    else 'pa'). The 'pa' branch additionally writes back to invoice_ids /
    ap_invoices — netting is shared code, so this confirms rather than
    suspects, but it has never actually run."""
    from app.models.payment import PaymentRecord
    pa = _pa(payment_amount=Decimal("100.00"), po_id=uuid.uuid4(),
             po_number="PO-TEST-1")
    db_session.add(pa)
    db_session.add(_credit(vendor_id=pa.vendor_id, amount=Decimal("30.00"),
                           total_amount=Decimal("30.00"),
                           remaining_amount=Decimal("30.00")))
    await db_session.flush()

    res = await _run_execute(db_session, pa, user_id=uuid.uuid4())

    assert res.doc_kind == "pa"
    rec = (await db_session.execute(sa.select(PaymentRecord).where(
        PaymentRecord.id == res.payment_record_id))).scalar_one()
    assert rec.amount == Decimal("70.00")
    assert rec.credit_applied == Decimal("30.00")
