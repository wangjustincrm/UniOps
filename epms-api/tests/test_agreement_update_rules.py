"""Purchase Agreement — update()-time re-validation against the MERGED state,
milestone replace-vs-clear semantics, and the claimed-stage guard.

Split out from tests/test_agreement_recurrence_schema.py (pure pydantic
schema checks on AgreementCreate, no DB) because everything here needs a
persisted agreement and the actual PATCH code path — a schema-level
model_validator on AgreementUpdate cannot see the merged state a partial
PATCH body produces (task-3 review Finding 1).
"""
import uuid
from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.crud import agreement as agr_crud
from app.crud import agreement_schedule
from app.crud import user as user_crud
from app.models.vendor import Vendor
from app.schemas.agreement import AgreementCreate, AgreementUpdate, MilestoneRowIn
from app.schemas.auth import RegisterRequest

pytestmark = pytest.mark.asyncio

AGR_URL = "/api/v1/agreements"


async def _seed_vendor_and_user(test_engine, vendor_name="Bell Canada"):
    """Seed a vendor + user through the ASYNC session and commit them.

    The conftest `seeded_vendor` / `system_user_id` fixtures write through an
    uncommitted psycopg2 connection the async engine cannot see (mirrors
    tests/test_agreements.py::seed_vendor_and_user).
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


def _agr_payload(vendor_id, **over):
    body = dict(
        title="Bell circuit", agreement_type="house_account", vendor_id=str(vendor_id),
        valid_from="2026-01-01", valid_to="2026-12-31",
    )
    body.update(over)
    return body


# ── Finding 1: PATCH must re-run the same coherence rules as create() ──────
# Each test creates a CLEAN agreement (would pass AgreementCreate), then
# PATCHes it into exactly one of the four states Finding 1 named as a bypass.

async def test_patch_cannot_strand_quarterly_without_anchor_month(admin_client, test_engine):
    vendor_id, _, _ = await _seed_vendor_and_user(test_engine)
    created = (await admin_client.post(AGR_URL, json=_agr_payload(
        vendor_id, agreement_type="recurring",
        recurring_type="monthly", expected_invoice_day=5))).json()

    r = await admin_client.patch(
        f"{AGR_URL}/{created['id']}", json={"recurring_type": "quarterly"})
    assert r.status_code == 409, r.text
    assert "anchor_month" in r.text


async def test_patch_cannot_push_weekly_invoice_day_above_7(admin_client, test_engine):
    vendor_id, _, _ = await _seed_vendor_and_user(test_engine)
    created = (await admin_client.post(AGR_URL, json=_agr_payload(
        vendor_id, agreement_type="recurring",
        recurring_type="weekly", expected_invoice_day=1))).json()

    r = await admin_client.patch(
        f"{AGR_URL}/{created['id']}", json={"expected_invoice_day": 15})
    assert r.status_code == 409, r.text
    assert "1..7" in r.text


async def test_patch_cannot_extend_valid_to_past_the_row_cap(admin_client, test_engine):
    vendor_id, _, _ = await _seed_vendor_and_user(test_engine)
    created = (await admin_client.post(AGR_URL, json=_agr_payload(
        vendor_id, agreement_type="recurring", recurring_type="weekly",
        expected_invoice_day=1, valid_to="2026-12-31"))).json()

    r = await admin_client.patch(
        f"{AGR_URL}/{created['id']}", json={"valid_to": "2040-01-01"})
    assert r.status_code == 409, r.text
    assert "500" in r.text


async def test_patch_cannot_set_amount_pct_stage_without_a_ceiling(admin_client, test_engine):
    vendor_id, _, _ = await _seed_vendor_and_user(test_engine)
    created = (await admin_client.post(
        AGR_URL, json=_agr_payload(vendor_id, agreement_type="milestone"))).json()
    assert created["not_to_exceed"] is None

    r = await admin_client.patch(f"{AGR_URL}/{created['id']}", json={
        "milestones": [{"milestone_name": "Deposit", "amount_pct": "30"}],
    })
    assert r.status_code == 409, r.text
    assert "not_to_exceed" in r.text


# ── Finding 3: milestones None vs [] semantics + the claimed-stage guard ───

async def _seed_milestone_agreement(db, *, not_to_exceed=Decimal("50000.00")):
    vendor = Vendor(
        code=f"V-{uuid.uuid4().hex[:8]}", name="Bell Canada", category="supplier",
        contact_name="AP", contact_email="ap@bell.example",
    )
    db.add(vendor)
    user = await user_crud.create(db, RegisterRequest(
        email=f"agr-{uuid.uuid4().hex[:8]}@example.com", password="TestPass1!",
        full_name="Agreement Tester", role="procurement_officer",
    ))
    await db.flush()
    body = AgreementCreate(
        title="Install project", agreement_type="milestone", vendor_id=vendor.id,
        valid_from=date(2026, 1, 1), valid_to=date(2026, 12, 31),
        not_to_exceed=not_to_exceed,
        milestones=[MilestoneRowIn(
            milestone_name="Deposit on signing", expected_timing="On signing",
            expected_amount=Decimal("15000.00"))],
    )
    return await agr_crud.create(db, body, vendor_name=vendor.name, created_by=user.id)


async def test_update_with_milestones_none_leaves_existing_stages_untouched(test_engine):
    factory = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as db:
        agr = await _seed_milestone_agreement(db)
        assert len(await agreement_schedule.list_rows(db, agr.id)) == 1

        await agr_crud.update(db, agr, AgreementUpdate(title="Renamed"))

        after = await agreement_schedule.list_rows(db, agr.id)
        assert len(after) == 1
        assert after[0].milestone_name == "Deposit on signing"


async def test_update_with_milestones_empty_list_clears_stages(test_engine):
    factory = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as db:
        agr = await _seed_milestone_agreement(db)
        assert len(await agreement_schedule.list_rows(db, agr.id)) == 1

        await agr_crud.update(db, agr, AgreementUpdate(milestones=[]))

        assert await agreement_schedule.list_rows(db, agr.id) == []


async def test_replace_milestone_rows_refuses_to_wipe_a_claimed_stage(test_engine):
    factory = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as db:
        agr = await _seed_milestone_agreement(db)
        rows = await agreement_schedule.list_rows(db, agr.id)
        rows[0].invoice_id = uuid.uuid4()
        await db.commit()

        with pytest.raises(ValueError, match="already have an invoice matched"):
            await agreement_schedule.replace_milestone_rows(
                db, agr, [MilestoneRowIn(milestone_name="Replacement stage")])

        # And the row must still be there — the raise happens before any delete.
        assert len(await agreement_schedule.list_rows(db, agr.id)) == 1


async def test_patch_maps_claimed_milestone_conflict_to_409_not_500(admin_client, test_engine):
    factory = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as db:
        agr = await _seed_milestone_agreement(db)
        rows = await agreement_schedule.list_rows(db, agr.id)
        rows[0].invoice_id = uuid.uuid4()
        await db.commit()
        agr_id = agr.id

    r = await admin_client.patch(f"{AGR_URL}/{agr_id}", json={
        "milestones": [{"milestone_name": "New stage"}],
    })
    assert r.status_code == 409, r.text
    assert "already have an invoice matched" in r.text
