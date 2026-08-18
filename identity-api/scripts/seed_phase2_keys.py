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
    # Budget Dashboard DATA SCOPE (orthogonal to view_budget_dashboard, which
    # only decides whether the page is reachable). Registered by identity
    # 0010_budget_view_scope_perms; budget-api/finance-api's budget_scope.py
    # admits callers on these two keys and fails closed without either.
    "finance.budget.view_all":  ("finance", "Full Access Budget View",          113),
    "finance.budget.view_dept": ("finance", "Department-Related Budget View",   114),
    "budget.catalog.write":  ("budget",  "Edit Budget Catalog",     120),
    "budget.plan.write":     ("budget",  "Edit Budget Plans",       121),
    "budget.opening.write":  ("budget",  "Edit Opening Balances",   122),
    "mdm.finance.write":     ("mdm",     "Edit Finance Master Data", 130),
    "mdm.vendor.write":      ("mdm",     "Edit Vendor Master Data",  131),
    "epms.agreement.read":   ("epms",    "View Agreements",          104),
    "epms.agreement.write":  ("epms",    "Create / Edit Agreements", 105),
    # Label carries a dependency hint — byte-for-byte identical to identity
    # 0007_receipt_write_perm._KEYS; see that migration for why (whole-branch
    # review I5: Access Control has no key-to-key linkage, so a hand-granted
    # receipt.write without epms.agreement.read is a silently dead role).
    "epms.agreement.receipt.write": ("epms", "Record Agreement Receipts (needs View Agreements)", 106),
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
    # The two halves of 0010_budget_view_scope_perms' grants. That migration
    # seeds view_dept with a NOT IN over role_defs, so it also covers
    # admin-created custom roles AND payment_officer (added later, by migration
    # 0008); this script lists only the seed_authz built-ins, because
    # role_permissions.role_code FKs to role_defs and this script has to be
    # safe against a role_defs that predates those rows.
    "finance.budget.view_all":  ("gm", "finance_manager", "ap_clerk", "system_admin",
                                 "finance_bp", "auditor", "cfo"),
    "finance.budget.view_dept": ("requester", "dept_admin", "dept_manager", "supervisor",
                                 "director", "opm", "procurement_officer",
                                 "procurement_manager", "warehouse_staff",
                                 "vendor_manager", "erp_pa_officer"),
    "budget.catalog.write": ("system_admin", "finance_manager", "finance_bp"),
    "budget.plan.write":    ("system_admin", "finance_manager", "finance_bp", "dept_manager"),
    # literal tuple is ("finance_manager","finance_bp") — system_admin passes via short-circuit
    "budget.opening.write": ("system_admin", "finance_manager", "finance_bp"),
    "mdm.finance.write":    ("system_admin", "finance_manager", "ap_clerk"),
    "mdm.vendor.write":     ("system_admin", "vendor_manager", "finance_manager"),
    # Byte-for-byte the same role set as identity 0006_agreement_perms._GRANTS
    # (the two halves of one registration), PLUS dept_admin and cfo/
    # erp_pa_officer (both fix-round 1 on 0007_receipt_write_perm — see that
    # migration's docstring): dept_admin got epms.agreement.receipt.write
    # below without epms.agreement.read, a key that opens a door it can't
    # reach (no read grant means no nav entry, no GET /agreements/{id}, no
    # GET .../receipts). cfo/erp_pa_officer hold view_pa and can open a PA's
    # chain-attachments panel, which now calls the same
    # epms.agreement.read-gated routes to fetch agreement-receipt evidence
    # (Task 11) — without this grant that panel 403s the receipt queries for
    # exactly the role (cfo) the evidence package exists to serve. Includes
    # every role that can sit in the `agr` approval chain — without a read
    # grant the step-0 approver 403s on GET /agreements/{id} and no agreement
    # can ever be activated. "gm_or_opm" is deliberately absent: it is a
    # pseudo-role resolved into gm/opm and is not a role_defs code
    # (role_permissions.role_code FKs to it).
    "epms.agreement.read":  ("system_admin", "procurement_officer", "procurement_manager",
                             "ap_clerk", "finance_bp", "finance_manager", "auditor",
                             "dept_manager", "director", "gm", "opm", "requester",
                             "dept_admin", "cfo", "erp_pa_officer"),
    "epms.agreement.write": ("system_admin", "procurement_officer", "procurement_manager"),
    # Byte-for-byte the same role set as identity 0007_receipt_write_perm._GRANTS.
    # Deliberately narrower than epms.agreement.write's grant set — recording
    # an agreement receipt and editing the agreement's own terms are now
    # separate permissions; see that migration's docstring for why
    # procurement_officer/procurement_manager are NOT seeded here (no
    # incumbent user to preserve).
    "epms.agreement.receipt.write": ("system_admin", "ap_clerk", "dept_admin"),
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
