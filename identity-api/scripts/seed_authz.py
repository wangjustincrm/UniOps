"""One-shot idempotent seed: epms company_config matrix -> identity authz tables.

Run INSIDE the identity container (host .env points at prod!):
    docker exec uniops_identity_api python -m scripts.seed_authz
identity 与 epms 共享同一物理库,直接 SQL 读 company_config。
"""
import asyncio
import json

import sqlalchemy as sa

from app.db.base import AsyncSessionLocal

MODULE_BY_KEY = {
    "view_pr": "epms", "view_po": "epms", "view_gr": "epms",
    "view_invoice": "epms", "view_pa": "epms", "create_pr": "epms",
    "create_gr": "epms", "invoice_upload": "epms", "vendor_master": "epms",
    "parts_catalog": "epms", "admin_panel": "epms", "data_maintenance": "epms",
    "pa_override_receipt": "epms",
    "view_budget_dashboard": "finance", "view_budget_plans": "finance",
    "view_finance": "finance",
    "view_booking": "booking", "manage_meeting_rooms": "booking",
}
PERMISSION_KEYS = list(MODULE_BY_KEY)  # keeps epms UI order

ROLE_LABELS = {  # built-in 17
    "requester": "Requester", "dept_admin": "Department Admin",
    "dept_manager": "Department Manager", "supervisor": "Supervisor",
    "director": "Director", "gm": "General Manager", "opm": "Operations Manager",
    "procurement_officer": "Procurement Officer",
    "procurement_manager": "Procurement Manager",
    "warehouse_staff": "Warehouse Staff", "ap_clerk": "AP Clerk",
    "finance_bp": "Finance BP", "finance_manager": "Finance Manager",
    "vendor_manager": "Vendor Manager", "cfo": "CFO", "auditor": "Auditor",
    "system_admin": "System Admin",
}

LOCKED = {
    "requester": {"view_pr"},
    "procurement_officer": {"view_pr", "view_po", "view_gr"},
    "procurement_manager": {"view_pr", "view_po", "view_gr"},
    "warehouse_staff": {"view_gr"},
    "ap_clerk": {"view_invoice", "view_pa"},
    "finance_bp": {"view_pa"},
    "finance_manager": {"view_pa"},
    "system_admin": {"admin_panel"},
}

# —— 与 epms get_effective_role_permissions 等价的默认矩阵(一次性拷贝) ——
_VIEW_ALL = {k: True for k in ("view_pr", "view_po", "view_gr", "view_invoice", "view_pa")}
_FINANCE_ALL = {"view_budget_dashboard": True, "view_budget_plans": True, "view_finance": True}
_BOOKING = {"view_booking": True}
_BUDGET_VIEW = {"view_budget_dashboard": True, "view_budget_plans": True}

def _p(**kw):
    base = {k: False for k in PERMISSION_KEYS}
    base.update(kw)
    return base

DEFAULTS = {
    "requester":           _p(create_pr=True, create_gr=True, **_VIEW_ALL, **_BOOKING),
    "dept_admin":          _p(create_pr=True, create_gr=True, **_VIEW_ALL, **_BOOKING),
    "dept_manager":        _p(create_pr=True, create_gr=True, **_VIEW_ALL, **_BUDGET_VIEW, **_BOOKING),
    "supervisor":          _p(view_pr=True, **_BOOKING),
    "director":            _p(view_pr=True, view_pa=True, **_BOOKING),
    "gm":                  _p(create_pr=True, create_gr=True, **_VIEW_ALL, **_BOOKING),
    "opm":                 _p(create_pr=True, create_gr=True, **_VIEW_ALL, **_BOOKING),
    "procurement_officer": _p(create_gr=True, vendor_master=True, parts_catalog=True, pa_override_receipt=True, **_VIEW_ALL, **_BOOKING),
    "procurement_manager": _p(create_gr=True, vendor_master=True, parts_catalog=True, pa_override_receipt=True, **_VIEW_ALL, **_BOOKING),
    "warehouse_staff":     _p(create_gr=True, view_gr=True, **_BOOKING),
    "ap_clerk":            _p(create_gr=True, invoice_upload=True, **_VIEW_ALL, **_FINANCE_ALL, **_BOOKING),
    "finance_bp":          _p(create_gr=True, pa_override_receipt=True, **_VIEW_ALL, **_FINANCE_ALL, **_BOOKING),
    "finance_manager":     _p(create_pr=True, create_gr=True, admin_panel=True, pa_override_receipt=True, **_VIEW_ALL, **_FINANCE_ALL, **_BOOKING),
    "vendor_manager":      _p(vendor_master=True, admin_panel=True, **_BOOKING),
    "cfo":                 _p(pa_override_receipt=True, **_VIEW_ALL, **_FINANCE_ALL, **_BOOKING),
    "auditor":             _p(**_VIEW_ALL, **_BOOKING),
    "system_admin":        {k: True for k in PERMISSION_KEYS},
}


def compute_effective(stored: dict, custom_roles: list) -> dict[str, dict[str, bool]]:
    result = {}
    for role, defaults in DEFAULTS.items():
        merged = {k: bool(stored.get(role, {}).get(k, defaults[k])) for k in PERMISSION_KEYS}
        for k in LOCKED.get(role, set()):
            merged[k] = True
        result[role] = merged
    for cr in custom_roles:
        if not cr.get("is_active", True):
            continue
        code = cr["code"]
        result[code] = {k: bool(stored.get(code, {}).get(k, False)) for k in PERMISSION_KEYS}
    return result


async def seed_authz(session) -> dict:
    row = (await session.execute(sa.text(
        "SELECT role_permissions, custom_roles FROM company_config LIMIT 1"))).first()
    stored = row[0] if row else {}
    custom = row[1] if row else []
    if isinstance(stored, str):
        stored = json.loads(stored)
    if isinstance(custom, str):
        custom = json.loads(custom)

    for i, (code, label) in enumerate(ROLE_LABELS.items()):
        await session.execute(sa.text(
            "INSERT INTO role_defs(code,label,sort,is_active) VALUES (:c,:l,:s,true) "
            "ON CONFLICT (code) DO NOTHING"), {"c": code, "l": label, "s": i})
    for cr in custom:
        await session.execute(sa.text(
            "INSERT INTO role_defs(code,label,sort,is_active) VALUES (:c,:l,900,:a) "
            "ON CONFLICT (code) DO NOTHING"),
            {"c": cr["code"], "l": cr.get("label", cr["code"]), "a": cr.get("is_active", True)})

    for i, (key, module) in enumerate(MODULE_BY_KEY.items()):
        label = key.replace("_", " ").title()
        await session.execute(sa.text(
            "INSERT INTO permission_defs(key,module,label,sort) VALUES (:k,:m,:l,:s) "
            "ON CONFLICT (key) DO NOTHING"), {"k": key, "m": module, "l": label, "s": i})

    granted = 0
    for role, perms in compute_effective(stored, custom).items():
        for key, val in perms.items():
            if val:
                r = await session.execute(sa.text(
                    "INSERT INTO role_permissions(role_code,permission_key) VALUES (:r,:k) "
                    "ON CONFLICT DO NOTHING"), {"r": role, "k": key})
                granted += r.rowcount or 0
    for role, keys in LOCKED.items():
        for key in keys:
            await session.execute(sa.text(
                "INSERT INTO role_permission_locks(role_code,permission_key) VALUES (:r,:k) "
                "ON CONFLICT DO NOTHING"), {"r": role, "k": key})
    return {"granted_inserted": granted}


async def main():
    async with AsyncSessionLocal() as session:
        counts = await seed_authz(session)
        await session.commit()
        print(f"seed_authz done: {counts}")


if __name__ == "__main__":
    asyncio.run(main())
