"""Seed correctness + idempotency against a fabricated company_config row."""
import pytest
import sqlalchemy as sa

from scripts.seed_authz import seed_authz, compute_effective, PERMISSION_KEYS, DEFAULTS

pytestmark = pytest.mark.asyncio


async def test_seed_matches_effective_matrix(db_session):
    import json
    # Clear authz tables from any previous test (idempotency test)
    await db_session.execute(sa.text("DELETE FROM role_permission_locks"))
    await db_session.execute(sa.text("DELETE FROM role_permissions"))
    await db_session.execute(sa.text("DELETE FROM permission_defs"))
    await db_session.execute(sa.text("DELETE FROM user_roles"))
    await db_session.execute(sa.text("DELETE FROM role_defs"))
    # Drop and recreate shadow company_config table with all required columns
    # (identity_test has no epms config, but other tests need the CompanyConfig columns)
    await db_session.execute(sa.text("DROP TABLE IF EXISTS company_config"))
    await db_session.execute(sa.text(
        "CREATE TABLE company_config ("
        "id uuid PRIMARY KEY DEFAULT gen_random_uuid(), "
        "mfa_enabled boolean NOT NULL DEFAULT true, "
        "password_expiry_days integer, "
        "smtp_host varchar(255), "
        "smtp_port integer, "
        "smtp_user varchar(255), "
        "smtp_password varchar(255), "
        "smtp_use_tls boolean, "
        "smtp_from varchar(255), "
        "role_permissions jsonb DEFAULT '{}'::jsonb, "
        "custom_roles jsonb DEFAULT '[]'::jsonb"
        ")"))
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
