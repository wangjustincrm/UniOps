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


# ── Task 3: minimum weekly output constraint + per-week exceptions ─────────


@pytest.mark.anyio
async def test_min_output_qty_is_an_accepted_constraint_type(client, auth_headers):
    r = await client.post("/api/v1/capacity/rules", headers=auth_headers, json={
        "scope_type": "factory", "constraint_type": "min_output_qty",
        "limit_value": "20000", "uom": "KG", "effective_from": "2026-01-01",
    })
    assert r.status_code == 201, r.text


@pytest.mark.anyio
async def test_min_output_above_max_output_is_rejected(client, auth_headers):
    await client.post("/api/v1/capacity/rules", headers=auth_headers, json={
        "scope_type": "factory", "constraint_type": "max_output_qty",
        "limit_value": "40000", "uom": "KG", "effective_from": "2026-01-01",
    })
    r = await client.post("/api/v1/capacity/rules", headers=auth_headers, json={
        "scope_type": "factory", "constraint_type": "min_output_qty",
        "limit_value": "50000", "uom": "KG", "effective_from": "2026-01-01",
    })
    assert r.status_code == 422
    assert "min" in r.json()["detail"].lower()


@pytest.mark.anyio
async def test_week_exception_overrides_the_standing_rule(client, auth_headers, db_session):
    from app.services.capacity import resolve_limits_for_week
    await client.post("/api/v1/capacity/rules", headers=auth_headers, json={
        "scope_type": "factory", "constraint_type": "max_output_qty",
        "limit_value": "40000", "uom": "KG", "effective_from": "2026-01-01",
    })
    await client.post("/api/v1/capacity/exceptions", headers=auth_headers, json={
        "week_start": "2026-08-10", "scope_type": "factory",
        "constraint_type": "max_output_qty", "limit_value": "0", "uom": "KG",
        "reason": "annual maintenance",
    })
    normal = await resolve_limits_for_week(db_session, date(2026, 8, 3))
    shut = await resolve_limits_for_week(db_session, date(2026, 8, 10))
    assert str(normal.max_output_qty) == "40000.000"
    assert str(shut.max_output_qty) == "0.000"


@pytest.mark.anyio
async def test_inactive_exception_is_ignored(client, auth_headers, db_session):
    from app.services.capacity import resolve_limits_for_week
    await client.post("/api/v1/capacity/rules", headers=auth_headers, json={
        "scope_type": "factory", "constraint_type": "max_output_qty",
        "limit_value": "40000", "uom": "KG", "effective_from": "2026-01-01",
    })
    exc = (await client.post("/api/v1/capacity/exceptions", headers=auth_headers, json={
        "week_start": "2026-09-07", "scope_type": "factory",
        "constraint_type": "max_output_qty", "limit_value": "0", "uom": "KG",
        "reason": "cancelled",
    })).json()
    await client.patch(f"/api/v1/capacity/exceptions/{exc['id']}",
                       json={"is_active": False}, headers=auth_headers)
    assert str((await resolve_limits_for_week(db_session,
                                              date(2026, 9, 7))).max_output_qty) == "40000.000"


# ── Fix round 1 (code review) ───────────────────────────────────────────────


@pytest.mark.anyio
async def test_exception_with_scope_ref_does_not_leak_into_factory_wide_resolution(client, auth_headers, db_session):
    """scope_type='factory' with a non-null scope_ref is nonsensical but not
    schema-forbidden. resolve_limits_for_week must filter on
    scope_ref IS NULL (not just scope_type == 'factory') so a stray row like
    this can never be swept into the factory-wide resolution alongside the
    real factory-wide rule."""
    from app.services.capacity import resolve_limits_for_week
    await client.post("/api/v1/capacity/rules", headers=auth_headers, json={
        "scope_type": "factory", "constraint_type": "max_output_qty",
        "limit_value": "40000", "uom": "KG", "effective_from": "2026-01-01",
    })
    r = await client.post("/api/v1/capacity/exceptions", headers=auth_headers, json={
        "week_start": "2026-08-10", "scope_type": "factory", "scope_ref": "line-1",
        "constraint_type": "max_output_qty", "limit_value": "99999", "uom": "KG",
        "reason": "scoped to a specific line, must not apply factory-wide",
    })
    assert r.status_code == 201, r.text
    limits = await resolve_limits_for_week(db_session, date(2026, 8, 10))
    assert str(limits.max_output_qty) == "40000.000"


@pytest.mark.anyio
async def test_rule_create_rejects_unknown_constraint_type(client, auth_headers):
    r = await client.post("/api/v1/capacity/rules", headers=auth_headers, json={
        "scope_type": "factory", "constraint_type": "max_output_qtyy",
        "limit_value": "1000", "uom": "KG", "effective_from": "2026-01-01",
    })
    assert r.status_code == 422
    assert "max_output_qtyy" in r.json()["detail"]


@pytest.mark.anyio
async def test_exception_create_rejects_unknown_constraint_type(client, auth_headers):
    r = await client.post("/api/v1/capacity/exceptions", headers=auth_headers, json={
        "week_start": "2026-08-10", "scope_type": "factory",
        "constraint_type": "bogus_type", "limit_value": "0", "uom": "KG",
    })
    assert r.status_code == 422
    assert "bogus_type" in r.json()["detail"]
