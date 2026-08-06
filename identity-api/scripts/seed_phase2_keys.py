"""Register phase-2 permission keys and seed their defaults.

Defaults are the EXACT admission sets of the require_roles()/inline gates they
replace. Note every one includes system_admin: require_roles short-circuits it
(`if role == "system_admin": return payload`), so a gate whose literal tuple
omits system_admin — e.g. budget's _OPENING_WRITE_ROLES — still admitted it.
Dropping it here would be a behaviour change (a tightening).

Run INSIDE the identity container (host .env points at prod!):
    docker exec uniops_identity_api python -m scripts.seed_phase2_keys
"""
import asyncio

import sqlalchemy as sa

from app.db.base import AsyncSessionLocal

# key -> (module, label, sort)
PHASE2_KEYS: dict[str, tuple[str, str, int]] = {
    "epms.invoice.match":    ("epms",    "Match Invoices",          100),
    "epms.po.write":         ("epms",    "Create / Edit POs",       101),
    "epms.pa.write":         ("epms",    "Create / Edit PAs",       102),
    "epms.gr.receive":       ("epms",    "Receive Goods",           103),
    "finance.coa.manage":    ("finance", "Manage Chart of Accounts", 110),
    "finance.period.close":  ("finance", "Close Periods",           111),
    "finance.jv.post":       ("finance", "Post Journal Vouchers",   112),
    "budget.catalog.write":  ("budget",  "Edit Budget Catalog",     120),
    "budget.plan.write":     ("budget",  "Edit Budget Plans",       121),
    "budget.opening.write":  ("budget",  "Edit Opening Balances",   122),
    "mdm.finance.write":     ("mdm",     "Edit Finance Master Data", 130),
    "mdm.vendor.write":      ("mdm",     "Edit Vendor Master Data",  131),
}

# key -> roles admitted TODAY (system_admin included everywhere: short-circuit)
PHASE2_DEFAULTS: dict[str, tuple[str, ...]] = {
    "epms.invoice.match":   ("system_admin", "ap_clerk", "finance_manager", "finance_bp"),
    "epms.po.write":        ("system_admin", "procurement_officer", "procurement_manager"),
    "epms.pa.write":        ("system_admin", "finance_bp", "finance_manager", "ap_clerk", "requester", "erp_pa_officer", "procurement_officer"),
    "epms.gr.receive":      ("system_admin", "warehouse_staff", "procurement_officer"),
    "finance.coa.manage":   ("system_admin", "finance_manager"),
    "finance.period.close": ("system_admin", "finance_manager"),
    "finance.jv.post":      ("system_admin", "finance_manager", "finance_bp"),
    "budget.catalog.write": ("system_admin", "finance_manager", "finance_bp"),
    "budget.plan.write":    ("system_admin", "finance_manager", "finance_bp", "dept_manager"),
    # literal tuple is ("finance_manager","finance_bp") — system_admin passes via short-circuit
    "budget.opening.write": ("system_admin", "finance_manager", "finance_bp"),
    "mdm.finance.write":    ("system_admin", "finance_manager", "ap_clerk"),
    "mdm.vendor.write":     ("system_admin", "vendor_manager", "finance_manager"),
}


async def seed_phase2_keys(session) -> dict:
    counts = {"keys": 0, "granted": 0}
    for key, (module, label, sort) in PHASE2_KEYS.items():
        r = await session.execute(sa.text(
            "INSERT INTO permission_defs (key, module, label, sort) "
            "VALUES (:k, :m, :l, :s) ON CONFLICT (key) DO NOTHING"),
            {"k": key, "m": module, "l": label, "s": sort})
        counts["keys"] += r.rowcount or 0
    for key, roles in PHASE2_DEFAULTS.items():
        for role in roles:
            r = await session.execute(sa.text(
                "INSERT INTO role_permissions (role_code, permission_key) "
                "VALUES (:r, :k) ON CONFLICT (role_code, permission_key) DO NOTHING"),
                {"r": role, "k": key})
            counts["granted"] += r.rowcount or 0
    return counts


async def main():
    async with AsyncSessionLocal() as session:
        counts = await seed_phase2_keys(session)
        await session.commit()
        print(f"seed_phase2_keys done: {counts}")


if __name__ == "__main__":
    asyncio.run(main())
