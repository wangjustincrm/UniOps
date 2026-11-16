"""ISS-0268: a vendor added or edited by hand that looks like an existing one
(same name ignoring case/spaces, or same ERP ID) is reported so the Vendors
page can warn — not refuse (VND-001 checks only the POID)."""
import uuid

import pytest

URL = "/api/v1/vendors"


async def _create(client, **kw):
    body = {"code": f"SIM-{uuid.uuid4().hex[:6].upper()}", "name": f"Vendor {uuid.uuid4().hex[:6]}",
            "category": "Parts", "contact_name": "X", "contact_email": "x@x.com",
            "payment_terms": "net30", "currency": "CAD", **kw}
    r = await client.post(URL, json=body)
    assert r.status_code == 201, r.text
    return r.json()


@pytest.mark.asyncio
async def test_same_name_ignoring_case_and_spaces_and_same_erp_id(admin_client):
    tag = uuid.uuid4().hex[:6]
    als = await _create(admin_client, code=f"ALS{tag}".upper(), name=f"Atlas Lighting & Sales Ltd. {tag}",
                        erp_id=f"0001155{tag}")

    r = await admin_client.get(f"{URL}/similar", params={
        "name": f"  atlas lighting &  SALES ltd. {tag.upper()} ", "erp_id": f" 0001155{tag.upper()} "})
    assert r.status_code == 200, r.text
    d = r.json()
    assert [v["code"] for v in d["name_matches"]] == [als["code"]]
    assert [v["code"] for v in d["erp_id_matches"]] == [als["code"]]

    # Editing the vendor itself is not a duplicate of itself.
    r = await admin_client.get(f"{URL}/similar", params={
        "name": als["name"], "erp_id": als["erp_id"], "exclude_id": als["id"]})
    assert r.json() == {"name_matches": [], "erp_id_matches": []}

    # A different name and ERP ID: nothing.
    r = await admin_client.get(f"{URL}/similar", params={"name": f"Atlas Lighting {tag}", "erp_id": "999"})
    assert r.json() == {"name_matches": [], "erp_id_matches": []}


@pytest.mark.asyncio
async def test_an_inactive_duplicate_still_counts(admin_client):
    tag = uuid.uuid4().hex[:6]
    v = await _create(admin_client, name=f"Dormant Co {tag}")
    await admin_client.patch(f"{URL}/{v['id']}", json={"is_active": False})
    d = (await admin_client.get(f"{URL}/similar", params={"name": f"dormant co {tag}"})).json()
    assert [(m["code"], m["is_active"]) for m in d["name_matches"]] == [(v["code"], False)]


@pytest.mark.asyncio
async def test_a_duplicate_name_is_still_saved(admin_client):
    """Warning only: the server does not refuse it."""
    tag = uuid.uuid4().hex[:6]
    await _create(admin_client, name=f"Twin Ltd {tag}", erp_id=f"E{tag}")
    await _create(admin_client, name=f"Twin Ltd {tag}", erp_id=f"E{tag}")


@pytest.mark.asyncio
async def test_needs_vendor_master(requester_client):
    r = await requester_client.get(f"{URL}/similar", params={"name": "x"})
    assert r.status_code == 403
