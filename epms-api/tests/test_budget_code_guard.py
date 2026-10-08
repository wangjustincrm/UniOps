"""budget_code must be a budget account code — nothing else gets written.

`purchase_requests.budget_code`, `purchase_orders.budget_code` and
`purchase_agreements.budget_code` are plain VARCHAR with no foreign key to
budget-api's catalog, and until now nothing checked what went into them. The PR
form wrote "<code>-<name>" until 2026-07-17 and the PO form was a free-text box;
6,099 production rows had to be repaired on 2026-09-18 because an equality join
on that column silently matched almost nothing.

The pickers are fixed. These tests pin the API-side half, which is what an
importer, a script, or a stale client would go through.

Note the pairing: every "this is rejected" assertion has a "this is accepted"
twin. A validator that rejects everything satisfies the rejection test on its
own -- see the denial/admission rule in the team's notes.
"""
import httpx
import pytest

from app.services import budget_client


_CATALOG = [
    {"code": "CRM00901", "name": "Building Repairs & maintenance"},
    {"code": "CRM0090201", "name": "Machinery Spare Parts Supplies"},
]


def _patch_transport(monkeypatch, handler) -> None:
    """Route budget_client's own httpx.AsyncClient through a mock transport."""
    real = httpx.AsyncClient

    def factory(*args, **kwargs):
        kwargs["transport"] = httpx.MockTransport(handler)
        return real(*args, **kwargs)

    monkeypatch.setattr(budget_client.httpx, "AsyncClient", factory)


def _serving_catalog(monkeypatch) -> None:
    _patch_transport(monkeypatch, lambda request: httpx.Response(200, json=_CATALOG))


def _unreachable(monkeypatch) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("budget-api down", request=request)

    _patch_transport(monkeypatch, handler)


@pytest.mark.asyncio
async def test_known_code_is_accepted(monkeypatch):
    _serving_catalog(monkeypatch)
    assert await budget_client.account_code_is_known("t", "CRM00901") is True
    await budget_client.ensure_known_budget_code("t", "CRM00901")   # does not raise


@pytest.mark.asyncio
async def test_code_glued_to_its_name_is_rejected(monkeypatch):
    """The exact shape 6,099 rows were carrying."""
    _serving_catalog(monkeypatch)
    assert await budget_client.account_code_is_known(
        "t", "CRM0090201-Machinery Spare Parts Supplies") is False
    with pytest.raises(Exception) as exc:
        await budget_client.ensure_known_budget_code(
            "t", "CRM0090201-Machinery Spare Parts Supplies")
    assert exc.value.status_code == 422


@pytest.mark.asyncio
async def test_free_text_is_rejected(monkeypatch):
    """`NONE`, `PROJECT`, `Inventory - Technical Store` — all real values found
    in production, all typed into what used to be a free-text box."""
    _serving_catalog(monkeypatch)
    for junk in ("NONE", "PROJECT", "Inventory - Technical Store", "CRM003-01"):
        with pytest.raises(Exception) as exc:
            await budget_client.ensure_known_budget_code("t", junk)
        assert exc.value.status_code == 422, junk


@pytest.mark.asyncio
async def test_blank_is_accepted(monkeypatch):
    """The column is nullable and "no budget account" is a legitimate state —
    an NC-imported PO has never carried one."""
    _serving_catalog(monkeypatch)
    for blank in (None, ""):
        assert await budget_client.account_code_is_known("t", blank) is True
        await budget_client.ensure_known_budget_code("t", blank)


@pytest.mark.asyncio
async def test_unreachable_budget_api_cannot_be_read_as_empty(monkeypatch):
    """"Don't know" and "not in the catalog" must stay distinguishable.

    Collapse them and a budget-api outage stops every PO and PR from being
    saved — a strictly worse failure than the one the guard exists to prevent,
    and one the user cannot work around because the picker that supplies the
    value is filled from the same service.
    """
    _unreachable(monkeypatch)
    assert await budget_client.get_accounts("t") is None
    assert await budget_client.account_code_is_known("t", "CRM00901") is None
    assert await budget_client.account_code_is_known("t", "whatever junk") is None
    # Fail open: neither raises.
    await budget_client.ensure_known_budget_code("t", "CRM00901")
    await budget_client.ensure_known_budget_code("t", "whatever junk")


@pytest.mark.asyncio
async def test_catalog_is_fetched_once_per_window(monkeypatch):
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(200, json=_CATALOG)

    _patch_transport(monkeypatch, handler)
    await budget_client.ensure_known_budget_code("t", "CRM00901")
    await budget_client.ensure_known_budget_code("t", "CRM0090201")
    await budget_client.get_account_name("t", "CRM00901")
    assert calls["n"] == 1

    # ...and the seam the autouse fixture relies on actually drops it.
    budget_client.clear_accounts_cache()
    await budget_client.ensure_known_budget_code("t", "CRM00901")
    assert calls["n"] == 2


@pytest.mark.asyncio
async def test_a_failed_fetch_is_not_cached(monkeypatch):
    """Caching "unreachable" would pin the whole instance into fail-open for a
    minute after a single blip.

    One transport that flips, not two _patch_transport calls: monkeypatch
    replaces the module-global httpx.AsyncClient, so a second patch wraps the
    first and the inner factory's `transport=` wins. The service would look
    permanently unreachable and the test would fail for a reason that has
    nothing to do with the cache.
    """
    down = {"yes": True}

    def handler(request: httpx.Request) -> httpx.Response:
        if down["yes"]:
            raise httpx.ConnectError("budget-api down", request=request)
        return httpx.Response(200, json=_CATALOG)

    _patch_transport(monkeypatch, handler)
    assert await budget_client.get_accounts("t") is None

    down["yes"] = False
    assert await budget_client.account_code_is_known("t", "CRM00901") is True


# ── The guard is reachable through the endpoints, not just callable ──────────
#
# A validator nothing calls passes its own unit tests perfectly. These go
# through the real routes, because the routes are where the bad values arrived.

_VENDOR_URL = "/api/v1/vendors"
_PO_URL = "/api/v1/po"


async def _vendor(client, code):
    resp = await client.post(_VENDOR_URL, json={
        "code": code, "name": "Budget Guard Vendor", "category": "Services",
        "contact_name": "Bob", "contact_email": "bob@vendor.com",
        "payment_terms": "net30", "currency": "CAD",
    })
    assert resp.status_code == 201, resp.text
    return resp.json()


def _po_payload(vendor_id, budget_code):
    return {
        "title": "Guarded PO", "type": 2, "vendor_id": vendor_id,
        "currency": "CAD", "tax_rate": "0.13", "budget_code": budget_code,
        "line_items": [{
            "description": "Hydraulic Filter HF-200",
            "qty": "4", "unit": "EA", "unit_price": "45.00",
        }],
    }


@pytest.mark.asyncio
async def test_post_po_rejects_a_code_glued_to_its_name(admin_client, monkeypatch):
    _serving_catalog(monkeypatch)
    v = await _vendor(admin_client, "VND-BCG-01")
    resp = await admin_client.post(
        _PO_URL, json=_po_payload(v["id"], "CRM0090201-Machinery Spare Parts Supplies"))
    assert resp.status_code == 422, resp.text
    assert "not a budget account code" in resp.text


@pytest.mark.asyncio
async def test_post_po_accepts_the_bare_code(admin_client, monkeypatch):
    """The admission half. Without it, a guard that rejected every PO would
    still satisfy the test above."""
    _serving_catalog(monkeypatch)
    v = await _vendor(admin_client, "VND-BCG-02")
    resp = await admin_client.post(_PO_URL, json=_po_payload(v["id"], "CRM0090201"))
    assert resp.status_code == 201, resp.text
    assert resp.json()["budget_code"] == "CRM0090201"


@pytest.mark.asyncio
async def test_patch_po_rejects_a_bad_code(admin_client, monkeypatch):
    _serving_catalog(monkeypatch)
    v = await _vendor(admin_client, "VND-BCG-03")
    created = await admin_client.post(_PO_URL, json=_po_payload(v["id"], "CRM00901"))
    assert created.status_code == 201, created.text
    po_id = created.json()["id"]

    bad = await admin_client.patch(f"{_PO_URL}/{po_id}", json={"budget_code": "NONE"})
    assert bad.status_code == 422, bad.text

    # ...and an unrelated edit, which sends no budget_code at all, still goes
    # through — the guard must not turn every PATCH into a budget check.
    ok = await admin_client.patch(f"{_PO_URL}/{po_id}", json={"title": "Renamed"})
    assert ok.status_code == 200, ok.text
    assert ok.json()["budget_code"] == "CRM00901"
