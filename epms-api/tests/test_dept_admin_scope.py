"""dept_admin 作用域回归测试。

Bug: access_scope 里把受限角色写成了 `department_admin`,而系统里真实的角色代码
是 `dept_admin`(见 crud/config.py / identity seed_authz.py)。拼写漂移导致:
  - `dept_admin` 不在 `_RESTRICTED_ROLES` → `_has_unrestricted_special_role` 误判为
    "持有非受限特殊角色" → 该用户被当成 unrestricted,越权看到全公司单据;
  - 且当 base role 是 requester 时,list_invoices 的 own_uploads 分支反而把发票
    收窄成"只有本人上传的",requester+dept_admin 用户看不到别人(AP)上传、
    却已匹配到自己 PO 的发票(PO-665-2607-01 / Kris Enriquez 现网案例)。

这些测试锁死:dept_admin 是受限角色,且不误伤真正非受限的角色。
"""
import uuid

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.access_scope import (
    _has_unrestricted_special_role,
    visible_pr_subquery,
)
from app.crud import config as config_crud


@pytest.fixture
async def db(test_engine):
    """Session with the approval_dept_routing table present (director derivation
    in _effective_role_codes queries it) — mirrors test_access_scope_dept.py."""
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


async def _grant_user_role(db, user_id: uuid.UUID, role_code: str) -> None:
    await db.execute(
        text("INSERT INTO user_roles (user_id, role_code) VALUES (:u, :r)"),
        {"u": str(user_id), "r": role_code},
    )
    await db.commit()


@pytest.mark.asyncio
async def test_dept_admin_is_treated_as_restricted(db):
    """★ 根因锁:持有 dept_admin(额外角色)的用户必须被判为受限,而不是 unrestricted。

    修前 `_RESTRICTED_ROLES` 只含 `department_admin`,`dept_admin` 落到"非受限特殊
    角色" → 返回 True(unrestricted)。这条断言在修前失败、修后通过。
    """
    user_id = uuid.uuid4()
    await _grant_user_role(db, user_id, "dept_admin")

    assert await _has_unrestricted_special_role(db, "requester", user_id) is False


@pytest.mark.asyncio
async def test_requester_plus_dept_admin_scope_is_not_none(db):
    """requester + dept_admin 应得到一个受限的 PR 作用域(非 None),这样 list_invoices
    才会把 po_subq 作为发票可见性的 OR 条件之一,而不是退化成 own_uploads-only。"""
    user_id = uuid.uuid4()
    await _grant_user_role(db, user_id, "dept_admin")

    subq = await visible_pr_subquery(db, {"role": "requester", "sub": str(user_id)})

    assert subq is not None  # None == unrestricted == the bug


@pytest.mark.asyncio
async def test_genuinely_unrestricted_role_still_unrestricted(db):
    """负向防过纠:真正的非受限角色(procurement_manager)仍应判为 unrestricted。"""
    user_id = uuid.uuid4()
    await _grant_user_role(db, user_id, "procurement_manager")

    assert await _has_unrestricted_special_role(db, "requester", user_id) is True
    assert await visible_pr_subquery(db, {"role": "requester", "sub": str(user_id)}) is None
