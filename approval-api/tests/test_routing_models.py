"""Routing tables exist with the defaults the engine relies on."""
import uuid

import pytest
import sqlalchemy as sa

pytestmark = pytest.mark.asyncio


async def test_dept_routing_defaults_match_engine_semantics(engine_db_session):
    db = engine_db_session
    dept = uuid.uuid4()
    await db.execute(sa.text(
        "INSERT INTO approval_dept_routing (dept_id) VALUES (:d)"), {"d": str(dept)})
    row = (await db.execute(sa.text(
        "SELECT gm_or_opm, director_user_id, supervisor_enabled "
        "FROM approval_dept_routing WHERE dept_id = :d"), {"d": str(dept)})).first()
    # Engine defaults: gm_or_opm -> "gm"; no director; NO supervisor layer.
    assert row[0] == "gm"
    assert row[1] is None
    assert row[2] is False


async def test_backup_roundtrip(engine_db_session):
    db = engine_db_session
    uid = uuid.uuid4()
    await db.execute(sa.text(
        "INSERT INTO approval_backups (role_code, backup_user_id) VALUES ('gm', :u)"),
        {"u": str(uid)})
    got = (await db.execute(sa.text(
        "SELECT backup_user_id FROM approval_backups WHERE role_code='gm'"))).scalar_one()
    assert str(got) == str(uid)
