"""PRD-OA §2.3 — GET /api/v1/vendors: server-side ILIKE search on EpmsVendor."""
import uuid
import pytest

import app.db.base as db_module
from app.models.epms_mirrors import EpmsVendor


async def _seed_vendor(name: str, code: str, active: bool = True) -> EpmsVendor:
    async with db_module.AsyncSessionLocal() as db:
        v = EpmsVendor(id=uuid.uuid4(), name=name, code=code, is_active=active)
        db.add(v)
        await db.commit()
        return v


@pytest.mark.asyncio
async def test_list_vendors_empty(admin_client):
    """Before seeding, returns empty list — not an error."""
    resp = await admin_client.get("/api/v1/vendors")
    assert resp.status_code == 200
    assert "items" in resp.json()


@pytest.mark.asyncio
async def test_list_vendors_with_data(admin_client):
    await _seed_vendor("Titan Power Ltd", "TIT-001")
    resp = await admin_client.get("/api/v1/vendors?search=titan")
    assert resp.status_code == 200
    names = [v["name"] for v in resp.json()["items"]]
    assert any("Titan" in n for n in names)


@pytest.mark.asyncio
async def test_vendor_search_partial_match(admin_client):
    await _seed_vendor("Northern Dairy Supplies", "NDS-001")
    resp = await admin_client.get("/api/v1/vendors?search=dairy")
    assert resp.status_code == 200
    assert resp.json()["total"] >= 1


@pytest.mark.asyncio
async def test_vendor_search_case_insensitive(admin_client):
    await _seed_vendor("Acme Packaging", "ACM-001")
    resp = await admin_client.get("/api/v1/vendors?search=ACME")
    assert resp.status_code == 200
    assert resp.json()["total"] >= 1


@pytest.mark.asyncio
async def test_vendor_active_only_default(admin_client):
    """active_only=true is the default; inactive vendors must be excluded."""
    await _seed_vendor("OldVendorInactive", "OVI-001", active=False)
    resp = await admin_client.get("/api/v1/vendors?search=OldVendorInactive")
    assert resp.status_code == 200
    assert resp.json()["total"] == 0


@pytest.mark.asyncio
async def test_vendor_unauthenticated(client):
    resp = await client.get("/api/v1/vendors")
    assert resp.status_code == 403
