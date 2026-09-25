"""Remittance advice — notification log, grouping, sending."""
import uuid
from email.utils import getaddresses
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
    KIND_VENDOR, SCOPE_BATCH, SCOPE_PAYMENT, SCOPE_SELECTION, SENT, RemittanceNotification,
)
from app.models.payment_batch import EXECUTED, PaymentBatch
from app.services import remittance_config as rc
from app.services import remittance_template as tpl


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


async def test_send_email_non_ascii_subject_serializes_cleanly():
    """The rendered remittance subject contains an em dash. `send_email`
    builds a MIMEMultipart under Python's default compat32 policy with no
    explicit header encoding — a mock never flattens the message, so this
    proves the real thing by calling as_bytes() on what is actually handed
    to aiosmtplib.send, then re-parsing those bytes and decoding the header
    back, the way a real MTA/mailbox would."""
    import email
    from email.header import decode_header, make_header

    from app.services.email import send_email

    subject = "Remittance Advice — Canada Royal Milk — BP-20260722-0001"
    with patch("app.services.email.aiosmtplib.send", new=AsyncMock()) as m:
        await send_email("a@b.test", subject, "<p>x</p>", smtp_host="h", smtp_port=587,
                         smtp_user="u", smtp_password="p", smtp_use_tls=True,
                         smtp_from="from@b.test")

    msg = m.await_args.args[0]
    raw = msg.as_bytes()          # must not raise (UnicodeEncodeError etc.)
    assert b"\xe2\x80\x94" not in raw   # the em dash must be header-encoded, not raw UTF-8 bytes

    reparsed = email.message_from_bytes(raw)
    decoded = str(make_header(decode_header(reparsed["Subject"])))
    assert decoded == subject


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


def test_safe_logo_url_accepts_images_and_rejects_injection():
    from app.services.remittance_config import _safe_logo_url
    # accepted
    assert _safe_logo_url("data:image/png;base64,AAAA") == "data:image/png;base64,AAAA"
    assert _safe_logo_url("https://cdn.example.com/logo.png") == "https://cdn.example.com/logo.png"
    # rejected -> None
    assert _safe_logo_url(None) is None
    assert _safe_logo_url("") is None
    assert _safe_logo_url('data:image/png;base64,AA" onload=alert(1)') is None   # attribute-injection via a quote
    assert _safe_logo_url("javascript:alert(1)") is None
    assert _safe_logo_url("data:text/html,<script>") is None


# ── Task 6: payee grouping and block reasons ────────────────────────────────────

def _pa(vendor_id, amount="100.00", invoice_ids=None, po_id=None):
    return PaymentApplication(
        pa_number=f"PA-{uuid.uuid4().hex[:8]}", title="Widgets", pa_type="regular",
        status="approved", po_id=po_id, po_number="PO-1" if po_id else None,
        vendor_id=vendor_id, vendor_name="ACME", invoice_ids=invoice_ids or [],
        payment_amount=Decimal(amount), currency="CAD", created_by=uuid.uuid4(),
    )


def _record(pa, batch_id=None, status="completed", doc_number=None):
    return PaymentRecord(
        doc_kind="pa" if pa.po_id else "pa_dir", doc_id=pa.id,
        doc_number=doc_number if doc_number is not None else pa.pa_number,
        pa_id=pa.id, pa_number=pa.pa_number, vendor_id=pa.vendor_id,
        vendor_name=pa.vendor_name, payment_date=date(2026, 7, 22),
        payment_method="bank_transfer", amount=pa.payment_amount, currency="CAD",
        recorded_by=uuid.uuid4(), status=status, batch_id=batch_id,
        # Explicit, matching what app/crud/payment_execute.py's execute()
        # always sets on a real PaymentRecord (a sum() with a Decimal("0.00")
        # start, never left to the column's own Python-side default of a
        # bare Decimal("0")). This helper bypasses execute(), so without this
        # a record built here carries a client-side-only Decimal("0") that
        # was never round-tripped through the numeric(15,2) column — it
        # still serializes as "0", not "0.00", even after flush, because
        # nothing re-reads it from Postgres. Real production rows never
        # exhibit that mismatch; only this shortcut construction can.
        credit_applied=Decimal("0.00"),
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


async def _expense_invoice(db, pa_id, number="EXP-INV-1"):
    from app.models.mirrors import ExpenseInvoice
    ei = ExpenseInvoice(pa_id=pa_id, invoice_number=number)
    db.add(ei)
    await db.flush()
    return ei


async def test_oa_direct_pa_resolves_invoice_no_via_expense_invoices_fallback(db_session):
    """Fix 4: an OA-created Direct PA's invoice_ids holds an
    expense_invoices.id (see expense-api/app/api/v1/pa.py setting
    invoice_ids=[str(body.invoice_id)]) — a different table in a different
    id space than epms's `invoices`. The epms Invoice lookup never matches
    it, so without the expense_invoices fallback this group is blocked
    missing_invoice_no forever, with no screen anywhere able to fix it."""
    bp = await _vendor(db_session, remit="remit@acme.test")
    pa = _pa(bp.id, "10.00", invoice_ids=[])
    db_session.add(pa)
    await db_session.flush()
    ei = await _expense_invoice(db_session, pa.id, "EXP-INV-1")
    pa.invoice_ids = [str(ei.id)]
    await db_session.flush()
    rec = _record(pa)
    db_session.add(rec)
    await db_session.flush()

    g = (await rem.build_groups(db_session, [rec]))[0]
    assert rem.BLOCK_MISSING_INVOICE_NO not in g.block_reasons
    assert g.lines[0].vendor_inv_no == "EXP-INV-1"


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

    # Decoy: a different vendor, a different amount, a different batch. If
    # resolve_scope ignored scope_kind/scope_id and just returned every
    # completed record, this decoy would leak into both scopes and this test
    # would still pass without it — it exists to make that failure visible.
    decoy_bp = await _vendor(db_session, email="decoy@other.test", remit="remit@other.test")
    decoy_inv = await _invoice(db_session, "VINV-DECOY")
    decoy_pa = _pa(decoy_bp.id, "999.00", [str(decoy_inv.id)])
    db_session.add(decoy_pa)
    await db_session.flush()
    decoy_batch_id = uuid.uuid4()
    db_session.add(_record(decoy_pa, batch_id=decoy_batch_id))
    await db_session.flush()

    by_batch = await rem.build_groups(db_session, await rem.resolve_scope(db_session, "batch", batch_id))
    by_payment = await rem.build_groups(db_session, await rem.resolve_scope(db_session, "payment", rec.id))
    assert len(by_batch) == 1
    assert len(by_payment) == 1
    assert [g.total for g in by_batch] == [g.total for g in by_payment] == [Decimal("75.00")]


async def test_resolve_scope_rejects_unknown_kind(db_session):
    with pytest.raises(ValueError):
        await rem.resolve_scope(db_session, "bogus", uuid.uuid4())


async def test_pa_and_pa_dir_for_one_vendor_collapse_into_one_group(db_session):
    """A vendor appearing via a PO-based PA and a Direct PA in the same scope
    must still collapse into a single payee group (grouping is by vendor_id,
    not by doc_kind)."""
    bp = await _vendor(db_session, remit="remit@acme.test")
    i1, i2 = await _invoice(db_session, "VINV-20"), await _invoice(db_session, "VINV-21")
    po_pa = _pa(bp.id, "100.00", [str(i1.id)], po_id=uuid.uuid4())
    dir_pa = _pa(bp.id, "50.00", [str(i2.id)])
    db_session.add_all([po_pa, dir_pa])
    await db_session.flush()
    recs = [_record(po_pa), _record(dir_pa)]
    db_session.add_all(recs)
    await db_session.flush()

    assert recs[0].doc_kind == "pa"
    assert recs[1].doc_kind == "pa_dir"

    groups = await rem.build_groups(db_session, recs)
    assert len(groups) == 1
    g = groups[0]
    assert g.total == Decimal("150.00")
    assert sorted(l.vendor_inv_no for l in g.lines) == ["VINV-20", "VINV-21"]


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


# ── Task 7: email templates ─────────────────────────────────────────────────

def _group(kind="vendor", inv="VINV-1", doc="PA-0001"):
    return rem.PayeeGroup(
        recipient_kind=kind, party_id=uuid.uuid4(), party_name="ACME",
        email="ap@acme.test", currency="CAD",
        lines=[rem.GroupLine(vendor_inv_no=inv, doc_number=doc,
                             payment_date=date(2026, 7, 22), amount=Decimal("100.00"))],
        total=Decimal("100.00"),
    )


def test_vendor_template_shows_invoice_no_and_hides_pa_no():
    subject, html = tpl.render(_group(), company_name="Canada Royal Milk",
                               reference="BP-20260722-0001", payment_method="bank_transfer")
    assert "VINV-1" in html
    assert "PA-0001" not in html          # internal document number, not the vendor's concern
    assert "100.00" in html
    assert "2026-07-22" in html
    assert "Remittance Advice" in subject


def test_employee_template_shows_claim_no():
    _, html = tpl.render(_group(kind="employee", inv="", doc="EXP-0007"),
                         company_name="Canada Royal Milk",
                         reference="BP-20260722-0001", payment_method="bank_transfer")
    assert "EXP-0007" in html
    assert "Invoice" not in html


def test_template_escapes_payee_name():
    g = _group()
    g.party_name = "<script>x</script>"
    _, html = tpl.render(g, company_name="C", reference="R", payment_method="bank_transfer")
    assert "<script>" not in html


# ── Task 1: template substitution, brand colour, logo ───────────────────────

def test_template_substitutes_placeholders_in_all_text_fields():
    g = _group()  # vendor group, party_name "ACME", one line VINV-1 / 100.00 CAD
    template = {
        "subject": "Payment to {{payee_name}} — {{reference}}",
        "heading": "{{company_name}} Remittance",
        "greeting": "Hello {{payee_name}},",
        "intro": "We paid your {{doc_type}}, total {{total}} via {{payment_method}}.",
        "footer": "Ref {{reference}} • {{currency}}",
    }
    subject, html = tpl.render(g, company_name="Canada Royal Milk",
                               reference="BP-20260723-0001", payment_method="bank_transfer",
                               template=template)
    assert subject == "Payment to ACME — BP-20260723-0001"
    assert "Canada Royal Milk Remittance" in html
    assert "Hello ACME," in html
    assert "We paid your invoices, total 100.00 CAD via Bank Transfer." in html
    assert "Ref BP-20260723-0001 • CAD" in html


def test_blank_or_absent_template_falls_back_to_defaults():
    g = _group()
    # absent
    subject1, html1 = tpl.render(g, company_name="CRM", reference="R", payment_method="eft")
    # present but all-blank
    subject2, html2 = tpl.render(g, company_name="CRM", reference="R", payment_method="eft",
                                 template={"subject": "", "heading": "  ", "intro": None})
    assert subject1 == subject2 == "Remittance Advice — CRM — R"
    assert "Remittance Advice" in html1 and "Remittance Advice" in html2
    assert "The following invoices have been paid." in html1


def test_doc_type_differs_vendor_vs_employee():
    from app.crud import remittance as rem2
    vendor = _group()  # recipient_kind == 'vendor'
    employee = rem2.PayeeGroup(recipient_kind="employee", party_id=vendor.party_id,
                               party_name="Jane Doe", email=vendor.email, currency="CAD",
                               lines=list(vendor.lines), total=vendor.total)
    _, hv = tpl.render(vendor, company_name="C", reference="R", payment_method="eft",
                       template={"intro": "Paid: {{doc_type}}"})
    _, he = tpl.render(employee, company_name="C", reference="R", payment_method="eft",
                       template={"intro": "Paid: {{doc_type}}"})
    assert "Paid: invoices" in hv
    assert "Paid: expense claims" in he


def test_invalid_brand_color_falls_back_and_is_not_emitted_raw():
    g = _group()
    _, html = tpl.render(g, company_name="C", reference="R", payment_method="eft",
                         template={"brand_color": "red;}</style><script>x</script>"})
    assert "<script>" not in html
    assert "#085E5E" in html            # fell back to the default heading colour


def test_placeholder_value_is_escaped_in_html_but_subject_is_plain():
    g = _group()
    g.party_name = "<b>ACME</b> & Co"
    subject, html = tpl.render(g, company_name="C", reference="R", payment_method="eft",
                               template={"greeting": "Dear {{payee_name}},",
                                         "subject": "To {{payee_name}}"})
    assert "&lt;b&gt;ACME&lt;/b&gt; &amp; Co" in html      # escaped in the HTML body
    assert "<b>ACME</b>" not in html
    assert subject == "To <b>ACME</b> & Co"                # raw in the plain-text subject


def test_subject_strips_newlines_to_prevent_header_injection():
    g = _group()
    g.party_name = "Acme\nBcc: attacker@evil.test"
    subject, _ = tpl.render(g, company_name="C", reference="R", payment_method="eft",
                            template={"subject": "To {{payee_name}}"})
    assert "\n" not in subject and "\r" not in subject
    assert "Bcc:" in subject   # flattened into the single subject line, not a separate header


def test_logo_shown_only_when_enabled_and_present():
    g = _group()
    _, with_logo = tpl.render(g, company_name="C", reference="R", payment_method="eft",
                              template={"show_logo": True}, logo_data_url="data:image/png;base64,AAAA")
    _, no_flag = tpl.render(g, company_name="C", reference="R", payment_method="eft",
                            template={"show_logo": False}, logo_data_url="data:image/png;base64,AAAA")
    _, no_url = tpl.render(g, company_name="C", reference="R", payment_method="eft",
                           template={"show_logo": True}, logo_data_url=None)
    assert 'src="data:image/png;base64,AAAA"' in with_logo
    # Outlook renders via Word and ignores CSS max-height, so the logo MUST carry
    # an HTML height attribute or it renders at native size (see the 2026-07-24
    # oversized-logo report). CSS alone is not enough.
    assert 'height="40"' in with_logo
    assert "<img" not in no_flag
    assert "<img" not in no_url


def test_no_template_arg_still_renders_todays_email():
    g = _group()
    subject, html = tpl.render(g, company_name="Canada Royal Milk",
                               reference="BP-1", payment_method="bank_transfer")
    assert subject == "Remittance Advice — Canada Royal Milk — BP-1"
    assert "The following invoices have been paid." in html
    assert "This is an automated notification" in html


def test_unknown_placeholder_is_left_literal():
    g = _group()
    _, html = tpl.render(g, company_name="C", reference="R", payment_method="eft",
                         template={"intro": "Hello {{nope}} and {{payee_name}}"})
    assert "{{nope}}" in html          # unknown token shown literally, not blanked
    assert "ACME" in html              # a real placeholder still substitutes


# ── Task 8: sending and the send log ────────────────────────────────────────

from app.crud import remittance_send as rsend


def _sender():
    return rc.RemittanceSettings(
        enabled=True, from_email="ap@crm.test", from_name="CRM AP",
        cc_email="apbox@crm.test", smtp_host="h", smtp_port=587,
        smtp_user="u", smtp_password="p", smtp_use_tls=True,
    )


async def _send(db, groups, scope_id, side_effect=None, resend=False):
    """`resend=True` here is a test-helper convenience that resends EVERY
    group passed in — every caller of this helper passes a single uniform
    group list where that is exactly the desired blanket behaviour. The real
    per-payee mechanism (Fix 3 Round 2) is `resend_ids`, a set of
    `(recipient_kind, party_id)`; see
    test_resend_flag_does_not_leak_to_other_recipients_in_same_request for a
    test that exercises two groups with different resend flags in one call,
    the way the API layer actually uses this parameter."""
    resend_ids = {(g.recipient_kind, g.party_id) for g in groups} if resend else set()
    with patch("app.crud.remittance_send.send_email",
               new=AsyncMock(side_effect=side_effect)) as m:
        results = await rsend.send_groups(
            db, scope_kind=SCOPE_BATCH, scope_id=scope_id, groups=groups,
            reference="BP-20260722-0001", payment_method="bank_transfer",
            company_name="Canada Royal Milk", sender=_sender(),
            actor_id=uuid.uuid4(), resend_ids=resend_ids,
        )
    return results, m


async def test_send_writes_log_and_uses_cc(db_session):
    g = _group()
    scope_id = uuid.uuid4()
    results, m = await _send(db_session, [g], scope_id)
    assert [r["status"] for r in results] == ["sent"]
    assert m.await_args.args[0] == "ap@acme.test"
    assert m.await_args.kwargs["cc"] == "apbox@crm.test"

    row = (await db_session.execute(select(RemittanceNotification).where(
        RemittanceNotification.scope_id == scope_id))).scalar_one()
    assert row.status == SENT
    assert row.attempts == 1
    assert row.sent_at is not None


async def test_resend_upserts_and_increments_attempts(db_session):
    """The second send is a deliberate resend (Fix 3's `resend=True`) — a
    plain repeat with the pre-fix default would now be refused as `skipped`
    (see test_unqualified_resend_of_already_sent_payee_is_skipped), which is
    exactly the point of that fix, not something this test contradicts."""
    g = _group()
    scope_id = uuid.uuid4()
    await _send(db_session, [g], scope_id)
    await _send(db_session, [g], scope_id, resend=True)

    rows = (await db_session.execute(select(RemittanceNotification).where(
        RemittanceNotification.scope_id == scope_id))).scalars().all()
    assert len(rows) == 1
    assert rows[0].attempts == 2


async def test_failed_resend_does_not_downgrade_sent_status(db_session):
    """Fix 6: 'was ever delivered' and 'the latest attempt failed' are
    different facts. A resend that fails must not flip a row that already
    recorded a successful send from SENT back to FAILED — that flip is what
    made the hub column read Sent -> Not sent for a vendor who already holds
    the advice, inviting a third send. The individual attempt is still
    honestly reported as failed to the caller; only the persisted log row's
    `status` (what the hub and the cross-scope truthful check read) holds."""
    g = _group()
    scope_id = uuid.uuid4()
    await _send(db_session, [g], scope_id)          # first attempt: succeeds

    async def _boom(*a, **k):
        raise RuntimeError("smtp down")

    results, m = await _send(db_session, [g], scope_id, side_effect=_boom, resend=True)
    assert [r["status"] for r in results] == ["failed"]   # this attempt is reported honestly

    row = (await db_session.execute(select(RemittanceNotification).where(
        RemittanceNotification.scope_id == scope_id))).scalar_one()
    assert row.status == SENT            # the delivered fact is preserved
    assert row.attempts == 2


async def test_one_failure_does_not_stop_the_others(db_session):
    g1, g2 = _group(), _group()
    g1.email = "first@acme.test"
    g2.party_id = uuid.uuid4()
    g2.email = "second@acme.test"
    scope_id = uuid.uuid4()
    calls = {"n": 0}

    async def _boom(*a, **k):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("smtp down")

    with patch("app.crud.remittance_send.send_email", new=AsyncMock(side_effect=_boom)):
        results = await rsend.send_groups(
            db_session, scope_kind=SCOPE_BATCH, scope_id=scope_id, groups=[g1, g2],
            reference="R", payment_method="bank_transfer",
            company_name="C", sender=_sender(), actor_id=uuid.uuid4())

    assert sorted(r["status"] for r in results) == ["failed", "sent"]
    rows = (await db_session.execute(select(RemittanceNotification).where(
        RemittanceNotification.scope_id == scope_id))).scalars().all()
    assert len(rows) == 2


async def test_blocked_group_is_skipped_not_sent(db_session):
    g = _group()
    g.block_reasons = [rem.BLOCK_MISSING_EMAIL]
    g.email = ""
    scope_id = uuid.uuid4()
    results, m = await _send(db_session, [g], scope_id)
    assert [r["status"] for r in results] == ["skipped"]
    assert m.await_count == 0
    rows = (await db_session.execute(select(RemittanceNotification).where(
        RemittanceNotification.scope_id == scope_id))).scalars().all()
    assert rows == []


# ── Fix: durability and upsert race ─────────────────────────────────────────

async def test_render_failure_does_not_stop_the_others(db_session):
    """Same shape as test_one_failure_does_not_stop_the_others, but the
    failure is injected into render() rather than send_email() — proving
    render() is inside the same per-payee isolation boundary as send_email(),
    not sitting outside it where a template error on payee N would abort the
    loop for every payee after N."""
    g1, g2 = _group(), _group()
    g1.email = "first@acme.test"
    g2.party_id = uuid.uuid4()
    g2.email = "second@acme.test"
    scope_id = uuid.uuid4()
    calls = {"n": 0}

    def _boom(*a, **k):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("template broken")
        return "subject", "<p>ok</p>"

    with patch("app.crud.remittance_send.render", side_effect=_boom), \
         patch("app.crud.remittance_send.send_email", new=AsyncMock()) as m:
        results = await rsend.send_groups(
            db_session, scope_kind=SCOPE_BATCH, scope_id=scope_id, groups=[g1, g2],
            reference="R", payment_method="bank_transfer",
            company_name="C", sender=_sender(), actor_id=uuid.uuid4())

    assert sorted(r["status"] for r in results) == ["failed", "sent"]
    assert m.await_count == 1                 # second payee's send was actually attempted
    assert m.await_args.args[0] == "second@acme.test"
    rows = (await db_session.execute(select(RemittanceNotification).where(
        RemittanceNotification.scope_id == scope_id))).scalars().all()
    assert len(rows) == 2                      # both outcomes landed in the log


async def test_send_durably_commits_not_just_flushes(db_session):
    """A resend must be recorded even if a later payee, or the caller,
    blows up before any trailing commit would run. A trailing-commit-only
    implementation (or one that only flushes and leaves committing to the
    caller — the pre-fix state of this module) would leave the row visible
    only within this same session's still-open transaction; rolling that
    session back right after send_groups returns would then wipe an
    uncommitted insert. Surviving that rollback proves the row was already
    hard-committed before send_groups returned."""
    g = _group()
    scope_id = uuid.uuid4()
    await _send(db_session, [g], scope_id)

    await db_session.rollback()

    row = (await db_session.execute(select(RemittanceNotification).where(
        RemittanceNotification.scope_id == scope_id))).scalar_one()
    assert row.status == SENT


async def test_concurrent_double_send_lands_on_update_not_integrityerror(db_session):
    """Exercises the real race, not a stand-in for it: a second, genuinely
    concurrent `AsyncSession` (its own connection, its own open transaction)
    holds an *uncommitted* INSERT for this exact
    (scope_kind, scope_id, recipient_kind, party_id) key while `send_groups`
    runs on `db_session` for the same key. PostgreSQL makes `send_groups`'s
    `INSERT ... ON CONFLICT DO UPDATE` block on that row's key lock until the
    other session's transaction resolves — this test asserts the block
    actually happens (`task` is not done after a short wait), then commits
    the other session and asserts `send_groups` unblocks onto the UPDATE
    path (`"sent"`, `attempts == 2`, one row, no exception) rather than
    racing the SELECT of a select-then-write `_upsert` and losing to
    IntegrityError after the email already went out.

    Proved both ways by temporarily reverting `_upsert` to a
    select-then-write form (see the fix report): against that code this same
    test fails — the blocked INSERT unblocks once the other session commits,
    finds the row already there, and raises `IntegrityError` (unique
    violation), which propagates out of `send_groups` because nothing there
    catches it. Restored to the atomic `ON CONFLICT DO UPDATE` implementation,
    it passes."""
    import asyncio

    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from tests.conftest import ASYNC_URL

    g = _group()
    scope_id = uuid.uuid4()

    # A second session on its own connection — simulates another in-flight
    # send for the same payee that has started (inserted its row) but not
    # yet committed.
    engine2 = create_async_engine(ASYNC_URL, echo=False)
    maker2 = async_sessionmaker(engine2, expire_on_commit=False)
    session2 = maker2()
    try:
        session2.add(RemittanceNotification(
            scope_kind=SCOPE_BATCH, scope_id=scope_id,
            recipient_kind=g.recipient_kind, party_id=g.party_id, party_name=g.party_name,
            email=g.email, payment_record_ids=[], amount=Decimal("1.00"), currency="CAD",
            status=SENT, attempts=1, sent_at=datetime.now(timezone.utc),
            created_by=uuid.uuid4(),
        ))
        await session2.flush()   # row exists only inside session2's open transaction

        task = asyncio.create_task(_send(db_session, [g], scope_id))
        await asyncio.sleep(0.3)   # let the task reach and block on the row lock
        assert not task.done(), (
            "expected send_groups's INSERT to block on session2's uncommitted "
            "row for the same key — it returned instead, so nothing was "
            "actually overlapping"
        )

        await session2.commit()   # release the lock; the blocked statement can now resolve

        results, m = await asyncio.wait_for(task, timeout=10)
    finally:
        await session2.close()
        await engine2.dispose()

    assert [r["status"] for r in results] == ["sent"]
    assert m.await_count == 1
    rows = (await db_session.execute(select(RemittanceNotification).where(
        RemittanceNotification.scope_id == scope_id))).scalars().all()
    assert len(rows) == 1
    assert rows[0].attempts == 2


# ── Task 9: preview and send endpoints ──────────────────────────────────────

async def _configured(db):
    db.add(CompanyConfig(role_management={}, remittance_config={
        "enabled": True, "from_email": "ap@crm.test", "cc_email": "apbox@crm.test"}))
    await db.flush()
    await db.execute(sa.text(
        "UPDATE company_config SET po_smtp_host='po.host', po_smtp_port=587,"
        " po_smtp_use_tls=true"))


async def test_preview_requires_payment_authority(client, db_session):
    batch = PaymentBatch(batch_number="BP-1", batch_date=date(2026, 7, 22),
                         status=EXECUTED, currency="CAD", total=Decimal("0"),
                         payment_method="bank_transfer", created_by=uuid.uuid4())
    db_session.add(batch)
    await db_session.flush()
    r = await client.get(f"/finance/v1/payments/batches/{batch.id}/remittance/preview",
                         headers=_h("requester"))
    assert r.status_code == 403


async def test_preview_409_when_batch_not_executed(client, db_session):
    batch = PaymentBatch(batch_number="BP-2", batch_date=date(2026, 7, 22),
                         status="draft", currency="CAD", total=Decimal("0"),
                         payment_method="bank_transfer", created_by=uuid.uuid4())
    db_session.add(batch)
    await db_session.flush()
    r = await client.get(f"/finance/v1/payments/batches/{batch.id}/remittance/preview",
                         headers=_h())
    assert r.status_code == 409


async def test_preview_lists_group_with_block_reasons(client, db_session):
    await _configured(db_session)
    bp = await _vendor(db_session, email="", remit=None)
    inv = await _invoice(db_session, "VINV-20")
    pa = _pa(bp.id, "42.00", [str(inv.id)])
    db_session.add(pa)
    await db_session.flush()
    batch = PaymentBatch(batch_number="BP-3", batch_date=date(2026, 7, 22),
                         status=EXECUTED, currency="CAD", total=Decimal("42.00"),
                         payment_method="bank_transfer", created_by=uuid.uuid4())
    db_session.add(batch)
    await db_session.flush()
    db_session.add(_record(pa, batch_id=batch.id))
    await db_session.flush()

    body = (await client.get(
        f"/finance/v1/payments/batches/{batch.id}/remittance/preview",
        headers=_h())).json()
    assert body["enabled"] is True
    assert body["reference"] == "BP-3"
    assert len(body["groups"]) == 1
    assert body["groups"][0]["block_reasons"] == ["missing_email"]
    assert body["groups"][0]["total"] == "42.00"      # Decimal serialized as string


async def test_preview_line_exposes_gross_and_credit_notes_for_a_netted_payment(client, db_session):
    """`_serialize_groups` widened for Task 8 (brief:
    remittance-preview-brief.md) — a netted payment's preview line must carry
    `gross`, `credit_applied`, and the applied credit notes named by the
    VENDOR's own number, not just the net `amount` it always carried. This is
    what lets AP see, before clicking Send, the same three-tier breakdown
    (gross / less credit X / net) the outbound email itself renders (see
    remittance_template.py's `_amount_cell`)."""
    from app.models.vendor_credit import VendorCredit, VendorCreditApplication

    await _configured(db_session)
    bp = await _vendor(db_session, remit="remit@acme.test")
    inv = await _invoice(db_session, "VINV-30")
    pa = _pa(bp.id, "70.00", [str(inv.id)])
    db_session.add(pa)
    await db_session.flush()
    rec = _record(pa)
    rec.credit_applied = Decimal("30.00")   # net 70.00 + credit 30.00 = gross 100.00
    db_session.add(rec)
    await db_session.flush()

    vc = VendorCredit(
        credit_number="VC-TEST-30", vendor_id=bp.id, vendor_name="ACME",
        vendor_credit_number="11DJ-MFHX-N4JG", credit_date=date(2026, 7, 1),
        currency="CAD", amount=Decimal("30.00"), tax_amount=Decimal("0"),
        total_amount=Decimal("30.00"), applied_amount=Decimal("30.00"),
        remaining_amount=Decimal("0.00"), status="applied", line_items=[],
        uploaded_by=uuid.uuid4(), uploaded_at=datetime.now(timezone.utc),
    )
    db_session.add(vc)
    await db_session.flush()
    db_session.add(VendorCreditApplication(
        credit_id=vc.id, payment_record_id=rec.id, doc_kind="pa_dir", doc_id=pa.id,
        doc_number=pa.pa_number, applied_amount=Decimal("30.00"),
        applied_at=datetime.now(timezone.utc), applied_by=uuid.uuid4(),
    ))
    await db_session.flush()

    body = (await client.get(
        f"/finance/v1/payments/{rec.id}/remittance/preview", headers=_h())).json()
    assert len(body["groups"]) == 1
    line = body["groups"][0]["lines"][0]
    assert line["reference"] == "VINV-30"
    assert line["amount"] == "70.00"       # net cash paid — unchanged meaning
    assert line["gross"] == "100.00"
    assert line["credit_applied"] == "30.00"
    assert line["credit_notes"] == [
        {"vendor_credit_number": "11DJ-MFHX-N4JG", "applied_amount": "30.00"}]


async def test_send_endpoint_sends_and_reports(client, db_session):
    await _configured(db_session)
    bp = await _vendor(db_session, remit="remit@acme.test")
    inv = await _invoice(db_session, "VINV-21")
    pa = _pa(bp.id, "42.00", [str(inv.id)])
    db_session.add(pa)
    await db_session.flush()
    rec = _record(pa)
    db_session.add(rec)
    await db_session.flush()

    with patch("app.crud.remittance_send.send_email", new=AsyncMock()):
        r = await client.post(f"/finance/v1/payments/{rec.id}/remittance/send",
                              json={"recipients": None}, headers=_h())
    assert r.status_code == 200
    assert r.json()["sent"] == 1


async def test_send_endpoint_refuses_blocked_payee(client, db_session):
    await _configured(db_session)
    bp = await _vendor(db_session, email="", remit=None)
    inv = await _invoice(db_session, "VINV-22")
    pa = _pa(bp.id, "42.00", [str(inv.id)])
    db_session.add(pa)
    await db_session.flush()
    rec = _record(pa)
    db_session.add(rec)
    await db_session.flush()

    with patch("app.crud.remittance_send.send_email", new=AsyncMock()) as m:
        r = await client.post(f"/finance/v1/payments/{rec.id}/remittance/send",
                              json={"recipients": [
                                  {"recipient_kind": "vendor", "party_id": str(bp.id)}]},
                              headers=_h())
    assert r.json()["skipped"] == 1
    assert r.json()["sent"] == 0
    assert m.await_count == 0


async def test_preview_payment_scope_returns_preview_shape_not_payment_detail(client, db_session):
    """Regression guard for route-registration order: payments.py declares a
    catch-all GET /{payment_id} at the bottom of the file specifically so
    literal routes match first. If remittance_router were mounted after that
    catch-all (or matched behind it for any other reason), this GET would be
    swallowed by get_payment() and come back shaped like a payment detail
    (has "id"/"doc_kind"/"amount" at the top level, no "groups" key) instead
    of the preview shape asserted here.

    Uses an expense_claim payment rather than a PA/vendor payment: Fix 5
    (see test_single_vendor_payment_reference_is_not_the_pa_number) makes a
    vendor payee's `reference` resolve through its invoice number instead of
    doc_number/pa_number, which would make this route-order regression test
    entangled with that unrelated behaviour. An employee payment's
    `doc_number` (the claim number) is unaffected by that fix and remains
    the spec-sanctioned reference for an employee — see
    remittance_template.py."""
    await _configured(db_session)
    claim = ExpenseClaim(claim_number=f"EXP-{uuid.uuid4().hex[:8]}", claim_type="EXP",
                         status="approved", employee_id=uuid.uuid4(), employee_name="Jane Doe",
                         currency="CAD", total_amount=Decimal("15.00"), tax_amount=Decimal("0"),
                         net_amount=Decimal("15.00"))
    db_session.add(claim)
    await db_session.flush()
    rec = PaymentRecord(
        doc_kind="expense_claim", doc_id=claim.id, doc_number=claim.claim_number,
        payment_date=date(2026, 7, 22), payment_method="bank_transfer",
        amount=Decimal("15.00"), currency="CAD", recorded_by=uuid.uuid4(),
        status="completed",
    )
    db_session.add(rec)
    await db_session.flush()

    r = await client.get(f"/finance/v1/payments/{rec.id}/remittance/preview", headers=_h())
    assert r.status_code == 200
    body = r.json()
    assert "groups" in body
    assert "enabled" in body
    assert "id" not in body                # not the payment-detail payload
    assert body["reference"] == rec.doc_number


# ── Fix 5: a single vendor payment must not show the vendor its own PA number

async def test_single_vendor_payment_reference_is_not_the_pa_number(client, db_session):
    """`doc_number` IS `pa_number` for a vendor payment (payment_execute.execute
    sets doc_number=pa.pa_number), so the old `rec.doc_number or rec.pa_number`
    always resolved to the PA number for a vendor payee — an internal
    document number the spec (§3) says means nothing to, and should never
    reach, the vendor. A `payment` scope always covers exactly one PA, so its
    own vendor invoice number is used instead: unambiguous, and actually
    meaningful to the vendor rather than merely non-PA."""
    await _configured(db_session)
    bp = await _vendor(db_session, remit="remit@acme.test")
    inv = await _invoice(db_session, "VINV-60")
    pa = _pa(bp.id, "15.00", [str(inv.id)])
    db_session.add(pa)
    await db_session.flush()
    rec = _record(pa)
    db_session.add(rec)
    await db_session.flush()

    body = (await client.get(
        f"/finance/v1/payments/{rec.id}/remittance/preview", headers=_h())).json()
    assert body["reference"] == "VINV-60"
    assert body["reference"] != rec.pa_number
    assert body["reference"] != rec.doc_number


async def test_send_batch_endpoint_sends_and_reports(client, db_session):
    """Batch-scope equivalent of test_send_endpoint_sends_and_reports (which
    only exercises the payment-scope route). POST
    /payments/batches/{batch_id}/remittance/send was previously untested
    entirely; the scope_kind/scope_id assertions below are what actually
    distinguish this route from the payment-scope one — a handler that
    accidentally always logged SCOPE_PAYMENT, or logged under the payment
    record's id instead of the batch's, would still report sent == 1 here."""
    await _configured(db_session)
    bp = await _vendor(db_session, remit="remit@acme.test")
    inv = await _invoice(db_session, "VINV-30")
    pa = _pa(bp.id, "42.00", [str(inv.id)])
    db_session.add(pa)
    await db_session.flush()
    batch = PaymentBatch(batch_number="BP-30", batch_date=date(2026, 7, 22),
                         status=EXECUTED, currency="CAD", total=Decimal("42.00"),
                         payment_method="bank_transfer", created_by=uuid.uuid4())
    db_session.add(batch)
    await db_session.flush()
    db_session.add(_record(pa, batch_id=batch.id))
    await db_session.flush()

    with patch("app.crud.remittance_send.send_email", new=AsyncMock()):
        r = await client.post(f"/finance/v1/payments/batches/{batch.id}/remittance/send",
                              json={"recipients": None}, headers=_h())
    assert r.status_code == 200
    assert r.json()["sent"] == 1

    row = (await db_session.execute(select(RemittanceNotification).where(
        RemittanceNotification.scope_id == batch.id))).scalar_one()
    assert row.scope_kind == SCOPE_BATCH
    assert row.scope_id == batch.id


async def test_preview_404_for_nonexistent_batch(client, db_session):
    r = await client.get(
        f"/finance/v1/payments/batches/{uuid.uuid4()}/remittance/preview", headers=_h())
    assert r.status_code == 404


async def test_preview_404_for_nonexistent_payment(client, db_session):
    r = await client.get(
        f"/finance/v1/payments/{uuid.uuid4()}/remittance/preview", headers=_h())
    assert r.status_code == 404


async def test_preview_409_when_payment_not_completed(client, db_session):
    """Payment-scope equivalent of test_preview_409_when_batch_not_executed."""
    bp = await _vendor(db_session, remit="remit@acme.test")
    inv = await _invoice(db_session, "VINV-31")
    pa = _pa(bp.id, "10.00", [str(inv.id)])
    db_session.add(pa)
    await db_session.flush()
    rec = _record(pa, status="cancelled")
    db_session.add(rec)
    await db_session.flush()

    r = await client.get(f"/finance/v1/payments/{rec.id}/remittance/preview", headers=_h())
    assert r.status_code == 409


# ── Fix 2: a batch-scope send must be visible from the payment scope ────────

async def test_batch_send_is_visible_from_payment_scope_preview(client, db_session):
    """A payment paid as part of a batch has two independent notification-log
    identities: a `batch` row and a `payment` row for the same payee. Sending
    from the batch dialog must not leave the payment drawer showing 'Ready'
    (and pre-checked) for that vendor — that is exactly what let an operator
    send a real second advice listing only that one invoice."""
    await _configured(db_session)
    bp = await _vendor(db_session, remit="remit@acme.test")
    inv = await _invoice(db_session, "VINV-40")
    pa = _pa(bp.id, "42.00", [str(inv.id)])
    db_session.add(pa)
    await db_session.flush()
    batch = PaymentBatch(batch_number="BP-40", batch_date=date(2026, 7, 22),
                         status=EXECUTED, currency="CAD", total=Decimal("42.00"),
                         payment_method="bank_transfer", created_by=uuid.uuid4())
    db_session.add(batch)
    await db_session.flush()
    rec = _record(pa, batch_id=batch.id)
    db_session.add(rec)
    await db_session.flush()

    with patch("app.crud.remittance_send.send_email", new=AsyncMock()):
        r = await client.post(f"/finance/v1/payments/batches/{batch.id}/remittance/send",
                              json={"recipients": None}, headers=_h())
    assert r.json()["sent"] == 1

    body = (await client.get(
        f"/finance/v1/payments/{rec.id}/remittance/preview", headers=_h())).json()
    assert len(body["groups"]) == 1
    last_send = body["groups"][0]["last_send"]
    assert last_send is not None
    assert last_send["status"] == "sent"


# ── Fix 3: the server must refuse a duplicate send unless resend=True ───────

async def test_unqualified_resend_of_already_sent_payee_is_skipped(client, db_session):
    """A payee already `sent` under the effective scope must be refused —
    not silently re-emailed — when the client does not opt in with
    `resend: true`. Before this fix nothing on the server consulted the log
    at all; a payee already `sent` was re-sent whenever it reappeared in
    `recipients`."""
    await _configured(db_session)
    bp = await _vendor(db_session, remit="remit@acme.test")
    inv = await _invoice(db_session, "VINV-51")
    pa = _pa(bp.id, "42.00", [str(inv.id)])
    db_session.add(pa)
    await db_session.flush()
    rec = _record(pa)
    db_session.add(rec)
    await db_session.flush()

    with patch("app.crud.remittance_send.send_email", new=AsyncMock()) as m1:
        first = await client.post(f"/finance/v1/payments/{rec.id}/remittance/send",
                                  json={"recipients": None}, headers=_h())
    assert first.json()["sent"] == 1
    assert m1.await_count == 1

    with patch("app.crud.remittance_send.send_email", new=AsyncMock()) as m2:
        second = await client.post(f"/finance/v1/payments/{rec.id}/remittance/send",
                                   json={"recipients": None}, headers=_h())
    assert second.json()["sent"] == 0
    assert second.json()["skipped"] == 1
    assert m2.await_count == 0        # no second email ever attempted


async def test_resend_true_sends_again(client, db_session):
    """The explicit opt-in still works — resending is a deliberate, supported
    operation, not something to simply refuse outright. `resend` now lives on
    the individual RecipientRef (Fix 3 Round 2), not as a request-level flag,
    so the opt-in is expressed as an explicit recipients entry rather than
    `{"recipients": None, "resend": True}`."""
    await _configured(db_session)
    bp = await _vendor(db_session, remit="remit@acme.test")
    inv = await _invoice(db_session, "VINV-52")
    pa = _pa(bp.id, "42.00", [str(inv.id)])
    db_session.add(pa)
    await db_session.flush()
    rec = _record(pa)
    db_session.add(rec)
    await db_session.flush()

    with patch("app.crud.remittance_send.send_email", new=AsyncMock()):
        first = await client.post(f"/finance/v1/payments/{rec.id}/remittance/send",
                                  json={"recipients": None}, headers=_h())
    assert first.json()["sent"] == 1

    with patch("app.crud.remittance_send.send_email", new=AsyncMock()) as m2:
        second = await client.post(
            f"/finance/v1/payments/{rec.id}/remittance/send",
            json={"recipients": [
                {"recipient_kind": "vendor", "party_id": str(bp.id), "resend": True}]},
            headers=_h())
    assert second.json()["sent"] == 1
    assert second.json()["skipped"] == 0
    assert m2.await_count == 1


# ── Fix 3 Round 2: resend is per-payee, not per-request ─────────────────────

async def test_resend_flag_does_not_leak_to_other_recipients_in_same_request(client, db_session):
    """Batch BP pays ACME and BOREAL. A first batch send succeeds for ACME
    but fails for BOREAL. Independently, a colleague already resent BOREAL
    from the payment-scope drawer (simulated directly below), so BOREAL is
    genuinely `sent` under the OTHER scope by the time the operator's stale
    batch panel is used again. The operator, thinking ACME's copy "vanished",
    deliberately re-checks ACME for resend; BOREAL is also selected (the
    operator has no idea it was just resent), but WITHOUT the resend flag.

    Pre-fix (request-level `resend: bool`), selecting ACME for resend forced
    `resend=True` for the whole request, which would also waive BOREAL's
    guard and mail it a second time. Fixed (per-RecipientRef `resend`), ACME
    sends and BOREAL is refused as `skipped` with no mail — proven below by
    asserting `send_email` was called exactly once, for ACME's address only.
    """
    await _configured(db_session)
    acme = await _vendor(db_session, remit="remit@acme.test")
    boreal = await _vendor(db_session, remit="remit@boreal.test")
    inv_a, inv_b = await _invoice(db_session, "VINV-A1"), await _invoice(db_session, "VINV-B1")
    pa_a, pa_b = _pa(acme.id, "10.00", [str(inv_a.id)]), _pa(boreal.id, "20.00", [str(inv_b.id)])
    db_session.add_all([pa_a, pa_b])
    await db_session.flush()
    batch = PaymentBatch(batch_number="BP-90", batch_date=date(2026, 7, 22),
                         status=EXECUTED, currency="CAD", total=Decimal("30.00"),
                         payment_method="bank_transfer", created_by=uuid.uuid4())
    db_session.add(batch)
    await db_session.flush()
    rec_a, rec_b = _record(pa_a, batch_id=batch.id), _record(pa_b, batch_id=batch.id)
    db_session.add_all([rec_a, rec_b])
    await db_session.flush()

    # First batch send: ACME succeeds, BOREAL fails (SMTP down for BOREAL's
    # address only) — this is the initial partial failure in the scenario.
    async def _fail_boreal_only(to, *a, **k):
        if to == "remit@boreal.test":
            raise RuntimeError("smtp down")

    with patch("app.crud.remittance_send.send_email",
               new=AsyncMock(side_effect=_fail_boreal_only)):
        first = await client.post(
            f"/finance/v1/payments/batches/{batch.id}/remittance/send",
            json={"recipients": None}, headers=_h())
    assert first.json()["sent"] == 1
    assert first.json()["failed"] == 1

    # A colleague resends BOREAL from the payment-scope drawer, and it
    # succeeds this time — a genuinely SENT row under the OTHER scope
    # (payment), independent of and more recent than the batch-scope FAILED
    # row above.
    with patch("app.crud.remittance_send.send_email", new=AsyncMock()):
        colleague = await client.post(
            f"/finance/v1/payments/{rec_b.id}/remittance/send",
            json={"recipients": None}, headers=_h())
    assert colleague.json()["sent"] == 1

    # The operator's stale batch panel: ACME re-checked for a deliberate
    # resend, BOREAL selected too but NOT flagged for resend.
    with patch("app.crud.remittance_send.send_email", new=AsyncMock()) as m:
        second = await client.post(
            f"/finance/v1/payments/batches/{batch.id}/remittance/send",
            json={"recipients": [
                {"recipient_kind": "vendor", "party_id": str(acme.id), "resend": True},
                {"recipient_kind": "vendor", "party_id": str(boreal.id)},
            ]},
            headers=_h())
    body = second.json()
    assert body["sent"] == 1
    assert body["skipped"] == 1
    by_party = {r["party_id"]: r for r in body["results"]}
    assert by_party[str(acme.id)]["status"] == "sent"
    assert by_party[str(boreal.id)]["status"] == "skipped"
    assert m.await_count == 1                    # only ACME was actually mailed
    assert m.await_args.args[0] == "remit@acme.test"


async def test_preview_payment_scope_requires_payment_authority(client, db_session):
    """Payment-scope equivalent of test_preview_requires_payment_authority
    (which only exercises the batch scope)."""
    bp = await _vendor(db_session, remit="remit@acme.test")
    inv = await _invoice(db_session, "VINV-32")
    pa = _pa(bp.id, "10.00", [str(inv.id)])
    db_session.add(pa)
    await db_session.flush()
    rec = _record(pa)
    db_session.add(rec)
    await db_session.flush()

    r = await client.get(f"/finance/v1/payments/{rec.id}/remittance/preview",
                         headers=_h("requester"))
    assert r.status_code == 403


# ── Task 2: template + logo threaded from config into the sent email ────────


async def test_load_surfaces_template_and_logo(db_session):
    db_session.add(CompanyConfig(role_management={}, remittance_config={
        "enabled": True, "from_email": "ap@crm.test",
        "template": {"heading": "Custom Heading", "show_logo": True}}))
    await db_session.flush()
    await db_session.execute(sa.text(
        "UPDATE company_config SET po_smtp_host='po.host', po_smtp_port=587,"
        " po_smtp_use_tls=true, logo_data_url='data:image/png;base64,ZZZ'"))

    s = await rc.load(db_session)
    assert s is not None
    assert s.template.get("heading") == "Custom Heading"
    assert s.template.get("show_logo") is True
    assert s.logo_data_url == "data:image/png;base64,ZZZ"


async def test_send_uses_the_configured_template(db_session):
    # A configured heading must reach the actually-sent email.
    await _configured(db_session)  # enables remittance + po_smtp
    await db_session.execute(sa.text(
        "UPDATE company_config SET remittance_config = remittance_config || "
        "'{\"template\": {\"heading\": \"CRM Payment Notice\"}}'::jsonb"))
    bp = await _vendor(db_session, remit="remit@acme.test")
    inv = await _invoice(db_session, "VINV-77")
    pa = _pa(bp.id, "12.00", [str(inv.id)])
    db_session.add(pa)
    await db_session.flush()
    rec = _record(pa)
    db_session.add(rec)
    await db_session.flush()

    sent_html = {}

    async def _capture(to, subject, html, **kw):
        sent_html["subject"], sent_html["html"] = subject, html
    with patch("app.crud.remittance_send.send_email", new=AsyncMock(side_effect=_capture)):
        sender = await rc.load(db_session)
        await rsend.send_groups(
            db_session, scope_kind=SCOPE_PAYMENT, scope_id=rec.id,
            groups=await rem.build_groups(db_session, [rec]),
            reference="PAY-1", payment_method="bank_transfer",
            company_name="CRM", sender=sender, actor_id=uuid.uuid4())
    assert "CRM Payment Notice" in sent_html["html"]


# ── Selection scope: aggregated remittance for an arbitrary payment set ─────
# (increment: vendor filter + multi-select on the Payments hub)

async def test_selection_preview_two_payments_one_vendor_single_group(client, db_session):
    """A selection of two payments to one vendor previews as a single group
    covering both invoices, with the total summed."""
    await _configured(db_session)
    bp = await _vendor(db_session, remit="remit@acme.test")
    i1, i2 = await _invoice(db_session, "VINV-100"), await _invoice(db_session, "VINV-101")
    pa1, pa2 = _pa(bp.id, "30.00", [str(i1.id)]), _pa(bp.id, "70.00", [str(i2.id)])
    db_session.add_all([pa1, pa2])
    await db_session.flush()
    rec1, rec2 = _record(pa1), _record(pa2)
    db_session.add_all([rec1, rec2])
    await db_session.flush()

    r = await client.post(
        "/finance/v1/payments/remittance/selection/preview",
        json={"payment_ids": [str(rec1.id), str(rec2.id)]}, headers=_h())
    assert r.status_code == 200
    body = r.json()
    assert len(body["groups"]) == 1
    g = body["groups"][0]
    assert g["total"] == "100.00"
    assert sorted(l["reference"] for l in g["lines"]) == ["VINV-100", "VINV-101"]
    # Neither payment netted a vendor credit — the preview shape is uniform,
    # not conditional: every line carries gross/credit_applied/credit_notes,
    # with an un-netted line showing the "nothing happened" values rather
    # than omitting the fields.
    for l in g["lines"]:
        assert l["credit_applied"] == "0.00"
        assert l["credit_notes"] == []
        assert l["gross"] == l["amount"]
    # Reference is a plain ad-hoc label, never a PA/document number (spec §3).
    assert "PA-" not in body["reference"]
    assert pa1.pa_number not in body["reference"]
    assert pa2.pa_number not in body["reference"]


async def test_selection_preview_spanning_two_vendors_is_400(client, db_session):
    """A selection spanning two payees is rejected, not silently split."""
    await _configured(db_session)
    bp1 = await _vendor(db_session, remit="remit@acme.test")
    bp2 = await _vendor(db_session, remit="remit@boreal.test")
    i1, i2 = await _invoice(db_session, "VINV-110"), await _invoice(db_session, "VINV-111")
    pa1, pa2 = _pa(bp1.id, "10.00", [str(i1.id)]), _pa(bp2.id, "20.00", [str(i2.id)])
    db_session.add_all([pa1, pa2])
    await db_session.flush()
    rec1, rec2 = _record(pa1), _record(pa2)
    db_session.add_all([rec1, rec2])
    await db_session.flush()

    r = await client.post(
        "/finance/v1/payments/remittance/selection/preview",
        json={"payment_ids": [str(rec1.id), str(rec2.id)]}, headers=_h())
    assert r.status_code == 400
    assert "more than one payee" in r.json()["detail"]

    # The send endpoint applies the same guard.
    r2 = await client.post(
        "/finance/v1/payments/remittance/selection/send",
        json={"payment_ids": [str(rec1.id), str(rec2.id)]}, headers=_h())
    assert r2.status_code == 400


async def test_selection_send_writes_one_row_resend_upserts_to_two_attempts(client, db_session):
    """Sending a selection writes one `selection`-scope row whose
    `payment_record_ids` holds both ids; re-sending the exact same selection
    upserts onto that same row (one row, attempts == 2) rather than
    duplicating — proving the deterministic scope id (uuid5 over the sorted
    ids) agrees between the two requests."""
    await _configured(db_session)
    bp = await _vendor(db_session, remit="remit@acme.test")
    i1, i2 = await _invoice(db_session, "VINV-120"), await _invoice(db_session, "VINV-121")
    pa1, pa2 = _pa(bp.id, "15.00", [str(i1.id)]), _pa(bp.id, "25.00", [str(i2.id)])
    db_session.add_all([pa1, pa2])
    await db_session.flush()
    rec1, rec2 = _record(pa1), _record(pa2)
    db_session.add_all([rec1, rec2])
    await db_session.flush()
    ids = [str(rec1.id), str(rec2.id)]

    with patch("app.crud.remittance_send.send_email", new=AsyncMock()):
        r = await client.post("/finance/v1/payments/remittance/selection/send",
                              json={"payment_ids": ids}, headers=_h())
    assert r.status_code == 200
    assert r.json()["sent"] == 1

    rows = (await db_session.execute(select(RemittanceNotification).where(
        RemittanceNotification.scope_kind == SCOPE_SELECTION))).scalars().all()
    assert len(rows) == 1
    row = rows[0]
    assert sorted(row.payment_record_ids) == sorted(ids)
    assert row.attempts == 1

    # Re-sending the SAME set of ids, with resend requested, must upsert onto
    # the same row rather than insert a second one.
    with patch("app.crud.remittance_send.send_email", new=AsyncMock()):
        r2 = await client.post(
            "/finance/v1/payments/remittance/selection/send",
            json={"payment_ids": ids, "recipients": [
                {"recipient_kind": "vendor", "party_id": str(bp.id), "resend": True}]},
            headers=_h())
    assert r2.json()["sent"] == 1

    # `_upsert` writes via a raw Core `INSERT ... ON CONFLICT DO UPDATE`
    # (see its docstring), which bypasses the ORM unit-of-work entirely —
    # the `row` object above, already resident in this session's identity
    # map, is never told its `attempts` changed underneath it. Without this
    # expire, re-querying would silently hand back the SAME cached Python
    # object with its stale attempts==1, not the row's true DB state.
    db_session.expire_all()
    rows2 = (await db_session.execute(select(RemittanceNotification).where(
        RemittanceNotification.scope_kind == SCOPE_SELECTION))).scalars().all()
    assert len(rows2) == 1
    assert rows2[0].id == row.id
    assert rows2[0].attempts == 2


async def test_selection_preview_and_send_detect_prior_batch_send(client, db_session):
    """Cross-scope guard, the NEW direction added by this increment: a
    payment already sent under a `batch` scope must show as already-sent
    from a `selection` preview covering that same record, and an unqualified
    selection send of it must be refused (`skipped`, no mail). `resend: true`
    still sends.

    NOTE on what this test does and does not prove: `_other_scope("selection")`
    happens to fall through to its `else` branch and return `"batch"` — the
    SAME value the old pre-fix binary helper would compute for `"payment"`
    too, since it only ever recognised two scopes. That means THIS specific
    direction (prior BATCH send, checked from a selection) passes even
    against the old code, by coincidence, not because the old code actually
    generalizes. See
    test_selection_guard_generalizes_beyond_the_old_batch_payment_pair below
    for the direction that actually distinguishes old from new — a prior
    PAYMENT-scope send, which `_other_scope("selection")` can never resolve
    to since it only ever returns "batch" or "payment" for its own two
    recognised inputs.
    """
    await _configured(db_session)
    bp = await _vendor(db_session, remit="remit@acme.test")
    inv = await _invoice(db_session, "VINV-130")
    pa = _pa(bp.id, "42.00", [str(inv.id)])
    db_session.add(pa)
    await db_session.flush()
    batch = PaymentBatch(batch_number="BP-130", batch_date=date(2026, 7, 22),
                         status=EXECUTED, currency="CAD", total=Decimal("42.00"),
                         payment_method="bank_transfer", created_by=uuid.uuid4())
    db_session.add(batch)
    await db_session.flush()
    rec = _record(pa, batch_id=batch.id)
    db_session.add(rec)
    await db_session.flush()

    # Sent from the batch dialog.
    with patch("app.crud.remittance_send.send_email", new=AsyncMock()):
        sent = await client.post(f"/finance/v1/payments/batches/{batch.id}/remittance/send",
                                 json={"recipients": None}, headers=_h())
    assert sent.json()["sent"] == 1

    # A selection covering that same one record must show it as already sent.
    preview = (await client.post(
        "/finance/v1/payments/remittance/selection/preview",
        json={"payment_ids": [str(rec.id)]}, headers=_h())).json()
    assert len(preview["groups"]) == 1
    last_send = preview["groups"][0]["last_send"]
    assert last_send is not None
    assert last_send["status"] == "sent"

    # An unqualified selection send is refused — no second email.
    with patch("app.crud.remittance_send.send_email", new=AsyncMock()) as m:
        unqualified = await client.post(
            "/finance/v1/payments/remittance/selection/send",
            json={"payment_ids": [str(rec.id)]}, headers=_h())
    assert unqualified.json()["sent"] == 0
    assert unqualified.json()["skipped"] == 1
    assert m.await_count == 0

    # resend: true is the deliberate opt-in and still sends.
    with patch("app.crud.remittance_send.send_email", new=AsyncMock()) as m2:
        resent = await client.post(
            "/finance/v1/payments/remittance/selection/send",
            json={"payment_ids": [str(rec.id)], "recipients": [
                {"recipient_kind": "vendor", "party_id": str(bp.id), "resend": True}]},
            headers=_h())
    assert resent.json()["sent"] == 1
    assert m2.await_count == 1


async def test_selection_guard_generalizes_beyond_the_old_batch_payment_pair(client, db_session):
    """THE proof test for the safety-critical change: a payment already sent
    under a `payment` scope (the drawer) must be detected as already-sent
    from a `selection` preview/send covering that same record.

    This is the direction that actually distinguishes the fix from the old
    code. The old `_other_scope(scope_kind)` was `SCOPE_PAYMENT if scope_kind
    == SCOPE_BATCH else SCOPE_BATCH` — for `scope_kind="selection"` (which it
    was never written to know about) that unconditionally falls to the
    `else` branch and returns `"batch"`, NEVER `"payment"`. So a prior
    PAYMENT-scope send is invisible to the old code's cross-scope lookup
    from a selection, no matter what — this is not a coincidence to route
    around, it is the actual bug the widened `scope_kind != :current` filter
    fixes. Confirmed by temporarily reverting to the binary form and running
    this test: it fails (see the increment report for the captured output).
    """
    await _configured(db_session)
    bp = await _vendor(db_session, remit="remit@acme.test")
    inv = await _invoice(db_session, "VINV-140")
    pa = _pa(bp.id, "42.00", [str(inv.id)])
    db_session.add(pa)
    await db_session.flush()
    rec = _record(pa)
    db_session.add(rec)
    await db_session.flush()

    # Sent from the payment-scope drawer.
    with patch("app.crud.remittance_send.send_email", new=AsyncMock()):
        sent = await client.post(f"/finance/v1/payments/{rec.id}/remittance/send",
                                 json={"recipients": None}, headers=_h())
    assert sent.json()["sent"] == 1

    # A selection covering that same one record must show it as already sent.
    preview = (await client.post(
        "/finance/v1/payments/remittance/selection/preview",
        json={"payment_ids": [str(rec.id)]}, headers=_h())).json()
    assert len(preview["groups"]) == 1
    last_send = preview["groups"][0]["last_send"]
    assert last_send is not None
    assert last_send["status"] == "sent"

    # An unqualified selection send is refused — no second email.
    with patch("app.crud.remittance_send.send_email", new=AsyncMock()) as m:
        unqualified = await client.post(
            "/finance/v1/payments/remittance/selection/send",
            json={"payment_ids": [str(rec.id)]}, headers=_h())
    assert unqualified.json()["sent"] == 0
    assert unqualified.json()["skipped"] == 1
    assert m.await_count == 0

    # resend: true is the deliberate opt-in and still sends.
    with patch("app.crud.remittance_send.send_email", new=AsyncMock()) as m2:
        resent = await client.post(
            "/finance/v1/payments/remittance/selection/send",
            json={"payment_ids": [str(rec.id)], "recipients": [
                {"recipient_kind": "vendor", "party_id": str(bp.id), "resend": True}]},
            headers=_h())
    assert resent.json()["sent"] == 1
    assert m2.await_count == 1


async def test_selection_endpoints_require_payment_authority(client, db_session):
    r1 = await client.post("/finance/v1/payments/remittance/selection/preview",
                           json={"payment_ids": []}, headers=_h("requester"))
    assert r1.status_code == 403
    r2 = await client.post("/finance/v1/payments/remittance/selection/send",
                           json={"payment_ids": []}, headers=_h("requester"))
    assert r2.status_code == 403


# ── Recipient-address normalization (2026-08-17 prod incident) ───────────────
#
# Sangers Inc's remittance advice failed with
# `SMTPRecipientRefused(501, '5.1.3 Bad recipient address syntax', '')` because
# the vendor's remittance_email held TWO addresses joined by a semicolon.
# A semicolon is not an RFC 5322 separator, so Python's address parser does not
# salvage the good addresses — it collapses the whole header into ONE EMPTY
# recipient, the empty recipient is the only one, every recipient is therefore
# refused, and aiosmtplib raises. The fix is to normalize before the address
# ever reaches the SMTP layer, and to refuse locally (with a block reason the
# operator can read) when it cannot be normalized.

async def test_semicolon_separated_vendor_email_is_normalized_for_smtp(db_session):
    bp = await _vendor(db_session, remit="kyle@sangers.test; rob@sangers.test")
    inv = await _invoice(db_session, "VINV-101")
    pa = _pa(bp.id, "10.00", [str(inv.id)])
    db_session.add(pa)
    await db_session.flush()
    rec = _record(pa)
    db_session.add(rec)
    await db_session.flush()

    g = (await rem.build_groups(db_session, [rec]))[0]
    assert g.email == "kyle@sangers.test, rob@sangers.test"
    assert g.block_reasons == []
    # The assertion that actually reproduces the incident: the value we hand
    # to the To header must survive the same parser aiosmtplib uses to build
    # its RCPT TO list, with no empty recipient in it.
    assert [a for _, a in getaddresses([g.email])] == [
        "kyle@sangers.test", "rob@sangers.test"]


async def test_trailing_separator_in_vendor_email_is_dropped(db_session):
    bp = await _vendor(db_session, remit="a@x.test, b@x.test,")
    inv = await _invoice(db_session, "VINV-102")
    pa = _pa(bp.id, "10.00", [str(inv.id)])
    db_session.add(pa)
    await db_session.flush()
    rec = _record(pa)
    db_session.add(rec)
    await db_session.flush()

    g = (await rem.build_groups(db_session, [rec]))[0]
    assert g.email == "a@x.test, b@x.test"
    assert "" not in [a for _, a in getaddresses([g.email])]


async def test_malformed_vendor_email_blocks_group_instead_of_reaching_smtp(db_session):
    bp = await _vendor(db_session, remit="a@x.test, not-an-email")
    inv = await _invoice(db_session, "VINV-103")
    pa = _pa(bp.id, "10.00", [str(inv.id)])
    db_session.add(pa)
    await db_session.flush()
    rec = _record(pa)
    db_session.add(rec)
    await db_session.flush()

    g = (await rem.build_groups(db_session, [rec]))[0]
    assert rem.BLOCK_INVALID_EMAIL in g.block_reasons
    # Distinct from "no address at all" — the operator must be told the
    # address is unusable, not that it is absent.
    assert rem.BLOCK_MISSING_EMAIL not in g.block_reasons


async def test_malformed_employee_email_blocks_group(db_session):
    emp_id = uuid.uuid4()
    db_session.add(User(id=emp_id, email="jane@crm.test; jim@crm.test",
                        full_name="Jane Doe"))
    claim = ExpenseClaim(claim_number="EXP-101", claim_type="EXP", status="approved",
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
    assert g.email == "jane@crm.test, jim@crm.test"
    assert g.block_reasons == []


# ── Who may send remittance advice ──────────────────────────────────────────
#
# The 2026-08-13 SoD split removed ap_clerk from payment EXECUTION
# (tests/test_payment_authority.py::test_ap_clerk_can_no_longer_execute_payments
# is the guard for that, and must stay green). Remittance advice is not an act
# of paying — the money has already moved — it is AP telling the payee about a
# payment that is already recorded. Gating it on payment authority meant the
# only way to let AP send it was to hand them the authority to move money,
# which would undo the split. So the send surface gets its own role set.

async def test_ap_clerk_may_preview_remittance_without_payment_authority(client, db_session):
    batch = PaymentBatch(batch_number="BP-AC1", batch_date=date(2026, 7, 22),
                         status=EXECUTED, currency="CAD", total=Decimal("0"),
                         payment_method="bank_transfer", created_by=uuid.uuid4())
    db_session.add(batch)
    await db_session.flush()

    r = await client.get(f"/finance/v1/payments/batches/{batch.id}/remittance/preview",
                         headers=_h("ap_clerk"))
    assert r.status_code == 200


async def test_ap_clerk_may_send_remittance_without_payment_authority(client, db_session):
    await _configured(db_session)
    bp = await _vendor(db_session, remit="remit@acme.test")
    inv = await _invoice(db_session, "VINV-201")
    pa = _pa(bp.id, "10.00", [str(inv.id)])
    db_session.add(pa)
    await db_session.flush()
    rec = _record(pa)
    db_session.add(rec)
    await db_session.flush()

    with patch("app.crud.remittance_send.send_email", new=AsyncMock()) as m:
        r = await client.post(f"/finance/v1/payments/{rec.id}/remittance/send",
                              json={}, headers=_h("ap_clerk"))
    assert r.status_code == 200
    assert r.json()["sent"] == 1
    assert m.await_count == 1


async def test_remittance_send_still_refuses_a_role_with_no_finance_standing(client, db_session):
    await _configured(db_session)
    r = await client.post("/finance/v1/payments/remittance/selection/send",
                          json={"payment_ids": []}, headers=_h("requester"))
    assert r.status_code == 403


async def test_semicolon_separated_cc_is_normalized_not_silently_dropped(db_session):
    # The CC address goes into its own header, parsed separately from To, so a
    # malformed CC does NOT fail the send — the payee is accepted, only the
    # empty CC recipient is refused, and aiosmtplib raises only when EVERY
    # recipient is refused. The finance copy would just never arrive, with a
    # "sent" row in the log to say it did. Normalize it for the same reason
    # the payee address is normalized.
    db_session.add(CompanyConfig(role_management={}, remittance_config={
        "enabled": True, "from_email": "ap@crm.test",
        "cc_email": "finance@crm.test; ap@crm.test"}))
    await db_session.flush()
    await db_session.execute(sa.text(
        "UPDATE company_config SET po_smtp_host='po.host', po_smtp_port=587,"
        " po_smtp_use_tls=true"))

    s = await rc.load(db_session)
    assert s.cc_email == "finance@crm.test, ap@crm.test"
    assert "" not in [a for _, a in getaddresses([s.cc_email])]


async def test_unusable_cc_is_dropped_rather_than_breaking_the_header(db_session):
    db_session.add(CompanyConfig(role_management={}, remittance_config={
        "enabled": True, "from_email": "ap@crm.test", "cc_email": "not-an-email"}))
    await db_session.flush()
    await db_session.execute(sa.text(
        "UPDATE company_config SET po_smtp_host='po.host', po_smtp_port=587,"
        " po_smtp_use_tls=true"))

    s = await rc.load(db_session)
    assert s.cc_email is None


async def test_ap_clerk_as_an_additional_role_may_send_remittance(client, db_session):
    """AP Clerk held as an ADDITIONAL role (identity user_roles), not the JWT's
    primary role — which is how these roles are actually expected to be held in
    production (see test_payment_authority.py::
    test_payment_officer_works_as_an_additional_role). The primary-role branch
    alone would leave that operator at 403."""
    user_id = uuid.uuid4()
    await db_session.execute(sa.text(
        "INSERT INTO user_roles (user_id, role_code) VALUES (:u, 'ap_clerk')"),
        {"u": str(user_id)})
    batch = PaymentBatch(batch_number="BP-AC2", batch_date=date(2026, 7, 22),
                         status=EXECUTED, currency="CAD", total=Decimal("0"),
                         payment_method="bank_transfer", created_by=uuid.uuid4())
    db_session.add(batch)
    await db_session.flush()

    token = jwt.encode({"sub": str(user_id), "role": "requester",
                        "exp": datetime.now(timezone.utc) + timedelta(hours=1)},
                       settings.jwt_secret_key, algorithm=settings.jwt_algorithm)
    r = await client.get(f"/finance/v1/payments/batches/{batch.id}/remittance/preview",
                         headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 200


async def test_system_admin_as_an_additional_role_may_not_send_remittance(client, db_session):
    """Mirrors test_payment_authority.py::test_system_admin_as_additional_role_is_denied
    — system_admin is a PRIMARY-role grant only, and deriving _SEND_ROLES_ASSIGNED
    from _SEND_ROLES must not quietly reintroduce it."""
    user_id = uuid.uuid4()
    await db_session.execute(sa.text(
        "INSERT INTO user_roles (user_id, role_code) VALUES (:u, 'system_admin')"),
        {"u": str(user_id)})
    token = jwt.encode({"sub": str(user_id), "role": "requester",
                        "exp": datetime.now(timezone.utc) + timedelta(hours=1)},
                       settings.jwt_secret_key, algorithm=settings.jwt_algorithm)
    r = await client.post("/finance/v1/payments/remittance/selection/preview",
                          json={"payment_ids": []},
                          headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 403


# ── Advice payment date: what the payee is told the funds left ──────────────
#
# AP often sends the advice a day or two after the money actually moved (bank
# cut-off, a cheque run signed the day before), so the date recorded against
# the payment is not always the date the payee's bank will show. The send
# carries an optional `payment_date` that overrides what the advice DISPLAYS —
# and nothing else: the payment record behind the GL is never rewritten.


def _two_line_group():
    g = _group()
    g.lines.append(rem.GroupLine(vendor_inv_no="VINV-2", doc_number="PA-0002",
                                 payment_date=date(2026, 7, 23), amount=Decimal("50.00")))
    g.total = Decimal("150.00")
    return g


def test_render_override_replaces_the_date_on_every_line():
    g = _two_line_group()          # recorded 2026-07-22 and 2026-07-23
    _, html = tpl.render(g, company_name="C", reference="R", payment_method="eft",
                         payment_date=date(2026, 7, 20))
    assert html.count("2026-07-20") == 2      # both lines, not just the first
    assert "2026-07-22" not in html
    assert "2026-07-23" not in html


def test_render_without_override_keeps_each_line_its_recorded_date():
    """The admission half of the test above: absent an override the email is
    byte-for-byte the one AP has always sent, per-line dates and all."""
    g = _two_line_group()
    _, html = tpl.render(g, company_name="C", reference="R", payment_method="eft")
    assert "2026-07-22" in html and "2026-07-23" in html


def test_render_override_does_not_mutate_the_group_lines():
    """Display-only means display-only — the caller's GroupLine objects (and
    through them nothing else) come back untouched, so a second render, or a
    log write, still sees the recorded dates."""
    g = _two_line_group()
    tpl.render(g, company_name="C", reference="R", payment_method="eft",
               payment_date=date(2026, 7, 20))
    assert [l.payment_date for l in g.lines] == [date(2026, 7, 22), date(2026, 7, 23)]


async def test_send_groups_puts_the_override_in_the_email_body(db_session):
    g = _group()
    with patch("app.crud.remittance_send.send_email", new=AsyncMock()) as m:
        await rsend.send_groups(
            db_session, scope_kind=SCOPE_BATCH, scope_id=uuid.uuid4(), groups=[g],
            reference="R", payment_method="bank_transfer", company_name="C",
            sender=_sender(), actor_id=uuid.uuid4(), payment_date=date(2026, 7, 20))
    html = m.await_args.args[2]
    assert "2026-07-20" in html and "2026-07-22" not in html


async def test_send_endpoint_applies_the_operator_payment_date(client, db_session):
    await _configured(db_session)
    bp = await _vendor(db_session, remit="remit@acme.test")
    inv = await _invoice(db_session, "VINV-PD1")
    pa = _pa(bp.id, "42.00", [str(inv.id)])
    db_session.add(pa)
    await db_session.flush()
    rec = _record(pa)                      # payment_date recorded as 2026-07-22
    db_session.add(rec)
    await db_session.flush()

    with patch("app.crud.remittance_send.send_email", new=AsyncMock()) as m:
        r = await client.post(f"/finance/v1/payments/{rec.id}/remittance/send",
                              json={"recipients": None, "payment_date": "2026-07-20"},
                              headers=_h())
    assert r.status_code == 200 and r.json()["sent"] == 1
    assert "2026-07-20" in m.await_args.args[2]

    # The GL's own record of when the payment happened is untouched — this
    # field changes what the payee is told, never what finance has booked.
    await db_session.refresh(rec)
    assert rec.payment_date == date(2026, 7, 22)


async def test_send_endpoint_without_a_payment_date_uses_the_recorded_one(client, db_session):
    """Admission counterpart: omitting the field must leave the pre-existing
    behaviour exactly as it was, not blank the date or fall back to today."""
    await _configured(db_session)
    bp = await _vendor(db_session, remit="remit@acme.test")
    inv = await _invoice(db_session, "VINV-PD2")
    pa = _pa(bp.id, "42.00", [str(inv.id)])
    db_session.add(pa)
    await db_session.flush()
    rec = _record(pa)
    db_session.add(rec)
    await db_session.flush()

    with patch("app.crud.remittance_send.send_email", new=AsyncMock()) as m:
        r = await client.post(f"/finance/v1/payments/{rec.id}/remittance/send",
                              json={"recipients": None}, headers=_h())
    assert r.status_code == 200 and r.json()["sent"] == 1
    assert "2026-07-22" in m.await_args.args[2]


async def test_send_endpoint_refuses_a_future_payment_date_and_sends_nothing(client, db_session):
    """A remittance advice is only ever sent for a payment that has already
    completed, so a future date is a typo — and one the payee would read as a
    promise. Relative to today, never a hardcoded date that would decide this
    test's meaning by the calendar."""
    await _configured(db_session)
    bp = await _vendor(db_session, remit="remit@acme.test")
    inv = await _invoice(db_session, "VINV-PD3")
    pa = _pa(bp.id, "42.00", [str(inv.id)])
    db_session.add(pa)
    await db_session.flush()
    rec = _record(pa)
    db_session.add(rec)
    await db_session.flush()

    tomorrow = (date.today() + timedelta(days=1)).isoformat()
    with patch("app.crud.remittance_send.send_email", new=AsyncMock()) as m:
        r = await client.post(f"/finance/v1/payments/{rec.id}/remittance/send",
                              json={"recipients": None, "payment_date": tomorrow},
                              headers=_h())
    assert r.status_code == 400
    assert m.await_count == 0          # refused before anything left the building

    # ...and today itself is accepted — the guard rejects the future, not the
    # default the panel opens on.
    with patch("app.crud.remittance_send.send_email", new=AsyncMock()) as m:
        ok = await client.post(f"/finance/v1/payments/{rec.id}/remittance/send",
                               json={"recipients": None,
                                     "payment_date": date.today().isoformat()},
                               headers=_h())
    assert ok.status_code == 200 and ok.json()["sent"] == 1


async def test_selection_send_applies_the_operator_payment_date(client, db_session):
    """The selection scope carries the same field — it is a third send path,
    and a date that only worked on two of the three would be a trap."""
    await _configured(db_session)
    bp = await _vendor(db_session, remit="remit@acme.test")
    inv = await _invoice(db_session, "VINV-PD4")
    pa = _pa(bp.id, "42.00", [str(inv.id)])
    db_session.add(pa)
    await db_session.flush()
    rec = _record(pa)
    db_session.add(rec)
    await db_session.flush()

    with patch("app.crud.remittance_send.send_email", new=AsyncMock()) as m:
        r = await client.post("/finance/v1/payments/remittance/selection/send",
                              json={"payment_ids": [str(rec.id)], "recipients": None,
                                    "payment_date": "2026-07-20"},
                              headers=_h())
    assert r.status_code == 200 and r.json()["sent"] == 1
    assert "2026-07-20" in m.await_args.args[2]


# ── Confirmation preview: render exactly what send would send, send nothing ─
#
# Sending an advice is irreversible, so the panel no longer sends on one
# click: it first asks `.../remittance/render` for the emails and shows them,
# and only an explicit confirm calls `.../send`. render must therefore be
# (a) side-effect free and (b) byte-identical to what send delivers.


async def _one_payment(db, number):
    await _configured(db)
    bp = await _vendor(db, remit="remit@acme.test")
    inv = await _invoice(db, number)
    pa = _pa(bp.id, "42.00", [str(inv.id)])
    db.add(pa)
    await db.flush()
    rec = _record(pa)
    db.add(rec)
    await db.flush()
    return bp, rec


async def test_render_endpoint_returns_the_email_and_sends_nothing(client, db_session):
    _, rec = await _one_payment(db_session, "VINV-RD1")
    with patch("app.crud.remittance_send.send_email", new=AsyncMock()) as m:
        r = await client.post(f"/finance/v1/payments/{rec.id}/remittance/render",
                              json={"recipients": None, "payment_date": "2026-07-20"},
                              headers=_h())
    assert r.status_code == 200
    [email] = r.json()["emails"]
    assert email["status"] == "ready"
    assert email["to"] == "remit@acme.test"
    assert email["cc"] == "apbox@crm.test"
    assert email["from"] == "ap@crm.test"
    assert "VINV-RD1" in email["subject"]
    assert "VINV-RD1" in email["html"] and "2026-07-20" in email["html"]

    assert m.await_count == 0          # nothing left the building
    rows = (await db_session.execute(select(RemittanceNotification))).scalars().all()
    assert rows == []                  # and nothing claims it did


async def test_render_matches_what_send_then_delivers(client, db_session):
    """The whole point of the confirmation step: what AP approved is what the
    vendor receives — same To, Cc, subject and body."""
    _, rec = await _one_payment(db_session, "VINV-RD2")
    body = {"recipients": None, "payment_date": "2026-07-21"}
    [shown] = (await client.post(f"/finance/v1/payments/{rec.id}/remittance/render",
                                 json=body, headers=_h())).json()["emails"]
    with patch("app.crud.remittance_send.send_email", new=AsyncMock()) as m:
        r = await client.post(f"/finance/v1/payments/{rec.id}/remittance/send",
                              json=body, headers=_h())
    assert r.json()["sent"] == 1
    to, subject, html = m.await_args.args[:3]
    assert (to, subject, html) == (shown["to"], shown["subject"], shown["html"])
    assert m.await_args.kwargs["cc"] == shown["cc"]
    assert m.await_args.kwargs["smtp_from"] == shown["from"]


async def test_render_shows_already_sent_payee_as_skipped_unless_resend(client, db_session):
    """Same skip rule as send — otherwise the confirmation screen would show
    an email that send then silently refuses (or the reverse)."""
    bp, rec = await _one_payment(db_session, "VINV-RD3")
    with patch("app.crud.remittance_send.send_email", new=AsyncMock()):
        await client.post(f"/finance/v1/payments/{rec.id}/remittance/send",
                          json={"recipients": None}, headers=_h())

    [again] = (await client.post(f"/finance/v1/payments/{rec.id}/remittance/render",
                                 json={"recipients": None}, headers=_h())).json()["emails"]
    assert again["status"] == "skipped"
    assert again["error"] == "Already sent — resend not requested"
    assert "html" not in again

    resend = {"recipients": [{"recipient_kind": "vendor", "party_id": str(bp.id),
                              "resend": True}]}
    [forced] = (await client.post(f"/finance/v1/payments/{rec.id}/remittance/render",
                                  json=resend, headers=_h())).json()["emails"]
    assert forced["status"] == "ready" and forced["html"]


async def test_render_shows_blocked_payee_as_skipped(client, db_session):
    await _configured(db_session)
    bp = await _vendor(db_session, email="", remit=None)
    inv = await _invoice(db_session, "VINV-RD4")
    pa = _pa(bp.id, "42.00", [str(inv.id)])
    db_session.add(pa)
    await db_session.flush()
    rec = _record(pa)
    db_session.add(rec)
    await db_session.flush()
    [e] = (await client.post(f"/finance/v1/payments/{rec.id}/remittance/render",
                             json={"recipients": None}, headers=_h())).json()["emails"]
    assert e["status"] == "skipped" and e["error"] == "missing_email"


async def test_render_applies_the_same_gates_as_send(client, db_session):
    _, rec = await _one_payment(db_session, "VINV-RD5")
    url = f"/finance/v1/payments/{rec.id}/remittance/render"
    denied = await client.post(url, json={"recipients": None}, headers=_h("requester"))
    assert denied.status_code == 403
    tomorrow = (date.today() + timedelta(days=1)).isoformat()
    future = await client.post(url, json={"recipients": None, "payment_date": tomorrow},
                               headers=_h())
    assert future.status_code == 400
    # admission half: AP clerk (may send advice) may also preview it
    ok = await client.post(url, json={"recipients": None}, headers=_h("ap_clerk"))
    assert ok.status_code == 200 and ok.json()["emails"][0]["status"] == "ready"


async def test_render_batch_and_selection_scopes(client, db_session):
    """All three send paths have a confirmation preview — Payments (payment +
    selection) and Batch Payments (batch)."""
    await _configured(db_session)
    bp = await _vendor(db_session, remit="remit@acme.test")
    inv = await _invoice(db_session, "VINV-RD6")
    pa = _pa(bp.id, "42.00", [str(inv.id)])
    db_session.add(pa)
    await db_session.flush()
    batch = PaymentBatch(batch_number="BP-RD6", batch_date=date(2026, 7, 22),
                         status=EXECUTED, currency="CAD", total=Decimal("42.00"),
                         payment_method="bank_transfer", created_by=uuid.uuid4())
    db_session.add(batch)
    await db_session.flush()
    rec = _record(pa, batch_id=batch.id)
    db_session.add(rec)
    await db_session.flush()

    with patch("app.crud.remittance_send.send_email", new=AsyncMock()) as m:
        b = await client.post(f"/finance/v1/payments/batches/{batch.id}/remittance/render",
                              json={"recipients": None}, headers=_h())
        s = await client.post("/finance/v1/payments/remittance/selection/render",
                              json={"payment_ids": [str(rec.id)], "recipients": None},
                              headers=_h())
    assert m.await_count == 0
    [be] = b.json()["emails"]
    assert be["status"] == "ready" and "BP-RD6" in be["subject"]
    [se] = s.json()["emails"]
    assert se["status"] == "ready" and se["to"] == "remit@acme.test"
