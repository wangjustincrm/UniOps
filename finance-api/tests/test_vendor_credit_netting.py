"""Vendor Credit — Phase B (payment netting, GL, remittance)."""
import uuid
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

import pytest
import pytest_asyncio
import sqlalchemy as sa
from httpx import ASGITransport, AsyncClient
from jose import jwt

from app.core.config import settings
from app.db.base import get_db
from app.main import app


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


async def _posting_lines(db, event_id):
    """(debit, credit, account_code) per line_role. account_code is a third
    element appended after debit/credit — existing callers indexing [0]/[1]
    are unaffected."""
    rows = (await db.execute(sa.text(
        "SELECT line_role, debit, credit, account_code FROM posting_lines "
        "WHERE event_id = :e ORDER BY line_role"
    ), {"e": str(event_id)})).all()
    return {r[0]: (r[1], r[2], r[3]) for r in rows}


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
    debits = sum(d or Decimal("0") for d, _, _ in lines.values())
    credits = sum(c or Decimal("0") for _, c, _ in lines.values())
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

    # Pin the exact line set — without this, a stray extra posting line
    # would slip through unnoticed by the two amount assertions below.
    assert set(lines) == {"accounts_payable", "bank"}
    assert lines["accounts_payable"][0] == Decimal("40.00")
    assert lines["bank"][1] == Decimal("40.00")
    debits = sum(d or Decimal("0") for d, _, _ in lines.values())
    credits = sum(c or Decimal("0") for _, c, _ in lines.values())
    assert debits == credits


@pytest.mark.anyio
async def test_credit_side_sums_to_debit_including_clearing_line(db_session):
    """An unmapped 'vendor_credit_clearing' line_role is silently dropped by
    the balance-sheet/income-statement builders (app/crud/gl.py: `if not
    acct: continue`), which would throw the balance sheet out of balance by
    exactly credit_applied on every credited payment — invisible unless
    something asserts the clearing line's own presence and amount, not just
    that totals balance (totals alone would still balance if this line were
    never emitted and the bank line silently carried the full amount)."""
    pa = _pa(payment_amount=Decimal("100.00"))
    db_session.add(pa)
    db_session.add(_credit(vendor_id=pa.vendor_id, amount=Decimal("30.00"),
                           total_amount=Decimal("30.00"),
                           remaining_amount=Decimal("30.00")))
    await db_session.flush()

    res = await _run_execute(db_session, pa, user_id=uuid.uuid4())
    lines = await _posting_lines(db_session, res.posting_event_id)

    assert set(lines) == {"accounts_payable", "bank", "vendor_credit_clearing"}
    # The clearing line must itself exist with the expected credit amount —
    # not merely inferred from the totals matching. debit is NOT NULL
    # server_default 0 (app/models/posting.py), never None, for a
    # credit-only line. account_code is None here because this test's fresh
    # migration run finds no chart_of_accounts row for '1123' (the local
    # dev/test IFRS seed does not carry it — see
    # test_clearing_mapping_seeds_and_stamps_when_account_1123_present for
    # the case where it is present).
    assert lines["vendor_credit_clearing"] == (Decimal("0.00"), Decimal("30.00"), None)

    debit_total = lines["accounts_payable"][0]
    credit_total = lines["bank"][1] + lines["vendor_credit_clearing"][1]
    assert debit_total == credit_total == Decimal("100.00")


@pytest.mark.anyio
async def test_voucher_when_credit_fully_covers_the_payment(db_session):
    """net == 0: emit_event must drop the zero-amount bank line rather than
    post a spurious $0 bank leg. This is the case the credit-clearing feature
    exists for and depends on an implementation detail one layer away
    (emit_event's line filtering) that this suite would not otherwise catch
    if it changed."""
    pa = _pa(payment_amount=Decimal("30.00"))
    db_session.add(pa)
    db_session.add(_credit(vendor_id=pa.vendor_id, amount=Decimal("30.00"),
                           total_amount=Decimal("30.00"),
                           remaining_amount=Decimal("30.00")))
    await db_session.flush()

    res = await _run_execute(db_session, pa, user_id=uuid.uuid4())
    lines = await _posting_lines(db_session, res.posting_event_id)

    assert set(lines) == {"accounts_payable", "vendor_credit_clearing"}
    assert "bank" not in lines
    assert lines["accounts_payable"][0] == Decimal("30.00")
    assert lines["vendor_credit_clearing"][1] == Decimal("30.00")
    debits = sum(d or Decimal("0") for d, _, _ in lines.values())
    credits = sum(c or Decimal("0") for _, c, _ in lines.values())
    assert debits == credits


@pytest.mark.anyio
async def test_voucher_for_partial_payment_with_credit_applied(db_session):
    """The two changes in this task interacting: base comes from amount_paid
    (spec 6.6), and the credit side still splits against that base rather
    than against pa.payment_amount."""
    pa = _pa(payment_amount=Decimal("100.00"))
    db_session.add(pa)
    db_session.add(_credit(vendor_id=pa.vendor_id, amount=Decimal("15.00"),
                           total_amount=Decimal("15.00"),
                           remaining_amount=Decimal("15.00")))
    await db_session.flush()

    res = await _run_execute(db_session, pa, user_id=uuid.uuid4(),
                             amount_paid=Decimal("40.00"))
    lines = await _posting_lines(db_session, res.posting_event_id)

    assert set(lines) == {"accounts_payable", "bank", "vendor_credit_clearing"}
    assert lines["accounts_payable"][0] == Decimal("40.00")     # base, not payment_amount
    assert lines["bank"][1] == Decimal("25.00")                 # net = base - credit_applied
    assert lines["vendor_credit_clearing"][1] == Decimal("15.00")
    debits = sum(d or Decimal("0") for d, _, _ in lines.values())
    credits = sum(c or Decimal("0") for _, c, _ in lines.values())
    assert debits == credits


# The guarded, idempotent INSERT from alembic/versions/
# 0032_vendor_credit_clearing_mapping.py's upgrade() — kept as a literal
# copy here (not imported: alembic version modules use op.execute(), which
# needs an alembic Operations/MigrationContext bound to the connection, not
# a plain AsyncSession) so these two tests can exercise its exact SQL under
# both guard conditions without invoking alembic from a test. This service's
# alembic/env.py builds its engine with create_async_engine — alembic runs
# under asyncpg here in every environment, test and production alike, same
# as this test. The migration binds a uuid.UUID object (`id=uuid.uuid4()`);
# SQLAlchemy infers a bind parameter's type from the Python value, so a UUID
# object types correctly against the uuid column with no CAST needed. This
# test binds uuid.uuid4() the same way, so the SQL below is now a genuinely
# literal copy of the migration's — no CAST, no divergence.
_SEED_VENDOR_CREDIT_CLEARING_MAPPING = sa.text(
    "INSERT INTO account_mappings (id, mapping_type, source_code, account_code) "
    "SELECT :id, 'line_role', 'vendor_credit_clearing', '1123' "
    "WHERE EXISTS (SELECT 1 FROM chart_of_accounts WHERE code = '1123') "
    "ON CONFLICT (mapping_type, source_code) DO NOTHING"
)


async def _vendor_credit_clearing_mapping_account_code(db):
    row = (await db.execute(sa.text(
        "SELECT account_code FROM account_mappings "
        "WHERE mapping_type='line_role' AND source_code='vendor_credit_clearing'"
    ))).first()
    return row[0] if row else None


@pytest.mark.anyio
async def test_clearing_mapping_absent_when_account_1123_not_seeded(db_session):
    """Pins the guard's negative branch against the REAL migration outcome —
    not a simulation. db_session rebuilds this database from scratch via
    `alembic upgrade head` on every test (tests/conftest.py's _migrate()),
    and this repo's local dev/test IFRS seed (migration 0005) does not carry
    account 1123 — so 0032's guarded INSERT is correctly a no-op here, same
    as it would be in any environment before its first NC COA sync. A
    credited payment must still succeed, with account_code left NULL on the
    clearing line (Finding 1's 'does not block payment' contract)."""
    assert await _vendor_credit_clearing_mapping_account_code(db_session) is None

    pa = _pa(payment_amount=Decimal("50.00"))
    db_session.add(pa)
    db_session.add(_credit(vendor_id=pa.vendor_id, amount=Decimal("20.00"),
                           total_amount=Decimal("20.00"),
                           remaining_amount=Decimal("20.00")))
    await db_session.flush()

    res = await _run_execute(db_session, pa, user_id=uuid.uuid4())
    lines = await _posting_lines(db_session, res.posting_event_id)
    assert lines["vendor_credit_clearing"][2] is None   # account_code


@pytest.mark.anyio
async def test_clearing_mapping_seeds_and_stamps_when_account_1123_present(db_session):
    """The environment this migration is written for: the COA has been
    NC-synced to include 1123 'Advance to suppliers' (this repo's local
    dev/test seed does not carry it — see the sibling
    test_clearing_mapping_absent_when_account_1123_not_seeded for that real,
    unmodified state). Seeds chart_of_accounts with 1123 here to reproduce
    that environment, then replays 0032's exact guarded INSERT to prove:
    (1) it seeds the mapping with account_code='1123' once the account
    exists, (2) it is idempotent — replaying it a second time raises nothing
    and leaves exactly one row, and (3) _stamp_account_codes then stamps
    that code onto a credited payment's clearing line, which is the
    assertion that actually closes the balance-sheet hole Finding 1 raised
    (an account_code the balance-sheet/income-statement builders can find,
    not just a row in account_mappings)."""
    from app.models.coa import ChartOfAccount
    db_session.add(ChartOfAccount(
        code="1123", name="Advance to Suppliers", account_type="asset",
        normal_balance="debit",
    ))
    await db_session.flush()

    await db_session.execute(_SEED_VENDOR_CREDIT_CLEARING_MAPPING.bindparams(id=uuid.uuid4()))
    await db_session.execute(_SEED_VENDOR_CREDIT_CLEARING_MAPPING.bindparams(id=uuid.uuid4()))
    await db_session.flush()

    rows = (await db_session.execute(sa.text(
        "SELECT account_code FROM account_mappings "
        "WHERE mapping_type='line_role' AND source_code='vendor_credit_clearing'"
    ))).all()
    assert [r[0] for r in rows] == ["1123"]   # exactly one row — ON CONFLICT DO NOTHING held

    pa = _pa(payment_amount=Decimal("50.00"))
    db_session.add(pa)
    db_session.add(_credit(vendor_id=pa.vendor_id, amount=Decimal("20.00"),
                           total_amount=Decimal("20.00"),
                           remaining_amount=Decimal("20.00")))
    await db_session.flush()

    res = await _run_execute(db_session, pa, user_id=uuid.uuid4())
    lines = await _posting_lines(db_session, res.posting_event_id)
    assert lines["vendor_credit_clearing"][2] == "1123"   # account_code stamped by _stamp_account_codes


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


@pytest.mark.anyio
async def test_batch_isolates_a_line_naming_an_unavailable_credit(db_session):
    """CreditUnavailable inherits ValueError (Task 4) specifically so this
    path — an operator-selected credit that has gone stale by execute time —
    fails only its own line's savepoint rather than escaping execute_batch's
    per-line try/except and aborting the whole loop with already-executed
    lines' status flips stuck in the outer (uncommitted) transaction while
    the batch itself stays 'draft'. Two lines: one names a voided credit and
    must be recorded 'failed' with its error; the other is ordinary and must
    still pay, and the batch must still finish (not raise)."""
    from app.crud import payment_batch
    from app.models.payment import PaymentRecord
    from app.models.payment_batch import PaymentBatch, PaymentBatchLine

    bad_pa = _pa(payment_amount=Decimal("100.00"))
    good_pa = _pa(payment_amount=Decimal("50.00"))
    db_session.add_all([bad_pa, good_pa])
    voided = _credit(vendor_id=bad_pa.vendor_id, status="void")
    db_session.add(voided)
    batch = PaymentBatch(batch_number=f"B-{uuid.uuid4().hex[:6]}",
                         batch_date=date(2026, 8, 7), status="draft",
                         currency="CAD", total=Decimal("150.00"),
                         payment_method="bank_transfer", created_by=uuid.uuid4())
    db_session.add(batch)
    await db_session.flush()
    db_session.add_all([
        PaymentBatchLine(batch_id=batch.id, doc_kind="pa",
                         doc_id=bad_pa.id, doc_number=bad_pa.pa_number,
                         amount=Decimal("100.00"), status="pending"),
        PaymentBatchLine(batch_id=batch.id, doc_kind="pa",
                         doc_id=good_pa.id, doc_number=good_pa.pa_number,
                         amount=Decimal("50.00"), status="pending"),
    ])
    await db_session.flush()
    # Captured before execute_batch: the failed line's savepoint rollback
    # expires touched ORM objects, and re-fetching an expired attribute
    # outside an awaited context raises MissingGreenlet — so read these off
    # the (already-flushed, still-fresh) objects now rather than after.
    bad_pa_id, good_pa_id, batch_id = bad_pa.id, good_pa.id, batch.id

    result = await payment_batch.execute_batch(
        db_session, batch, {"sub": str(uuid.uuid4()), "role": "system_admin"},
        None, credit_ids_by_doc={bad_pa_id: [voided.id]})
    await db_session.flush()

    assert result.status == "executed"

    lines = (await db_session.execute(sa.select(PaymentBatchLine).where(
        PaymentBatchLine.batch_id == batch_id))).scalars().all()
    by_doc = {ln.doc_id: ln for ln in lines}
    assert by_doc[bad_pa_id].status == "failed"
    assert by_doc[bad_pa_id].error
    assert by_doc[good_pa_id].status == "paid"

    rec = (await db_session.execute(sa.select(PaymentRecord).where(
        PaymentRecord.doc_id == good_pa_id))).scalar_one()
    assert rec.amount == Decimal("50.00")


@pytest.mark.anyio
async def test_execute_batch_route_coerces_credit_ids_by_doc_keys(client, db_session):
    """The CRUD-level tests above call execute_batch() directly with a native
    dict whose keys are already uuid.UUID objects — that never proves the
    wire path works. A real JSON request body has STRING keys
    ({"credit_ids_by_doc": {"<uuid-string>": [...]}}), and the executor looks
    up with `.get(ln.doc_id)` where ln.doc_id is a uuid.UUID. If Pydantic did
    not coerce ExecuteBatchRequest.credit_ids_by_doc's string keys to
    uuid.UUID, the lookup would silently miss for every doc, every line would
    fall back to the automatic default, and an operator's deselection would
    be silently ignored. Goes through the real route (like
    tests/test_payment_batch.py::test_create_and_execute_batch) with an
    explicit empty list for the one line, so a passing assertion here can
    only mean the string key really reached the executor as a matching
    uuid.UUID."""
    from app.models.payment import PaymentRecord
    pa = _pa(payment_amount=Decimal("100.00"))
    db_session.add(pa)
    db_session.add(_credit(vendor_id=pa.vendor_id))
    await db_session.flush()

    r = await client.post("/finance/v1/payments/batches", headers=_h(),
                          json={"docs": [{"doc_kind": "pa_dir", "doc_id": str(pa.id)}]})
    assert r.status_code == 201, r.text
    batch_id = r.json()["id"]

    r2 = await client.post(f"/finance/v1/payments/batches/{batch_id}/execute",
                           headers=_h(),
                           json={"credit_ids_by_doc": {str(pa.id): []}})
    assert r2.status_code == 200, r2.text
    assert r2.json()["paid"] == 1 and r2.json()["failed"] == 0

    rec = (await db_session.execute(sa.select(PaymentRecord).where(
        PaymentRecord.doc_id == pa.id))).scalar_one()
    assert rec.credit_applied == Decimal("0.00")
    assert rec.amount == Decimal("100.00")


# ── Task 8: remittance advice explains a short payment ──────────────────────

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


def test_remittance_row_explains_a_netted_line():
    # NOTE: the brief's render(...) call omits `payment_method`, which is a
    # required keyword-only argument of the real render() in this codebase
    # (see tests/test_remittance.py's calls) — added here per the brief's own
    # fallback instruction rather than changing render()'s signature. render()
    # also returns (subject, html), not html alone; unpacked accordingly.
    from app.crud.remittance import GroupLine, PayeeGroup
    from app.services import remittance_template

    group = PayeeGroup(
        recipient_kind="vendor", party_id=uuid.uuid4(), party_name="ULINE",
        email="ap@uline.test", currency="CAD",
        lines=[
            GroupLine(vendor_inv_no="INV-1", doc_number="PA-1",
                      payment_date=date(2026, 8, 7), amount=Decimal("70.00"),
                      credit_applied=Decimal("30.00"), gross=Decimal("100.00")),
            GroupLine(vendor_inv_no="INV-2", doc_number="PA-2",
                      payment_date=date(2026, 8, 7), amount=Decimal("50.00")),
        ],
        total=Decimal("120.00"),
    )
    _, html = remittance_template.render(
        group, company_name="CRM", reference="REF-1", payment_method="bank_transfer",
        template={}, logo_data_url=None)

    assert "less credits" in html          # the netted line explains itself
    assert "100.00" in html and "70.00" in html
    assert html.count("less credits") == 1  # the ordinary line is unchanged
