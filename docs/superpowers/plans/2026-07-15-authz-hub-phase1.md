# 权限中枢一期 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把角色×权限矩阵从 EPMS 公司配置 JSONB 迁到 identity-api 五张正经表，加多角色（主+附加），epms 变纯转发网关，管理 UI 挪 Portal。

**Architecture:** identity-api 新增 authz 域（5 表 + `/authz/*` + `/me/permissions`），为唯一事实源；epms-api 的 `/config/*` 权限端点全部改为服务端代理（读带 60s 缓存+宕机回落冻结 JSONB），内部消费经新 `authz_client` 单点替换；三处前端 hook 改走 epms 代理 `GET /config/me/permissions`；Portal 新增 Access Control 管理页（矩阵+用户角色两 tab）。浏览器永不直连 identity（无公网域名，不动 DNS/Caddy/CORS）。

**Tech Stack:** FastAPI + SQLAlchemy async + alembic（identity `version_table="alembic_version_identity"`）、httpx（epms 已有 0.28.1）、React + TanStack Query + Tailwind（Portal）。

## Global Constraints

- Spec: `docs/superpowers/specs/2026-07-14-authz-hub-phase1-design.md`（含 2026-07-15 修订：epms 纯代理、custom_roles 页签下线）。
- **17 个权限键原样迁移，键名一字不改**（三处前端硬编码引用）。
- **零行为变化**：迁移+切换后 `GET /config/role-permissions` 返回值与切换前逐键相等。
- **JWT 不动**；`users.role` 仍是主角色；附加角色只影响 `/me/permissions` 并集。
- UI 文案**全英文**（项目铁律）；Portal 页面必须 PortalChromeLayout 包裹。
- ⚠️ 宿主 .env 指向生产库：**alembic/seed 一律容器内跑**（`docker exec uniops_identity_api ...`）。
- 测试命令（Windows Git Bash）：
  - identity：`cd /c/Project/uniops/identity-api && TEST_PG_PASSWORD=7c0a03bb8c2afef690d1852f8dc3a0195932db5f0f1670e9 ./.venv/Scripts/python -m pytest <file> -q`（本地 Redis 需 uniops_redis 在跑；单进程串行，不并发不后台）
  - epms：`docker exec uniops_epms_api python -m pytest <file> -q`（容器如缺 pytest：先 `docker exec uniops_epms_api pip install -q pytest pytest-asyncio httpx pytest-mock`）
  - 前端 typecheck：`cd <app> && npx tsc -p tsconfig.app.json --noEmit --ignoreDeprecations 6.0`
- git 提交只 add 指定文件，绝不 `-a`/`-A`。

---

### Task 1: identity 数据模型 + 迁移 + seed 脚本

**Files:**
- Create: `identity-api/app/models/authz.py`
- Modify: `identity-api/tests/conftest.py`（import authz 模型注册 metadata，第 21 行邻域）
- Create: `identity-api/alembic/versions/0002_authz_tables.py`
- Create: `identity-api/scripts/__init__.py`（空文件）、`identity-api/scripts/seed_authz.py`
- Test: `identity-api/tests/test_authz_seed.py`

**Interfaces:**
- Produces: ORM 模型 `RoleDef(code,label,sort,is_active)`、`PermissionDef(key,module,label,sort)`、`RolePermission(role_code,permission_key,updated_by,updated_at)`、`RolePermissionLock(role_code,permission_key)`、`UserRole(user_id,role_code)`；seed 幂等函数 `seed_authz(conn) -> dict`（返回计数）。Task 2 直接查这些表。

- [ ] **Step 1: 写模型**

`identity-api/app/models/authz.py`：

```python
"""Authz hub tables — roles, permissions, matrix, locks, additional user roles.

Source of truth for the Access Control Matrix (migrated out of epms
company_config.role_permissions JSONB, 2026-07). users.role stays the
PRIMARY role; user_roles holds ADDITIONAL roles (union in /me/permissions).
"""
import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class RoleDef(Base):
    __tablename__ = "role_defs"
    code: Mapped[str] = mapped_column(String(50), primary_key=True)
    label: Mapped[str] = mapped_column(String(100), nullable=False)
    sort: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)


class PermissionDef(Base):
    __tablename__ = "permission_defs"
    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    module: Mapped[str] = mapped_column(String(20), nullable=False)
    label: Mapped[str] = mapped_column(String(120), nullable=False)
    sort: Mapped[int] = mapped_column(Integer, nullable=False, default=0)


class RolePermission(Base):
    """Granted cells only — a row means allowed=True."""
    __tablename__ = "role_permissions"
    role_code: Mapped[str] = mapped_column(
        ForeignKey("role_defs.code", ondelete="CASCADE"), primary_key=True)
    permission_key: Mapped[str] = mapped_column(
        ForeignKey("permission_defs.key", ondelete="CASCADE"), primary_key=True)
    updated_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now())


class RolePermissionLock(Base):
    """Forced-True cells the UI may not edit (seeded from epms LOCKED_PERMISSIONS)."""
    __tablename__ = "role_permission_locks"
    role_code: Mapped[str] = mapped_column(
        ForeignKey("role_defs.code", ondelete="CASCADE"), primary_key=True)
    permission_key: Mapped[str] = mapped_column(
        ForeignKey("permission_defs.key", ondelete="CASCADE"), primary_key=True)


class UserRole(Base):
    """ADDITIONAL roles per user (primary stays users.role)."""
    __tablename__ = "user_roles"
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    role_code: Mapped[str] = mapped_column(
        ForeignKey("role_defs.code", ondelete="CASCADE"), primary_key=True)
```

conftest 第 21 行 import 列表加 `authz`：`from app.models import audit, authz, config, sod, user  # noqa: F401`

- [ ] **Step 2: 写迁移**

`identity-api/alembic/versions/0002_authz_tables.py`（`down_revision = "0001_sod_audit"`）：

```python
"""authz hub tables"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

revision = "0002_authz_tables"
down_revision = "0001_sod_audit"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "role_defs",
        sa.Column("code", sa.String(50), primary_key=True),
        sa.Column("label", sa.String(100), nullable=False),
        sa.Column("sort", sa.Integer, nullable=False, server_default="0"),
        sa.Column("is_active", sa.Boolean, nullable=False, server_default="true"),
    )
    op.create_table(
        "permission_defs",
        sa.Column("key", sa.String(64), primary_key=True),
        sa.Column("module", sa.String(20), nullable=False),
        sa.Column("label", sa.String(120), nullable=False),
        sa.Column("sort", sa.Integer, nullable=False, server_default="0"),
    )
    op.create_table(
        "role_permissions",
        sa.Column("role_code", sa.String(50),
                  sa.ForeignKey("role_defs.code", ondelete="CASCADE"), primary_key=True),
        sa.Column("permission_key", sa.String(64),
                  sa.ForeignKey("permission_defs.key", ondelete="CASCADE"), primary_key=True),
        sa.Column("updated_by", UUID(as_uuid=True), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
    )
    op.create_table(
        "role_permission_locks",
        sa.Column("role_code", sa.String(50),
                  sa.ForeignKey("role_defs.code", ondelete="CASCADE"), primary_key=True),
        sa.Column("permission_key", sa.String(64),
                  sa.ForeignKey("permission_defs.key", ondelete="CASCADE"), primary_key=True),
    )
    op.create_table(
        "user_roles",
        sa.Column("user_id", UUID(as_uuid=True),
                  sa.ForeignKey("users.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("role_code", sa.String(50),
                  sa.ForeignKey("role_defs.code", ondelete="CASCADE"), primary_key=True),
    )


def downgrade() -> None:
    op.drop_table("user_roles")
    op.drop_table("role_permission_locks")
    op.drop_table("role_permissions")
    op.drop_table("permission_defs")
    op.drop_table("role_defs")
```

- [ ] **Step 3: 写 seed 脚本**

`identity-api/scripts/seed_authz.py`。常量为 **epms `app/crud/config.py` 2026-07-15 时点的一次性拷贝**（迁移专用，日后以 identity 表为准）。核心逻辑：

```python
"""One-shot idempotent seed: epms company_config matrix -> identity authz tables.

Run INSIDE the identity container (host .env points at prod!):
    docker exec uniops_identity_api python -m scripts.seed_authz
identity 与 epms 共享同一物理库,直接 SQL 读 company_config。
"""
import asyncio
import json

import sqlalchemy as sa

from app.db.base import AsyncSessionLocal

MODULE_BY_KEY = {
    "view_pr": "epms", "view_po": "epms", "view_gr": "epms",
    "view_invoice": "epms", "view_pa": "epms", "create_pr": "epms",
    "create_gr": "epms", "invoice_upload": "epms", "vendor_master": "epms",
    "parts_catalog": "epms", "admin_panel": "epms", "data_maintenance": "epms",
    "view_budget_dashboard": "finance", "view_budget_plans": "finance",
    "view_finance": "finance",
    "view_booking": "booking", "manage_meeting_rooms": "booking",
}
PERMISSION_KEYS = list(MODULE_BY_KEY)  # keeps epms UI order

ROLE_LABELS = {  # built-in 17
    "requester": "Requester", "dept_admin": "Department Admin",
    "dept_manager": "Department Manager", "supervisor": "Supervisor",
    "director": "Director", "gm": "General Manager", "opm": "Operations Manager",
    "procurement_officer": "Procurement Officer",
    "procurement_manager": "Procurement Manager",
    "warehouse_staff": "Warehouse Staff", "ap_clerk": "AP Clerk",
    "finance_bp": "Finance BP", "finance_manager": "Finance Manager",
    "vendor_manager": "Vendor Manager", "cfo": "CFO", "auditor": "Auditor",
    "system_admin": "System Admin",
}

LOCKED = {
    "requester": {"view_pr"},
    "procurement_officer": {"view_pr", "view_po", "view_gr"},
    "procurement_manager": {"view_pr", "view_po", "view_gr"},
    "warehouse_staff": {"view_gr"},
    "ap_clerk": {"view_invoice", "view_pa"},
    "finance_bp": {"view_pa"},
    "finance_manager": {"view_pa"},
    "system_admin": {"admin_panel"},
}

# —— 与 epms get_effective_role_permissions 等价的默认矩阵(一次性拷贝) ——
_VIEW_ALL = {k: True for k in ("view_pr", "view_po", "view_gr", "view_invoice", "view_pa")}
_FINANCE_ALL = {"view_budget_dashboard": True, "view_budget_plans": True, "view_finance": True}
_BOOKING = {"view_booking": True}
_BUDGET_VIEW = {"view_budget_dashboard": True, "view_budget_plans": True}

def _p(**kw):
    base = {k: False for k in PERMISSION_KEYS}
    base.update(kw)
    return base

DEFAULTS = {
    "requester":           _p(create_pr=True, create_gr=True, **_VIEW_ALL, **_BOOKING),
    "dept_admin":          _p(create_pr=True, create_gr=True, **_VIEW_ALL, **_BOOKING),
    "dept_manager":        _p(create_pr=True, create_gr=True, **_VIEW_ALL, **_BUDGET_VIEW, **_BOOKING),
    "supervisor":          _p(view_pr=True, **_BOOKING),
    "director":            _p(view_pr=True, view_pa=True, **_BOOKING),
    "gm":                  _p(create_pr=True, create_gr=True, **_VIEW_ALL, **_BOOKING),
    "opm":                 _p(create_pr=True, create_gr=True, **_VIEW_ALL, **_BOOKING),
    "procurement_officer": _p(create_gr=True, vendor_master=True, parts_catalog=True, **_VIEW_ALL, **_BOOKING),
    "procurement_manager": _p(create_gr=True, vendor_master=True, parts_catalog=True, **_VIEW_ALL, **_BOOKING),
    "warehouse_staff":     _p(create_gr=True, view_gr=True, **_BOOKING),
    "ap_clerk":            _p(create_gr=True, invoice_upload=True, **_VIEW_ALL, **_FINANCE_ALL, **_BOOKING),
    "finance_bp":          _p(create_gr=True, **_VIEW_ALL, **_FINANCE_ALL, **_BOOKING),
    "finance_manager":     _p(create_pr=True, create_gr=True, admin_panel=True, **_VIEW_ALL, **_FINANCE_ALL, **_BOOKING),
    "vendor_manager":      _p(vendor_master=True, admin_panel=True, **_BOOKING),
    "cfo":                 _p(**_VIEW_ALL, **_FINANCE_ALL, **_BOOKING),
    "auditor":             _p(**_VIEW_ALL, **_BOOKING),
    "system_admin":        {k: True for k in PERMISSION_KEYS},
}


def compute_effective(stored: dict, custom_roles: list) -> dict[str, dict[str, bool]]:
    result = {}
    for role, defaults in DEFAULTS.items():
        merged = {k: bool(stored.get(role, {}).get(k, defaults[k])) for k in PERMISSION_KEYS}
        for k in LOCKED.get(role, set()):
            merged[k] = True
        result[role] = merged
    for cr in custom_roles:
        if not cr.get("is_active", True):
            continue
        code = cr["code"]
        result[code] = {k: bool(stored.get(code, {}).get(k, False)) for k in PERMISSION_KEYS}
    return result


async def seed_authz(session) -> dict:
    row = (await session.execute(sa.text(
        "SELECT role_permissions, custom_roles FROM company_config LIMIT 1"))).first()
    stored = row[0] if row else {}
    custom = row[1] if row else []
    if isinstance(stored, str):
        stored = json.loads(stored)
    if isinstance(custom, str):
        custom = json.loads(custom)

    for i, (code, label) in enumerate(ROLE_LABELS.items()):
        await session.execute(sa.text(
            "INSERT INTO role_defs(code,label,sort,is_active) VALUES (:c,:l,:s,true) "
            "ON CONFLICT (code) DO NOTHING"), {"c": code, "l": label, "s": i})
    for cr in custom:
        await session.execute(sa.text(
            "INSERT INTO role_defs(code,label,sort,is_active) VALUES (:c,:l,900,:a) "
            "ON CONFLICT (code) DO NOTHING"),
            {"c": cr["code"], "l": cr.get("label", cr["code"]), "a": cr.get("is_active", True)})

    for i, (key, module) in enumerate(MODULE_BY_KEY.items()):
        label = key.replace("_", " ").title()
        await session.execute(sa.text(
            "INSERT INTO permission_defs(key,module,label,sort) VALUES (:k,:m,:l,:s) "
            "ON CONFLICT (key) DO NOTHING"), {"k": key, "m": module, "l": label, "s": i})

    granted = 0
    for role, perms in compute_effective(stored, custom).items():
        for key, val in perms.items():
            if val:
                r = await session.execute(sa.text(
                    "INSERT INTO role_permissions(role_code,permission_key) VALUES (:r,:k) "
                    "ON CONFLICT DO NOTHING"), {"r": role, "k": key})
                granted += r.rowcount or 0
    for role, keys in LOCKED.items():
        for key in keys:
            await session.execute(sa.text(
                "INSERT INTO role_permission_locks(role_code,permission_key) VALUES (:r,:k) "
                "ON CONFLICT DO NOTHING"), {"r": role, "k": key})
    return {"granted_inserted": granted}


async def main():
    async with AsyncSessionLocal() as session:
        counts = await seed_authz(session)
        await session.commit()
        print(f"seed_authz done: {counts}")


if __name__ == "__main__":
    asyncio.run(main())
```

- [ ] **Step 4: 写失败测试**

`identity-api/tests/test_authz_seed.py`：

```python
"""Seed correctness + idempotency against a fabricated company_config row."""
import pytest
import sqlalchemy as sa

from scripts.seed_authz import seed_authz, compute_effective, PERMISSION_KEYS, DEFAULTS

pytestmark = pytest.mark.asyncio


async def test_seed_matches_effective_matrix(db_session):
    import json
    await db_session.execute(sa.text(
        "CREATE TABLE IF NOT EXISTS company_config "
        "(role_permissions jsonb default '{}'::jsonb, custom_roles jsonb default '[]'::jsonb)"))
    await db_session.execute(sa.text("DELETE FROM company_config"))
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
```


- [ ] **Step 5: 跑测试确认失败**（模型/脚本未建时 import error）

`cd /c/Project/uniops/identity-api && TEST_PG_PASSWORD=7c0a03bb8c2afef690d1852f8dc3a0195932db5f0f1670e9 ./.venv/Scripts/python -m pytest tests/test_authz_seed.py -q` → FAIL

- [ ] **Step 6: 落实现（Step 1-3 的文件）→ 测试转绿**

- [ ] **Step 7: 容器内跑迁移+seed（dev）**

```bash
docker exec uniops_identity_api alembic upgrade head
docker exec uniops_identity_api python -m scripts.seed_authz
docker exec uniops_postgres psql -U epms -d epms -c "SELECT count(*) FROM role_defs; SELECT count(*) FROM role_permissions"
```
预期:role_defs=17、role_permissions>0。

- [ ] **Step 8: Commit**

```bash
git add identity-api/app/models/authz.py identity-api/tests/conftest.py \
  identity-api/alembic/versions/0002_authz_tables.py identity-api/scripts/ \
  identity-api/tests/test_authz_seed.py
git commit -m "feat(identity): authz tables + idempotent seed from epms matrix"
```

---

### Task 2: identity `/authz/*` + `/me/permissions` 端点

**Files:**
- Create: `identity-api/app/api/v1/authz.py`
- Modify: `identity-api/app/main.py:27-29`（挂 router）
- Test: `identity-api/tests/test_authz_api.py`

**Interfaces:**
- Consumes: Task 1 的 5 张表。
- Produces（Task 3 epms 代理按此形状转发）:
  - `GET /identity/v1/authz/matrix` → `{role: {key: bool}}`（全键,授予∪锁定）
  - `PATCH /identity/v1/authz/matrix`，body `{"changes": {role: {key: bool}}}` → 更新后矩阵;锁定格 409 `{"detail": {"locked": [{"role":..,"key":..}]}}`;未知角色/键 422
  - `GET /identity/v1/authz/defs` → `{"roles":[{code,label,sort,is_active}], "permissions":[{key,module,label,sort,locked_for:[role]}]}`
  - `GET /identity/v1/me/permissions` → `{"permissions": {key: bool}, "roles": [primary, ...additional]}`
  - `PUT /identity/v1/authz/users/{user_id}/roles`，body `{"primary": str, "additional": [str]}` → 204

- [ ] **Step 1: 写失败测试**

`identity-api/tests/test_authz_api.py`（conftest 已有 admin/普通用户 client fixture 模式，沿用 `create_access_token(sub, role)` 造 client；seed 用 Task 1 的 `seed_authz`）：

```python
import uuid
import pytest
import sqlalchemy as sa
from httpx import ASGITransport, AsyncClient

from app.core.security import create_access_token
from app.main import app
from scripts.seed_authz import seed_authz

pytestmark = pytest.mark.asyncio
BASE = "/identity/v1"


@pytest.fixture
async def seeded(db_session):
    await db_session.execute(sa.text(
        "CREATE TABLE IF NOT EXISTS company_config "
        "(role_permissions jsonb default '{}'::jsonb, custom_roles jsonb default '[]'::jsonb)"))
    await db_session.execute(sa.text(
        "INSERT INTO company_config DEFAULT VALUES"))
    await seed_authz(db_session)
    await db_session.flush()


def _client(role="system_admin", sub=None):
    token = create_access_token(sub or str(uuid.uuid4()), role)
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test",
                       headers={"Authorization": f"Bearer {token}"})


async def test_matrix_shape(seeded):
    async with _client("requester") as c:
        r = await c.get(f"{BASE}/authz/matrix")
    assert r.status_code == 200
    m = r.json()
    assert m["requester"]["view_pr"] is True     # locked
    assert m["system_admin"]["admin_panel"] is True


async def test_patch_requires_admin(seeded):
    async with _client("requester") as c:
        r = await c.patch(f"{BASE}/authz/matrix",
                          json={"changes": {"auditor": {"view_pr": False}}})
    assert r.status_code == 403


async def test_patch_toggles_and_rejects_locked(seeded):
    async with _client() as c:
        r = await c.patch(f"{BASE}/authz/matrix",
                          json={"changes": {"auditor": {"view_pr": False}}})
        assert r.status_code == 200
        assert r.json()["auditor"]["view_pr"] is False
        r2 = await c.patch(f"{BASE}/authz/matrix",
                           json={"changes": {"requester": {"view_pr": False}}})
        assert r2.status_code == 409
        assert r2.json()["detail"]["locked"] == [{"role": "requester", "key": "view_pr"}]
        r3 = await c.patch(f"{BASE}/authz/matrix",
                           json={"changes": {"nosuch": {"view_pr": True}}})
        assert r3.status_code == 422


async def test_me_permissions_union(seeded, db_session):
    uid = uuid.uuid4()
    await db_session.execute(sa.text(
        "INSERT INTO users (id, email, hashed_password, full_name, role) "
        "VALUES (:i, :e, 'x', 'T User', 'warehouse_staff')"),
        {"i": str(uid), "e": f"{uid}@t.co"})
    await db_session.execute(sa.text(
        "INSERT INTO user_roles(user_id, role_code) VALUES (:i, 'ap_clerk')"),
        {"i": str(uid)})
    await db_session.flush()
    async with _client("warehouse_staff", str(uid)) as c:
        r = await c.get(f"{BASE}/me/permissions")
    body = r.json()
    assert body["roles"] == ["warehouse_staff", "ap_clerk"]
    assert body["permissions"]["view_gr"] is True       # from primary
    assert body["permissions"]["view_invoice"] is True  # from additional (union)


async def test_put_user_roles_transactional(seeded, db_session):
    uid = uuid.uuid4()
    await db_session.execute(sa.text(
        "INSERT INTO users (id, email, hashed_password, full_name, role) "
        "VALUES (:i, :e, 'x', 'T2', 'requester')"), {"i": str(uid), "e": f"{uid}@t.co"})
    await db_session.flush()
    async with _client() as c:
        r = await c.put(f"{BASE}/authz/users/{uid}/roles",
                        json={"primary": "auditor", "additional": ["cfo", "vendor_manager"]})
        assert r.status_code == 204
        r2 = await c.put(f"{BASE}/authz/users/{uid}/roles",
                         json={"primary": "nosuch", "additional": []})
        assert r2.status_code == 422
    role = (await db_session.execute(sa.text(
        "SELECT role FROM users WHERE id=:i"), {"i": str(uid)})).scalar_one()
    assert role == "auditor"
    add = (await db_session.execute(sa.text(
        "SELECT role_code FROM user_roles WHERE user_id=:i ORDER BY role_code"),
        {"i": str(uid)})).scalars().all()
    assert add == ["cfo", "vendor_manager"]
```

- [ ] **Step 2: 跑测试确认失败**（404 路由不存在）

- [ ] **Step 3: 实现 `identity-api/app/api/v1/authz.py`**

```python
"""Authz hub — matrix / defs / user roles / my effective permissions."""
import uuid

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from sqlalchemy import delete, select, text

from app.core.deps import CurrentUserPayload, SessionDep
from app.models.authz import (PermissionDef, RoleDef, RolePermission,
                              RolePermissionLock, UserRole)
from app.models.user import User

router = APIRouter(tags=["authz"])


def _require_admin(user: dict) -> uuid.UUID:
    if user.get("role") != "system_admin":
        raise HTTPException(status_code=403, detail="system_admin only")
    return uuid.UUID(user["sub"])


async def _load(db):
    roles = (await db.execute(select(RoleDef).order_by(RoleDef.sort))).scalars().all()
    perms = (await db.execute(select(PermissionDef).order_by(PermissionDef.sort))).scalars().all()
    granted = {(r.role_code, r.permission_key)
               for r in (await db.execute(select(RolePermission))).scalars().all()}
    locks = {(l.role_code, l.permission_key)
             for l in (await db.execute(select(RolePermissionLock))).scalars().all()}
    return roles, perms, granted, locks


def _matrix(roles, perms, granted, locks) -> dict:
    return {r.code: {p.key: ((r.code, p.key) in granted or (r.code, p.key) in locks)
                     for p in perms}
            for r in roles}


@router.get("/authz/matrix")
async def get_matrix(db: SessionDep, _: CurrentUserPayload) -> dict:
    return _matrix(*await _load(db))


class MatrixPatch(BaseModel):
    changes: dict[str, dict[str, bool]]


@router.patch("/authz/matrix")
async def patch_matrix(body: MatrixPatch, db: SessionDep, user: CurrentUserPayload) -> dict:
    actor = _require_admin(user)
    roles, perms, granted, locks = await _load(db)
    role_codes = {r.code for r in roles}
    perm_keys = {p.key for p in perms}
    hit_locks = []
    for role, kv in body.changes.items():
        if role not in role_codes:
            raise HTTPException(status_code=422, detail=f"Unknown role '{role}'")
        for key, val in kv.items():
            if key not in perm_keys:
                raise HTTPException(status_code=422, detail=f"Unknown permission '{key}'")
            if (role, key) in locks and val is False:
                hit_locks.append({"role": role, "key": key})
    if hit_locks:
        raise HTTPException(status_code=409, detail={"locked": hit_locks})
    for role, kv in body.changes.items():
        for key, val in kv.items():
            if val:
                await db.execute(text(
                    "INSERT INTO role_permissions(role_code,permission_key,updated_by) "
                    "VALUES (:r,:k,:u) ON CONFLICT (role_code,permission_key) "
                    "DO UPDATE SET updated_by=:u, updated_at=now()"),
                    {"r": role, "k": key, "u": str(actor)})
            else:
                await db.execute(delete(RolePermission).where(
                    RolePermission.role_code == role,
                    RolePermission.permission_key == key))
    return _matrix(*await _load(db))


@router.get("/authz/defs")
async def get_defs(db: SessionDep, _: CurrentUserPayload) -> dict:
    roles, perms, _granted, locks = await _load(db)
    locked_for: dict[str, list[str]] = {}
    for role, key in locks:
        locked_for.setdefault(key, []).append(role)
    return {
        "roles": [{"code": r.code, "label": r.label, "sort": r.sort,
                   "is_active": r.is_active} for r in roles],
        "permissions": [{"key": p.key, "module": p.module, "label": p.label,
                         "sort": p.sort, "locked_for": sorted(locked_for.get(p.key, []))}
                        for p in perms],
    }


@router.get("/me/permissions")
async def my_permissions(db: SessionDep, user: CurrentUserPayload) -> dict:
    uid = uuid.UUID(user["sub"])
    u = (await db.execute(select(User).where(User.id == uid))).scalar_one_or_none()
    if u is None:
        raise HTTPException(status_code=404, detail="User not found")
    active = {r.code for r in (await db.execute(
        select(RoleDef).where(RoleDef.is_active.is_(True)))).scalars().all()}
    additional = [ur.role_code for ur in (await db.execute(
        select(UserRole).where(UserRole.user_id == uid))).scalars().all()
        if ur.role_code in active]
    my_roles = ([u.role] if u.role in active else []) + sorted(additional)
    roles, perms, granted, locks = await _load(db)
    result = {p.key: any((rc, p.key) in granted or (rc, p.key) in locks
                         for rc in my_roles) for p in perms}
    return {"permissions": result, "roles": ([u.role] + sorted(additional))}


class UserRolesPut(BaseModel):
    primary: str
    additional: list[str] = []


@router.put("/authz/users/{user_id}/roles", status_code=204)
async def put_user_roles(user_id: uuid.UUID, body: UserRolesPut,
                         db: SessionDep, user: CurrentUserPayload) -> None:
    _require_admin(user)
    u = (await db.execute(select(User).where(User.id == user_id))).scalar_one_or_none()
    if u is None:
        raise HTTPException(status_code=404, detail="User not found")
    codes = {r.code for r in (await db.execute(select(RoleDef))).scalars().all()}
    if body.primary not in codes:
        raise HTTPException(status_code=422, detail=f"Unknown role '{body.primary}'")
    bad = [c for c in body.additional if c not in codes]
    if bad:
        raise HTTPException(status_code=422, detail=f"Unknown roles {bad}")
    u.role = body.primary
    await db.execute(delete(UserRole).where(UserRole.user_id == user_id))
    for code in set(body.additional) - {body.primary}:
        db.add(UserRole(user_id=user_id, role_code=code))
```

main.py 挂载：`from app.api.v1.authz import router as authz_router` + `app.include_router(authz_router, prefix="/identity/v1")`

- [ ] **Step 4: 跑测试转绿**（含既有 `tests/test_auth_flow.py` 回归）

- [ ] **Step 5: Commit**

```bash
git add identity-api/app/api/v1/authz.py identity-api/app/main.py identity-api/tests/test_authz_api.py
git commit -m "feat(identity): /authz matrix+defs+user-roles + /me/permissions union"
```

---

### Task 3: epms 转纯代理 + 内部消费切换 + custom-roles CRUD 下线

**Files:**
- Create: `epms-api/app/core/authz_client.py`
- Modify: `epms-api/app/api/v1/config.py:171-234`（authz 端点区）
- Modify: `epms-api/app/core/deps.py:88-95`、`epms-api/app/core/access_scope.py:164-175`
- Test: `epms-api/tests/test_authz_proxy.py`

**Interfaces:**
- Consumes: identity Task 2 端点（epms `settings.IDENTITY_API_URL` 已存在,config.py:91）。
- Produces:
  - `authz_client.get_matrix(db) -> dict[str, dict[str, bool]]`（60s 进程缓存;identity 失败回落 `get_effective_role_permissions(cfg)`）
  - `authz_client.forward(method, path, token, json=None) -> (status, body)` 透传器
  - epms 端点：GET `/config/role-permissions`（缓存读）、GET `/config/locked-permissions`（identity defs→旧形状 `{role:[keys]}`,失败回落 LOCKED_PERMISSIONS）、PATCH `/config/role-permissions`（透传,body 直转 identity `{"changes": body}`）、GET `/config/me/permissions`、GET `/config/authz-defs`、PUT `/config/users/{id}/roles`（均透传）;GET `/config/roles` → identity defs.roles（失败回落 `list_all_roles(cfg)`）;POST/PATCH/DELETE `/config/roles*` 路由删除。

- [ ] **Step 1: 写失败测试**（mock identity,不真连）

`epms-api/tests/test_authz_proxy.py`：

```python
"""epms authz proxy: pass-through, cache, and fallback when identity is down."""
import pytest

import app.core.authz_client as ac

pytestmark = pytest.mark.asyncio

FAKE_MATRIX = {"requester": {"view_pr": True}}


async def test_get_role_permissions_proxies_identity(admin_client, mocker):
    mocker.patch.object(ac, "_fetch_matrix", mocker.AsyncMock(return_value=FAKE_MATRIX))
    ac.invalidate_cache()
    r = await admin_client.get("/api/v1/config/role-permissions")
    assert r.status_code == 200
    assert r.json() == FAKE_MATRIX


async def test_matrix_falls_back_when_identity_down(admin_client, mocker):
    mocker.patch.object(ac, "_fetch_matrix",
                        mocker.AsyncMock(side_effect=RuntimeError("down")))
    ac.invalidate_cache()
    r = await admin_client.get("/api/v1/config/role-permissions")
    assert r.status_code == 200
    body = r.json()                       # frozen JSONB fallback
    assert body["system_admin"]["admin_panel"] is True


async def test_patch_forwards_and_no_local_write(admin_client, mocker):
    fwd = mocker.patch.object(
        ac, "forward", mocker.AsyncMock(return_value=(200, FAKE_MATRIX)))
    r = await admin_client.patch("/api/v1/config/role-permissions",
                                 json={"auditor": {"view_pr": False}})
    assert r.status_code == 200
    assert fwd.await_args.args[0] == "PATCH"
    assert fwd.await_args.kwargs["json"] == {"changes": {"auditor": {"view_pr": False}}}


async def test_patch_502_when_identity_down(admin_client, mocker):
    mocker.patch.object(ac, "forward",
                        mocker.AsyncMock(side_effect=RuntimeError("down")))
    r = await admin_client.patch("/api/v1/config/role-permissions",
                                 json={"auditor": {"view_pr": False}})
    assert r.status_code == 502


async def test_custom_role_crud_removed(admin_client):
    r = await admin_client.post("/api/v1/config/roles", json={"code": "x", "label": "X"})
    assert r.status_code == 405
```

- [ ] **Step 2: 跑测试确认失败**

- [ ] **Step 3: 实现 `authz_client.py`**

```python
"""Cached server-side client for the identity authz hub.

Single choke point: endpoints proxy through here, and the two internal
consumers (deps.require_permission, access_scope) read get_matrix(). On
identity outage reads fall back to the frozen company_config JSONB so EPMS
stays up (writes fail with 502 — no local fallback, no drift).
"""
import time

import httpx

from app.core.config import settings

_TTL = 60.0
_cache: tuple[float, dict] | None = None


def invalidate_cache() -> None:
    global _cache
    _cache = None


async def _fetch_matrix() -> dict:
    async with httpx.AsyncClient(timeout=5.0) as client:
        r = await client.get(f"{settings.IDENTITY_API_URL}/identity/v1/authz/matrix",
                             headers={"Authorization": f"Bearer {settings.SERVICE_TOKEN}"}
                             if getattr(settings, "SERVICE_TOKEN", None) else {})
        r.raise_for_status()
        return r.json()


async def get_matrix(db) -> dict:
    """Effective matrix from identity (60s cache); fallback = frozen JSONB."""
    global _cache
    now = time.monotonic()
    if _cache and now - _cache[0] < _TTL:
        return _cache[1]
    try:
        m = await _fetch_matrix()
        _cache = (now, m)
        return m
    except Exception:
        from app.crud import config as config_crud
        cfg = await config_crud.get_or_create(db)
        return config_crud.get_effective_role_permissions(cfg)


async def forward(method: str, path: str, token: str, json=None) -> tuple[int, dict]:
    """Pass a caller request through to identity, caller's own Bearer token."""
    async with httpx.AsyncClient(timeout=10.0) as client:
        r = await client.request(
            method, f"{settings.IDENTITY_API_URL}/identity/v1{path}",
            headers={"Authorization": f"Bearer {token}"}, json=json)
        body = r.json() if r.content else {}
        return r.status_code, body
```

实现要点（非可选）：
- `_fetch_matrix` 匿名调用会被 identity 401——**identity `GET /authz/matrix` 是登录即可**,所以 get_matrix 缓存路径需要一个 token。做法:`get_matrix(db, token: str | None)`,deps/access_scope 调用点手上都有请求者 token(`BearerToken` 依赖已存在于 epms invoices.py 的模式),端点同理透传;写缓存时 token 只用于取数,矩阵内容与调用者无关。**不引入服务账号**(YAGNI,②期再统一)。上面签名相应改为 `get_matrix(db, token)`、`_fetch_matrix(token)`,测试 mock 不受影响。
- PATCH 透传后调用 `invalidate_cache()`。
- config.py 端点区改写:GET role-permissions → `await authz_client.get_matrix(db, token)`;locked-permissions → `forward("GET","/authz/defs",token)` 成功则转 `{role:[keys]}`(由 permissions[].locked_for 反转),失败回落 `LOCKED_PERMISSIONS` 常量;PATCH → `forward` + 状态码直传(409/422 带原 detail),异常→502;新增三个纯透传端点(me/permissions、authz-defs、users/{id}/roles);GET /config/roles → forward defs 取 roles,失败回落 `list_all_roles(cfg)`;删除 create/update/delete_role 三个路由函数及其 schema import。
- deps.py:88-95 与 access_scope.py:164-175:`perms = await authz_client.get_matrix(db, token)` 替换 `get_effective_role_permissions(cfg)`(两处上下文均已 async 且可取到 token;access_scope 若个别调用点无 token 则传 None→直接走回落,行为等于现状)。

- [ ] **Step 4: 跑测试转绿 + epms 回归**

```bash
docker exec uniops_epms_api python -m pytest tests/test_authz_proxy.py tests/test_admin.py -q
```

- [ ] **Step 5: dev 平价实测（零行为变化的正面证据）**

```bash
# 切换前抓基线(实施本 Task 前先存):curl epms /config/role-permissions > /tmp/matrix_before.json
# 切换后:
curl -s -H "Authorization: Bearer $TOKEN" http://localhost:8000/api/v1/config/role-permissions > /tmp/matrix_after.json
python -c "import json;a=json.load(open('/tmp/matrix_before.json'));b=json.load(open('/tmp/matrix_after.json'));assert a==b,'MATRIX DRIFT';print('parity OK')"
```

- [ ] **Step 6: Commit**

```bash
git add epms-api/app/core/authz_client.py epms-api/app/api/v1/config.py \
  epms-api/app/core/deps.py epms-api/app/core/access_scope.py epms-api/tests/test_authz_proxy.py
git commit -m "feat(epms): authz endpoints become identity proxies with cache+fallback"
```

---

### Task 4: Portal Access Control 管理页

**Files:**
- Create: `portal/src/pages/admin/AccessControl.tsx`
- Modify: `portal/src/App.tsx:35-40`（加路由）、`portal/src/components/layout/navConfig.tsx:69-70`（加导航项）
- Modify: `portal/src/lib/api.ts`（epmsApi 如缺 `put`/`patch` 方法则按既有 get/post 模式补齐——同 `epmsRequest('PUT', ...)` 包装）

**Interfaces:**
- Consumes: epms 代理端点（Task 3）：`GET /config/authz-defs`、`GET /config/role-permissions`、`PATCH /config/role-permissions`、`PUT /config/users/{id}/roles`、`GET /users?page=&page_size=200`（既有 admin 端点,翻页取全）。
- Produces: 路由 `/admin/access-control`;navConfig 项 `{ label: 'Access Control', icon: ShieldCheck, href: 'portal:/admin/access-control', adminOnly: true }`。

- [ ] **Step 1: 页面骨架**（PortalChromeLayout + 两 tab;参照 `portal/src/pages/admin/DataMaintenance.tsx` 的 chrome/守卫写法）

核心结构（完整实现按此展开,全英文文案）：

```tsx
// Tab 1: Permission Matrix — rows grouped by module, columns = roles
// Tab 2: User Roles — all users, primary select + additional multi-select
export default function AccessControl() {
  const [tab, setTab] = useState<'matrix' | 'users'>('matrix')
  const defs = useQuery({ queryKey: ['authz-defs'], queryFn: () => epmsApi.get<AuthzDefs>('/config/authz-defs') })
  const matrix = useQuery({ queryKey: ['authz-matrix'], queryFn: () => epmsApi.get<Matrix>('/config/role-permissions') })
  // local dirty state: Record<`${role}.${key}`, boolean>
  const save = useMutation({
    mutationFn: (changes: Matrix) => epmsApi.patch<Matrix>('/config/role-permissions', changes),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ['authz-matrix'] }); setDirty({}) },
    onError: (e) => setError(e instanceof Error ? e.message : 'Save failed'),
  })
  // Matrix cell: checkbox; disabled + Lock icon when defs.permissions[key].locked_for.includes(role)
  // Module group headers: EPMS / FINANCE / BOOKING (defs.permissions grouped by module)
  ...
}
```

Tab 2 要点：
- 用户取全:`while` 翻页 `GET /users?page=${p}&page_size=200` 直到 `items.length < 200`(**别用默认 page_size=20**);
- 每行:姓名/邮箱/主角色 `<select>`(defs.roles where is_active)/附加角色多选(checkbox 组,过滤掉与主角色相同项)/Save 按钮 → `epmsApi.put(`/config/users/${id}/roles`, {primary, additional})`;
- 409/422 错误信息展示在行内(红字)。

- [ ] **Step 2: 路由+导航**

App.tsx 加 `<Route path="/admin/access-control" element={<ProtectedRoute><AccessControl /></ProtectedRoute>} />`;navConfig.tsx 第 70 行后加上述导航项（icon 用 lucide `ShieldCheck`,import 补上）。

- [ ] **Step 3: typecheck + 手工冒烟**

`cd /c/Project/uniops/portal && npx tsc -p tsconfig.app.json --noEmit --ignoreDeprecations 6.0` → 0 新增错误（对比基线计数）。
dev 冒烟：system_admin 登 Portal → Access Control → 改 auditor 一个非锁定格保存 → 刷新仍在;锁定格灰显;User Roles 给一个测试用户加附加角色保存 → identity `user_roles` 表有行。

- [ ] **Step 4: Commit**

```bash
git add portal/src/pages/admin/AccessControl.tsx portal/src/App.tsx \
  portal/src/components/layout/navConfig.tsx portal/src/lib/api.ts
git commit -m "feat(portal): Access Control admin page (matrix + user roles)"
```

---

### Task 5: 三前端 hook 切换 + EPMS 旧页签下线

**Files:**
- Modify: `portal/src/hooks/useRolePermissions.ts`、`portal/src/components/layout/PortalSidebar.tsx`、`portal/src/pages/PortalHome.tsx`（矩阵查表 → 扁平 perms）
- Modify: `finance/src/hooks/useRolePermissions.ts`（同 portal）
- Modify: `epms/src/hooks/useConfig.ts:55-70`（useRolePermissions 改 `/config/me/permissions`）、`epms/src/components/layout/Sidebar.tsx`（适配扁平形状）
- Modify: `epms/src/pages/admin/AdminPanel.tsx`（删 'access_matrix'、'custom_roles' 两页签:第 67 行邻域数组项、第 3225-3226 行 case、`AccessControlMatrix`/`CustomRolesSection` 组件整段、相关 import/service 调用）
- Modify: `epms/src/services/config.ts:230-240`（删 custom role create/update/delete 方法,保留 listRoles）

**Interfaces:**
- Consumes: `GET /config/me/permissions` → `{permissions: Record<string, boolean>, roles: string[]}`。
- Produces: 三个 app 的 hook 统一返回该形状;所有 `matrix?.[user.role]?.[key]` 调用点改 `perms?.[key]`。

- [ ] **Step 1: portal/finance hook 改写**（两文件同构）

```tsx
import { useQuery } from '@tanstack/react-query'
import { epmsApi } from '@/lib/api'

export type MyPermissions = { permissions: Record<string, boolean>; roles: string[] }

/** Current user's effective permissions (primary ∪ additional roles),
 *  served by identity via the epms proxy. */
export function useRolePermissions() {
  return useQuery<MyPermissions>({
    queryKey: ['my-permissions'],
    queryFn: () => epmsApi.get<MyPermissions>('/config/me/permissions'),
    staleTime: 60_000,
    retry: 1,
  })
}
```

调用点改法（PortalSidebar/PortalHome/finance 各处）:原 `const matrix = useRolePermissions().data; matrix?.[role]?.[key]` → `const perms = useRolePermissions().data?.permissions; perms?.[key]`。navConfig 的可见性判断函数把 `(userRole, matrix)` 参数替换成 `(userRole, perms)`（system_admin 短路逻辑保留）。

- [ ] **Step 2: epms Sidebar 同款切换;AdminPanel 删两页签**

useConfig.ts 的 useRolePermissions 查询改 `/config/me/permissions` 返回 MyPermissions;其 PATCH mutation（若在同 hook 文件）删除——矩阵编辑已移 Portal。AdminPanel 删页签后 `grep -n "AccessControlMatrix\|CustomRolesSection\|access_matrix\|custom_roles" epms/src/pages/admin/AdminPanel.tsx` 必须零命中。

- [ ] **Step 3: 三 app typecheck**

```bash
cd /c/Project/uniops/portal && npx tsc -p tsconfig.app.json --noEmit --ignoreDeprecations 6.0
cd /c/Project/uniops/finance && npx tsc -p tsconfig.app.json --noEmit --ignoreDeprecations 6.0
cd /c/Project/uniops/epms && npx tsc -p tsconfig.app.json --noEmit --ignoreDeprecations 6.0
```
epms 有 80 个既有基线错误——**对比错误计数与文件分布**,新增为零才算过。

- [ ] **Step 4: dev 冒烟**

- 普通用户(带附加角色)登 Portal:侧边栏出现附加角色带来的模块入口(多岗可见性生效);
- EPMS Admin Panel 无 Access Control Matrix/Custom Roles 页签;Users 页 role 下拉仍可改主角色;
- finance 前端 Finance 侧边栏项按 view_finance 正常显隐。

- [ ] **Step 5: Commit**

```bash
git add portal/src/hooks/useRolePermissions.ts portal/src/components/layout/PortalSidebar.tsx \
  portal/src/pages/PortalHome.tsx portal/src/components/layout/navConfig.tsx \
  finance/src/hooks/useRolePermissions.ts \
  epms/src/hooks/useConfig.ts epms/src/components/layout/Sidebar.tsx \
  epms/src/pages/admin/AdminPanel.tsx epms/src/services/config.ts
git commit -m "feat(frontends): switch to /me/permissions; retire EPMS matrix & custom-roles tabs"
```

---

### Task 6: 全量回归 + 发布材料

- [ ] identity 全套:`tests/` 全绿;epms 全套:失败集与基线一致（基线=本计划开工前 main 的失败清单,先跑一次存 /tmp/epms_fail_base.txt）
- [ ] spec §7 平价断言:Task 3 Step 5 的 parity OK 证据留存
- [ ] 发布清单补充:identity migrate(`alembic upgrade head`,经 migrate-prod.sh)+**生产 seed**(`docker compose run --rm identity-api python -m scripts.seed_authz`)——顺序:先 migrate 后 seed 再切流量(同 TAG 一次发布即满足,epms 代理有回落不怕 identity 短暂未 seed)
- [ ] Commit(如有杂项修正) + 汇报
