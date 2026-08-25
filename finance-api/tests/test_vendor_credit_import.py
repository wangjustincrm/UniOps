"""QBO vendor-credit import — the throwaway migration tool.

The rules under test are the ones that protect money: take `balance` not
`total_amt`, import nothing the caller did not explicitly map, stay idempotent,
and never silently overwrite a row we already imported.
"""
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


_UNSET = object()


async def _qbo_credit(db, *, qbo_id=None, vendor_id="V1", vendor_name="Alamfoods Inc",
                      balance="100.00", total_amt=None, currency="CAD",
                      doc_number=_UNSET, txn_date="2026-05-01", deleted=False):
    # doc_number needs a sentinel distinct from None: None is a REAL value here
    # (QBO leaves DocNumber unset), so it cannot double as "caller omitted it".
    from app.models.qbo import QboVendorCredit
    qid = qbo_id or uuid.uuid4().hex[:18]
    row = QboVendorCredit(
        qbo_id=qid,
        doc_number=f"CN-{qid[:6]}" if doc_number is _UNSET else doc_number,
        txn_date=txn_date,
        currency=currency,
        total_amt=Decimal(total_amt if total_amt is not None else balance),
        balance=Decimal(balance),
        counterparty_id=vendor_id,
        counterparty_name=vendor_name,
        deleted_at=datetime.now(timezone.utc) if deleted else None,
        raw={},
    )
    db.add(row)
    await db.flush()
    return row


async def _partner(db, name="Alamfoods Inc.", is_supplier=True):
    from app.models.mirrors import BusinessPartner
    p = BusinessPartner(id=uuid.uuid4(), code=f"C{uuid.uuid4().hex[:8]}", name=name,
                        contact_email="ap@example.com", is_supplier=is_supplier)
    db.add(p)
    await db.flush()
    return p


async def _full_reload(db, finished_at=None):
    from app.models.qbo import SUCCESS, QboSyncRun
    run = QboSyncRun(
        id=uuid.uuid4(), mode="full", status=SUCCESS,
        started_at=datetime.now(timezone.utc) - timedelta(minutes=5),
        finished_at=finished_at or datetime.now(timezone.utc),
        counters={}, watermarks={},
    )
    db.add(run)
    await db.flush()
    return run


# --------------------------------------------------------------- normalisation

def test_norm_matches_the_measured_cases():
    from app.crud.vendor_credit_import import norm
    # These are the real pairs the dev snapshot required; lower(strip()) alone
    # matches neither.
    assert norm("Alamfoods Inc") == norm("Alamfoods Inc.")
    assert norm("MSC  Industrial Supply ULC") == norm("MSC Industrial Supply ULC")
    assert norm("Independent Can Compan(US)") != norm("Independent Can Company")


def test_norm_keeps_a_single_word_name_intact():
    from app.crud.vendor_credit_import import norm
    # "Co" as the ONLY word is the company's name, not a suffix to strip.
    assert norm("Co") == "co"
    assert norm(None) == ""


# ------------------------------------------------------------------ candidates

@pytest.mark.anyio
async def test_candidates_lists_only_spendable_credits(db_session):
    from app.crud import vendor_credit_import as crud
    await _qbo_credit(db_session, vendor_id="V1", vendor_name="Spendable Co", balance="100.00")
    await _qbo_credit(db_session, vendor_id="V2", vendor_name="Consumed Co",
                      balance="0.00", total_amt="500.00")
    out = await crud.list_candidates(db_session)
    names = {c["qbo_display_name"] for c in out["candidates"]}
    assert "Spendable Co" in names
    assert "Consumed Co" not in names


@pytest.mark.anyio
async def test_candidate_total_sums_balance_not_face_value(db_session):
    from app.crud import vendor_credit_import as crud
    await _qbo_credit(db_session, vendor_id="V9", vendor_name="Partly Used Ltd",
                      balance="40.00", total_amt="900.00")
    await _qbo_credit(db_session, vendor_id="V9", vendor_name="Partly Used Ltd",
                      balance="10.00", total_amt="700.00")
    out = await crud.list_candidates(db_session)
    row = next(c for c in out["candidates"] if c["qbo_vendor_id"] == "V9")
    assert Decimal(row["credit_total"]) == Decimal("50.00")
    assert row["credit_count"] == 2


@pytest.mark.anyio
async def test_candidates_exclude_deleted_rows(db_session):
    from app.crud import vendor_credit_import as crud
    await _qbo_credit(db_session, vendor_id="VD", vendor_name="Deleted Co",
                      balance="77.00", deleted=True)
    out = await crud.list_candidates(db_session)
    assert not any(c["qbo_vendor_id"] == "VD" for c in out["candidates"])


@pytest.mark.anyio
async def test_exact_normalised_match_prefills_but_decides_nothing(db_session):
    from app.crud import vendor_credit_import as crud
    p = await _partner(db_session, name="Alamfoods Inc.")
    await _qbo_credit(db_session, vendor_id="VA", vendor_name="Alamfoods Inc", balance="12.00")
    out = await crud.list_candidates(db_session)
    row = next(c for c in out["candidates"] if c["qbo_vendor_id"] == "VA")
    assert row["suggested_vendor_id"] == str(p.id)
    assert row["suggested_vendor_name"] == "Alamfoods Inc."
    # A suggestion is a pre-fill. Nothing is imported without an explicit
    # mapping, which is what the next assertion proves.
    res = await crud.run_import(db_session, mapping={}, imported_by=uuid.uuid4())
    assert res["imported"] == 0
    assert res["skipped_unmapped"] == 1


@pytest.mark.anyio
async def test_two_partners_sharing_a_name_yield_no_suggestion(db_session):
    from app.crud import vendor_credit_import as crud
    await _partner(db_session, name="Twin Supply Ltd")
    await _partner(db_session, name="Twin Supply Ltd.")
    await _qbo_credit(db_session, vendor_id="VT", vendor_name="Twin Supply Ltd", balance="5.00")
    out = await crud.list_candidates(db_session)
    row = next(c for c in out["candidates"] if c["qbo_vendor_id"] == "VT")
    assert row["suggested_vendor_id"] is None


@pytest.mark.anyio
async def test_two_qbo_vendors_sharing_a_name_yield_no_suggestion(db_session):
    """Ambiguity on the QBO side matters too: pointing both at one partner
    would merge two vendors' credit into a single balance."""
    from app.crud import vendor_credit_import as crud
    await _partner(db_session, name="Shared Name Inc.")
    await _qbo_credit(db_session, vendor_id="VX1", vendor_name="Shared Name Inc", balance="5.00")
    await _qbo_credit(db_session, vendor_id="VX2", vendor_name="Shared Name Inc.", balance="6.00")
    out = await crud.list_candidates(db_session)
    for qid in ("VX1", "VX2"):
        row = next(c for c in out["candidates"] if c["qbo_vendor_id"] == qid)
        assert row["suggested_vendor_id"] is None


@pytest.mark.anyio
async def test_currencies_are_reported_as_a_distinct_set(db_session):
    from app.crud import vendor_credit_import as crud
    await _qbo_credit(db_session, vendor_id="VC", vendor_name="Multi Ccy Co",
                      balance="10.00", currency="USD")
    await _qbo_credit(db_session, vendor_id="VC", vendor_name="Multi Ccy Co",
                      balance="20.00", currency="CAD")
    await _qbo_credit(db_session, vendor_id="VC", vendor_name="Multi Ccy Co",
                      balance="30.00", currency="CAD")
    out = await crud.list_candidates(db_session)
    row = next(c for c in out["candidates"] if c["qbo_vendor_id"] == "VC")
    assert row["currencies"] == ["CAD", "USD"]


# ----------------------------------------------------------------- run_import

@pytest.mark.anyio
async def test_dry_run_creates_nothing_but_reports_what_it_would(db_session):
    from app.crud import vendor_credit_import as crud
    from app.models.vendor_credit import VendorCredit
    p = await _partner(db_session)
    await _qbo_credit(db_session, vendor_id="V1", balance="123.45", doc_number="CN-1")

    before = (await db_session.execute(
        sa.select(sa.func.count()).select_from(VendorCredit))).scalar()
    res = await crud.run_import(db_session, mapping={"V1": p.id},
                                imported_by=uuid.uuid4(), dry_run=True)
    after = (await db_session.execute(
        sa.select(sa.func.count()).select_from(VendorCredit))).scalar()

    assert res["dry_run"] is True
    assert res["imported"] == 1
    assert res["rows"][0]["vendor_credit_number"] == "CN-1"
    assert Decimal(res["total_amount"]) == Decimal("123.45")
    assert after == before


@pytest.mark.anyio
async def test_import_takes_balance_not_total_amt(db_session):
    """The rule that protects money: total_amt is the original face value, and
    importing it would hand the vendor credit they already spent."""
    from app.crud import vendor_credit_import as crud
    from app.models.vendor_credit import VendorCredit
    p = await _partner(db_session)
    await _qbo_credit(db_session, vendor_id="V1", balance="40.00", total_amt="900.00")

    await crud.run_import(db_session, mapping={"V1": p.id},
                          imported_by=uuid.uuid4(), dry_run=False)
    vc = (await db_session.execute(
        sa.select(VendorCredit).where(VendorCredit.vendor_id == p.id))).scalars().one()
    assert vc.total_amount == Decimal("40.00")
    assert vc.remaining_amount == Decimal("40.00")
    assert vc.applied_amount == Decimal("0.00")
    assert vc.status == "available"          # skips review — reconciled in QBO
    assert vc.source == "qbo_import"
    assert vc.opening_balance is True
    # The face value is not lost; it is recorded for reconciliation.
    assert "900.00" in (vc.notes or "")


@pytest.mark.anyio
async def test_unmapped_vendor_is_never_imported(db_session):
    from app.crud import vendor_credit_import as crud
    p = await _partner(db_session)
    await _qbo_credit(db_session, vendor_id="V1", balance="10.00")
    await _qbo_credit(db_session, vendor_id="V2", vendor_name="Not Mapped Co", balance="99.00")

    res = await crud.run_import(db_session, mapping={"V1": p.id},
                                imported_by=uuid.uuid4(), dry_run=False)
    assert res["imported"] == 1
    assert res["skipped_unmapped"] == 1
    assert Decimal(res["total_amount"]) == Decimal("10.00")


@pytest.mark.anyio
async def test_second_run_is_idempotent(db_session):
    from app.crud import vendor_credit_import as crud
    from app.models.vendor_credit import VendorCredit
    p = await _partner(db_session)
    await _qbo_credit(db_session, vendor_id="V1", balance="15.00")

    first = await crud.run_import(db_session, mapping={"V1": p.id},
                                  imported_by=uuid.uuid4(), dry_run=False)
    second = await crud.run_import(db_session, mapping={"V1": p.id},
                                   imported_by=uuid.uuid4(), dry_run=False)
    assert first["imported"] == 1
    assert second["imported"] == 0
    assert second["skipped_existing"] == 1
    count = (await db_session.execute(
        sa.select(sa.func.count()).select_from(VendorCredit)
        .where(VendorCredit.vendor_id == p.id))).scalar()
    assert count == 1


@pytest.mark.anyio
async def test_blank_doc_numbers_do_not_collide(db_session):
    """QBO permits an empty DocNumber, and the unique index on
    (vendor_id, vendor_credit_number) is not scoped by source — so two blank
    ones for the same vendor would collide with each other."""
    from app.crud import vendor_credit_import as crud
    from app.models.vendor_credit import VendorCredit
    p = await _partner(db_session)
    a = await _qbo_credit(db_session, vendor_id="V1", balance="1.00", doc_number="")
    b = await _qbo_credit(db_session, vendor_id="V1", balance="2.00", doc_number=None)

    res = await crud.run_import(db_session, mapping={"V1": p.id},
                                imported_by=uuid.uuid4(), dry_run=False)
    assert res["imported"] == 2
    numbers = set((await db_session.execute(
        sa.select(VendorCredit.vendor_credit_number)
        .where(VendorCredit.vendor_id == p.id))).scalars().all())
    assert numbers == {f"QBO-{a.qbo_id}", f"QBO-{b.qbo_id}"}


@pytest.mark.anyio
async def test_manual_upload_of_the_same_document_is_not_doubled(db_session):
    """A credit note already keyed in by hand must not be imported again —
    that would double the vendor's available credit."""
    from app.crud import vendor_credit as vc_crud
    from app.crud import vendor_credit_import as crud
    from app.schemas.vendor_credit import VendorCreditCreate
    p = await _partner(db_session)
    existing = await vc_crud.create(
        db_session,
        payload=VendorCreditCreate(vendor_id=p.id, vendor_name=p.name,
                                   vendor_credit_number="CN-DUP",
                                   credit_date=date(2026, 5, 1), currency="CAD",
                                   amount=Decimal("50.00")),
        uploaded_by=uuid.uuid4(), uploaded_by_name="AP")
    await _qbo_credit(db_session, vendor_id="V1", balance="50.00", doc_number="CN-DUP")

    res = await crud.run_import(db_session, mapping={"V1": p.id},
                                imported_by=uuid.uuid4(), dry_run=False)
    assert res["imported"] == 0
    assert res["skipped_duplicate"] == 1
    assert res["duplicates"][0]["existing_credit_number"] == existing.credit_number


@pytest.mark.anyio
async def test_imported_row_is_stamped_with_the_full_reload(db_session):
    from app.crud import vendor_credit_import as crud
    from app.models.vendor_credit import VendorCredit
    p = await _partner(db_session)
    old = await _full_reload(db_session,
                             finished_at=datetime.now(timezone.utc) - timedelta(days=2))
    latest = await _full_reload(db_session)
    await _qbo_credit(db_session, vendor_id="V1", balance="8.00")

    await crud.run_import(db_session, mapping={"V1": p.id},
                          imported_by=uuid.uuid4(), dry_run=False)
    vc = (await db_session.execute(
        sa.select(VendorCredit).where(VendorCredit.vendor_id == p.id))).scalars().one()
    assert vc.imported_from_sync_run_id == latest.id
    assert vc.imported_from_sync_run_id != old.id


# ----------------------------------------------------------------------- drift

@pytest.mark.anyio
async def test_no_drift_when_balances_agree(db_session):
    from app.crud import vendor_credit_import as crud
    p = await _partner(db_session)
    await _qbo_credit(db_session, vendor_id="V1", balance="30.00")
    await crud.run_import(db_session, mapping={"V1": p.id},
                          imported_by=uuid.uuid4(), dry_run=False)
    out = await crud.list_candidates(db_session)
    assert out["drift"] == []


@pytest.mark.anyio
async def test_qbo_side_change_is_reported_and_our_row_untouched(db_session):
    from app.crud import vendor_credit_import as crud
    from app.models.qbo import QboVendorCredit
    from app.models.vendor_credit import VendorCredit
    p = await _partner(db_session)
    q = await _qbo_credit(db_session, vendor_id="V1", balance="30.00")
    await crud.run_import(db_session, mapping={"V1": p.id},
                          imported_by=uuid.uuid4(), dry_run=False)

    # Somebody applied part of it inside QuickBooks after we imported.
    await db_session.execute(
        sa.update(QboVendorCredit).where(QboVendorCredit.qbo_id == q.qbo_id)
        .values(balance=Decimal("12.00")))
    await db_session.flush()

    out = await crud.list_candidates(db_session)
    assert len(out["drift"]) == 1
    d = out["drift"][0]
    assert d["source_ref"] == q.qbo_id
    assert Decimal(d["imported_total"]) == Decimal("30.00")
    assert Decimal(d["qbo_balance"]) == Decimal("12.00")

    vc = (await db_session.execute(
        sa.select(VendorCredit).where(VendorCredit.source_ref == q.qbo_id))).scalars().one()
    assert vc.total_amount == Decimal("30.00")
    assert vc.remaining_amount == Decimal("30.00")


@pytest.mark.anyio
async def test_a_credit_we_consumed_ourselves_is_not_drift(db_session):
    """Our own applications move applied/remaining and leave total alone. Drift
    compares against total_amount precisely so spending a credit through EPMS
    does not look like someone editing it in QuickBooks."""
    from app.crud import vendor_credit_import as crud
    from app.models.vendor_credit import VendorCredit
    p = await _partner(db_session)
    q = await _qbo_credit(db_session, vendor_id="V1", balance="30.00")
    await crud.run_import(db_session, mapping={"V1": p.id},
                          imported_by=uuid.uuid4(), dry_run=False)

    await db_session.execute(
        sa.update(VendorCredit).where(VendorCredit.source_ref == q.qbo_id)
        .values(applied_amount=Decimal("30.00"), remaining_amount=Decimal("0.00"),
                status="exhausted"))
    await db_session.flush()

    out = await crud.list_candidates(db_session)
    assert out["drift"] == []


# ------------------------------------------------------------------- API gates

@pytest.mark.anyio
async def test_candidates_requires_the_manage_permission(client):
    r = await client.get("/finance/v1/qbo-credit-import/candidates", headers=_h("ap_clerk"))
    assert r.status_code == 403


@pytest.mark.anyio
async def test_run_requires_the_manage_permission(client):
    r = await client.post("/finance/v1/qbo-credit-import/run",
                          json={"mapping": {}, "dry_run": True}, headers=_h("ap_clerk"))
    assert r.status_code == 403


@pytest.mark.anyio
async def test_routes_answer_for_a_permitted_caller(client, db_session):
    p = await _partner(db_session)
    await _qbo_credit(db_session, vendor_id="V1", balance="21.00")
    await db_session.flush()

    r = await client.get("/finance/v1/qbo-credit-import/candidates",
                         headers=_h("system_admin"))
    assert r.status_code == 200, r.text
    body = r.json()
    assert any(c["qbo_vendor_id"] == "V1" for c in body["candidates"])
    assert "cutover" in body

    r = await client.post("/finance/v1/qbo-credit-import/run",
                          json={"mapping": {"V1": str(p.id)}, "dry_run": True},
                          headers=_h("system_admin"))
    assert r.status_code == 200, r.text
    assert r.json()["imported"] == 1
    assert r.json()["dry_run"] is True


@pytest.mark.anyio
async def test_run_defaults_to_a_dry_run(client):
    """A caller that forgets the flag must not write."""
    r = await client.post("/finance/v1/qbo-credit-import/run", json={},
                          headers=_h("system_admin"))
    assert r.status_code == 200, r.text
    assert r.json()["dry_run"] is True


# ------------------------------------------------------------- vendor picker

@pytest.mark.anyio
async def test_vendor_options_list_suppliers_only(db_session):
    from app.crud import vendor_credit_import as crud
    sup = await _partner(db_session, name="Zed Supplies Ltd")
    await _partner(db_session, name="Zed Customer Ltd", is_supplier=False)
    items = await crud.list_vendors(db_session)
    ids = {i["id"] for i in items}
    assert str(sup.id) in ids
    assert all(i["name"] != "Zed Customer Ltd" for i in items)


@pytest.mark.anyio
async def test_vendor_options_search_matches_name_or_code(db_session):
    from app.crud import vendor_credit_import as crud
    p = await _partner(db_session, name="Findable Widgets Inc.")
    by_name = await crud.list_vendors(db_session, "Findable")
    by_code = await crud.list_vendors(db_session, p.code)
    assert str(p.id) in {i["id"] for i in by_name}
    assert str(p.id) in {i["id"] for i in by_code}


@pytest.mark.anyio
async def test_vendor_options_require_the_manage_permission(client):
    r = await client.get("/finance/v1/qbo-credit-import/vendors", headers=_h("ap_clerk"))
    assert r.status_code == 403
