"""One-shot idempotent backfill: every role holding 'vendor_master' also gets
'mdm.vendor.write', so the coupled "Vendor Master" checkbox works for roles
granted before the coupling shipped. See
docs/superpowers/specs/2026-08-01-vendor-master-authz-coupling-design.md.

Run INSIDE the identity container (host .env points at prod!):
    docker compose exec identity-api python -m scripts.backfill_vendor_master_coupling
"""
import asyncio

import sqlalchemy as sa

from app.db.base import AsyncSessionLocal


async def backfill_vendor_master_coupling(session) -> dict:
    # One-directional: only vendor_master holders get the mdm.vendor.write row.
    # updated_by is left NULL (default), matching seed_phase2_keys / seed_authz
    # precedent — don't inherit the source row's grantor.
    r = await session.execute(sa.text(
        "INSERT INTO role_permissions (role_code, permission_key) "
        "SELECT rp.role_code, 'mdm.vendor.write' "
        "FROM role_permissions rp "
        "WHERE rp.permission_key = 'vendor_master' "
        "ON CONFLICT (role_code, permission_key) DO NOTHING"))
    return {"granted": r.rowcount or 0}


async def main():
    async with AsyncSessionLocal() as session:
        counts = await backfill_vendor_master_coupling(session)
        await session.commit()
        print(f"backfill_vendor_master_coupling done: {counts}")


if __name__ == "__main__":
    asyncio.run(main())
