# 后端门禁统一(②期)Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让权限矩阵真正管住后端——57 个硬编码 `require_roles(...)` 换成 `require_permission("module.action")`,由新的后端共享包 `packages/authz` 统一实现。

**Architecture:** 新建 Python 共享包 `packages/authz`(对外只有 `require_permission` / `effective_permissions`),包内**同库直读** identity 的 `role_permissions ∪ role_permission_locks`——不走 HTTP、不缓存、不需要 token。①期为 epms 建的整套 `authz_client`(HTTP+缓存+宕机回落+写穿透镜像)因此整体退役,`access_scope` 拿不到 token 的老难题一并消失。6 个服务接入共享包(build context 从 `./xxx-api` 提到 `.`,照前端 `packages/shell` 的先例)。

**Tech Stack:** Python 3.12 + FastAPI 依赖注入 + SQLAlchemy async(raw SQL)、Docker 多服务 build context、alembic(identity)。

## Global Constraints

- Spec: `docs/superpowers/specs/2026-07-16-authz-backend-gates-phase2-design.md`。
- **零行为变化是铁律**。每个新键的默认角色集必须**逐字等于**它替换掉的那个 `require_roles(...)` 的**实际**准入集。
- **⚠️ `system_admin` 硬短路**:`require_roles` 实现里有 `if role == "system_admin": return payload`。所以 `_OPENING_WRITE_ROLES = ("finance_manager","finance_bp")` **字面不含 system_admin 但它实际能过**。每个新键的默认值必须算上 system_admin,逐个核对**实际**准入集,不要照抄字面元组。
- **⚠️ 角色并集必须查两源**:`users.role`(主) ∪ `user_roles`(副)。只查 `user_roles` 会丢掉主角色持有者(③期教训)。
- **别把③期的 finance_bp 规则套过来**:③期解决「谁是被指派的审批人」(持有职能≠被指派,故 finance_bp 只认 `user_roles`)。②期问的是「这个人的角色有没有这个权限」——按角色并集查矩阵是**正确**的,主角色 `finance_bp` 的人本来就该有 finance_bp 角色的权限。②期不涉及岗位解析。
- **纯 admin 端点不迁**:`require_roles("system_admin")` 保持原样(vms 3 处、booking 1 处、mdm 的 erp 同步等)。只迁业务门禁。
- **不做缓存**(YAGNI:矩阵 149 授予行 + 13 锁定行,走索引,复用同一连接)。
- UI 文案全英文(本期基本不碰前端)。
- ⚠️ 宿主 `.env` 指向生产库:**alembic/seed 一律容器内跑**(`docker exec uniops_identity_api ...`)。
- git 提交只 add 指定文件,**绝不 `-a`/`-A`**(仓库根有用户的未跟踪二进制)。
- 测试命令:
  - identity/finance/approval: `cd /c/Project/uniops/<svc> && TEST_PG_PASSWORD=7c0a03bb8c2afef690d1852f8dc3a0195932db5f0f1670e9 ./.venv/Scripts/python -m pytest <file> -q`(单进程串行,**不后台不并发**)
  - epms/expense/vms/budget/mdm: `docker exec uniops_<svc>_api python -m pytest <file> -q`(缺 pytest 则 `pip install -q pytest pytest-asyncio httpx pytest-mock`)
  - 前端 typecheck:portal/finance 用 `npx tsc -p tsconfig.app.json --noEmit --ignoreDeprecations 6.0`;**epms 是 TS 5.9.3**,用 `npx tsc -p tsconfig.app.json --noEmit`(基线 **69**)
- **基线失败集**(改动前先各跑一次存档,收尾 diff 必须零新增):epms 72(③期后)、finance 201 pass、expense 83 pass、vms 175 pass、approval 39 pass、identity 20 pass、budget/mdm/booking 各自跑一次记数。

---

### Task 1: `packages/authz` 共享包本体

**Files:**
- Create: `packages/authz/pyproject.toml`、`packages/authz/uniops_authz/__init__.py`、`packages/authz/uniops_authz/core.py`、`packages/authz/README.md`
- Test: `packages/authz/tests/test_core.py`

**Interfaces:**
- Produces(后续所有任务消费):
  - `async def effective_permissions(db, user_id: uuid.UUID, base_role: str) -> dict[str, bool]` —— 用户角色并集 × 生效矩阵
  - `async def user_role_codes(db, user_id: uuid.UUID, base_role: str) -> set[str]` —— 主∪副角色
  - `def bind(get_db_dep, get_user_dep) -> Callable` —— 各服务用自己的依赖 bind 一次,得到本服务的 `require_permission(key)` 工厂;端点写 `Depends(require_permission("epms.po.write"))`。**包不导出 require_permission 本身**(各服务依赖名不同,无法硬写 Depends)

- [ ] **Step 1: 包骨架**

`packages/authz/pyproject.toml`:
```toml
[project]
name = "uniops-authz"
version = "0.1.0"
description = "Shared authorization gate for UniOps backend services"
requires-python = ">=3.12"
dependencies = ["sqlalchemy[asyncio]>=2.0", "fastapi>=0.115"]

[build-system]
requires = ["setuptools>=68"]
build-backend = "setuptools.build_meta"

[tool.setuptools]
packages = ["uniops_authz"]
```

`packages/authz/uniops_authz/__init__.py`:
```python
"""Shared authorization gate for UniOps backend services.

Every service shares one physical database, so this reads identity's
role_permissions / role_permission_locks directly — no HTTP, no token, no
cache. If identity-api the *service* is down, this still works; only a DB
outage stops it, and that stops everything anyway.

Should the services ever be split across databases, swap the internals of
core.py for an HTTP client — consumers import only these two names and will
not notice.
"""
from uniops_authz.core import (  # noqa: F401
    bind,
    effective_permissions,
    user_role_codes,
)
```
(注意导出的是 `bind` 而非 `require_permission` —— 后者由各服务用自己的依赖 bind 出来,见 Step 5。)

- [ ] **Step 2: 写失败测试 `packages/authz/tests/test_core.py`**

```python
"""The gate's semantics, pinned. These are the rules every service inherits."""
import uuid

import pytest
import sqlalchemy as sa

from uniops_authz import effective_permissions, user_role_codes

pytestmark = pytest.mark.asyncio


async def _mk_user(db, role: str) -> uuid.UUID:
    uid = uuid.uuid4()
    await db.execute(sa.text(
        "INSERT INTO users (id, email, hashed_password, full_name, role, is_active) "
        "VALUES (:i, :e, 'x', 'U', :r, true)"),
        {"i": str(uid), "e": f"{uid}@t.co", "r": role})
    return uid


async def test_role_codes_union_primary_and_additional(authz_db):
    """A post/role held as the PRIMARY role counts, same as an ADDITIONAL one.
    Consulting only user_roles would silently lose primary-role holders."""
    db = authz_db
    uid = await _mk_user(db, "dept_manager")
    await db.execute(sa.text(
        "INSERT INTO user_roles (user_id, role_code) VALUES (:u, 'finance_bp')"),
        {"u": str(uid)})
    codes = await user_role_codes(db, uid, "dept_manager")
    assert codes == {"dept_manager", "finance_bp"}


async def test_permission_granted_via_any_role(authz_db):
    db = authz_db
    uid = await _mk_user(db, "requester")
    await db.execute(sa.text(
        "INSERT INTO user_roles (user_id, role_code) VALUES (:u, 'ap_clerk')"),
        {"u": str(uid)})
    await db.execute(sa.text(
        "INSERT INTO role_permissions (role_code, permission_key) VALUES ('ap_clerk', 'k.write')"))
    perms = await effective_permissions(db, uid, "requester")
    assert perms.get("k.write") is True


async def test_locked_cell_counts_as_granted(authz_db):
    """The effective matrix is granted UNION locked — a lock is a forced grant."""
    db = authz_db
    uid = await _mk_user(db, "requester")
    await db.execute(sa.text(
        "INSERT INTO role_permission_locks (role_code, permission_key) "
        "VALUES ('requester', 'k.locked')"))
    perms = await effective_permissions(db, uid, "requester")
    assert perms.get("k.locked") is True


async def test_ungranted_key_is_false_not_missing(authz_db):
    db = authz_db
    uid = await _mk_user(db, "requester")
    await db.execute(sa.text(
        "INSERT INTO permission_defs (key, module, label, sort) "
        "VALUES ('k.nope', 'test', 'Nope', 1)"))
    perms = await effective_permissions(db, uid, "requester")
    assert perms.get("k.nope") is False


async def test_inactive_additional_role_ignored(authz_db):
    """A role_defs row with is_active=false must not contribute permissions."""
    db = authz_db
    uid = await _mk_user(db, "requester")
    await db.execute(sa.text(
        "INSERT INTO role_defs (code, label, sort, is_active) "
        "VALUES ('retired_role', 'Retired', 99, false) ON CONFLICT DO NOTHING"))
    await db.execute(sa.text(
        "INSERT INTO user_roles (user_id, role_code) VALUES (:u, 'retired_role')"),
        {"u": str(uid)})
    await db.execute(sa.text(
        "INSERT INTO role_permissions (role_code, permission_key) "
        "VALUES ('retired_role', 'k.retired')"))
    perms = await effective_permissions(db, uid, "requester")
    assert perms.get("k.retired") is not True
```

`packages/authz/tests/conftest.py` —— 照 `identity-api/tests/conftest.py` 的建库套路,建一个 `authz_test` 库,用 raw SQL 建最小影子表(`users`/`user_roles`/`role_defs`/`role_permissions`/`role_permission_locks`/`permission_defs`),提供 `authz_db` fixture(每测清表)。列定义以 identity 物理表为准——**先 `docker exec uniops_postgres psql -U epms -d epms -c "\d user_roles"` 等逐个核对**,别照惯例猜(见 [[feedback_uniops_mirror_models_match_reality]])。

- [ ] **Step 3: 跑测试确认失败**

```bash
cd /c/Project/uniops/packages/authz && TEST_PG_PASSWORD=7c0a03bb8c2afef690d1852f8dc3a0195932db5f0f1670e9 python -m pytest tests -q
```
Expected: FAIL(`uniops_authz` 未安装/未实现)。先 `pip install -e .`(用 epms-api 的 venv 或新建一个,在报告里说明用了哪个解释器)。

- [ ] **Step 4: 实现 `packages/authz/uniops_authz/core.py`**

```python
"""The gate. Reads identity's matrix directly — same physical database."""
import uuid
from typing import Annotated, Callable

from fastapi import Depends, HTTPException, status
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


async def user_role_codes(db: AsyncSession, user_id: uuid.UUID, base_role: str) -> set[str]:
    """The user's PRIMARY role plus every ADDITIONAL role from identity's
    user_roles. Both sources count: the primary role is a real role, and
    consulting only user_roles would silently lose primary-role holders."""
    codes: set[str] = {base_role} if base_role else set()
    rows = (await db.execute(text(
        "SELECT ur.role_code FROM user_roles ur "
        " JOIN role_defs rd ON rd.code = ur.role_code AND rd.is_active "
        " WHERE ur.user_id = :u"), {"u": str(user_id)})).scalars().all()
    codes.update(rows)
    return codes


async def _effective_matrix(db: AsyncSession) -> dict[str, set[str]]:
    """role_code -> {permission_key}. Effective = granted UNION locked
    (a lock is a forced grant the UI may not clear)."""
    rows = (await db.execute(text(
        "SELECT role_code, permission_key FROM role_permissions "
        "UNION "
        "SELECT role_code, permission_key FROM role_permission_locks"))).all()
    out: dict[str, set[str]] = {}
    for role_code, key in rows:
        out.setdefault(role_code, set()).add(key)
    return out


async def effective_permissions(
    db: AsyncSession, user_id: uuid.UUID, base_role: str
) -> dict[str, bool]:
    """Every known permission key -> whether ANY of the user's roles grants it.

    Keys come from permission_defs so an ungranted key reads False rather than
    being absent — callers can `.get(k)` without worrying which it is.
    """
    keys = (await db.execute(text("SELECT key FROM permission_defs"))).scalars().all()
    codes = await user_role_codes(db, user_id, base_role)
    matrix = await _effective_matrix(db)
    granted: set[str] = set()
    for code in codes:
        granted |= matrix.get(code, set())
    return {k: (k in granted) for k in keys}


# NOTE: the gate itself (require_permission) cannot live at module level —
# each service wires in its OWN get_db / token-payload dependencies, whose
# names differ across services. It is produced by bind() — see Step 5.
```

- [ ] **Step 5: 解决「各服务依赖名不同」——`bind()` 工厂**

各服务的 `get_db` 与 token payload 依赖名不一致(epms `get_session`/`CurrentUserPayload`,finance `get_db`/`CurrentUser`,…),所以包不能硬写 `Depends(get_db)`。用一次性绑定:

```python
def bind(get_db_dep: Callable, get_user_dep: Callable) -> Callable:
    """Bind this service's own DB-session and token-payload dependencies once,
    and get back a require_permission(key) factory for that service.

    Usage (in each service's app/core/authz.py):
        from uniops_authz import bind
        from app.core.deps import get_session, get_token_payload
        require_permission = bind(get_session, get_token_payload)
    """
    def require_permission(key: str) -> Callable:
        async def _check(
            payload: Annotated[dict, Depends(get_user_dep)],
            db: Annotated[AsyncSession, Depends(get_db_dep)],
        ) -> dict:
            role = payload.get("role", "")
            if role == "system_admin":
                return payload
            uid = uuid.UUID(payload["sub"])
            codes = await user_role_codes(db, uid, role)
            matrix = await _effective_matrix(db)
            for code in codes:
                if key in matrix.get(code, set()):
                    return payload
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Insufficient permissions",
            )
        return _check
    return require_permission
```
把 `bind` 加进 `__init__.py` 的导出(与 `effective_permissions`/`user_role_codes` 并列)。给 `bind` 补两个测试:system_admin 短路放行、无权限 403。

- [ ] **Step 6: 测试转绿**

- [ ] **Step 7: Commit**

```bash
git add packages/authz
git commit -m "feat(authz): shared backend gate package — direct matrix reads, no HTTP"
```

---

### Task 2: identity 加 12 个新权限键 + 默认值 seed

**Files:**
- Create: `identity-api/alembic/versions/0004_phase2_permission_keys.py`
- Create: `identity-api/scripts/seed_phase2_keys.py`
- Test: `identity-api/tests/test_phase2_keys.py`

**Interfaces:**
- Consumes: 一期的 `permission_defs(key, module, label, sort)`、`role_permissions(role_code, permission_key)`。
- Produces: 12 个新键存在于 `permission_defs`,且 `role_permissions` 含下表默认授予行。Task 3-7 的 `require_permission("...")` 依赖它们。

- [ ] **Step 1: 核对每个常量的【实际】准入集**

**这是本任务的核心,不是抄字面元组。** 逐个打开下列文件,确认 `require_roles`/内联检查的实际语义(尤其 system_admin 短路):

```bash
grep -n "_AP_ROLES\|_PO_WRITE_ROLES\|_PA_WRITE_ROLES\|_WAREHOUSE_ROLES" epms-api/app/api/v1/*.py
grep -n "_MANAGE_ROLES\|_CLOSE_ROLES\|_JV_ROLES" finance-api/app/api/v1/*.py finance-api/app/crud/*.py
grep -n "_WRITE_ROLES\|_OPENING_WRITE_ROLES" budget-api/app/api/v1/*.py
grep -rn "require_roles(" mdm-api/app --include=*.py | grep -v "def require_roles"
sed -n '50,70p' budget-api/app/core/deps.py   # 看 require_roles 的 system_admin 短路
```
把每个常量的**实际**准入集写进报告,与下表逐项比对;**任何不一致以代码为准并在报告中指出**(下表是计划期读出来的,若代码已变以代码为准)。

- [ ] **Step 2: 写失败测试 `identity-api/tests/test_phase2_keys.py`**

```python
"""Phase-2 keys exist with defaults identical to the require_roles they replace."""
import pytest
import sqlalchemy as sa

from scripts.seed_phase2_keys import PHASE2_DEFAULTS, PHASE2_KEYS, seed_phase2_keys

pytestmark = pytest.mark.asyncio


async def test_keys_registered_with_module(db_session):
    await seed_phase2_keys(db_session)
    rows = {k: m for k, m in (await db_session.execute(sa.text(
        "SELECT key, module FROM permission_defs WHERE key = ANY(:ks)"),
        {"ks": list(PHASE2_KEYS)})).all()}
    assert set(rows) == set(PHASE2_KEYS)
    assert rows["epms.po.write"] == "epms"
    assert rows["budget.plan.write"] == "budget"
    assert rows["mdm.vendor.write"] == "mdm"


async def test_defaults_match_the_replaced_role_sets(db_session):
    await seed_phase2_keys(db_session)
    for key, roles in PHASE2_DEFAULTS.items():
        got = set((await db_session.execute(sa.text(
            "SELECT role_code FROM role_permissions WHERE permission_key = :k"),
            {"k": key})).scalars().all())
        assert got == set(roles), f"{key}: {got} != {set(roles)}"


async def test_system_admin_granted_on_every_phase2_key(db_session):
    """require_roles short-circuits system_admin, so every replaced gate admitted
    it — including _OPENING_WRITE_ROLES, whose literal tuple omits it."""
    await seed_phase2_keys(db_session)
    for key in PHASE2_KEYS:
        n = (await db_session.execute(sa.text(
            "SELECT count(*) FROM role_permissions "
            "WHERE permission_key = :k AND role_code = 'system_admin'"),
            {"k": key})).scalar_one()
        assert n == 1, f"{key} missing system_admin"


async def test_seed_is_idempotent(db_session):
    await seed_phase2_keys(db_session)
    counts = await seed_phase2_keys(db_session)
    assert counts["granted"] == 0
```

- [ ] **Step 3: 跑测试确认失败**

```bash
cd /c/Project/uniops/identity-api && TEST_PG_PASSWORD=7c0a03bb8c2afef690d1852f8dc3a0195932db5f0f1670e9 ./.venv/Scripts/python -m pytest tests/test_phase2_keys.py -q
```

- [ ] **Step 4: 写 seed `identity-api/scripts/seed_phase2_keys.py`**

```python
"""Register phase-2 permission keys and seed their defaults.

Defaults are the EXACT admission sets of the require_roles()/inline gates they
replace. Note every one includes system_admin: require_roles short-circuits it
(`if role == "system_admin": return payload`), so a gate whose literal tuple
omits system_admin — e.g. budget's _OPENING_WRITE_ROLES — still admitted it.
Dropping it here would be a behaviour change (a tightening).

Run INSIDE the identity container (host .env points at prod!):
    docker exec uniops_identity_api python -m scripts.seed_phase2_keys
"""
import asyncio

import sqlalchemy as sa

from app.db.base import AsyncSessionLocal

# key -> (module, label, sort)
PHASE2_KEYS: dict[str, tuple[str, str, int]] = {
    "epms.invoice.match":    ("epms",    "Match Invoices",          100),
    "epms.po.write":         ("epms",    "Create / Edit POs",       101),
    "epms.pa.write":         ("epms",    "Create / Edit PAs",       102),
    "epms.gr.receive":       ("epms",    "Receive Goods",           103),
    "finance.coa.manage":    ("finance", "Manage Chart of Accounts", 110),
    "finance.period.close":  ("finance", "Close Periods",           111),
    "finance.jv.post":       ("finance", "Post Journal Vouchers",   112),
    "budget.catalog.write":  ("budget",  "Edit Budget Catalog",     120),
    "budget.plan.write":     ("budget",  "Edit Budget Plans",       121),
    "budget.opening.write":  ("budget",  "Edit Opening Balances",   122),
    "mdm.finance.write":     ("mdm",     "Edit Finance Master Data", 130),
    "mdm.vendor.write":      ("mdm",     "Edit Vendor Master Data",  131),
}

# key -> roles admitted TODAY (system_admin included everywhere: short-circuit)
PHASE2_DEFAULTS: dict[str, tuple[str, ...]] = {
    "epms.invoice.match":   ("system_admin", "ap_clerk", "finance_manager", "finance_bp"),
    "epms.po.write":        ("system_admin", "procurement_officer", "procurement_manager"),
    "epms.pa.write":        ("system_admin", "finance_bp", "finance_manager", "ap_clerk", "requester"),
    "epms.gr.receive":      ("system_admin", "warehouse_staff", "procurement_officer"),
    "finance.coa.manage":   ("system_admin", "finance_manager"),
    "finance.period.close": ("system_admin", "finance_manager"),
    "finance.jv.post":      ("system_admin", "finance_manager", "finance_bp"),
    "budget.catalog.write": ("system_admin", "finance_manager", "finance_bp"),
    "budget.plan.write":    ("system_admin", "finance_manager", "finance_bp", "dept_manager"),
    # literal tuple is ("finance_manager","finance_bp") — system_admin passes via short-circuit
    "budget.opening.write": ("system_admin", "finance_manager", "finance_bp"),
    "mdm.finance.write":    ("system_admin", "finance_manager", "ap_clerk"),
    "mdm.vendor.write":     ("system_admin", "vendor_manager", "finance_manager"),
}


async def seed_phase2_keys(session) -> dict:
    counts = {"keys": 0, "granted": 0}
    for key, (module, label, sort) in PHASE2_KEYS.items():
        r = await session.execute(sa.text(
            "INSERT INTO permission_defs (key, module, label, sort) "
            "VALUES (:k, :m, :l, :s) ON CONFLICT (key) DO NOTHING"),
            {"k": key, "m": module, "l": label, "s": sort})
        counts["keys"] += r.rowcount or 0
    for key, roles in PHASE2_DEFAULTS.items():
        for role in roles:
            r = await session.execute(sa.text(
                "INSERT INTO role_permissions (role_code, permission_key) "
                "VALUES (:r, :k) ON CONFLICT (role_code, permission_key) DO NOTHING"),
                {"r": role, "k": key})
            counts["granted"] += r.rowcount or 0
    return counts


async def main():
    async with AsyncSessionLocal() as session:
        counts = await seed_phase2_keys(session)
        await session.commit()
        print(f"seed_phase2_keys done: {counts}")


if __name__ == "__main__":
    asyncio.run(main())
```

- [ ] **Step 5: 确认无需迁移**

12 个键是**数据**不是 schema——`permission_defs`/`role_permissions` 两张表①期就建好了,本任务只往里灌行,所以**没有 alembic 迁移,只有 seed**(与①③期一致:表结构走迁移,矩阵数据走幂等 seed)。跑一次 `docker exec uniops_identity_api alembic heads` 确认链尾仍是 `0003_post_role_singleton` 且**你没有**新增迁移,在报告里写明这一点。

- [ ] **Step 6: 测试转绿 + identity 全量**

```bash
cd /c/Project/uniops/identity-api && TEST_PG_PASSWORD=... ./.venv/Scripts/python -m pytest tests -q
```
Expected: 20 既有 + 4 新 = 24 passed。

- [ ] **Step 7: 容器内 seed(dev)**

```bash
docker exec uniops_identity_api python -m scripts.seed_phase2_keys
docker exec uniops_postgres psql -U epms -d epms -c \
  "SELECT count(*) FROM permission_defs; SELECT count(*) FROM role_permissions;"
```
Expected: `permission_defs` 17→**29**;`role_permissions` 149→149+42=**191**(12 键的默认授予行合计 42 条,按 PHASE2_DEFAULTS 数一遍核对)。再跑一次 seed 应全 0(幂等)。

- [ ] **Step 8: Commit**

```bash
git add identity-api/scripts/seed_phase2_keys.py identity-api/tests/test_phase2_keys.py
git commit -m "feat(identity): register 12 phase-2 permission keys with today's exact role sets"
```

---

### Task 3: epms 接入共享包(模式确立 + ①期遗产退役)

**Files:**
- Create: `epms-api/app/core/authz.py`(bind 出本服务的 `require_permission`)
- Modify: `epms-api/Dockerfile`、`docker-compose.dev.yml`(epms-api 块)、`docker-compose.prod.yml`(epms-api 块)、`epms-api/requirements.txt`
- Modify: `epms-api/app/core/access_scope.py`(`_effective_permissions` 改用包)、`epms-api/app/core/deps.py`(`require_permission` 改用包)
- Modify: `epms-api/app/api/v1/{invoices,po,pa,gr}.py`(4 个业务门禁)
- Delete: `epms-api/app/core/authz_client.py`
- Modify: `epms-api/app/api/v1/config.py`(authz 代理端点改用包直读)
- Test: `epms-api/tests/test_authz_proxy.py`(重写)、既有测试

**Interfaces:**
- Consumes: Task 1 的 `bind`/`effective_permissions`、Task 2 的 12 键。
- Produces: **build context 改造模式**(Task 5-7 照抄):
  ```yaml
  # docker-compose.{dev,prod}.yml
  epms-api:
    build:
      context: .                        # was: { context: ./epms-api }
      dockerfile: epms-api/Dockerfile
  ```
  ```dockerfile
  # epms-api/Dockerfile
  COPY packages/authz /packages/authz
  RUN pip install --no-cache-dir /packages/authz
  COPY epms-api/requirements.txt .      # was: COPY requirements.txt .
  RUN pip install --no-cache-dir -r requirements.txt
  COPY epms-api .                       # was: COPY . .
  ```
  ```yaml
  # docker-compose.dev.yml 的 epms-api volumes —— dev 靠 bind mount 覆盖镜像里的包,
  # 改包立即生效;prod 无此挂载,用镜像内的副本。
  volumes:
    - ./epms-api:/app
    - ./packages/authz:/packages/authz
  ```
  ⚠️ **不要**用 `pip install -e` 指向宿主路径:dev bind mount 下 editable 尚可,但 prod 无挂载会装成空壳。统一用上面的 `pip install /packages/authz`(非 editable);dev 改了包后**必须 `docker compose -f docker-compose.dev.yml build epms-api && docker restart uniops_epms_api`**,不能只 restart(见 [[feedback_uniops_shell_install_links_stale]] 的同类教训)。

- [ ] **Step 1: 存基线**

```bash
docker exec uniops_epms_api sh -c "python -m pytest tests -q 2>&1 | grep -E '^FAILED|^ERROR'" | sort > /tmp/epms_p2_base.txt
wc -l < /tmp/epms_p2_base.txt    # 预期 72(③期后)
```

- [ ] **Step 2: build context 改造 + 包接入**

按上面 Interfaces 的三段代码改 Dockerfile / 两个 compose;`epms-api/requirements.txt` **不加** uniops-authz(它由 Dockerfile 的 `pip install /packages/authz` 装,不走 PyPI)。然后:
```bash
docker compose -f docker-compose.dev.yml build epms-api && docker restart uniops_epms_api
docker exec uniops_epms_api python -c "import uniops_authz; print('authz pkg OK')"
```

- [ ] **Step 3: bind 本服务的 require_permission**

`epms-api/app/core/authz.py`:
```python
"""This service's authz gate, bound to its own DB/token dependencies.

Import require_permission FROM HERE, not from uniops_authz directly — the
package needs this service's get_session/get_token_payload wired in.
"""
from uniops_authz import bind

from app.core.deps import get_session, get_token_payload

require_permission = bind(get_session, get_token_payload)
```
(依赖名以 `epms-api/app/core/deps.py` 实际为准——先读它确认 `get_session`/`get_token_payload` 的真名。)

- [ ] **Step 4: 改 4 个业务门禁**

`epms-api/app/api/v1/invoices.py` 的 `_AP_ROLES` 门禁 → `Depends(require_permission("epms.invoice.match"))`;`po.py` 的 `_PO_WRITE_ROLES` → `epms.po.write`;`pa.py` 的 `_PA_WRITE_ROLES` → `epms.pa.write`;`gr.py` 的 `_WAREHOUSE_ROLES` → `epms.gr.receive`。删掉这四个常量本体(grep 确认无其他引用)。**注意 `invoices.py` 里 `_AP_ROLES` 还被 `_has_open_match_task` 之类的业务逻辑用到——那是可见性/任务解析,不是门禁,别一起删**(逐处判断,报告里列出你保留了哪些及原因)。

- [ ] **Step 5: `access_scope` 与 `deps` 改用包**

`epms-api/app/core/access_scope.py` 的 `_effective_permissions`:
```python
async def _effective_permissions(db, base_role: str, user_id: uuid.UUID) -> dict[str, bool]:
    """Union of all permissions across the user's roles.

    Reads identity's matrix directly via the shared authz package (same physical
    DB — no HTTP, no token). Phase 1's authz_client (HTTP + cache + outage
    fallback + write-through mirror) existed only because this pure-DB call
    chain had no token to call identity with; that whole apparatus is gone.
    """
    from uniops_authz import effective_permissions
    return await effective_permissions(db, user_id, base_role)
```
`epms-api/app/core/deps.py` 里那个自己实现的 `require_permission` → 改为从 `app.core.authz` re-export(或直接让调用方 import 新的),保持调用方签名不变。

- [ ] **Step 6: 退役 authz_client**

删 `epms-api/app/core/authz_client.py`;`epms-api/app/api/v1/config.py` 的 authz 端点改为直读:
- `GET /config/role-permissions` → 用包内 `_effective_matrix` 的等价 SQL 拼成旧形状 `{role: {key: bool}}`(**booking 前端仍读这个端点,响应形状不得变**)
- `GET /config/me/permissions` → `effective_permissions(db, uid, role)` + 用户角色列表
- `PATCH /config/role-permissions` → 仍转发 identity(**写仍走 identity API**——它有锁定格校验和审计列;只有读改直读)
- `GET /config/locked-permissions`、`/config/authz-defs`、`/config/user-roles`、`PUT /config/users/{id}/roles` → 保持转发 identity(它们是 identity 的域)
- **删掉写穿透镜像**:PATCH 成功后不再写 `company_config.role_permissions`(该 JSONB 从此无人读;列留档不删——`reconstruct.py` 读的是 `role_management` 不是它,但仍不删以免误伤)

- [ ] **Step 7: 重写 `epms-api/tests/test_authz_proxy.py`**

旧测试测的是 HTTP 代理/缓存/回落——那套没了。新测试测:`GET /config/role-permissions` 返回旧形状且内容等于矩阵;`GET /config/me/permissions` 并集正确;`PATCH` 仍转发 identity 且 409/422 透传;identity 宕机时**读端点仍工作**(直读不依赖 identity 服务——这是本期的新保证,值得钉一个测试)。

- [ ] **Step 8: 回归**

```bash
docker exec uniops_epms_api sh -c "python -m pytest tests -q 2>&1 | grep -E '^FAILED|^ERROR'" | sort > /tmp/epms_p2_now.txt
comm -13 /tmp/epms_p2_base.txt /tmp/epms_p2_now.txt   # 新增失败,必须空
comm -23 /tmp/epms_p2_base.txt /tmp/epms_p2_now.txt   # 消失的(重写 test_authz_proxy 会带走一些,说明原因)
```

- [ ] **Step 9: Commit**

```bash
git add epms-api/Dockerfile epms-api/app/core/authz.py epms-api/app/core/access_scope.py \
  epms-api/app/core/deps.py epms-api/app/api/v1/invoices.py epms-api/app/api/v1/po.py \
  epms-api/app/api/v1/pa.py epms-api/app/api/v1/gr.py epms-api/app/api/v1/config.py \
  epms-api/tests/test_authz_proxy.py docker-compose.dev.yml docker-compose.prod.yml
git rm epms-api/app/core/authz_client.py
git commit -m "feat(epms): gates via the shared authz package; retire phase-1 http client + write-through mirror"
```

---

### Task 4: 平价断言脚本

**Files:**
- Create: `packages/authz/scripts/verify_gate_parity.py`
- Test: 无(它本身是验收工具;其正确性由 Task 3 的实测证明)

**Interfaces:**
- Consumes: Task 2 的 `PHASE2_DEFAULTS`、Task 1 的包。
- Produces: 可重复运行的验收门。**这是本期铁律**(对标③期的 PARITY OK)。

- [ ] **Step 1: 写脚本**

`packages/authz/scripts/verify_gate_parity.py`:对每个 (角色 × 12 新键) 组合,比对:
- **OLD**:该键替换掉的那个 `require_roles(...)` 的准入判定 —— 即 `role in PHASE2_DEFAULTS[key]`(**这是计划期从代码读出的准入集,脚本里内联一份拷贝,不 import 被测代码**——否则两边同源,PARITY OK 就没有意义)
- **NEW**:`role_code` 在生效矩阵里有没有该键 —— 直读 `role_permissions ∪ role_permission_locks`

任一不等 → 打印 `DIFF role=<code> key=<key> old=<bool> new=<bool>` 并非零退出;全等 → `GATE PARITY OK (<n> roles x 12 keys)`。

角色全集从 `role_defs` 取(17 个内建 + 任何自定义)。

- [ ] **Step 2: dev 实跑**

```bash
docker exec uniops_identity_api python -m scripts.verify_gate_parity
```
(脚本放 `packages/authz/scripts/` 但要在有库连接的容器里跑——放不进 identity 容器的话,复制到 `identity-api/scripts/` 亦可,在报告里说明最终位置。)
Expected: `GATE PARITY OK (17 roles x 12 keys)`。**任何 DIFF 都要查清,不许改脚本掩盖。**

- [ ] **Step 3: Commit**

```bash
git add packages/authz/scripts/verify_gate_parity.py
git commit -m "test(authz): gate parity verifier — new keys must admit exactly who require_roles did"
```

---

### Task 5: finance 接入 + 3 个内联门禁迁移

**Files:**
- Create: `finance-api/app/core/authz.py`
- Modify: `finance-api/Dockerfile`、两个 compose 的 finance-api 块
- Modify: `finance-api/app/api/v1/coa.py`(`_MANAGE_ROLES`)、`finance-api/app/api/v1/periods.py`(`_CLOSE_ROLES`)、`finance-api/app/crud/journal_voucher.py`(`_JV_ROLES`)
- Test: `finance-api/tests/test_coa.py`、`finance-api/tests/test_periods.py`(或既有对应文件)

**Interfaces:**
- Consumes: Task 1 的 `bind`、Task 2 的 `finance.coa.manage` / `finance.period.close` / `finance.jv.post`。
- Produces: 无(终端消费者)。

- [ ] **Step 1: 存基线**:`cd /c/Project/uniops/finance-api && TEST_PG_PASSWORD=... ./.venv/Scripts/python -m pytest tests -q | tail -1`(预期 201 passed)

- [ ] **Step 2: build context 改造**(照 Task 3 的 Interfaces 三段代码,把 `epms-api` 换成 `finance-api`)+ `docker compose -f docker-compose.dev.yml build finance-api && docker restart uniops_finance_api` + `docker exec uniops_finance_api python -c "import uniops_authz; print('OK')"`

- [ ] **Step 3: bind**

`finance-api/app/core/authz.py`:
```python
from uniops_authz import bind

from app.core.deps import get_db, get_token_payload

require_permission = bind(get_db, get_token_payload)
```
(依赖真名以 `finance-api/app/core/deps.py` 为准——它现在导出 `CurrentUser = Annotated[dict, Depends(get_token_payload)]`,先读确认。)

- [ ] **Step 4: 迁移三处**

`coa.py::_can_manage` 现在是「JWT role in `_MANAGE_ROLES` OR user_roles 含 finance_manager/finance_bp」(③期改过)。②期改为:`_require_manage` 直接换成 `Depends(require_permission("finance.coa.manage"))`。**注意**:③期把 finance_bp 从 coa 拿掉了(它不是被指派的审批人),而 `finance.coa.manage` 的默认角色集是 `(system_admin, finance_manager)` —— **不含 finance_bp**,与③期修复后的行为一致 ✓。`periods.py::_CLOSE_ROLES` → `finance.period.close`;`journal_voucher.py::_JV_ROLES` → `finance.jv.post`(它是 crud 层不是端点,若不便用 Depends 则调 `effective_permissions` 判断,报告说明)。

- [ ] **Step 5: 回归**:`... pytest tests -q`(必须仍 201 passed 或更多)

- [ ] **Step 6: Commit**

```bash
git add finance-api/Dockerfile finance-api/app/core/authz.py finance-api/app/api/v1/coa.py \
  finance-api/app/api/v1/periods.py finance-api/app/crud/journal_voucher.py \
  finance-api/tests/ docker-compose.dev.yml docker-compose.prod.yml
git commit -m "feat(finance): coa/period/jv gates via the shared authz package"
```

---

### Task 6: budget 接入 + 34 处迁移(4 个概念)

**Files:**
- Create: `budget-api/app/core/authz.py`
- Modify: `budget-api/Dockerfile`、两个 compose 的 budget-api 块
- Modify: `budget-api/app/api/v1/catalog.py`、`factor.py`、`plan.py`、`actual.py`
- Test: budget-api 既有测试

**Interfaces:**
- Consumes: Task 1 的 `bind`、Task 2 的 `budget.catalog.write` / `budget.plan.write` / `budget.opening.write`。

- [ ] **Step 1: 存基线**:`docker exec uniops_budget_api python -m pytest tests -q | tail -1`(记数;缺 pytest 先装)

- [ ] **Step 2: build context 改造**(照 Task 3 模式)+ 重建 + `import uniops_authz` 冒烟

- [ ] **Step 3: bind**(`budget-api/app/core/deps.py` 的依赖真名以文件为准)

- [ ] **Step 4: 迁移**

34 处调用只用了 4 个常量,所以是**改 4 个常量的用法**不是改 34 处逻辑:
- `catalog.py::_WRITE_ROLES` + `factor.py::_WRITE_ROLES`(两文件同值 `("system_admin","finance_manager","finance_bp")`)→ 都用 `budget.catalog.write`
- `plan.py::_WRITE_ROLES`(多 `dept_manager`)→ `budget.plan.write`
- `actual.py::_OPENING_WRITE_ROLES` → `budget.opening.write`(⚠️ 字面不含 system_admin 但短路让它能过,新键默认值**已含** system_admin,行为一致)
把 `Depends(require_roles(*_WRITE_ROLES))` 换成 `Depends(require_permission("budget.catalog.write"))` 等,删掉四个常量。

- [ ] **Step 5: 回归**:`docker exec uniops_budget_api python -m pytest tests -q`(与基线持平)

- [ ] **Step 6: Commit**

```bash
git add budget-api/Dockerfile budget-api/app/core/authz.py budget-api/app/api/v1/catalog.py \
  budget-api/app/api/v1/factor.py budget-api/app/api/v1/plan.py budget-api/app/api/v1/actual.py \
  budget-api/tests/ docker-compose.dev.yml docker-compose.prod.yml
git commit -m "feat(budget): catalog/plan/opening gates via the shared authz package"
```

---

### Task 7: mdm 接入 + 迁移(2 个业务门禁;纯 admin 不动)

**Files:**
- Create: `mdm-api/app/core/authz.py`
- Modify: `mdm-api/Dockerfile`、两个 compose 的 mdm-api 块
- Modify: `mdm-api/app/api/v1/*.py`(5 处 `("system_admin","finance_manager","ap_clerk")` + 1 处 `("system_admin","vendor_manager","finance_manager")`)
- Test: mdm-api 既有测试

**Interfaces:**
- Consumes: Task 1 的 `bind`、Task 2 的 `mdm.finance.write` / `mdm.vendor.write`。

- [ ] **Step 1: 存基线**:`docker exec uniops_mdm_api python -m pytest tests -q | tail -1`

- [ ] **Step 2: build context 改造**(照 Task 3 模式)+ 重建 + 冒烟

- [ ] **Step 3: bind**(依赖真名以 `mdm-api/app/core/deps.py` 为准)

- [ ] **Step 4: 迁移**

5 处 `require_roles("system_admin","finance_manager","ap_clerk")` → `require_permission("mdm.finance.write")`;1 处 `require_roles("system_admin","vendor_manager","finance_manager")` → `require_permission("mdm.vendor.write")`。
**不动**:`erp_mdm.py` 里 `if user.get("role") != "system_admin"` 这类纯 admin 门禁(决策 2)。逐处判断并在报告里列出你迁了哪些、留了哪些及原因。

- [ ] **Step 5: 回归**:`docker exec uniops_mdm_api python -m pytest tests -q`(与基线持平)

- [ ] **Step 6: Commit**

```bash
git add mdm-api/Dockerfile mdm-api/app/core/authz.py mdm-api/app/api/v1/ mdm-api/tests/ \
  docker-compose.dev.yml docker-compose.prod.yml
git commit -m "feat(mdm): finance/vendor master-data gates via the shared authz package"
```

---

### Task 8: 全量回归 + 平价 + 端到端证明 + 发布材料

**Files:**
- Create: `docs/superpowers/plans/2026-07-16-authz-backend-gates-phase2-release.md`
- Modify: 无代码(除非发现问题)

- [ ] **Step 1: 平价断言**

```bash
docker exec uniops_identity_api python -m scripts.verify_gate_parity
```
Expected: `GATE PARITY OK (17 roles x 12 keys)`。**任何 DIFF 停下查清。**

- [ ] **Step 2: 全量回归(全部前台跑,逐个报尾行)**

```bash
cd /c/Project/uniops/identity-api && TEST_PG_PASSWORD=... ./.venv/Scripts/python -m pytest tests -q
cd /c/Project/uniops/finance-api  && TEST_PG_PASSWORD=... ./.venv/Scripts/python -m pytest tests -q
cd /c/Project/uniops/approval-api && TEST_PG_PASSWORD=... ./.venv/Scripts/python -m pytest tests -q
cd /c/Project/uniops/packages/authz && TEST_PG_PASSWORD=... python -m pytest tests -q
docker exec uniops_epms_api sh -c "python -m pytest tests -q 2>&1 | grep -E '^FAILED|^ERROR'" | sort > /tmp/epms_p2_final.txt
comm -13 /tmp/epms_p2_base.txt /tmp/epms_p2_final.txt    # 必须空
docker exec uniops_expense_api python -m pytest tests -q
docker exec uniops_vms_api python -m pytest tests -q
docker exec uniops_budget_api python -m pytest tests -q
docker exec uniops_mdm_api python -m pytest tests -q
cd /c/Project/uniops/portal && npx tsc -p tsconfig.app.json --noEmit --ignoreDeprecations 6.0
cd /c/Project/uniops/epms && npx tsc -p tsconfig.app.json --noEmit 2>&1 | grep -c "error TS"
```
Expected:identity 24 / finance 201 / approval 39 / authz 包 7 / epms 零新增失败 / expense 83 / vms 175 / budget、mdm 与基线持平 / portal tsc 0 / epms tsc 69。

- [ ] **Step 3: 端到端证明——矩阵真的管住了后端(②期存在的全部意义)**

```bash
docker exec uniops_epms_api python -c "
import asyncio, uuid, httpx
from app.core.security import create_access_token
from app.db.session import AsyncSessionLocal
from sqlalchemy import text

async def main():
    async with AsyncSessionLocal() as db:
        # 找一个有 epms.po.write 的角色(procurement_officer)的真实用户
        uid = (await db.execute(text(
            \"SELECT id FROM users WHERE role='procurement_officer' AND is_active LIMIT 1\"))).scalar()
    if not uid:
        print('no procurement_officer user on dev — pick another role'); return
    t = create_access_token(str(uid), 'procurement_officer')
    h = {'Authorization': f'Bearer {t}'}
    B = 'http://localhost:8000/api/v1'
    # 关掉该角色的 epms.po.write → 端点应从可用变 403
    async with AsyncSessionLocal() as db:
        await db.execute(text(
            \"DELETE FROM role_permissions WHERE role_code='procurement_officer' \"
            \"AND permission_key='epms.po.write'\"))
        await db.commit()
    r_off = httpx.get(f'{B}/po', headers=h)
    async with AsyncSessionLocal() as db:
        await db.execute(text(
            \"INSERT INTO role_permissions (role_code, permission_key) \"
            \"VALUES ('procurement_officer','epms.po.write') ON CONFLICT DO NOTHING\"))
        await db.commit()
    r_on = httpx.get(f'{B}/po', headers=h)
    print('matrix OFF ->', r_off.status_code, '| matrix ON ->', r_on.status_code)
asyncio.run(main())
"
```
挑一个真正被 `require_permission("epms.po.write")` 守住的**写**端点(GET 可能没门禁——按实际改成 POST/PATCH 并造合法 body,或直接调 `require_permission` 的依赖函数验证)。**预期:关掉矩阵开关后 403,打开后放行**。这是①期做不到、②期才有的能力,必须留下正面证据。跑完把开关恢复原状。

- [ ] **Step 4: 写发布材料**

`docs/superpowers/plans/2026-07-16-authz-backend-gates-phase2-release.md`,照 `2026-07-15-approval-routing-phase3-release.md` 的结构/语气(中文),必须写明:
- **②③合并发布**(用户决定),故本文档与③期的 release 文档**合并阅读**:③的 2 个迁移(approval `0001_approval_routing`、identity `0003_post_role_singleton`)+ ③的 seed(`seed_routing`)+ ②的 seed(`seed_phase2_keys`)。
- **★ 两个 seed 都必做**,顺序:migrate → `seed_routing` → `seed_phase2_keys` → 两个平价脚本(`verify_routing_parity` + `verify_gate_parity`)→ up。
- **不跑 `seed_phase2_keys` 的后果**:12 个新键在矩阵里不存在 → `require_permission` 对所有非 system_admin 一律 403 → **PO/PA/发票匹配/收货/预算/主数据全线不可用**。说白了要明写。
- **镜像必须全部重建**:build context 改了(9→6 个服务从 `./xxx-api` 变 `.`),且共享包是 `COPY` 进镜像的——**必须 build+push 全部 15 个镜像的 `:<sha>`**(本来就是铁律,见 [[reference_uniops_prod_release_workflow]])。
- 回滚:②③都是 additive(新键/新表/新包);回退 TAG 即恢复旧镜像的硬编码门禁;identity 的新键留着无害。
- 发布后验证:`verify_gate_parity` + Portal → Access Control 能看到 29 个键(按 module 分组含新的 budget/mdm 组)。

- [ ] **Step 5: Commit**

```bash
git add docs/superpowers/plans/2026-07-16-authz-backend-gates-phase2-release.md
git commit -m "docs(release): phase 2 backend gates — release notes (ships together with phase 3)"
```
