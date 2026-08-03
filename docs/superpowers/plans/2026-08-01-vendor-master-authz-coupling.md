# Vendor Master 权限联动 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让 Access Control 里勾选一次 "Vendor Master" 就能端到端新建/编辑 Vendor —— 消除 `vendor_master`（EPMS 网关）与 `mdm.vendor.write`（mdm 网关）两个 key 各自独立、漏勾即 403 "权限不足" 的漂移。

**Architecture:** 方案 B「合并成一个勾」，只改**授予侧**、不动任何网关语义。三处改动：① identity `patch_matrix` 授予时把 `vendor_master` 联动镜像到 `mdm.vendor.write`；② 幂等回填脚本给现存持 `vendor_master` 的角色补 `mdm.vendor.write`；③ 前端矩阵隐藏冗余的 "Edit Vendor Master Data" 行。`finance_manager`（只有 mdm.vendor.write）按方案 (a) 保持不动。

**Tech Stack:** FastAPI + SQLAlchemy async (identity-api)、pytest-asyncio（identity_test 本地 Postgres）、React + TanStack Query + TypeScript（portal）。

**Spec:** `docs/superpowers/specs/2026-08-01-vendor-master-authz-coupling-design.md`

---

## File Structure

- **Modify** `identity-api/app/api/v1/authz.py` — 在 `patch_matrix` 写入循环里加联动镜像 + 常量 `COUPLED_PERMISSIONS` + 私有 helper `_apply_grant`。
- **Create** `identity-api/scripts/backfill_vendor_master_coupling.py` — 幂等回填脚本（仿 `seed_phase2_keys.py`）。
- **Create** `identity-api/tests/test_vendor_master_coupling.py` — 联动的 API 测试。
- **Create** `identity-api/tests/test_backfill_vendor_master_coupling.py` — 回填脚本测试。
- **Modify** `portal/src/pages/admin/AccessControl.tsx` — `permissions` memo 过滤掉 `mdm.vendor.write` 行。

**测试库前置（执行前确认一次）：** identity 测试用本地 Postgres（conftest 默认 `localhost:5432`，user `epms` / pw `epms_dev`，DB `identity_test`，`create_all` 建空表）。参见记忆 feedback「epms-api测试库env」「测试库禁止并发跑」——同刻只跑一个套件。测试 fixture 会自行 `seed_authz` + `seed_phase2_keys`（后者注册 `mdm.vendor.write` 这条 `permission_defs`，联动/回填的外键依赖它）。

---

## Task 1: 后端联动 —— `patch_matrix` 把 `vendor_master` 镜像到 `mdm.vendor.write`

**Files:**
- Test: `identity-api/tests/test_vendor_master_coupling.py` (create)
- Modify: `identity-api/app/api/v1/authz.py`（`patch_matrix`，当前 47-76 行）

- [ ] **Step 1: 写失败测试**

Create `identity-api/tests/test_vendor_master_coupling.py`:

```python
"""Granting 'vendor_master' must couple-grant 'mdm.vendor.write' (one checkbox,
both gates). See docs/superpowers/specs/2026-08-01-vendor-master-authz-coupling-design.md."""
import uuid

import pytest
import sqlalchemy as sa
from httpx import ASGITransport, AsyncClient

from app.core.security import create_access_token
from app.main import app
from scripts.seed_authz import seed_authz
from scripts.seed_phase2_keys import seed_phase2_keys

pytestmark = pytest.mark.asyncio
BASE = "/identity/v1"


@pytest.fixture
async def seeded_full(db_session):
    # seed_authz expects these jsonb columns on company_config (test-only; the
    # physical epms table already has them).
    await db_session.execute(sa.text(
        "ALTER TABLE company_config ADD COLUMN IF NOT EXISTS role_permissions jsonb DEFAULT '{}'::jsonb"))
    await db_session.execute(sa.text(
        "ALTER TABLE company_config ADD COLUMN IF NOT EXISTS custom_roles jsonb DEFAULT '[]'::jsonb"))
    await seed_authz(db_session)        # epms keys incl vendor_master + role_defs + defaults
    await seed_phase2_keys(db_session)  # registers mdm.vendor.write permission_def (+ defaults)
    await db_session.commit()
    yield


def _admin_client():
    token = create_access_token(str(uuid.uuid4()), "system_admin")
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test",
                       headers={"Authorization": f"Bearer {token}"})


async def _has(db_session, role: str, key: str) -> bool:
    return bool((await db_session.execute(sa.text(
        "SELECT 1 FROM role_permissions WHERE role_code=:r AND permission_key=:k"),
        {"r": role, "k": key})).scalar())


async def test_granting_vendor_master_couples_mdm_write(seeded_full, db_session):
    # 'auditor' starts with neither key.
    async with _admin_client() as c:
        r = await c.patch(f"{BASE}/authz/matrix",
                          json={"changes": {"auditor": {"vendor_master": True}}})
        assert r.status_code == 200
    assert await _has(db_session, "auditor", "vendor_master")
    assert await _has(db_session, "auditor", "mdm.vendor.write")


async def test_revoking_vendor_master_revokes_mdm_write(seeded_full, db_session):
    # 'cfo' starts with neither key.
    async with _admin_client() as c:
        await c.patch(f"{BASE}/authz/matrix",
                      json={"changes": {"cfo": {"vendor_master": True}}})
        r = await c.patch(f"{BASE}/authz/matrix",
                          json={"changes": {"cfo": {"vendor_master": False}}})
        assert r.status_code == 200
    assert not await _has(db_session, "cfo", "vendor_master")
    assert not await _has(db_session, "cfo", "mdm.vendor.write")


async def test_other_key_does_not_touch_mdm_write(seeded_full, db_session):
    # 'gm' starts with neither key; toggling an unrelated key must not add it.
    async with _admin_client() as c:
        r = await c.patch(f"{BASE}/authz/matrix",
                          json={"changes": {"gm": {"parts_catalog": True}}})
        assert r.status_code == 200
    assert not await _has(db_session, "gm", "mdm.vendor.write")


async def test_coupling_is_idempotent(seeded_full, db_session):
    # 'dept_admin' starts with neither key.
    async with _admin_client() as c:
        for _ in range(2):
            r = await c.patch(f"{BASE}/authz/matrix",
                              json={"changes": {"dept_admin": {"vendor_master": True}}})
            assert r.status_code == 200
    n = (await db_session.execute(sa.text(
        "SELECT count(*) FROM role_permissions "
        "WHERE role_code='dept_admin' AND permission_key='mdm.vendor.write'"))).scalar_one()
    assert n == 1


async def test_coupling_does_not_bypass_locks(seeded_full, db_session):
    # Lock vendor_master for a role and pre-grant mdm.vendor.write. A delta that
    # tries to REVOKE vendor_master hits the lock check (authz.py 60-63), which
    # 409s the WHOLE patch before the write loop — so the coupling never runs
    # and mdm.vendor.write is NOT removed. 'warehouse_staff' is unused elsewhere
    # in this file, keeping the test self-contained.
    role = "warehouse_staff"
    await db_session.execute(sa.text(
        "INSERT INTO role_permission_locks(role_code,permission_key) "
        "VALUES (:r,'vendor_master') ON CONFLICT DO NOTHING"), {"r": role})
    await db_session.execute(sa.text(
        "INSERT INTO role_permissions(role_code,permission_key) "
        "VALUES (:r,'mdm.vendor.write') ON CONFLICT DO NOTHING"), {"r": role})
    await db_session.commit()
    async with _admin_client() as c:
        r = await c.patch(f"{BASE}/authz/matrix",
                          json={"changes": {role: {"vendor_master": False}}})
        assert r.status_code == 409
    assert await _has(db_session, role, "mdm.vendor.write")  # not revoked
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd identity-api && python -m pytest tests/test_vendor_master_coupling.py -v`
Expected: `test_granting_vendor_master_couples_mdm_write` FAIL —— `mdm.vendor.write` 未被联动写入（`_has(...,"auditor","mdm.vendor.write")` 为 False）。其余用例可能偶然通过（还没联动逻辑时它们本就不写 mdm 行），但 granting 必失败，证明测试锚点有效。

- [ ] **Step 3: 实现联动**

Edit `identity-api/app/api/v1/authz.py`. 在 `router = APIRouter(...)`（第 13 行）之后、`_require_admin` 之前，加入常量：

```python
# Granting the EPMS "Vendor Master" permission must also grant the mdm-layer
# key that actually gates vendor create/update, so one Access Control checkbox
# works end to end. See
# docs/superpowers/specs/2026-08-01-vendor-master-authz-coupling-design.md
COUPLED_PERMISSIONS: dict[str, str] = {"vendor_master": "mdm.vendor.write"}
```

在 `patch_matrix` 之前加入 helper（把原来内联的 INSERT/DELETE 抽出来，供主键与联动键共用）：

```python
async def _apply_grant(db, role: str, key: str, val: bool, actor: uuid.UUID) -> None:
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
```

把 `patch_matrix` 里的写入循环（当前 64-75 行）整体替换为：

```python
    for role, kv in body.changes.items():
        for key, val in kv.items():
            await _apply_grant(db, role, key, val, actor)
            coupled = COUPLED_PERMISSIONS.get(key)
            # Guard on perm_keys: only mirror when the coupled key is a
            # registered permission_def, else the FK insert would blow up an
            # env where phase-2 keys were never seeded.
            if coupled and coupled in perm_keys:
                await _apply_grant(db, role, coupled, val, actor)
    return _matrix(*await _load(db))
```

（`text`、`delete`、`RolePermission`、`uuid` 均已在文件顶部导入，无需新增 import。）

- [ ] **Step 4: 跑测试确认通过**

Run: `cd identity-api && python -m pytest tests/test_vendor_master_coupling.py -v`
Expected: 5 passed.

- [ ] **Step 5: 跑既有 authz 测试确认无回归**

Run: `cd identity-api && python -m pytest tests/test_authz_api.py -v`
Expected: 全部 passed（联动不改既有行为：`vendor_master` 从不出现在这些用例的 delta 里）。

- [ ] **Step 6: Commit**

```bash
git add identity-api/app/api/v1/authz.py identity-api/tests/test_vendor_master_coupling.py
git commit -m "feat(authz): couple vendor_master grant to mdm.vendor.write

Granting the EPMS 'Vendor Master' permission now also grants the mdm-layer
'mdm.vendor.write' key that gates vendor create/update, so one checkbox works
end to end. Fixes the two-gate 权限不足 on vendor create.

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 2: 幂等回填脚本 —— 给现存持 `vendor_master` 的角色补 `mdm.vendor.write`

**Files:**
- Test: `identity-api/tests/test_backfill_vendor_master_coupling.py` (create)
- Create: `identity-api/scripts/backfill_vendor_master_coupling.py`

- [ ] **Step 1: 写失败测试**

Create `identity-api/tests/test_backfill_vendor_master_coupling.py`:

```python
"""Backfill grants mdm.vendor.write to every role already holding vendor_master,
one-directionally — roles that only hold mdm.vendor.write (finance_manager)
must not gain vendor_master. See
docs/superpowers/specs/2026-08-01-vendor-master-authz-coupling-design.md."""
import pytest
import sqlalchemy as sa

from scripts.seed_authz import seed_authz
from scripts.seed_phase2_keys import seed_phase2_keys
from scripts.backfill_vendor_master_coupling import backfill_vendor_master_coupling

pytestmark = pytest.mark.asyncio


@pytest.fixture
async def seeded_full(db_session):
    await db_session.execute(sa.text(
        "ALTER TABLE company_config ADD COLUMN IF NOT EXISTS role_permissions jsonb DEFAULT '{}'::jsonb"))
    await db_session.execute(sa.text(
        "ALTER TABLE company_config ADD COLUMN IF NOT EXISTS custom_roles jsonb DEFAULT '[]'::jsonb"))
    await seed_authz(db_session)
    await seed_phase2_keys(db_session)
    await db_session.commit()
    yield


async def _has(db_session, role: str, key: str) -> bool:
    return bool((await db_session.execute(sa.text(
        "SELECT 1 FROM role_permissions WHERE role_code=:r AND permission_key=:k"),
        {"r": role, "k": key})).scalar())


async def test_backfill_grants_mdm_write_to_vendor_master_holders(seeded_full, db_session):
    # procurement_officer holds vendor_master but NOT mdm.vendor.write by default.
    assert await _has(db_session, "procurement_officer", "vendor_master")
    assert not await _has(db_session, "procurement_officer", "mdm.vendor.write")
    counts = await backfill_vendor_master_coupling(db_session)
    await db_session.commit()
    assert await _has(db_session, "procurement_officer", "mdm.vendor.write")
    assert await _has(db_session, "procurement_manager", "mdm.vendor.write")
    assert counts["granted"] >= 2


async def test_backfill_leaves_mdm_only_roles_untouched(seeded_full, db_session):
    # finance_manager holds mdm.vendor.write but NOT vendor_master; backfill is
    # one-directional and must not grant it vendor_master.
    assert await _has(db_session, "finance_manager", "mdm.vendor.write")
    assert not await _has(db_session, "finance_manager", "vendor_master")
    await backfill_vendor_master_coupling(db_session)
    await db_session.commit()
    assert not await _has(db_session, "finance_manager", "vendor_master")


async def test_backfill_idempotent(seeded_full, db_session):
    await backfill_vendor_master_coupling(db_session)
    await db_session.commit()
    counts = await backfill_vendor_master_coupling(db_session)
    await db_session.commit()
    assert counts["granted"] == 0
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd identity-api && python -m pytest tests/test_backfill_vendor_master_coupling.py -v`
Expected: FAIL at import —— `ModuleNotFoundError: No module named 'scripts.backfill_vendor_master_coupling'`。

- [ ] **Step 3: 实现回填脚本**

Create `identity-api/scripts/backfill_vendor_master_coupling.py`:

```python
"""One-shot idempotent backfill: every role holding 'vendor_master' also gets
'mdm.vendor.write', so the coupled "Vendor Master" checkbox works for roles
granted before the coupling shipped. See
docs/superpowers/specs/2026-08-01-vendor-master-authz-coupling-design.md.

Run INSIDE the identity container (host .env points at prod!):
    docker compose exec identity-api python -m scripts.backfill_vendor_master_coupling
"""
import asyncio

import sqlalchemy as sa

from app.db.base import AsyncSessionLocal


async def backfill_vendor_master_coupling(session) -> dict:
    # updated_by is nullable; copying the source row's value (NULL for seeded
    # grants) is fine. One-directional: only vendor_master holders get the row.
    r = await session.execute(sa.text(
        "INSERT INTO role_permissions (role_code, permission_key, updated_by) "
        "SELECT rp.role_code, 'mdm.vendor.write', rp.updated_by "
        "FROM role_permissions rp "
        "WHERE rp.permission_key = 'vendor_master' "
        "ON CONFLICT (role_code, permission_key) DO NOTHING"))
    return {"granted": r.rowcount or 0}


async def main():
    async with AsyncSessionLocal() as session:
        counts = await backfill_vendor_master_coupling(session)
        await session.commit()
        print(f"backfill_vendor_master_coupling done: {counts}")


if __name__ == "__main__":
    asyncio.run(main())
```

- [ ] **Step 4: 跑测试确认通过**

Run: `cd identity-api && python -m pytest tests/test_backfill_vendor_master_coupling.py -v`
Expected: 3 passed.

- [ ] **Step 5: Commit**

```bash
git add identity-api/scripts/backfill_vendor_master_coupling.py identity-api/tests/test_backfill_vendor_master_coupling.py
git commit -m "feat(authz): idempotent backfill of mdm.vendor.write for vendor_master holders

One-directional: existing roles holding vendor_master gain mdm.vendor.write so
the coupled checkbox works retroactively; mdm-only roles (finance_manager) are
left untouched.

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 3: 前端合并成一个勾 —— 隐藏 "Edit Vendor Master Data" 行

**Files:**
- Modify: `portal/src/pages/admin/AccessControl.tsx`（`permissions` memo，当前 197-200 行）

- [ ] **Step 1: 改渲染过滤**

Edit `portal/src/pages/admin/AccessControl.tsx`. 把 `permissions` memo（当前 197-200 行）：

```ts
  const permissions = useMemo(
    () => [...(defsQ.data?.permissions ?? [])].sort((a, b) => a.sort - b.sort),
    [defsQ.data],
  )
```

替换为（在文件 `Matrix` 类型定义附近，第 32 行之后，加一个模块常量；再在 memo 里用它过滤）：

先在第 32 行 `type Matrix = ...` 之后加：

```ts
/**
 * Permission keys hidden from the matrix because they are coupled to another,
 * visible checkbox and granted automatically by the backend. `mdm.vendor.write`
 * is granted whenever the EPMS "Vendor Master" row is ticked (identity
 * patch_matrix couples them), so showing it as a separate toggle would let an
 * admin desync the two. See
 * docs/superpowers/specs/2026-08-01-vendor-master-authz-coupling-design.md
 */
const HIDDEN_PERMISSION_KEYS = new Set<string>(['mdm.vendor.write'])
```

再把 memo 改为：

```ts
  const permissions = useMemo(
    () => [...(defsQ.data?.permissions ?? [])]
      .filter((p) => !HIDDEN_PERMISSION_KEYS.has(p.key))
      .sort((a, b) => a.sort - b.sort),
    [defsQ.data],
  )
```

- [ ] **Step 2: 类型检查 + 构建通过**

Run: `cd portal && npm run build`
Expected: `tsc -b` 无类型错误，`vite build` 成功产出。（改动仅是数组 filter，无类型变化。）

- [ ] **Step 3: Lint 通过**

Run: `cd portal && npm run lint`
Expected: 无新增 error（对比改动前基线）。

- [ ] **Step 4: Commit**

```bash
git add portal/src/pages/admin/AccessControl.tsx
git commit -m "feat(portal): hide coupled mdm.vendor.write row in Access Control matrix

'Edit Vendor Master Data' is now granted automatically with 'Vendor Master',
so it is hidden to present a single checkbox and prevent desync.

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 4: 集成校验（人工冒烟，部署前）

> 这一步在合并/部署到有 mdm-api 的环境后做；本 worktree 无法端到端跑 EPMS→mdm 链路。

- [ ] **Step 1: 后端全套回归**

Run: `cd identity-api && python -m pytest tests/ -v`
Expected: 新增 8 个用例全过（coupling 5 + backfill 3）；既有用例无回归（对齐既有基线，勿把存量失败误判为本次引入 —— 参见记忆 feedback「验证要正面证据」）。

- [ ] **Step 2: 部署后冒烟（清单，app server 无 ssh 给命令）**

1. 在 identity 容器跑一次回填：
   `docker compose exec identity-api python -m scripts.backfill_vendor_master_coupling`
   预期输出 `granted: N`（N = 现存持 vendor_master 的角色数，首次 > 0，再跑一次 = 0）。
2. Portal → Access Control → Permission Matrix：确认 **MDM 组不再出现 "Edit Vendor Master Data"**，只有 "Vendor Master" 一个勾。
3. 给「Purchase officer」角色勾上 "Vendor Master" 并保存；用该角色用户新建 Vendor → **能保存成功**（不再 "权限不足"）。
4. 取消「Purchase officer」的 "Vendor Master" 并保存；确认该角色用户新建 Vendor 恢复被拒（联动撤销生效）。

---

## 发布说明（汇合时，遵循并行开发纪律与发布流程）

- 真建镜像：`identity-api`、`portal`；其余 13 个 retag 自上一版同 sha。
- **无 alembic 迁移** → 部署不跑 `migrate-prod.sh`；改为在 identity 容器跑一次性回填命令（见 Task 4 Step 2.1）。
- R5：push / 发布前复核生产当前 TAG（勿默认 == origin/main）。
- 冒烟见 Task 4 Step 2。
