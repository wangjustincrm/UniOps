"""Seed correctness + idempotency against a fabricated company_config row."""
import pytest
import sqlalchemy as sa

from scripts.seed_authz import seed_authz, compute_effective, PERMISSION_KEYS, DEFAULTS

pytestmark = pytest.mark.asyncio


async def test_seed_matches_effective_matrix(db_session):
    import json
    # Drop and recreate shadow company_config table (identity_test has no epms config)
    await db_session.execute(sa.text("DROP TABLE IF EXISTS company_config"))
    await db_session.execute(sa.text(
        "CREATE TABLE company_config "
        "(role_permissions jsonb default '{}'::jsonb, custom_roles jsonb default '[]'::jsonb)"))
    await db_session.commit()
    stored = {"requester": {"view_po": False, "view_booking": False}}
    await db_session.execute(
        sa.text("INSERT INTO company_config(role_permissions, custom_roles) "
                "VALUES (CAST(:s AS jsonb), '[]'::jsonb)"),
        {"s": json.dumps(stored)})
    counts = await seed_authz(db_session)
    assert counts["granted_inserted"] > 0

    rows = (await db_session.execute(sa.text(
        "SELECT permission_key FROM role_permissions WHERE role_code='requester'"))).scalars().all()
    effective = compute_effective(stored, [])
    expected = {k for k, v in effective["requester"].items() if v}
    assert set(rows) == expected
    assert "view_po" not in rows            # override respected
    assert "view_pr" in rows                # locked forced true

    # idempotent re-run inserts nothing new
    counts2 = await seed_authz(db_session)
    assert counts2["granted_inserted"] == 0


async def test_defaults_cover_all_keys(db_session):
    for role, perms in DEFAULTS.items():
        assert set(perms) == set(PERMISSION_KEYS), role
