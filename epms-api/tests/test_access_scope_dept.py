"""access_scope 的部门可见性表征测试。

锁住 _mapped_dept_ids / _director_dept_ids 的行为 —— 数据源已从
company_config.dept_* JSONB 切到 approval-api 拥有的 approval_dept_routing 表
(phase-3 Task 2)。
"""
import uuid

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.access_scope import _mapped_dept_ids, _director_dept_ids
from app.crud import config as config_crud


@pytest.fixture
async def db(test_engine):
    """Session pre-seeded with the CompanyConfig singleton row + a local
    approval_dept_routing table.

    conftest.py has no plain `db` fixture — every test module builds its own
    sessionmaker off the session-scoped `test_engine` (see test_pr_scoping.py /
    test_config_director_mapping.py). CompanyConfig is no longer read by the
    functions under test, but config_crud.get_or_create is kept here (harmless,
    matches the established pattern) in case other fixtures in this session
    expect the singleton row to exist.

    approval_dept_routing is owned by approval-api (its own alembic head) —
    epms_test has no such table by default. Create it here with
    CREATE TABLE IF NOT EXISTS (schema copied from
    approval-api/alembic/versions/0001_approval_routing.py) so this file's
    tests can seed routing rows without depending on approval-api's migrations
    running against the shared test DB.
    """
    factory = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as session:
        await config_crud.get_or_create(session)
        await session.execute(text(
            "CREATE TABLE IF NOT EXISTS approval_dept_routing ("
            " dept_id uuid PRIMARY KEY,"
            " gm_or_opm varchar(3) NOT NULL DEFAULT 'gm',"
            " director_user_id uuid NULL,"
            " supervisor_enabled boolean NOT NULL DEFAULT false,"
            " updated_by uuid NULL,"
            " updated_at timestamptz NOT NULL DEFAULT now()"
            ")"
        ))
        await session.commit()
        yield session


async def _set_routing(db, rows: dict[uuid.UUID, str], directors: dict[uuid.UUID, uuid.UUID] | None = None):
    """rows: {dept_id: 'gm'|'opm'}; directors: {dept_id: user_id}"""
    await db.execute(text("DELETE FROM approval_dept_routing"))
    directors = directors or {}
    for dept_id, code in rows.items():
        await db.execute(text(
            "INSERT INTO approval_dept_routing (dept_id, gm_or_opm, director_user_id, supervisor_enabled) "
            "VALUES (:d, :g, :dir, false)"),
            {"d": str(dept_id), "g": code, "dir": str(directors[dept_id]) if dept_id in directors else None})
    await db.commit()


@pytest.mark.asyncio
async def test_mapped_dept_ids_returns_only_depts_mapped_to_that_role(db):
    gm_dept, opm_dept = uuid.uuid4(), uuid.uuid4()
    await _set_routing(db, {gm_dept: "gm", opm_dept: "opm"})

    assert await _mapped_dept_ids(db, "gm") == [gm_dept]
    assert await _mapped_dept_ids(db, "opm") == [opm_dept]


@pytest.mark.asyncio
async def test_dept_with_default_gm_row_is_visible_to_gm(db):
    """★ 相对旧 JSONB 的有意语义变化。

    旧:未列在 dept_gm_opm_mapping 里的部门,GM 看不见。
    新:approval_dept_routing 给每个部门都有行,未配置的取默认 'gm' → GM 看得见。
    这与审批路由一致(engine 的 gm_opm.get(dept, 'gm') 本就把它派给 GM 审),
    旧代码"GM 要审却看不见"才是 bug。此测试锁住新语义,防止它被无意改回。
    """
    default_dept = uuid.uuid4()
    await _set_routing(db, {default_dept: "gm"})   # seed_routing 对未配置部门就是写 'gm'
    assert default_dept in await _mapped_dept_ids(db, "gm")


@pytest.mark.asyncio
async def test_opm_dept_not_visible_to_gm(db):
    """负向:映射给 OPM 的部门,GM 不该看见(防止实现退化成'返回所有部门')。"""
    opm_dept = uuid.uuid4()
    await _set_routing(db, {opm_dept: "opm"})
    assert await _mapped_dept_ids(db, "gm") == []


@pytest.mark.asyncio
async def test_director_dept_ids_returns_depts_this_user_directs(db):
    d1, d2, me, other = uuid.uuid4(), uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    await _set_routing(db, {d1: "gm", d2: "gm"}, directors={d1: me, d2: other})

    assert await _director_dept_ids(db, me) == [d1]
    assert await _director_dept_ids(db, other) == [d2]


@pytest.mark.asyncio
async def test_empty_mapping_returns_empty_not_error(db):
    await _set_routing(db, {})

    assert await _mapped_dept_ids(db, "gm") == []
    assert await _director_dept_ids(db, uuid.uuid4()) == []
