"""Capacity rules master data (Phase 1B Task 1).

`resolve_effective_rules` filters `mrp_capacity_rules` down to the active
rows whose [effective_from, effective_to] window covers the first day of a
given 'YYYY-MM' month — this is what the (later) MPS algorithm reads to know
how much factory capacity is available for a given planning month.

The CRUD API mirrors app/api/v1/consignment.py's shape: `mrp.report.view`
gates GET, `mrp.param.write` gates POST/PATCH/DELETE (same permission keys
`test_permission_gates.py::test_admin_sync_write_gate_403s_non_permitted_role`
already exercises for mrp.param.write on a different endpoint).
"""
from datetime import date
from decimal import Decimal

import pytest

from app.models.capacity import MrpCapacityRule
from app.services.capacity import resolve_effective_rules


@pytest.mark.anyio
async def test_resolve_effective_rules_filters_by_active_and_window(db_session):
    db_session.add_all([
        MrpCapacityRule(scope_type="factory", scope_ref=None, constraint_type="max_sku_count",
                        limit_value=12, uom=None, effective_from=date(2026,1,1),
                        effective_to=None, is_active=True),
        MrpCapacityRule(scope_type="factory", scope_ref=None, constraint_type="max_output_qty",
                        limit_value=160000, uom="KG", effective_from=date(2026,1,1),
                        effective_to=date(2026,6,30), is_active=True),  # expired for 2026-09
        MrpCapacityRule(scope_type="factory", scope_ref=None, constraint_type="max_sku_count",
                        limit_value=99, uom=None, effective_from=date(2026,1,1),
                        effective_to=None, is_active=False),  # inactive
    ])
    await db_session.commit()
    rules = await resolve_effective_rules(db_session, "2026-09")
    kinds = {(r.constraint_type, r.limit_value) for r in rules}
    assert kinds == {("max_sku_count", 12)}


@pytest.mark.anyio
async def test_crud_roundtrip_and_permission_gate(client, admin_token, non_admin_token, monkeypatch):
    headers = {"Authorization": f"Bearer {admin_token}"}
    r = await client.post(
        "/api/v1/capacity/rules",
        json={
            "scope_type": "factory",
            "constraint_type": "max_sku_count",
            "limit_value": "12",
            "effective_from": "2026-01-01",
        },
        headers=headers,
    )
    assert r.status_code == 201, r.text
    created = r.json()
    assert created["limit_value"] == "12.000"
    assert created["is_active"] is True

    r = await client.get("/api/v1/capacity/rules", headers=headers)
    assert r.status_code == 200
    ids = [row["id"] for row in r.json()]
    assert created["id"] in ids

    r = await client.patch(
        f"/api/v1/capacity/rules/{created['id']}",
        json={"is_active": False},
        headers=headers,
    )
    assert r.status_code == 200
    assert r.json()["is_active"] is False

    # A token without mrp.param.write must not be able to create a rule.
    import uniops_authz.core as authz_core

    async def _user_role_codes(db, user_id, base_role):
        return {base_role}

    async def _effective_matrix(db):
        return {}

    monkeypatch.setattr(authz_core, "user_role_codes", _user_role_codes)
    monkeypatch.setattr(authz_core, "_effective_matrix", _effective_matrix)
    denied_headers = {"Authorization": f"Bearer {non_admin_token}"}
    r = await client.post(
        "/api/v1/capacity/rules",
        json={
            "scope_type": "factory",
            "constraint_type": "max_sku_count",
            "limit_value": "5",
            "effective_from": "2026-01-01",
        },
        headers=denied_headers,
    )
    assert r.status_code == 403
