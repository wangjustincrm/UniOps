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


# ── Task 8: sending and the send log ────────────────────────────────────────

from app.crud import remittance_send as rsend


def _sender():
    return rc.RemittanceSettings(
        enabled=True, from_email="ap@crm.test", from_name="CRM AP",
        cc_email="apbox@crm.test", smtp_host="h", smtp_port=587,
        smtp_user="u", smtp_password="p", smtp_use_tls=True,
    )


async def _send(db, groups, scope_id, side_effect=None):
    with patch("app.crud.remittance_send.send_email",
               new=AsyncMock(side_effect=side_effect)) as m:
        results = await rsend.send_groups(
            db, scope_kind=SCOPE_BATCH, scope_id=scope_id, groups=groups,
            reference="BP-20260722-0001", payment_method="bank_transfer",
            company_name="Canada Royal Milk", sender=_sender(),
            actor_id=uuid.uuid4(),
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
    g = _group()
    scope_id = uuid.uuid4()
    await _send(db_session, [g], scope_id)
    await _send(db_session, [g], scope_id)

    rows = (await db_session.execute(select(RemittanceNotification).where(
        RemittanceNotification.scope_id == scope_id))).scalars().all()
    assert len(rows) == 1
    assert rows[0].attempts == 2


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
    of the preview shape asserted here."""
    await _configured(db_session)
    bp = await _vendor(db_session, remit="remit@acme.test")
    inv = await _invoice(db_session, "VINV-23")
    pa = _pa(bp.id, "15.00", [str(inv.id)])
    db_session.add(pa)
    await db_session.flush()
    rec = _record(pa)
    db_session.add(rec)
    await db_session.flush()

    r = await client.get(f"/finance/v1/payments/{rec.id}/remittance/preview", headers=_h())
    assert r.status_code == 200
    body = r.json()
    assert "groups" in body
    assert "enabled" in body
    assert "id" not in body                # not the payment-detail payload
    assert body["reference"] == rec.pa_number
