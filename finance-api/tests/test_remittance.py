"""Remittance advice — notification log, grouping, sending."""
import uuid
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from unittest.mock import AsyncMock, patch

import pytest
import pytest_asyncio
import sqlalchemy as sa
from httpx import ASGITransport, AsyncClient
from jose import jwt
from sqlalchemy import select

from app.core.config import settings
from app.crud import remittance as rem
from app.db.base import get_db
from app.main import app
from app.models.mirrors import BusinessPartner, CompanyConfig, ExpenseClaim, Invoice, User
from app.models.pa import PaymentApplication
from app.models.payment import PaymentRecord
from app.models.remittance import (
    KIND_VENDOR, SCOPE_BATCH, SENT, RemittanceNotification,
)
from app.services import remittance_config as rc


def _token(role="finance_manager"):
    return jwt.encode({"sub": str(uuid.uuid4()), "role": role,
                       "exp": datetime.now(timezone.utc) + timedelta(hours=1)},
                      settings.jwt_secret_key, algorithm=settings.jwt_algorithm)


def _h(role="finance_manager"):
    return {"Authorization": f"Bearer {_token(role)}"}


@pytest_asyncio.fixture
async def client(db_session):
    async def _override_get_db():
        yield db_session
    app.dependency_overrides[get_db] = _override_get_db
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        yield c
    app.dependency_overrides.clear()


async def test_notification_row_round_trips(db_session):
    rec_id = uuid.uuid4()
    row = RemittanceNotification(
        scope_kind=SCOPE_BATCH, scope_id=uuid.uuid4(),
        recipient_kind=KIND_VENDOR, party_id=uuid.uuid4(), party_name="ACME",
        email="ap@acme.test", payment_record_ids=[str(rec_id)],
        amount=Decimal("100.00"), currency="CAD", status=SENT,
        attempts=1, sent_at=datetime.now(timezone.utc), created_by=uuid.uuid4(),
    )
    db_session.add(row)
    await db_session.flush()

    got = (await db_session.execute(
        select(RemittanceNotification).where(RemittanceNotification.id == row.id)
    )).scalar_one()
    assert got.payment_record_ids == [str(rec_id)]
    assert got.amount == Decimal("100.00")
    assert got.attempts == 1


async def test_partner_mirror_reads_remittance_email(db_session):
    bp = BusinessPartner(
        code=f"V-{uuid.uuid4().hex[:6]}", name="ACME", contact_email="ap@acme.test",
        remittance_email="remit@acme.test", is_supplier=True,
    )
    db_session.add(bp)
    await db_session.flush()

    got = (await db_session.execute(
        select(BusinessPartner).where(BusinessPartner.id == bp.id)
    )).scalar_one()
    assert got.remittance_email == "remit@acme.test"
    assert got.contact_email == "ap@acme.test"


async def test_send_email_uses_starttls_on_587():
    from app.services.email import send_email
    with patch("app.services.email.aiosmtplib.send", new=AsyncMock()) as m:
        await send_email("a@b.test", "s", "<p>x</p>", smtp_host="h", smtp_port=587,
                         smtp_user="u", smtp_password="p", smtp_use_tls=True,
                         smtp_from="from@b.test")
    assert m.await_args.kwargs["start_tls"] is True
    assert m.await_args.kwargs["use_tls"] is False


async def test_send_email_uses_implicit_tls_on_465():
    from app.services.email import send_email
    with patch("app.services.email.aiosmtplib.send", new=AsyncMock()) as m:
        await send_email("a@b.test", "s", "<p>x</p>", smtp_host="h", smtp_port=465,
                         smtp_user="u", smtp_password="p", smtp_use_tls=True,
                         smtp_from="from@b.test")
    assert m.await_args.kwargs["use_tls"] is True
    assert m.await_args.kwargs["start_tls"] is False


async def test_settings_none_when_switch_absent(db_session):
    db_session.add(CompanyConfig(role_management={}, remittance_config={}))
    await db_session.flush()
    assert await rc.load(db_session) is None


async def test_settings_prefer_po_smtp_and_own_from(db_session):
    cfg = CompanyConfig(role_management={}, remittance_config={
        "enabled": True, "from_email": "ap@crm.test", "from_name": "CRM AP",
        "cc_email": "apbox@crm.test",
    })
    db_session.add(cfg)
    await db_session.flush()
    await db_session.execute(sa.text(
        "UPDATE company_config SET po_smtp_host='po.host', po_smtp_port=587,"
        " po_smtp_user='po_user', po_smtp_password='pw', po_smtp_use_tls=true,"
        " smtp_host='int.host', smtp_port=25 WHERE id = :i"), {"i": str(cfg.id)})

    s = await rc.load(db_session)
    assert s is not None
    assert s.smtp_host == "po.host"          # outbound sender wins
    assert s.from_email == "ap@crm.test"     # never the shared smtp_from
    assert s.cc_email == "apbox@crm.test"
    assert s.smtp_user == "po_user"          # no override configured


async def test_settings_per_field_fallback_from_partial_po_smtp(db_session):
    """A po_smtp_* override that only sets host/user (leaving port and TLS
    NULL to inherit) must take port/TLS from the internal smtp_* profile
    per-field, not fall back to the internal profile wholesale nor to
    hardcoded defaults. This fails against the old all-or-nothing block
    switch, which would force port=587 and smtp_use_tls=False here."""
    cfg = CompanyConfig(role_management={}, remittance_config={
        "enabled": True, "from_email": "ap@crm.test",
    })
    db_session.add(cfg)
    await db_session.flush()
    await db_session.execute(sa.text(
        "UPDATE company_config SET po_smtp_host='po.host', po_smtp_user='po_user',"
        " po_smtp_port=NULL, po_smtp_use_tls=NULL,"
        " smtp_host='int.host', smtp_port=2525, smtp_use_tls=true"
        " WHERE id = :i"), {"i": str(cfg.id)})

    s = await rc.load(db_session)
    assert s is not None
    assert s.smtp_host == "po.host"      # from po profile (set)
    assert s.smtp_user == "po_user"      # from po profile (set)
    assert s.smtp_port == 2525           # inherited from internal profile, not 587
    assert s.smtp_use_tls is True        # inherited from internal profile, not False


async def test_settings_explicit_po_use_tls_false_is_honoured(db_session):
    """po_smtp_use_tls explicitly False is a real setting ('this relay does
    not use TLS') and must not fall through to the internal profile's True."""
    cfg = CompanyConfig(role_management={}, remittance_config={
        "enabled": True, "from_email": "ap@crm.test",
    })
    db_session.add(cfg)
    await db_session.flush()
    await db_session.execute(sa.text(
        "UPDATE company_config SET po_smtp_host='po.host', po_smtp_use_tls=false,"
        " smtp_use_tls=true WHERE id = :i"), {"i": str(cfg.id)})

    s = await rc.load(db_session)
    assert s is not None
    assert s.smtp_use_tls is False


async def test_settings_none_when_from_email_blank(db_session):
    cfg = CompanyConfig(role_management={}, remittance_config={
        "enabled": True, "from_email": "   ",
    })
    db_session.add(cfg)
    await db_session.flush()
    await db_session.execute(sa.text(
        "UPDATE company_config SET po_smtp_host='po.host' WHERE id = :i"), {"i": str(cfg.id)})

    assert await rc.load(db_session) is None


async def test_settings_none_when_no_smtp_host_anywhere(db_session):
    cfg = CompanyConfig(role_management={}, remittance_config={
        "enabled": True, "from_email": "ap@crm.test",
    })
    db_session.add(cfg)
    await db_session.flush()
    await db_session.execute(sa.text(
        "UPDATE company_config SET po_smtp_host=NULL, smtp_host=NULL WHERE id = :i"), {"i": str(cfg.id)})

    assert await rc.load(db_session) is None


# ── Task 6: payee grouping and block reasons ────────────────────────────────────

def _pa(vendor_id, amount="100.00", invoice_ids=None, po_id=None):
    return PaymentApplication(
        pa_number=f"PA-{uuid.uuid4().hex[:8]}", title="Widgets", pa_type="regular",
        status="approved", po_id=po_id, po_number="PO-1" if po_id else None,
        vendor_id=vendor_id, vendor_name="ACME", invoice_ids=invoice_ids or [],
        payment_amount=Decimal(amount), currency="CAD", created_by=uuid.uuid4(),
    )


def _record(pa, batch_id=None, status="completed"):
    return PaymentRecord(
        doc_kind="pa" if pa.po_id else "pa_dir", doc_id=pa.id, doc_number=pa.pa_number,
        pa_id=pa.id, pa_number=pa.pa_number, vendor_id=pa.vendor_id,
        vendor_name=pa.vendor_name, payment_date=date(2026, 7, 22),
        payment_method="bank_transfer", amount=pa.payment_amount, currency="CAD",
        recorded_by=uuid.uuid4(), status=status, batch_id=batch_id,
    )


async def _vendor(db, email="ap@acme.test", remit=None):
    bp = BusinessPartner(code=f"V-{uuid.uuid4().hex[:6]}", name="ACME",
                         contact_email=email, remittance_email=remit, is_supplier=True)
    db.add(bp)
    await db.flush()
    return bp


async def _invoice(db, number="VINV-1"):
    # NOTE: the brief's helper (`Invoice(vendor_invoice_number=number)`) omits
    # every other NOT NULL column on this mirror (internal_ref, vendor_id,
    # vendor_name, amount, tax_amount, total_amount, currency, invoice_date,
    # due_date, status) and would fail at flush. Filled in following the
    # pattern already used by test_payment_batch.py's inline Invoice() calls.
    inv = Invoice(
        internal_ref=f"INV-{uuid.uuid4().hex[:8]}", vendor_invoice_number=number,
        vendor_id=uuid.uuid4(), vendor_name="ACME", amount=Decimal("100"),
        tax_amount=Decimal("0"), total_amount=Decimal("100"), currency="CAD",
        invoice_date=date(2026, 6, 1), due_date=date(2026, 7, 1), status="matched",
    )
    db.add(inv)
    await db.flush()
    return inv


async def test_two_pas_for_one_vendor_collapse_into_one_group(db_session):
    bp = await _vendor(db_session, remit="remit@acme.test")
    i1, i2 = await _invoice(db_session, "VINV-1"), await _invoice(db_session, "VINV-2")
    pas = [_pa(bp.id, "100.00", [str(i1.id)]), _pa(bp.id, "50.00", [str(i2.id)])]
    db_session.add_all(pas)
    await db_session.flush()
    recs = [_record(p) for p in pas]
    db_session.add_all(recs)
    await db_session.flush()

    groups = await rem.build_groups(db_session, recs)
    assert len(groups) == 1
    g = groups[0]
    assert g.email == "remit@acme.test"
    assert g.total == Decimal("150.00")
    assert sorted(l.vendor_inv_no for l in g.lines) == ["VINV-1", "VINV-2"]
    assert g.block_reasons == []


async def test_missing_email_blocks_group(db_session):
    bp = await _vendor(db_session, email="", remit=None)
    inv = await _invoice(db_session, "VINV-3")
    pa = _pa(bp.id, "10.00", [str(inv.id)])
    db_session.add(pa)
    await db_session.flush()
    rec = _record(pa)
    db_session.add(rec)
    await db_session.flush()

    g = (await rem.build_groups(db_session, [rec]))[0]
    assert rem.BLOCK_MISSING_EMAIL in g.block_reasons


async def test_direct_pa_without_invoice_no_blocks_group(db_session):
    bp = await _vendor(db_session, remit="remit@acme.test")
    pa = _pa(bp.id, "10.00", invoice_ids=[])          # Direct PA, no invoice
    db_session.add(pa)
    await db_session.flush()
    rec = _record(pa)
    db_session.add(rec)
    await db_session.flush()

    g = (await rem.build_groups(db_session, [rec]))[0]
    assert rem.BLOCK_MISSING_INVOICE_NO in g.block_reasons


async def test_employee_group_never_blocked_for_invoice_no(db_session):
    emp_id = uuid.uuid4()
    # NOTE: the brief's User(...) call omits full_name, which is NOT NULL on
    # this mirror with no default — added here to avoid a flush failure.
    db_session.add(User(id=emp_id, email="jane@crm.test", full_name="Jane Doe"))
    claim = ExpenseClaim(claim_number="EXP-1", claim_type="EXP", status="approved",
                         employee_id=emp_id, employee_name="Jane Doe", currency="CAD",
                         total_amount=Decimal("20.00"), tax_amount=Decimal("0"),
                         net_amount=Decimal("20.00"))
    db_session.add(claim)
    await db_session.flush()
    rec = PaymentRecord(
        doc_kind="expense_claim", doc_id=claim.id, doc_number=claim.claim_number,
        payment_date=date(2026, 7, 22), payment_method="bank_transfer",
        amount=Decimal("20.00"), currency="CAD", recorded_by=uuid.uuid4(),
        status="completed",
    )
    db_session.add(rec)
    await db_session.flush()

    g = (await rem.build_groups(db_session, [rec]))[0]
    assert g.recipient_kind == "employee"
    assert g.email == "jane@crm.test"
    assert g.block_reasons == []


async def test_scope_batch_and_scope_payment_agree_for_one_record(db_session):
    bp = await _vendor(db_session, remit="remit@acme.test")
    inv = await _invoice(db_session, "VINV-9")
    pa = _pa(bp.id, "75.00", [str(inv.id)])
    db_session.add(pa)
    await db_session.flush()
    batch_id = uuid.uuid4()
    rec = _record(pa, batch_id=batch_id)
    db_session.add(rec)
    await db_session.flush()

    by_batch = await rem.build_groups(db_session, await rem.resolve_scope(db_session, "batch", batch_id))
    by_payment = await rem.build_groups(db_session, await rem.resolve_scope(db_session, "payment", rec.id))
    assert [g.total for g in by_batch] == [g.total for g in by_payment] == [Decimal("75.00")]


async def test_failed_record_is_excluded(db_session):
    bp = await _vendor(db_session, remit="remit@acme.test")
    inv = await _invoice(db_session, "VINV-10")
    pa = _pa(bp.id, "5.00", [str(inv.id)])
    db_session.add(pa)
    await db_session.flush()
    batch_id = uuid.uuid4()
    db_session.add(_record(pa, batch_id=batch_id, status="cancelled"))
    await db_session.flush()

    assert await rem.resolve_scope(db_session, "batch", batch_id) == []
