"""Vendor Credit — Phase A (record + review, no money movement)."""
import uuid
from datetime import date, datetime, timezone
from decimal import Decimal

import pytest
import pytest_asyncio
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


@pytest.mark.anyio
async def test_create_converts_racing_duplicate_to_duplicate_credit(db_session, monkeypatch):
    """_find_duplicate is check-then-act: a concurrent create() can commit its
    row between our SELECT and our flush. True concurrency is awkward to
    force in a single session, so this simulates the miss directly: the fast
    -path SELECT is stubbed to report "no duplicate" exactly once (as it
    would if the race window closed the wrong way), while the real duplicate
    row already exists and is committed. create() must still convert the
    resulting IntegrityError from uq_vendor_credits_vendor_docno into
    DuplicateCredit — never let it leak — because callers only handle
    DuplicateCredit and would otherwise surface a 500 instead of a 409.
    """
    from app.crud import vendor_credit as crud

    vid = uuid.uuid4()
    first = await crud.create(db_session, payload=_create_payload(vendor_id=vid),
                              uploaded_by=uuid.uuid4(), uploaded_by_name="AP")
    await db_session.commit()

    real_find_duplicate = crud._find_duplicate
    calls = {"n": 0}

    async def _find_duplicate_misses_once(db, vendor_id, doc_number):
        calls["n"] += 1
        if calls["n"] == 1:
            return None
        return await real_find_duplicate(db, vendor_id, doc_number)

    monkeypatch.setattr(crud, "_find_duplicate", _find_duplicate_misses_once)

    with pytest.raises(crud.DuplicateCredit) as exc:
        await crud.create(db_session, payload=_create_payload(vendor_id=vid),
                          uploaded_by=uuid.uuid4(), uploaded_by_name="AP")
    assert exc.value.existing.id == first.id
    assert calls["n"] == 2  # fast-path miss, then the post-IntegrityError re-query


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
