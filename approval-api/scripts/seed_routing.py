"""One-shot idempotent migration: EPMS approval assignments -> owned tables.

Run INSIDE the approval container (host .env points at prod!):
    docker exec uniops_approval_api python -m scripts.seed_routing

Maps (see spec 2026-07-15-approval-routing-phase3-design.md):
  role_management.*_user_id / finance_bp_user_ids -> identity user_roles (additional)
  role_management.*_backup_user_id                -> approval_backups
  dept_gm_opm_mapping / dept_director_mapping /
  dept_supervisor_enabled                         -> approval_dept_routing (one row per active dept)

Engine defaults preserved verbatim: gm_or_opm -> 'gm'; director -> NULL;
supervisor_enabled -> FALSE (an unlisted dept has NO supervisor layer today).
"""
import asyncio
import json

import sqlalchemy as sa

from app.db.base import AsyncSessionLocal

# role_management key -> role code carried as an additional role
_POST_KEYS = {
    "gm_user_id": "gm",
    "opm_user_id": "opm",
    "vendor_manager_user_id": "vendor_manager",
    "finance_manager_user_id": "finance_manager",
    "procurement_manager_user_id": "procurement_manager",
}
_BACKUP_KEYS = {"gm_backup_user_id": "gm", "opm_backup_user_id": "opm"}


def _as_dict(v):
    return json.loads(v) if isinstance(v, str) else (v or {})


async def seed_routing(session) -> dict:
    row = (await session.execute(sa.text(
        "SELECT role_management, dept_gm_opm_mapping, dept_director_mapping,"
        " dept_supervisor_enabled FROM company_config LIMIT 1"))).first()
    rm = _as_dict(row[0]) if row else {}
    gm_opm = _as_dict(row[1]) if row else {}
    director = _as_dict(row[2]) if row else {}
    supervisor = _as_dict(row[3]) if row else {}

    counts = {"user_roles": 0, "dept_rows": 0, "backups": 0}

    # 1) post holders -> user_roles (skip when it already is the user's primary role)
    pairs: list[tuple[str, str]] = []
    for key, code in _POST_KEYS.items():
        uid = rm.get(key)
        if uid:
            pairs.append((str(uid), code))
    for uid in rm.get("finance_bp_user_ids", []) or []:
        pairs.append((str(uid), "finance_bp"))
    for uid, code in pairs:
        primary = (await session.execute(sa.text(
            "SELECT role FROM users WHERE id = :u"), {"u": uid})).scalar_one_or_none()
        if primary == code:
            continue
        r = await session.execute(sa.text(
            "INSERT INTO user_roles (user_id, role_code) VALUES (:u, :c) "
            "ON CONFLICT DO NOTHING"), {"u": uid, "c": code})
        counts["user_roles"] += r.rowcount or 0

    # 2) backups
    for key, code in _BACKUP_KEYS.items():
        uid = rm.get(key)
        if uid:
            r = await session.execute(sa.text(
                "INSERT INTO approval_backups (role_code, backup_user_id) VALUES (:c, :u) "
                "ON CONFLICT (role_code) DO NOTHING"), {"c": code, "u": str(uid)})
            counts["backups"] += r.rowcount or 0

    # 3) one row per ACTIVE department, engine defaults preserved
    depts = (await session.execute(sa.text(
        "SELECT id FROM departments WHERE is_active"))).scalars().all()
    for d in depts:
        ds = str(d)
        r = await session.execute(sa.text(
            "INSERT INTO approval_dept_routing "
            " (dept_id, gm_or_opm, director_user_id, supervisor_enabled) "
            "VALUES (:d, :g, :dir, :s) ON CONFLICT (dept_id) DO NOTHING"),
            {"d": ds,
             "g": gm_opm.get(ds, "gm"),
             "dir": director.get(ds),
             "s": bool(supervisor.get(ds, False))})
        counts["dept_rows"] += r.rowcount or 0
    return counts


async def main():
    async with AsyncSessionLocal() as session:
        counts = await seed_routing(session)
        await session.commit()
        print(f"seed_routing done: {counts}")


if __name__ == "__main__":
    asyncio.run(main())
