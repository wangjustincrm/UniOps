"""Purchase Agreement — model, CRUD, approval, and match/PA integration."""
import uuid
from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.crud import user as user_crud
from app.models.agreement import PurchaseAgreement
from app.models.vendor import Vendor
from app.schemas.auth import RegisterRequest

pytestmark = pytest.mark.asyncio


async def test_agreement_model_roundtrip(test_engine):
    # NOTE: the `seeded_vendor` / `system_user_id` conftest fixtures insert via a
    # separate, uncommitted psycopg2 connection (pg_cur) — every existing use of
    # them (tests/test_nc_purchase_writer.py) reads back through that same
    # connection. This test writes through the async ORM session (test_engine)
    # instead, which is a *different* Postgres connection and would never see
    # those uncommitted rows (FK violation, confirmed empirically). So the
    # vendor/user rows are seeded in-session here, matching the pattern used by
    # tests/test_backfill_invoice_gr_links.py.
    factory = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as db:
        vendor = Vendor(
            code=f"V-{uuid.uuid4().hex[:8]}", name="Princess Auto", category="supplier",
            contact_name="AP Contact", contact_email="ap@princessauto.example",
        )
        db.add(vendor)
        user = await user_crud.create(db, RegisterRequest(
            email=f"agr-{uuid.uuid4().hex[:8]}@example.com", password="TestPass1!",
            full_name="Agreement Tester", role="procurement_officer",
        ))
        await db.flush()

        agr = PurchaseAgreement(
            number=f"AGR-202608-{uuid.uuid4().hex[:4]}",
            title="Princess Auto house account",
            agreement_type="house_account",
            contract_no="CN-2026-001",
            contact_email="ap@princessauto.example",
            vendor_id=vendor.id,
            vendor_name=vendor.name,
            vendor_reference="PO-585-2606-01",
            valid_from=date(2026, 1, 1),
            valid_to=date(2026, 12, 31),
            not_to_exceed=Decimal("50000.00"),
            created_by=user.id,
        )
        db.add(agr)
        await db.commit()
        await db.refresh(agr)

    async with factory() as db:
        got = (await db.execute(
            select(PurchaseAgreement).where(PurchaseAgreement.id == agr.id)
        )).scalar_one()

    assert got.status == "draft"                    # server default
    assert got.grace_days == 30                     # server default
    assert got.consumed_amount == Decimal("0")      # server default
    assert got.currency == "CAD"
    assert got.approval_step_idx == 0
    assert got.vendor_reference == "PO-585-2606-01"


async def seed_vendor_and_user(test_engine, vendor_name="Princess Auto"):
    """Seed a vendor + user through the ASYNC session and commit them.

    The conftest `seeded_vendor` / `system_user_id` fixtures write through an
    uncommitted psycopg2 connection the async engine cannot see; anything
    touching the ORM or the API needs committed rows on the same engine.
    Returns (vendor_id: uuid.UUID, vendor_name: str, user_id: uuid.UUID).
    """
    factory = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as db:
        vendor = Vendor(
            code=f"V-{uuid.uuid4().hex[:8]}", name=vendor_name, category="supplier",
            contact_name="AP Contact", contact_email="ap@example.com",
        )
        db.add(vendor)
        user = await user_crud.create(db, RegisterRequest(
            email=f"agr-{uuid.uuid4().hex[:8]}@example.com", password="TestPass1!",
            full_name="Agreement Tester", role="procurement_officer",
        ))
        await db.commit()
        await db.refresh(vendor)
        await db.refresh(user)
        return vendor.id, vendor.name, user.id


AGR_URL = "/api/v1/agreements"


def _agr_payload(vendor_id, **over):
    body = {
        "title": "Princess Auto house account",
        "agreement_type": "house_account",
        "vendor_id": str(vendor_id),
        "vendor_reference": "PO-585-2606-01",
        "valid_from": "2026-01-01",
        "valid_to": "2026-12-31",
        "not_to_exceed": "50000.00",
        "currency": "CAD",
        "tax_rate": "0.13",
        "tax_code": "HST13",
    }
    body.update(over)
    return body


async def test_create_agreement_allocates_number_and_defaults(admin_client, test_engine):
    vendor_id, vendor_name, _ = await seed_vendor_and_user(test_engine)
    r = await admin_client.post(AGR_URL, json=_agr_payload(vendor_id))
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["number"].startswith("AGR-")
    assert len(body["number"]) == len("AGR-202608-0001")
    assert body["status"] == "draft"
    assert body["vendor_name"] == vendor_name
    assert body["consumed_amount"] == "0.00"
    assert body["grace_days"] == 30


async def test_agreement_numbers_are_sequential(admin_client, test_engine):
    vendor_id, _, _ = await seed_vendor_and_user(test_engine)
    a = (await admin_client.post(AGR_URL, json=_agr_payload(vendor_id))).json()
    b = (await admin_client.post(AGR_URL, json=_agr_payload(vendor_id))).json()
    assert int(b["number"].rsplit("-", 1)[1]) == int(a["number"].rsplit("-", 1)[1]) + 1


async def test_create_agreement_rejects_inverted_validity(admin_client, test_engine):
    vendor_id, _, _ = await seed_vendor_and_user(test_engine)
    r = await admin_client.post(AGR_URL, json=_agr_payload(
        vendor_id, valid_from="2026-12-31", valid_to="2026-01-01"))
    assert r.status_code == 422
    assert "valid_to" in r.text


async def test_create_agreement_unknown_vendor_404(admin_client):
    r = await admin_client.post(AGR_URL, json=_agr_payload(str(uuid.uuid4())))
    assert r.status_code == 404


async def test_get_and_list_agreement(admin_client, test_engine):
    vendor_id, _, _ = await seed_vendor_and_user(test_engine)
    created = (await admin_client.post(AGR_URL, json=_agr_payload(vendor_id))).json()

    got = await admin_client.get(f"{AGR_URL}/{created['id']}")
    assert got.status_code == 200
    assert got.json()["number"] == created["number"]

    listed = await admin_client.get(AGR_URL, params={"vendor_id": str(vendor_id)})
    assert listed.status_code == 200
    assert created["id"] in [i["id"] for i in listed.json()["items"]]


async def test_patch_agreement_only_in_draft(admin_client, test_engine):
    vendor_id, _, _ = await seed_vendor_and_user(test_engine)
    created = (await admin_client.post(AGR_URL, json=_agr_payload(vendor_id))).json()

    ok = await admin_client.patch(f"{AGR_URL}/{created['id']}", json={"title": "Renamed"})
    assert ok.status_code == 200
    assert ok.json()["title"] == "Renamed"

    # Force the agreement past draft, then the same edit must be refused.
    factory = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as db:
        agr = (await db.execute(select(PurchaseAgreement).where(
            PurchaseAgreement.id == uuid.UUID(created["id"])))).scalar_one()
        agr.status = "active"
        await db.commit()

    blocked = await admin_client.patch(f"{AGR_URL}/{created['id']}", json={"title": "Nope"})
    assert blocked.status_code == 409


async def test_agreement_action_endpoint_delegates(admin_client, test_engine, monkeypatch):
    vendor_id, _, _ = await seed_vendor_and_user(test_engine)
    created = (await admin_client.post(AGR_URL, json=_agr_payload(vendor_id))).json()

    calls = []

    async def _fake_delegate(doc_type, doc_id, action, comment, token):
        calls.append((doc_type, doc_id, action))
        return {"status": "in_review", "step_idx": 1}

    monkeypatch.setattr("app.api.v1.agreements.delegate_action", _fake_delegate)

    r = await admin_client.post(f"{AGR_URL}/{created['id']}/action",
                                json={"action": "submit", "comment": None})
    assert r.status_code == 200, r.text
    assert calls == [("agr", created["id"], "submit")]


async def test_agreement_action_unknown_id_404(admin_client):
    r = await admin_client.post(f"{AGR_URL}/{uuid.uuid4()}/action", json={"action": "submit"})
    assert r.status_code == 404
