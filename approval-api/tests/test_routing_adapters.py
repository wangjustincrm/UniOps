"""Getters read the new tables but keep the exact legacy dict shape the engine
consumes — that is what makes the engine's ~20 call sites need no change."""
import uuid

import pytest
import sqlalchemy as sa

from app.crud.workflow import (get_dept_director_mapping, get_dept_gm_opm_mapping,
                               get_dept_supervisor_enabled, get_role_management)

pytestmark = pytest.mark.asyncio


async def _reset_shadow_tables(db):
    """approval-api's ORM mirror of `users` (app/models/user.py) only carries the
    columns the approval ENGINE reads (no email/hashed_password/full_name), and
    it has no model at all for `user_roles` (identity-owned physical table in the
    shared DB). Following test_seed_routing.py's idiom, drop the engine's
    stand-in and rebuild minimal shadow tables scoped to what these tests need.
    """
    await db.execute(sa.text("DROP TABLE IF EXISTS user_roles CASCADE"))
    await db.execute(sa.text("DROP TABLE IF EXISTS users CASCADE"))
    await db.execute(sa.text(
        "CREATE TABLE users (id uuid PRIMARY KEY, email varchar(255),"
        " hashed_password varchar(255), full_name varchar(255), role varchar(50),"
        " is_active boolean NOT NULL DEFAULT true)"))
    await db.execute(sa.text(
        "CREATE TABLE user_roles (user_id uuid NOT NULL, role_code varchar(50) NOT NULL,"
        " PRIMARY KEY (user_id, role_code))"))
    await db.commit()


async def test_role_management_shape_from_tables(engine_db_session):
    db = engine_db_session
    await _reset_shadow_tables(db)
    gm, opm, bp = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    for uid, role in ((gm, "dept_manager"), (opm, "dept_manager"), (bp, "requester")):
        await db.execute(sa.text(
            "INSERT INTO users (id, email, hashed_password, full_name, role) "
            "VALUES (:i, :e, 'x', 'U', :r)"), {"i": str(uid), "e": f"{uid}@t.co", "r": role})
    await db.execute(sa.text(
        "INSERT INTO user_roles (user_id, role_code) VALUES (:g,'gm'), (:o,'opm'), (:b,'finance_bp')"),
        {"g": str(gm), "o": str(opm), "b": str(bp)})
    await db.execute(sa.text(
        "INSERT INTO approval_backups (role_code, backup_user_id) VALUES ('gm', :o)"),
        {"o": str(opm)})
    await db.flush()

    rm = await get_role_management(db)
    assert rm["gm_user_id"] == str(gm)          # str, not UUID — engine does uuid.UUID(x)
    assert rm["opm_user_id"] == str(opm)
    assert rm["finance_bp_user_ids"] == [str(bp)]
    assert rm["gm_backup_user_id"] == str(opm)
    assert rm["vendor_manager_user_id"] is None


async def test_post_from_primary_role_is_included(engine_db_session):
    """A post held as someone's PRIMARY role must resolve too."""
    db = engine_db_session
    await _reset_shadow_tables(db)
    uid = uuid.uuid4()
    await db.execute(sa.text(
        "INSERT INTO users (id, email, hashed_password, full_name, role) "
        "VALUES (:i, :e, 'x', 'Primary GM', 'gm')"), {"i": str(uid), "e": f"{uid}@t.co"})
    await db.flush()
    rm = await get_role_management(db)
    assert rm["gm_user_id"] == str(uid)


async def test_finance_bp_primary_role_alone_is_not_included(engine_db_session):
    """finance_bp is a multi-holder JOB FUNCTION, not a company-unique post —
    holding it as a PRIMARY role is not an assignment. Only a curated
    user_roles row makes someone an approver. This is the exact prod
    situation (a user with users.role='finance_bp' but no user_roles row for
    it) and must NOT resolve as a finance_bp approver.
    """
    db = engine_db_session
    await _reset_shadow_tables(db)
    uid = uuid.uuid4()
    await db.execute(sa.text(
        "INSERT INTO users (id, email, hashed_password, full_name, role) "
        "VALUES (:i, :e, 'x', 'Primary Only BP', 'finance_bp')"),
        {"i": str(uid), "e": f"{uid}@t.co"})
    await db.flush()
    rm = await get_role_management(db)
    assert rm["finance_bp_user_ids"] == []


async def test_dept_getters_shape(engine_db_session):
    db = engine_db_session
    await _reset_shadow_tables(db)
    d1, d2 = uuid.uuid4(), uuid.uuid4()
    dir_uid = uuid.uuid4()
    await db.execute(sa.text(
        "INSERT INTO users (id, email, hashed_password, full_name, role) "
        "VALUES (:i, :e, 'x', 'D', 'director')"), {"i": str(dir_uid), "e": f"{dir_uid}@t.co"})
    await db.execute(sa.text(
        "INSERT INTO approval_dept_routing (dept_id, gm_or_opm, director_user_id, supervisor_enabled)"
        " VALUES (:d1,'opm',:u,true), (:d2,'gm',NULL,false)"),
        {"d1": str(d1), "d2": str(d2), "u": str(dir_uid)})
    await db.flush()

    assert (await get_dept_gm_opm_mapping(db)) == {str(d1): "opm", str(d2): "gm"}
    assert (await get_dept_director_mapping(db)) == {str(d1): str(dir_uid)}
    sup = await get_dept_supervisor_enabled(db)
    assert sup.get(str(d1)) is True
    assert not sup.get(str(d2))          # engine treats missing/False identically
