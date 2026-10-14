"""Every endpoint's permission key, exercised with a NON-admin token.

system_admin short-circuits every gate, so a suite that only uses it would
pass with misspelled keys. Here the role matrix is faked: the caller holds
exactly the keys a test grants, and both sides are asserted — denied without
the key AND admitted with it (an all-403 run would otherwise "pass").
"""
import pytest

READS = [
    ("get", "/api/v1/admin/nc-sync/status", None),
    ("get", "/api/v1/admin/wms-sync/status", None),
    ("get", "/api/v1/admin/nc-sync/tieout-issues", None),
    ("get", "/api/v1/settings/params", None),
    ("get", "/api/v1/settings/warehouse-classes", None),
    ("get", "/api/v1/settings/no-batch-materials", None),
]
WRITES = [
    ("put", "/api/v1/settings/params/nc_sync_interval_minutes", {"value": 30}),
    ("put", "/api/v1/settings/warehouse-classes/SC99", {"warehouse_class": "external"}),
    ("put", "/api/v1/settings/no-batch-materials/CF0099", {"since_date": "2026-01-01"}),
    ("delete", "/api/v1/settings/no-batch-materials/CF0099", None),
]


def _grant(monkeypatch, *keys):
    async def codes(db, user_id, base_role):
        return {"tester"}

    async def matrix(db):
        return {"tester": set(keys)}

    monkeypatch.setattr("uniops_authz.core.user_role_codes", codes)
    monkeypatch.setattr("uniops_authz.core._effective_matrix", matrix)


async def _call(client, token, method, url, body):
    kw = {"headers": {"Authorization": f"Bearer {token}"}}
    if body is not None:
        kw["json"] = body
    return await getattr(client, method)(url, **kw)


@pytest.mark.parametrize("method,url,body", READS)
async def test_reads_need_inventory_view(client, non_admin_token, monkeypatch, method, url, body):
    _grant(monkeypatch, "mrp.report.view")  # an unrelated key must not open the door
    assert (await _call(client, non_admin_token, method, url, body)).status_code == 403
    _grant(monkeypatch, "inventory.view")
    assert (await _call(client, non_admin_token, method, url, body)).status_code == 200


@pytest.mark.parametrize("method,url,body", WRITES + [("post", "/api/v1/admin/nc-sync", None),
                                                   ("post", "/api/v1/admin/wms-sync", None)])
async def test_writes_need_inventory_admin(client, non_admin_token, monkeypatch, method, url, body):
    monkeypatch.setattr("app.api.v1.admin_sync.nc_configured", lambda: False)
    monkeypatch.setattr("app.services.wms_sync.service.wms_configured", lambda: False)
    _grant(monkeypatch, "inventory.view", "inventory.cost.view")
    assert (await _call(client, non_admin_token, method, url, body)).status_code == 403
    _grant(monkeypatch, "inventory.admin")
    r = await _call(client, non_admin_token, method, url, body)
    # Admitted: 200/204, or 503 for the sync trigger with NC unconfigured.
    assert r.status_code in (200, 204, 503), r.text


async def test_tieout_amount_rows_need_cost_view(client, non_admin_token, monkeypatch, db_session):
    """A tie-out finding measured in money is an amount (design §9)."""
    from decimal import Decimal

    from sqlalchemy import insert

    from app.models import InvTieoutIssue
    await db_session.execute(insert(InvTieoutIssue), [
        {"check_name": "gl_lines_vs_balance", "measure": "amount", "key": "2026|1404",
         "label": "1404 · 2026", "mirror_value": Decimal("10"), "nc_value": Decimal("11")},
        {"check_name": "ic_flow_vs_onhand", "measure": "qty", "key": "k", "label": "SC11 · X",
         "mirror_value": Decimal("1"), "nc_value": Decimal("2")},
    ])
    await db_session.commit()
    h = {"Authorization": f"Bearer {non_admin_token}"}
    url = "/api/v1/admin/nc-sync/tieout-issues"

    _grant(monkeypatch, "inventory.view")
    body = (await client.get(url, headers=h)).json()
    assert [i["measure"] for i in body["items"]] == ["qty"]
    assert body["amount_issues_hidden"] == 1 and body["amounts_visible"] is False
    assert "gl_lines_vs_balance" not in body["by_check"]

    _grant(monkeypatch, "inventory.view", "inventory.cost.view")
    body = (await client.get(url, headers=h)).json()
    assert sorted(i["measure"] for i in body["items"]) == ["amount", "qty"]
    assert body["amount_issues_hidden"] == 0
