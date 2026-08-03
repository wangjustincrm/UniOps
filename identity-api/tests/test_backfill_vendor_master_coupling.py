"""Backfill grants mdm.vendor.write to every role already holding vendor_master,
one-directionally — roles that only hold mdm.vendor.write (finance_manager)
must not gain vendor_master. See
docs/superpowers/specs/2026-08-01-vendor-master-authz-coupling-design.md."""
import pytest
import sqlalchemy as sa

from scripts.seed_authz import seed_authz
from scripts.seed_phase2_keys import seed_phase2_keys
from scripts.backfill_vendor_master_coupling import backfill_vendor_master_coupling

pytestmark = pytest.mark.asyncio


@pytest.fixture
async def seeded_full(db_session):
    await db_session.execute(sa.text(
        "ALTER TABLE company_config ADD COLUMN IF NOT EXISTS role_permissions jsonb DEFAULT '{}'::jsonb"))
    await db_session.execute(sa.text(
        "ALTER TABLE company_config ADD COLUMN IF NOT EXISTS custom_roles jsonb DEFAULT '[]'::jsonb"))
    await seed_authz(db_session)
    await seed_phase2_keys(db_session)
    await db_session.commit()
    yield


async def _has(db_session, role: str, key: str) -> bool:
    return bool((await db_session.execute(sa.text(
        "SELECT 1 FROM role_permissions WHERE role_code=:r AND permission_key=:k"),
        {"r": role, "k": key})).scalar())


async def test_backfill_grants_mdm_write_to_vendor_master_holders(seeded_full, db_session):
    # procurement_officer holds vendor_master but NOT mdm.vendor.write by default.
    assert await _has(db_session, "procurement_officer", "vendor_master")
    assert not await _has(db_session, "procurement_officer", "mdm.vendor.write")
    counts = await backfill_vendor_master_coupling(db_session)
    assert await _has(db_session, "procurement_officer", "mdm.vendor.write")
    assert await _has(db_session, "procurement_manager", "mdm.vendor.write")
    assert counts["granted"] >= 2


async def test_backfill_leaves_mdm_only_roles_untouched(seeded_full, db_session):
    # finance_manager holds mdm.vendor.write but NOT vendor_master; backfill is
    # one-directional and must not grant it vendor_master.
    assert await _has(db_session, "finance_manager", "mdm.vendor.write")
    assert not await _has(db_session, "finance_manager", "vendor_master")
    await backfill_vendor_master_coupling(db_session)
    assert not await _has(db_session, "finance_manager", "vendor_master")


async def test_backfill_idempotent(seeded_full, db_session):
    await backfill_vendor_master_coupling(db_session)
    counts = await backfill_vendor_master_coupling(db_session)
    assert counts["granted"] == 0
