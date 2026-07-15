"""Seed maps the EPMS JSONB trio + role_management onto the new tables."""
import json
import uuid

import pytest
import sqlalchemy as sa

from scripts.seed_routing import seed_routing

pytestmark = pytest.mark.asyncio


async def _reset_shadow_tables(db):
    """approval-api's ORM mirrors of `users`/`company_config` (app/models/user.py,
    app/models/config.py) only carry the columns the approval ENGINE reads, and
    it has no model at all for `departments`/`user_roles` (epms/identity-owned
    physical tables in the shared DB). This seed needs the full column set on
    all four, so — following identity-api's tests/test_authz_seed.py idiom —
    drop the engine's stand-ins and rebuild minimal shadow tables scoped to
    exactly what seed_routing() and this test need.
    """
    # CASCADE: other engine-owned tables (payment_applications, purchase_requests,
    # tasks, approval_events) hold FK constraints into `users` — cascade drops
    # just those constraint objects, not the referencing tables.
    await db.execute(sa.text("DROP TABLE IF EXISTS company_config CASCADE"))
    await db.execute(sa.text("DROP TABLE IF EXISTS user_roles CASCADE"))
    await db.execute(sa.text("DROP TABLE IF EXISTS users CASCADE"))
    await db.execute(sa.text("DROP TABLE IF EXISTS departments CASCADE"))
    await db.execute(sa.text(
        "CREATE TABLE users (id uuid PRIMARY KEY, email varchar(255),"
        " hashed_password varchar(255), full_name varchar(255), role varchar(50))"))
    await db.execute(sa.text(
        "CREATE TABLE departments (id uuid PRIMARY KEY, code varchar(50),"
        " name varchar(255), is_active boolean NOT NULL DEFAULT true)"))
    await db.execute(sa.text(
        "CREATE TABLE user_roles (user_id uuid NOT NULL, role_code varchar(50) NOT NULL,"
        " PRIMARY KEY (user_id, role_code))"))
    # Mirrors identity-api's 0003_post_role_singleton partial unique index so
    # the seed's own INSERTs get the same backstop they'll face in prod.
    await db.execute(sa.text(
        "CREATE UNIQUE INDEX uq_user_roles_singleton_post ON user_roles (role_code) "
        "WHERE role_code IN ('gm','opm','vendor_manager','finance_manager','procurement_manager')"))
    await db.commit()
    # company_config is intentionally left dropped: _fixture_config()'s own
    # "CREATE TABLE IF NOT EXISTS" (4 jsonb columns, no id) then creates it
    # fresh, free of the ORM mirror's extra NOT NULL columns (id/workflow_defs/
    # budget_admin_config) that a raw 4-column INSERT would otherwise violate.


async def _fixture_config(db, depts, rm):
    await db.execute(sa.text(
        "CREATE TABLE IF NOT EXISTS company_config ("
        " role_management jsonb DEFAULT '{}'::jsonb,"
        " dept_gm_opm_mapping jsonb DEFAULT '{}'::jsonb,"
        " dept_director_mapping jsonb DEFAULT '{}'::jsonb,"
        " dept_supervisor_enabled jsonb DEFAULT '{}'::jsonb)"))
    await db.execute(sa.text("DELETE FROM company_config"))
    await db.execute(sa.text(
        "INSERT INTO company_config (role_management, dept_gm_opm_mapping,"
        " dept_director_mapping, dept_supervisor_enabled) VALUES "
        "(CAST(:rm AS jsonb), CAST(:g AS jsonb), CAST(:d AS jsonb), CAST(:s AS jsonb))"),
        {"rm": json.dumps(rm["role_management"]), "g": json.dumps(rm["gm_opm"]),
         "d": json.dumps(rm["director"]), "s": json.dumps(rm["supervisor"])})


async def test_seed_maps_posts_depts_and_backups(engine_db_session):
    db = engine_db_session
    await _reset_shadow_tables(db)
    d1, d2 = uuid.uuid4(), uuid.uuid4()
    gm_uid, opm_uid, bp_uid, dir_uid = (uuid.uuid4() for _ in range(4))
    for uid, name in ((gm_uid, "GM"), (opm_uid, "OPM"), (bp_uid, "BP"), (dir_uid, "DIR")):
        await db.execute(sa.text(
            "INSERT INTO users (id, email, hashed_password, full_name, role) "
            "VALUES (:i, :e, 'x', :n, 'dept_manager')"),
            {"i": str(uid), "e": f"{uid}@t.co", "n": name})
    for d, code in ((d1, "D1"), (d2, "D2")):
        await db.execute(sa.text(
            "INSERT INTO departments (id, code, name, is_active) VALUES (:i, :c, :c, true)"),
            {"i": str(d), "c": code})
    await _fixture_config(db, [d1, d2], {
        "role_management": {"gm_user_id": str(gm_uid), "opm_user_id": str(opm_uid),
                            "finance_bp_user_ids": [str(bp_uid)],
                            "gm_backup_user_id": str(opm_uid)},
        "gm_opm": {str(d1): "opm"},                 # d2 unlisted -> default "gm"
        "director": {str(d1): str(dir_uid)},        # d2 -> no director
        "supervisor": {str(d1): False},             # neither has a supervisor layer
    })
    await db.flush()

    counts = await seed_routing(db)
    assert counts["dept_rows"] == 2

    rows = {str(r[0]): (r[1], r[2], r[3]) for r in (await db.execute(sa.text(
        "SELECT dept_id, gm_or_opm, director_user_id, supervisor_enabled "
        "FROM approval_dept_routing"))).all()}
    assert rows[str(d1)][0] == "opm"
    assert str(rows[str(d1)][1]) == str(dir_uid)
    assert rows[str(d1)][2] is False
    # Unlisted department keeps engine defaults: gm, no director, NO supervisor.
    assert rows[str(d2)] == ("gm", None, False)

    posts = {r[0]: str(r[1]) for r in (await db.execute(sa.text(
        "SELECT role_code, user_id FROM user_roles"))).all()}
    assert posts["gm"] == str(gm_uid)
    assert posts["opm"] == str(opm_uid)
    assert posts["finance_bp"] == str(bp_uid)

    backup = (await db.execute(sa.text(
        "SELECT backup_user_id FROM approval_backups WHERE role_code='gm'"))).scalar_one()
    assert str(backup) == str(opm_uid)

    # idempotent
    again = await seed_routing(db)
    assert again["user_roles"] == 0


async def test_seed_skips_post_equal_to_primary_role(engine_db_session):
    """If the assignee's PRIMARY role already is the post, no additional row."""
    db = engine_db_session
    await _reset_shadow_tables(db)
    uid = uuid.uuid4()
    await db.execute(sa.text(
        "INSERT INTO users (id, email, hashed_password, full_name, role) "
        "VALUES (:i, :e, 'x', 'Primary GM', 'gm')"), {"i": str(uid), "e": f"{uid}@t.co"})
    await _fixture_config(db, [], {
        "role_management": {"gm_user_id": str(uid)},
        "gm_opm": {}, "director": {}, "supervisor": {}})
    await db.flush()
    await seed_routing(db)
    n = (await db.execute(sa.text(
        "SELECT count(*) FROM user_roles WHERE role_code='gm'"))).scalar_one()
    assert n == 0


async def test_seed_reassigns_post_held_by_wrong_user(engine_db_session):
    """role_management is authoritative: a singleton post held by someone else
    must be reassigned (DELETE + INSERT), not silently skipped by ON CONFLICT.
    """
    db = engine_db_session
    await _reset_shadow_tables(db)
    old_uid, new_uid = uuid.uuid4(), uuid.uuid4()
    for uid, name in ((old_uid, "Wrong Holder"), (new_uid, "Right Holder")):
        await db.execute(sa.text(
            "INSERT INTO users (id, email, hashed_password, full_name, role) "
            "VALUES (:i, :e, 'x', :n, 'dept_manager')"),
            {"i": str(uid), "e": f"{uid}@t.co", "n": name})
    # Pre-existing (stale/manually-added) assignment for the wrong user.
    await db.execute(sa.text(
        "INSERT INTO user_roles (user_id, role_code) VALUES (:u, 'finance_manager')"),
        {"u": str(old_uid)})
    await _fixture_config(db, [], {
        "role_management": {"finance_manager_user_id": str(new_uid)},
        "gm_opm": {}, "director": {}, "supervisor": {}})
    await db.flush()

    counts = await seed_routing(db)

    holder = (await db.execute(sa.text(
        "SELECT user_id FROM user_roles WHERE role_code='finance_manager'"))).scalar_one()
    assert str(holder) == str(new_uid)
    old_still_present = (await db.execute(sa.text(
        "SELECT count(*) FROM user_roles WHERE user_id=:u AND role_code='finance_manager'"),
        {"u": str(old_uid)})).scalar_one()
    assert old_still_present == 0
    assert counts["reassigned"] == 1


async def test_seed_does_not_touch_non_post_roles(engine_db_session):
    """Reassignment logic must be scoped to the five singleton post roles only."""
    db = engine_db_session
    await _reset_shadow_tables(db)
    uid = uuid.uuid4()
    await db.execute(sa.text(
        "INSERT INTO users (id, email, hashed_password, full_name, role) "
        "VALUES (:i, :e, 'x', 'AP Clerk', 'ap_clerk')"), {"i": str(uid), "e": f"{uid}@t.co"})
    await db.execute(sa.text(
        "INSERT INTO user_roles (user_id, role_code) VALUES (:u, 'ap_clerk')"),
        {"u": str(uid)})
    await _fixture_config(db, [], {
        "role_management": {}, "gm_opm": {}, "director": {}, "supervisor": {}})
    await db.flush()

    counts = await seed_routing(db)

    n = (await db.execute(sa.text(
        "SELECT count(*) FROM user_roles WHERE user_id=:u AND role_code='ap_clerk'"),
        {"u": str(uid)})).scalar_one()
    assert n == 1
    assert counts["reassigned"] == 0


async def test_finance_bp_allows_multiple_holders(engine_db_session):
    """finance_bp is a list, not a singleton post — multiple holders coexist,
    with no reassignment logic applied to it.
    """
    db = engine_db_session
    await _reset_shadow_tables(db)
    bp1, bp2 = uuid.uuid4(), uuid.uuid4()
    for uid, name in ((bp1, "BP One"), (bp2, "BP Two")):
        await db.execute(sa.text(
            "INSERT INTO users (id, email, hashed_password, full_name, role) "
            "VALUES (:i, :e, 'x', :n, 'dept_manager')"),
            {"i": str(uid), "e": f"{uid}@t.co", "n": name})
    await _fixture_config(db, [], {
        "role_management": {"finance_bp_user_ids": [str(bp1), str(bp2)]},
        "gm_opm": {}, "director": {}, "supervisor": {}})
    await db.flush()

    counts = await seed_routing(db)

    holders = {str(r[0]) for r in (await db.execute(sa.text(
        "SELECT user_id FROM user_roles WHERE role_code='finance_bp'"))).all()}
    assert holders == {str(bp1), str(bp2)}
    assert counts["reassigned"] == 0
