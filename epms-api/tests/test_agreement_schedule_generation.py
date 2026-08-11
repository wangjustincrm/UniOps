"""排期行在协议转 active 时生成,且必须幂等。"""
import uuid
from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.crud import agreement_schedule as sched_crud
from app.crud import user as user_crud
from app.models.agreement import PurchaseAgreement
from app.models.agreement_schedule import AgreementPaymentSchedule
from app.models.vendor import Vendor
from app.schemas.auth import RegisterRequest

pytestmark = pytest.mark.asyncio


async def _seed(db, **over):
    """种子必须建在同一个 async session 里 —— conftest 的 seeded_vendor 走的是
    另一条未提交的 psycopg2 连接,async engine 看不见(FK 违约)。"""
    vendor = Vendor(code=f"V-{uuid.uuid4().hex[:8]}", name="Bell", category="supplier",
                    contact_name="AP", contact_email="ap@bell.example")
    db.add(vendor)
    user = await user_crud.create(db, RegisterRequest(
        email=f"agr-{uuid.uuid4().hex[:8]}@example.com", password="TestPass1!",
        full_name="T", role="procurement_officer"))
    await db.flush()
    kw = dict(
        number=f"AGR-202608-{uuid.uuid4().hex[:4]}", title="Bell", agreement_type="recurring",
        vendor_id=vendor.id, vendor_name=vendor.name,
        valid_from=date(2026, 1, 1), valid_to=date(2026, 3, 31),
        recurring_type="monthly", expected_invoice_day=5,
        expected_amount_per_period=Decimal("1200.00"), tolerance_pct=Decimal("5.00"),
        overdue_after_days=7, status="active", created_by=user.id,
    )
    kw.update(over)
    agr = PurchaseAgreement(**kw)
    db.add(agr)
    await db.flush()
    return agr


async def test_generates_one_row_per_period_with_agreement_defaults(test_engine):
    factory = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as db:
        agr = await _seed(db)
        assert await sched_crud.ensure_period_rows(db, agr) == 3
        await db.commit()
        rows = (await db.execute(
            select(AgreementPaymentSchedule)
            .where(AgreementPaymentSchedule.agreement_id == agr.id)
            .order_by(AgreementPaymentSchedule.sequence)
        )).scalars().all()
        assert [r.period_label for r in rows] == ["2026-01", "2026-02", "2026-03"]
        assert all(r.expected_amount == Decimal("1200.00") for r in rows)
        assert all(r.tolerance_pct == Decimal("5.00") for r in rows)
        assert all(r.overdue_after_days == 7 for r in rows)
        assert all(r.status == "pending" for r in rows)
        assert all(r.invoice_id is None for r in rows)


async def test_is_idempotent(test_engine):
    factory = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as db:
        agr = await _seed(db)
        assert await sched_crud.ensure_period_rows(db, agr) == 3
        assert await sched_crud.ensure_period_rows(db, agr) == 0
        await db.commit()
        rows = (await db.execute(
            select(AgreementPaymentSchedule)
            .where(AgreementPaymentSchedule.agreement_id == agr.id)
            .order_by(AgreementPaymentSchedule.sequence)
        )).scalars().all()
        # A bug that inserted duplicate rows while still returning 0 from the
        # second call would slip past an assertion on the return value alone —
        # the resync path replays this call, and a doubled schedule means
        # every period gets claimed twice. Assert the DB state directly.
        assert len(rows) == 3
        assert [r.sequence for r in rows] == [1, 2, 3]


async def test_non_recurring_agreement_generates_nothing(test_engine):
    factory = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as db:
        agr = await _seed(db, agreement_type="house_account", recurring_type=None,
                          expected_invoice_day=None, expected_amount_per_period=None,
                          tolerance_pct=None)
        assert await sched_crud.ensure_period_rows(db, agr) == 0
        await db.commit()
        rows = (await db.execute(select(AgreementPaymentSchedule).where(
            AgreementPaymentSchedule.agreement_id == agr.id))).scalars().all()
        assert rows == []


async def test_null_per_period_amount_leaves_rows_without_an_amount(test_engine):
    # 决策 3:每期金额选填。不填 → 排期行不带金额,认领时不做金额校验。
    factory = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as db:
        agr = await _seed(db, expected_amount_per_period=None)
        await sched_crud.ensure_period_rows(db, agr)
        await db.commit()
        rows = (await db.execute(select(AgreementPaymentSchedule).where(
            AgreementPaymentSchedule.agreement_id == agr.id))).scalars().all()
        assert rows and all(r.expected_amount is None for r in rows)


# ── Endpoint-level tests ──────────────────────────────────────────────────
# The CRUD-level tests above prove ensure_period_rows works in isolation.
# They do NOT prove the /agreements/{id}/action endpoint actually calls it —
# deleting the `if agr.status == "active":` hook in agreements.py leaves all
# of the above green. These go through the real HTTP endpoint stack instead.

AGR_URL = "/api/v1/agreements"


async def _seed_vendor_and_user(test_engine) -> uuid.UUID:
    """Seed + COMMIT a vendor/user through the async engine so the running
    app (a separate connection under admin_client) can see them — same
    rationale as the module docstring on _seed above."""
    factory = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as db:
        vendor = Vendor(code=f"V-{uuid.uuid4().hex[:8]}", name="Bell", category="supplier",
                        contact_name="AP", contact_email="ap@bell.example")
        db.add(vendor)
        await user_crud.create(db, RegisterRequest(
            email=f"agr-{uuid.uuid4().hex[:8]}@example.com", password="TestPass1!",
            full_name="T", role="procurement_officer"))
        await db.commit()
        await db.refresh(vendor)
        return vendor.id


def _recurring_payload(vendor_id: uuid.UUID, **over) -> dict:
    body = {
        "title": "Bell recurring",
        "agreement_type": "recurring",
        "vendor_id": str(vendor_id),
        "valid_from": "2026-01-01",
        "valid_to": "2026-03-31",
        "currency": "CAD",
        "recurring_type": "monthly",
        "expected_invoice_day": 5,
        "expected_amount_per_period": "1200.00",
        "tolerance_pct": "5.00",
        "overdue_after_days": 7,
    }
    body.update(over)
    return body


def _house_account_payload(vendor_id: uuid.UUID, **over) -> dict:
    body = {
        "title": "Bell house account",
        "agreement_type": "house_account",
        "vendor_id": str(vendor_id),
        "valid_from": "2026-01-01",
        "valid_to": "2026-12-31",
        "currency": "CAD",
    }
    body.update(over)
    return body


def _make_fake_delegate_to_active(test_engine):
    """A delegate_action stand-in that actually flips the row to active in the
    DB (unlike test_agreements.py::test_agreement_action_endpoint_delegates's
    fake, which is DB-inert and leaves the agreement in draft — that's why
    that pre-existing test is unaffected by the schedule-generation hook)."""
    async def _fake(doc_type, doc_id, action, comment, token):
        factory = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
        async with factory() as db:
            agr = (await db.execute(select(PurchaseAgreement).where(
                PurchaseAgreement.id == uuid.UUID(doc_id)))).scalar_one()
            agr.status = "active"
            await db.commit()
        return {"status": "active", "step_idx": 3}
    return _fake


async def test_action_endpoint_generates_schedule_for_recurring_agreement(
    admin_client, test_engine, monkeypatch
):
    vendor_id = await _seed_vendor_and_user(test_engine)
    created = (await admin_client.post(AGR_URL, json=_recurring_payload(vendor_id))).json()

    monkeypatch.setattr(
        "app.api.v1.agreements.delegate_action", _make_fake_delegate_to_active(test_engine))

    r = await admin_client.post(f"{AGR_URL}/{created['id']}/action",
                                json={"action": "approve", "comment": None})
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "active"

    factory = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as db:
        rows = (await db.execute(
            select(AgreementPaymentSchedule)
            .where(AgreementPaymentSchedule.agreement_id == uuid.UUID(created["id"]))
            .order_by(AgreementPaymentSchedule.sequence)
        )).scalars().all()
    assert [row.period_label for row in rows] == ["2026-01", "2026-02", "2026-03"]


async def test_action_endpoint_generates_no_schedule_for_non_recurring_agreement(
    admin_client, test_engine, monkeypatch
):
    vendor_id = await _seed_vendor_and_user(test_engine)
    created = (await admin_client.post(AGR_URL, json=_house_account_payload(vendor_id))).json()

    monkeypatch.setattr(
        "app.api.v1.agreements.delegate_action", _make_fake_delegate_to_active(test_engine))

    r = await admin_client.post(f"{AGR_URL}/{created['id']}/action",
                                json={"action": "approve", "comment": None})
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "active"

    factory = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as db:
        rows = (await db.execute(select(AgreementPaymentSchedule).where(
            AgreementPaymentSchedule.agreement_id == uuid.UUID(created["id"])))).scalars().all()
    assert rows == []


async def test_get_schedule_endpoint_returns_rows_and_404s_for_unknown_agreement(
    admin_client, test_engine, monkeypatch
):
    vendor_id = await _seed_vendor_and_user(test_engine)
    created = (await admin_client.post(AGR_URL, json=_recurring_payload(vendor_id))).json()

    monkeypatch.setattr(
        "app.api.v1.agreements.delegate_action", _make_fake_delegate_to_active(test_engine))
    await admin_client.post(f"{AGR_URL}/{created['id']}/action",
                            json={"action": "approve", "comment": None})

    got = await admin_client.get(f"{AGR_URL}/{created['id']}/schedule")
    assert got.status_code == 200
    items = got.json()["items"]
    assert [i["period_label"] for i in items] == ["2026-01", "2026-02", "2026-03"]

    missing = await admin_client.get(f"{AGR_URL}/{uuid.uuid4()}/schedule")
    assert missing.status_code == 404
