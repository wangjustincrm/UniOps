"""Object-level authz (IDOR fix) tests for PA read endpoints.

Locks in Task A1: GET /pa/{id}, GET /pa/{id}/history, GET /pa/by-po/{po_id}
previously only checked login (`_: CurrentUserDep`), not ownership/participation
— any logged-in employee could read another employee's PA. These tests verify
`_can_view_pa` (pa.py) is now enforced: 403 for unrelated users, 200 for the
owner, and per-item filtering on the by-po list endpoint.
"""
import uuid
from decimal import Decimal

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from tests.conftest import _client, _make_token
from app.models.pa import PaymentApplication


async def _seed_pa(test_engine, created_by: uuid.UUID, status: str = "submitted",
                    po_id: uuid.UUID | None = None) -> uuid.UUID:
    factory = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
    pa_id = uuid.uuid4()
    async with factory() as s:
        s.add(PaymentApplication(
            id=pa_id, pa_number=f"PA-T-{pa_id.hex[:8]}", title="Test PA",
            vendor_id=uuid.uuid4(), vendor_name="Test Vendor",
            subtotal=Decimal("100.00"), payment_amount=Decimal("100.00"),
            currency="CAD", status=status, created_by=created_by, po_id=po_id,
        ))
        await s.commit()
    return pa_id


@pytest.mark.asyncio
async def test_get_pa_forbidden_for_unrelated_user(test_engine):
    pa_id = await _seed_pa(test_engine, created_by=uuid.uuid4())
    async with _client(_make_token("requester", str(uuid.uuid4()))) as c:
        r = await c.get(f"/api/v1/pa/{pa_id}")
    assert r.status_code == 403


@pytest.mark.asyncio
async def test_get_pa_ok_for_owner(test_engine):
    owner = uuid.uuid4()
    pa_id = await _seed_pa(test_engine, created_by=owner)
    async with _client(_make_token("requester", str(owner))) as c:
        r = await c.get(f"/api/v1/pa/{pa_id}")
    assert r.status_code == 200


@pytest.mark.asyncio
async def test_get_pa_history_forbidden_for_unrelated_user(test_engine):
    pa_id = await _seed_pa(test_engine, created_by=uuid.uuid4())
    async with _client(_make_token("requester", str(uuid.uuid4()))) as c:
        r = await c.get(f"/api/v1/pa/{pa_id}/history")
    assert r.status_code == 403


@pytest.mark.asyncio
async def test_by_po_filters_out_unrelated_pas(test_engine):
    po_id = uuid.uuid4()
    await _seed_pa(test_engine, created_by=uuid.uuid4(), po_id=po_id)  # someone else's
    async with _client(_make_token("requester", str(uuid.uuid4()))) as c:
        r = await c.get(f"/api/v1/pa/by-po/{po_id}")
    assert r.status_code == 200
    assert r.json()["total"] == 0
