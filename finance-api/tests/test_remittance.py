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
from app.db.base import get_db
from app.main import app
from app.models.mirrors import BusinessPartner, CompanyConfig
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
