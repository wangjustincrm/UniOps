"""access_scope 的部门可见性表征测试。

锁住 _mapped_dept_ids / _director_dept_ids 的现有行为,以便把数据源从
company_config.dept_* JSONB 换成 approval_dept_routing 表时能证明可见性不变。
"""
import uuid

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.access_scope import _mapped_dept_ids, _director_dept_ids
from app.crud import config as config_crud
from app.models.config import CompanyConfig


@pytest.fixture
async def db(test_engine):
    """Session pre-seeded with the CompanyConfig singleton row.

    conftest.py has no plain `db` fixture — every test module builds its own
    sessionmaker off the session-scoped `test_engine` (see test_pr_scoping.py /
    test_config_director_mapping.py). `_mapped_dept_ids` / `_director_dept_ids`
    both do `select(CompanyConfig).limit(1)).scalar_one_or_none()`, but this
    file's tests use `.scalar_one()` (matching the brief) which requires the
    singleton row to already exist — so ensure it via config_crud.get_or_create
    first, exactly like test_pr_scoping.py's test_director_sees_mapped_dept_prs
    does before writing to dept_director_mapping.
    """
    factory = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as session:
        await config_crud.get_or_create(session)
        await session.commit()
        yield session


@pytest.mark.asyncio
async def test_mapped_dept_ids_returns_only_depts_mapped_to_that_role(db):
    gm_dept, opm_dept = uuid.uuid4(), uuid.uuid4()
    cfg = (await db.execute(select(CompanyConfig).limit(1))).scalar_one()
    cfg.dept_gm_opm_mapping = {str(gm_dept): "gm", str(opm_dept): "opm"}
    await db.commit()

    assert await _mapped_dept_ids(db, "gm") == [gm_dept]
    assert await _mapped_dept_ids(db, "opm") == [opm_dept]


@pytest.mark.asyncio
async def test_mapped_dept_ids_excludes_unmapped_dept(db):
    """未映射的部门 GM 看不见 —— 这正是换数据源时最容易悄悄扩权的点。"""
    gm_dept, unlisted = uuid.uuid4(), uuid.uuid4()
    cfg = (await db.execute(select(CompanyConfig).limit(1))).scalar_one()
    cfg.dept_gm_opm_mapping = {str(gm_dept): "gm"}
    await db.commit()

    assert unlisted not in await _mapped_dept_ids(db, "gm")


@pytest.mark.asyncio
async def test_director_dept_ids_returns_depts_this_user_directs(db):
    d1, d2, me, other = uuid.uuid4(), uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    cfg = (await db.execute(select(CompanyConfig).limit(1))).scalar_one()
    cfg.dept_director_mapping = {str(d1): str(me), str(d2): str(other)}
    await db.commit()

    assert await _director_dept_ids(db, me) == [d1]
    assert await _director_dept_ids(db, other) == [d2]


@pytest.mark.asyncio
async def test_empty_mapping_returns_empty_not_error(db):
    cfg = (await db.execute(select(CompanyConfig).limit(1))).scalar_one()
    cfg.dept_gm_opm_mapping, cfg.dept_director_mapping = {}, {}
    await db.commit()

    assert await _mapped_dept_ids(db, "gm") == []
    assert await _director_dept_ids(db, uuid.uuid4()) == []
