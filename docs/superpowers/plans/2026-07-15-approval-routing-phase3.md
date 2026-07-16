# 审批路由归位(③期)Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把 EPMS `company_config` 里的审批指派拆开——人选进 identity `user_roles`(副角色)、部门路由规则进 approval-api 自己的表,approval 不再读 EPMS 配置,管理界面挪 Portal,EPMS Role Management 页签(含死掉的代班功能)下线。

**Architecture:** approval-api 首次拥有自己的表(需新建 alembic,照 budget-api 模板,`version_table="alembic_version_approval"`)。所有服务同库,故一律**只读镜像直读**,零 HTTP/零 token。改造手法是**适配器**:`crud/workflow.py` 的 getter 保持返回形状不变,只换数据来源,使 `crud/engine.py` 约 20 处消费点一行不改——平价天然成立。

**Tech Stack:** FastAPI + SQLAlchemy async + alembic(approval 新建)、React + TanStack Query + Tailwind(Portal)。

## Global Constraints

- Spec: `docs/superpowers/specs/2026-07-15-approval-routing-phase3-design.md`(含 2026-07-15 三次修订:岗位唯一跨两表、approval 无 alembic、缺省语义修正)。
- **零行为变化是铁律**。平价断言:迁移前后,每个 (部门 × 单据类型) 组合解析出的审批人**逐个相同**。
- **⚠️ 缺省语义(勿凭字段名臆断,已从 engine + 权威测试核实)**:`gm_or_opm` 缺省 `"gm"`;`director_user_id` 缺省无(跳过该层);**`supervisor_enabled` 缺省 `false`(无 supervisor 层)**——dev 12 个部门**无一为 true**,迁移后必须仍全是 false。
- **岗位唯一性**:`gm`/`opm`/`vendor_manager`/`finance_manager`/`procurement_manager` 全局各只一人,**校验须跨 `users.role`(主) ∪ `user_roles`(副) 两表**;`finance_bp` 不受限(列表语义)。
- UI 文案**全英文**;Portal 页面必须 PortalChromeLayout/同款 chrome 包裹(参照 `portal/src/pages/admin/DataMaintenance.tsx`)。
- ⚠️ 宿主 .env 指向生产库:**alembic/seed 一律容器内跑**(`docker exec uniops_approval_api ...`)。
- 测试命令:
  - approval: `cd /c/Project/uniops/approval-api && TEST_PG_PASSWORD=7c0a03bb8c2afef690d1852f8dc3a0195932db5f0f1670e9 ./.venv/Scripts/python -m pytest <file> -q`(单进程串行,不并发不后台)
  - identity: `cd /c/Project/uniops/identity-api && TEST_PG_PASSWORD=<同上> ./.venv/Scripts/python -m pytest <file> -q`
  - epms/expense: `docker exec uniops_epms_api python -m pytest <file> -q`(缺 pytest 则 `pip install -q pytest pytest-asyncio httpx pytest-mock`)
  - finance: `cd /c/Project/uniops/finance-api && TEST_PG_PASSWORD=<同上> ./.venv/Scripts/python -m pytest <file> -q`
  - 前端 typecheck:`cd <app> && npx tsc -p tsconfig.app.json --noEmit --ignoreDeprecations 6.0`(**epms 例外**:它是 TS 5.9.3,`--ignoreDeprecations 6.0` 会报 TS5103,用 `npx tsc -p tsconfig.app.json --noEmit`,基线 69 个既有错误)
- git 提交只 add 指定文件,**绝不 `-a`/`-A`**(仓库根有用户的未跟踪二进制)。

---

### Task 1: approval-api 加 alembic + 两张路由表

**Files:**
- Create: `approval-api/alembic.ini`、`approval-api/alembic/env.py`、`approval-api/alembic/script.py.mako`、`approval-api/alembic/versions/0001_approval_routing.py`
- Create: `approval-api/app/models/routing.py`
- Modify: `approval-api/tests/conftest.py:65-72`(把 `DeptRouting.__table__`、`ApprovalBackup.__table__` 加进 `_ENGINE_TABLES` 白名单 + 顶部 import)
- Modify: `migrate-prod.sh:16`(SERVICES 加 approval-api)
- Test: `approval-api/tests/test_routing_models.py`

**Interfaces:**
- Produces: ORM `DeptRouting(dept_id, gm_or_opm, director_user_id, supervisor_enabled, updated_by, updated_at)`、`ApprovalBackup(role_code, backup_user_id, updated_by, updated_at)`;表名 `approval_dept_routing`、`approval_backups`。Task 2/3/5 消费。

- [ ] **Step 1: 抄 budget-api 的 alembic 骨架**

`approval-api/alembic/env.py` 照 `budget-api/alembic/env.py` 逐行抄,只改两处:`version_table="alembic_version_approval"`;`from app.models.routing import ...` 让 `target_metadata = Base.metadata` 包含新表。`alembic.ini`、`script.py.mako` 同样从 budget-api 拷贝(`script_location = alembic`,sqlalchemy.url 由 env.py 从 settings 读——照 budget 的做法)。

- [ ] **Step 2: 写模型 `approval-api/app/models/routing.py`**

```python
"""Approval routing rules — owned by approval-api (phase 3).

Replaces the EPMS company_config JSONB trio (dept_gm_opm_mapping /
dept_director_mapping / dept_supervisor_enabled) and the *_backup_user_id
fields of role_management. One row per active department.
"""
import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, String, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class DeptRouting(Base):
    __tablename__ = "approval_dept_routing"

    dept_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    # 'gm' | 'opm' — which post approves this department's gm_or_opm step.
    gm_or_opm: Mapped[str] = mapped_column(String(3), nullable=False, server_default="gm")
    director_user_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    # Default FALSE: an unlisted department has no supervisor layer today
    # (engine.py:414 + test_engine_optional_levels.py:64). Do not flip this.
    supervisor_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="false")
    updated_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now())


class ApprovalBackup(Base):
    """Stand-in approver when the primary post-holder is unavailable."""
    __tablename__ = "approval_backups"

    role_code: Mapped[str] = mapped_column(String(50), primary_key=True)   # 'gm' | 'opm'
    backup_user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    updated_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now())
```

- [ ] **Step 3: 写迁移 `0001_approval_routing.py`**

```python
"""approval routing tables (phase 3)"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

revision = "0001_approval_routing"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "approval_dept_routing",
        sa.Column("dept_id", UUID(as_uuid=True), primary_key=True),
        sa.Column("gm_or_opm", sa.String(3), nullable=False, server_default="gm"),
        sa.Column("director_user_id", UUID(as_uuid=True), nullable=True),
        sa.Column("supervisor_enabled", sa.Boolean, nullable=False, server_default="false"),
        sa.Column("updated_by", UUID(as_uuid=True), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
    )
    op.create_table(
        "approval_backups",
        sa.Column("role_code", sa.String(50), primary_key=True),
        sa.Column("backup_user_id", UUID(as_uuid=True), nullable=False),
        sa.Column("updated_by", UUID(as_uuid=True), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
    )


def downgrade() -> None:
    op.drop_table("approval_backups")
    op.drop_table("approval_dept_routing")
```

无 FK 到 `departments`/`users`:approval 只读镜像它们,加 FK 会让 approval 的迁移依赖 epms 的建表顺序(同库但不同 alembic 链)。

- [ ] **Step 4: 写失败测试 `approval-api/tests/test_routing_models.py`**

```python
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
```

**⚠️ conftest 的建表机制(计划期核实,别按惯例猜)**:`approval-api/tests/conftest.py` **不是 `create_all(全部)`**,而是 `Base.metadata.create_all(eng, tables=_ENGINE_TABLES)` ——一个**显式白名单**(:65-72,现含 User/CompanyConfig/PaymentApplication/PurchaseRequest/Task/ApprovalEvent)。所以:

- 新表必须**加进 `_ENGINE_TABLES`**:`DeptRouting.__table__`、`ApprovalBackup.__table__`(顶部 import `from app.models.routing import ApprovalBackup, DeptRouting`),否则测试库里根本没有这两张表。
- `departments` / `user_roles` **不在白名单也没有模型**(approval 至今不读它们)。Task 3/4/6 的测试需要它们 → 在**各自测试文件里**用 `CREATE TABLE IF NOT EXISTS` 建最小影子表(照 identity 一期 `tests/test_authz_seed.py` 的影子表套路),不要动 conftest 白名单去加别人的表。

fixture 用 `engine_db_session`(已核实存在,:93)。

- [ ] **Step 5: 跑测试确认失败**

`cd /c/Project/uniops/approval-api && TEST_PG_PASSWORD=7c0a03bb8c2afef690d1852f8dc3a0195932db5f0f1670e9 ./.venv/Scripts/python -m pytest tests/test_routing_models.py -q` → FAIL(表不存在)

- [ ] **Step 6: 落实现 → 测试转绿 + approval 全量回归**

`... -m pytest tests -q`(既有测试必须全绿)

- [ ] **Step 7: 容器内跑迁移(dev)**

```bash
docker exec uniops_approval_api alembic upgrade head
docker exec uniops_postgres psql -U epms -d epms -c "\d approval_dept_routing"
```
若容器无 alembic 可执行,先确认 `approval-api/requirements.txt` 是否含 alembic;缺则加入并 `docker compose -f docker-compose.dev.yml build approval-api && docker restart uniops_approval_api`,在报告里说明。

- [ ] **Step 8: `migrate-prod.sh` 加 approval-api**

第 16 行 `SERVICES="finance-api epms-api mdm-api identity-api budget-api expense-api vms-api booking-api"` → 在 `identity-api` 之后插入 `approval-api`(顺序:它依赖 identity 的 user_roles 已建)。

- [ ] **Step 9: Commit**

```bash
git add approval-api/alembic.ini approval-api/alembic approval-api/app/models/routing.py \
  approval-api/tests/conftest.py approval-api/tests/test_routing_models.py migrate-prod.sh
git commit -m "feat(approval): own routing tables — alembic + dept routing/backups"
```

---

### Task 2: 岗位唯一性约束(identity)

**Files:**
- Create: `identity-api/alembic/versions/0003_post_role_singleton.py`
- Modify: `identity-api/app/api/v1/authz.py`(`put_user_roles` 加跨表校验)
- Test: `identity-api/tests/test_authz_api.py`(加 2 个用例)

**Interfaces:**
- Consumes: 一期的 `user_roles`、`users.role`、`PUT /identity/v1/authz/users/{id}/roles`。
- Produces: 该端点在岗位冲突时返回 **409**,body `{"detail": {"conflict": {"role": "<code>", "held_by": "<user_id>"}}}`。Task 6 的 Portal 页据此显示错误。

- [ ] **Step 1: 写失败测试(加进 `identity-api/tests/test_authz_api.py`)**

```python
POST_ROLES = ("gm", "opm", "vendor_manager", "finance_manager", "procurement_manager")


async def test_post_role_singleton_conflicts(seeded, db_session):
    """gm/opm/... may be held by exactly one user — as primary OR additional."""
    a, b = uuid.uuid4(), uuid.uuid4()
    for i, uid in enumerate((a, b)):
        await db_session.execute(sa.text(
            "INSERT INTO users (id, email, hashed_password, full_name, role) "
            "VALUES (:i, :e, 'x', 'U', 'requester')"), {"i": str(uid), "e": f"{uid}@t.co"})
    await db_session.flush()
    async with _client() as c:
        r1 = await c.put(f"{BASE}/authz/users/{a}/roles",
                         json={"primary": "requester", "additional": ["gm"]})
        assert r1.status_code == 204
        # second user cannot also hold gm
        r2 = await c.put(f"{BASE}/authz/users/{b}/roles",
                         json={"primary": "requester", "additional": ["gm"]})
        assert r2.status_code == 409
        assert r2.json()["detail"]["conflict"]["role"] == "gm"
        assert r2.json()["detail"]["conflict"]["held_by"] == str(a)
        # finance_bp is NOT a singleton — both may hold it
        assert (await c.put(f"{BASE}/authz/users/{a}/roles",
                            json={"primary": "requester", "additional": ["finance_bp"]})).status_code == 204
        assert (await c.put(f"{BASE}/authz/users/{b}/roles",
                            json={"primary": "requester", "additional": ["finance_bp"]})).status_code == 204


async def test_post_role_singleton_checks_primary_role_too(seeded, db_session):
    """A post held as someone's PRIMARY role also blocks it as another's additional."""
    a, b = uuid.uuid4(), uuid.uuid4()
    await db_session.execute(sa.text(
        "INSERT INTO users (id, email, hashed_password, full_name, role) "
        "VALUES (:i, :e, 'x', 'Primary GM', 'gm')"), {"i": str(a), "e": f"{a}@t.co"})
    await db_session.execute(sa.text(
        "INSERT INTO users (id, email, hashed_password, full_name, role) "
        "VALUES (:i, :e, 'x', 'Other', 'requester')"), {"i": str(b), "e": f"{b}@t.co"})
    await db_session.flush()
    async with _client() as c:
        r = await c.put(f"{BASE}/authz/users/{b}/roles",
                        json={"primary": "requester", "additional": ["gm"]})
    assert r.status_code == 409
    assert r.json()["detail"]["conflict"]["held_by"] == str(a)


async def test_setting_own_post_again_is_idempotent(seeded, db_session):
    """Re-saving the same user's own post must not 409 against itself."""
    a = uuid.uuid4()
    await db_session.execute(sa.text(
        "INSERT INTO users (id, email, hashed_password, full_name, role) "
        "VALUES (:i, :e, 'x', 'U', 'requester')"), {"i": str(a), "e": f"{a}@t.co"})
    await db_session.flush()
    async with _client() as c:
        assert (await c.put(f"{BASE}/authz/users/{a}/roles",
                            json={"primary": "requester", "additional": ["gm"]})).status_code == 204
        assert (await c.put(f"{BASE}/authz/users/{a}/roles",
                            json={"primary": "requester", "additional": ["gm", "finance_bp"]})).status_code == 204
```

沿用该文件既有的 `seeded` fixture / `_client()` helper / `BASE` 常量(读文件确认)。

- [ ] **Step 2: 跑测试确认失败** → `... -m pytest tests/test_authz_api.py -q` FAIL(现无 409)

- [ ] **Step 3: 迁移 `identity-api/alembic/versions/0003_post_role_singleton.py`**

`down_revision = "0002_authz_tables"`(先 `docker exec uniops_identity_api alembic heads` 确认链尾就是它):

```python
"""post roles are singletons (partial unique index on user_roles)"""
from alembic import op

revision = "0003_post_role_singleton"
down_revision = "0002_authz_tables"
branch_labels = None
depends_on = None

_POSTS = "'gm','opm','vendor_manager','finance_manager','procurement_manager'"


def upgrade() -> None:
    # Backstop only — the API also checks users.role (a post held as a PRIMARY
    # role is invisible to this index). See put_user_roles.
    op.execute(
        f"CREATE UNIQUE INDEX uq_user_roles_singleton_post ON user_roles (role_code) "
        f"WHERE role_code IN ({_POSTS})")


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS uq_user_roles_singleton_post")
```

⚠️ 迁移前若 dev/生产 `user_roles` 已有重复岗位行,建索引会失败 → 实施时先查:
`docker exec uniops_postgres psql -U epms -d epms -c "SELECT role_code, count(*) FROM user_roles WHERE role_code IN ('gm','opm','vendor_manager','finance_manager','procurement_manager') GROUP BY 1 HAVING count(*)>1"`(预期 0 行,因一期至今 user_roles 基本为空)。

- [ ] **Step 4: `put_user_roles` 加跨表校验**

在 `identity-api/app/api/v1/authz.py` 的 `put_user_roles` 里,校验未知角色之后、写入之前插入:

```python
_POST_ROLES = frozenset({"gm", "opm", "vendor_manager", "finance_manager", "procurement_manager"})


async def _post_conflict(db, user_id: uuid.UUID, wanted: set[str]) -> dict | None:
    """A post role may be held by exactly one user — as primary OR additional."""
    posts = wanted & _POST_ROLES
    if not posts:
        return None
    holder = (await db.execute(select(User.id, User.role).where(
        User.role.in_(posts), User.id != user_id))).first()
    if holder is not None:
        return {"role": holder[1], "held_by": str(holder[0])}
    row = (await db.execute(select(UserRole.role_code, UserRole.user_id).where(
        UserRole.role_code.in_(posts), UserRole.user_id != user_id))).first()
    if row is not None:
        return {"role": row[0], "held_by": str(row[1])}
    return None
```

在 `put_user_roles` 中 `u.role = body.primary` 之前:

```python
    conflict = await _post_conflict(db, user_id, {body.primary, *body.additional})
    if conflict is not None:
        raise HTTPException(status_code=409, detail={"conflict": conflict})
```

(`{body.primary, *body.additional}` 一起查:把某人主角色设成 gm 而他人已持 gm,同样要拦。)

- [ ] **Step 5: 测试转绿 + identity 全量**

`... -m pytest tests -q` → 全绿(一期 17 + 新 3)

- [ ] **Step 6: 容器内迁移**

`docker exec uniops_identity_api alembic upgrade head`

- [ ] **Step 7: Commit**

```bash
git add identity-api/alembic/versions/0003_post_role_singleton.py \
  identity-api/app/api/v1/authz.py identity-api/tests/test_authz_api.py
git commit -m "feat(identity): approval post roles are singletons (409 across primary+additional)"
```

---

### Task 3: 迁移脚本(JSONB → 新表 + user_roles)

**Files:**
- Create: `approval-api/scripts/__init__.py`(空)、`approval-api/scripts/seed_routing.py`
- Test: `approval-api/tests/test_seed_routing.py`

**Interfaces:**
- Consumes: Task 1 的两张表、Task 2 的唯一约束、epms `company_config`(同库,直接 SQL 读)、`departments`、identity `user_roles`。
- Produces: `seed_routing(session) -> dict` 计数(`{"user_roles": n, "dept_rows": n, "backups": n}`),幂等。

- [ ] **Step 1: 写失败测试 `approval-api/tests/test_seed_routing.py`**

```python
"""Seed maps the EPMS JSONB trio + role_management onto the new tables."""
import json
import uuid

import pytest
import sqlalchemy as sa

from scripts.seed_routing import seed_routing

pytestmark = pytest.mark.asyncio


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
```

测试需要 `users`/`departments` 表存在于 approval 的测试库——若 conftest 的 create_all 不含它们(approval 只镜像了部分),在测试里按上面 SQL 直接建最小影子表(照 identity 一期 `test_authz_seed.py` 的影子表套路),并在报告说明。

- [ ] **Step 2: 跑测试确认失败**

- [ ] **Step 3: 实现 `approval-api/scripts/seed_routing.py`**

```python
"""One-shot idempotent migration: EPMS approval assignments -> owned tables.

Run INSIDE the approval container (host .env points at prod!):
    docker exec uniops_approval_api python -m scripts.seed_routing

Maps (see spec 2026-07-15-approval-routing-phase3-design.md):
  role_management.*_user_id / finance_bp_user_ids -> identity user_roles (additional)
  role_management.*_backup_user_id                -> approval_backups
  dept_gm_opm_mapping / dept_director_mapping /
  dept_supervisor_enabled                         -> approval_dept_routing (one row per active dept)

Engine defaults preserved verbatim: gm_or_opm -> 'gm'; director -> NULL;
supervisor_enabled -> FALSE (an unlisted dept has NO supervisor layer today).
"""
import asyncio
import json

import sqlalchemy as sa

from app.db.base import AsyncSessionLocal

# role_management key -> role code carried as an additional role
_POST_KEYS = {
    "gm_user_id": "gm",
    "opm_user_id": "opm",
    "vendor_manager_user_id": "vendor_manager",
    "finance_manager_user_id": "finance_manager",
    "procurement_manager_user_id": "procurement_manager",
}
_BACKUP_KEYS = {"gm_backup_user_id": "gm", "opm_backup_user_id": "opm"}


def _as_dict(v):
    return json.loads(v) if isinstance(v, str) else (v or {})


async def seed_routing(session) -> dict:
    row = (await session.execute(sa.text(
        "SELECT role_management, dept_gm_opm_mapping, dept_director_mapping,"
        " dept_supervisor_enabled FROM company_config LIMIT 1"))).first()
    rm = _as_dict(row[0]) if row else {}
    gm_opm = _as_dict(row[1]) if row else {}
    director = _as_dict(row[2]) if row else {}
    supervisor = _as_dict(row[3]) if row else {}

    counts = {"user_roles": 0, "dept_rows": 0, "backups": 0}

    # 1) post holders -> user_roles (skip when it already is the user's primary role)
    pairs: list[tuple[str, str]] = []
    for key, code in _POST_KEYS.items():
        uid = rm.get(key)
        if uid:
            pairs.append((str(uid), code))
    for uid in rm.get("finance_bp_user_ids", []) or []:
        pairs.append((str(uid), "finance_bp"))
    for uid, code in pairs:
        primary = (await session.execute(sa.text(
            "SELECT role FROM users WHERE id = :u"), {"u": uid})).scalar_one_or_none()
        if primary == code:
            continue
        r = await session.execute(sa.text(
            "INSERT INTO user_roles (user_id, role_code) VALUES (:u, :c) "
            "ON CONFLICT DO NOTHING"), {"u": uid, "c": code})
        counts["user_roles"] += r.rowcount or 0

    # 2) backups
    for key, code in _BACKUP_KEYS.items():
        uid = rm.get(key)
        if uid:
            r = await session.execute(sa.text(
                "INSERT INTO approval_backups (role_code, backup_user_id) VALUES (:c, :u) "
                "ON CONFLICT (role_code) DO NOTHING"), {"c": code, "u": str(uid)})
            counts["backups"] += r.rowcount or 0

    # 3) one row per ACTIVE department, engine defaults preserved
    depts = (await session.execute(sa.text(
        "SELECT id FROM departments WHERE is_active"))).scalars().all()
    for d in depts:
        ds = str(d)
        r = await session.execute(sa.text(
            "INSERT INTO approval_dept_routing "
            " (dept_id, gm_or_opm, director_user_id, supervisor_enabled) "
            "VALUES (:d, :g, :dir, :s) ON CONFLICT (dept_id) DO NOTHING"),
            {"d": ds,
             "g": gm_opm.get(ds, "gm"),
             "dir": director.get(ds),
             "s": bool(supervisor.get(ds, False))})
        counts["dept_rows"] += r.rowcount or 0
    return counts


async def main():
    async with AsyncSessionLocal() as session:
        counts = await seed_routing(session)
        await session.commit()
        print(f"seed_routing done: {counts}")


if __name__ == "__main__":
    asyncio.run(main())
```

`AsyncSessionLocal` 的导入路径以 approval-api 实际为准(先 `grep -rn "async_sessionmaker" approval-api/app/db/`)。

- [ ] **Step 4: 测试转绿**

- [ ] **Step 5: dev 实跑 + 逐项核对**

```bash
docker exec uniops_approval_api python -m scripts.seed_routing
docker exec uniops_postgres psql -U epms -d epms -c \
 "SELECT (SELECT count(*) FROM approval_dept_routing) dept_rows,
         (SELECT count(*) FROM approval_dept_routing WHERE supervisor_enabled) sup_on,
         (SELECT count(*) FROM approval_backups) backups,
         (SELECT count(*) FROM user_roles) user_roles"
```
**预期(dev 实测基线)**:`dept_rows=12`、**`sup_on=0`**(关键:无任何部门有 supervisor 层)、`backups=2`、`user_roles=6`(gm/opm/vendor_manager/finance_manager/procurement_manager/finance_bp 各 1;Farshid 兼 opm+finance_manager、PM test 兼 procurement_manager+finance_bp)。

- [ ] **Step 6: Commit**

```bash
git add approval-api/scripts approval-api/tests/test_seed_routing.py
git commit -m "feat(approval): idempotent seed — assignments to user_roles, dept trio to routing table"
```

---

### Task 4: 适配器 getter(approval 改读自有表,engine 零改动)

**Files:**
- Modify: `approval-api/app/crud/workflow.py`(`get_role_management`、`get_dept_gm_opm_mapping` 换数据源;新增两个 getter)
- Modify: `approval-api/app/crud/engine.py`(**仅**改 `dept_director_mapping`/`dept_supervisor_enabled` 的取数处——查清它们从哪来后改成新 getter;`_build_role_map`/`_resolve_*`/`_can_act` 的**函数体一行不改**)
- Modify: `approval-api/app/models/config.py`(CompanyConfig 镜像删掉 `role_management`/`dept_*` 四列——approval 不再读它们;`workflow_defs`、`budget_admin_config` 保留)
- Test: `approval-api/tests/test_routing_adapters.py`

**Interfaces:**
- Consumes: Task 1 表、Task 3 seed 后的数据。
- Produces(**形状必须与旧 JSONB 逐字段一致**):
  - `get_role_management(db) -> dict`:`{"gm_user_id": str|None, "opm_user_id": str|None, "vendor_manager_user_id": ..., "finance_manager_user_id": ..., "procurement_manager_user_id": ..., "finance_bp_user_ids": [str], "gm_backup_user_id": str|None, "opm_backup_user_id": str|None}`(值为 **str**,因 engine 做 `uuid.UUID(uid_str)`)
  - `get_dept_gm_opm_mapping(db) -> dict[str, str]`:`{dept_id_str: "gm"|"opm"}`
  - `get_dept_director_mapping(db) -> dict[str, str]`:`{dept_id_str: user_id_str}`(仅含有 director 的部门)
  - `get_dept_supervisor_enabled(db) -> dict[str, bool]`:`{dept_id_str: True}`(**仅含 enabled 的部门**——engine 用 `.get(dept)` falsy 判断,含 False 的键也无妨,但只放 True 更贴合语义)

- [ ] **Step 1: 写失败测试 `approval-api/tests/test_routing_adapters.py`**

```python
"""Getters read the new tables but keep the exact legacy dict shape the engine
consumes — that is what makes the engine's ~20 call sites need no change."""
import uuid

import pytest
import sqlalchemy as sa

from app.crud.workflow import (get_dept_director_mapping, get_dept_gm_opm_mapping,
                               get_dept_supervisor_enabled, get_role_management)

pytestmark = pytest.mark.asyncio


async def test_role_management_shape_from_tables(engine_db_session):
    db = engine_db_session
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
    uid = uuid.uuid4()
    await db.execute(sa.text(
        "INSERT INTO users (id, email, hashed_password, full_name, role) "
        "VALUES (:i, :e, 'x', 'Primary GM', 'gm')"), {"i": str(uid), "e": f"{uid}@t.co"})
    await db.flush()
    rm = await get_role_management(db)
    assert rm["gm_user_id"] == str(uid)


async def test_dept_getters_shape(engine_db_session):
    db = engine_db_session
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
```

- [ ] **Step 2: 跑测试确认失败**

- [ ] **Step 3: 改 `workflow.py` 的 getter**

```python
_POST_CODES = ("gm", "opm", "vendor_manager", "finance_manager", "procurement_manager")


async def _post_holders(db: AsyncSession) -> dict[str, list[str]]:
    """code -> [user_id str]. A post can be held as a PRIMARY role (users.role)
    or an ADDITIONAL role (user_roles) — both count."""
    out: dict[str, list[str]] = {}
    rows = (await db.execute(sa.text(
        "SELECT role AS code, id::text AS uid FROM users WHERE role = ANY(:codes) AND is_active "
        "UNION ALL "
        "SELECT ur.role_code, ur.user_id::text FROM user_roles ur "
        " JOIN users u ON u.id = ur.user_id "
        " WHERE ur.role_code = ANY(:codes) AND u.is_active"),
        {"codes": list(_POST_CODES) + ["finance_bp"]})).all()
    for code, uid in rows:
        out.setdefault(code, []).append(uid)
    return out


async def get_role_management(db: AsyncSession) -> dict:
    """Legacy-shaped dict, now sourced from user_roles + approval_backups.

    Shape is deliberately identical to the retired company_config.role_management
    JSONB so engine.py's call sites keep working untouched.
    """
    holders = await _post_holders(db)
    rm: dict = {f"{code}_user_id": (holders.get(code) or [None])[0] for code in _POST_CODES}
    rm["finance_bp_user_ids"] = holders.get("finance_bp", [])
    backups = (await db.execute(sa.text(
        "SELECT role_code, backup_user_id::text FROM approval_backups"))).all()
    for code, uid in backups:
        rm[f"{code}_backup_user_id"] = uid
    return rm


async def get_dept_gm_opm_mapping(db: AsyncSession) -> dict:
    rows = (await db.execute(sa.text(
        "SELECT dept_id::text, gm_or_opm FROM approval_dept_routing"))).all()
    return {d: g for d, g in rows}


async def get_dept_director_mapping(db: AsyncSession) -> dict:
    rows = (await db.execute(sa.text(
        "SELECT dept_id::text, director_user_id::text FROM approval_dept_routing "
        "WHERE director_user_id IS NOT NULL"))).all()
    return {d: u for d, u in rows}


async def get_dept_supervisor_enabled(db: AsyncSession) -> dict:
    rows = (await db.execute(sa.text(
        "SELECT dept_id::text FROM approval_dept_routing WHERE supervisor_enabled"))).all()
    return {d: True for (d,) in rows}
```

`is_active` 列名以 users 物理表为准(先核对 information_schema——见 [[feedback_uniops_mirror_models_match_reality]]);`import sqlalchemy as sa` 按文件现有风格补。

- [ ] **Step 4: engine 的取数处改用新 getter**

先 `grep -n "dept_director_mapping\|dept_supervisor_enabled\|cfg\." approval-api/app/crud/engine.py`,把从 `cfg.dept_director_mapping` / `cfg.dept_supervisor_enabled` / `cfg.role_management` / `cfg.dept_gm_opm_mapping` 取数的地方,换成 `await get_dept_director_mapping(db)` 等。**`_build_role_map`/`_resolve_gm_or_opm`/`_resolve_director`/`_resolve_supervisor`/`_can_act` 的函数体不动**(它们只吃 dict)。

- [ ] **Step 5: 删 CompanyConfig 镜像的四列**

`approval-api/app/models/config.py` 删 `role_management`、`dept_gm_opm_mapping`、`dept_director_mapping`、`dept_supervisor_enabled` 四个 Mapped 列(保留 `workflow_defs`、`budget_admin_config` 等仍在读的)。删后 `grep -rn "role_management\|dept_gm_opm\|dept_director\|dept_supervisor" approval-api/app` 应只剩注释/getter 内部名。

- [ ] **Step 6: 测试转绿 + approval 全量回归**

`cd /c/Project/uniops/approval-api && TEST_PG_PASSWORD=... ./.venv/Scripts/python -m pytest tests -q` → **既有测试全绿**(尤其 `test_engine_dept_manager_routing.py`、`test_engine_optional_levels.py`、`test_effective_workflow_endpoint.py`);若某些既有测试直接往 company_config 写 JSONB 造数据,改为往新表写(它们测的是 engine 行为,不是数据源)。

- [ ] **Step 7: Commit**

```bash
git add approval-api/app/crud/workflow.py approval-api/app/crud/engine.py \
  approval-api/app/models/config.py approval-api/tests/test_routing_adapters.py
git commit -m "feat(approval): read routing from own tables via legacy-shaped getters; drop epms config mirror columns"
```

---

### Task 5: 其余消费方改读 user_roles(epms / finance / expense)

**Files:**
- Modify: `epms-api/app/core/access_scope.py:102-125`(`_effective_role_codes`:`CompanyConfig.role_management` → `user_roles`)
- Modify: `finance-api/app/crud/payment_execute.py:84 附近`、`finance-api/app/crud/journal_voucher.py:21 附近`
- Modify: `expense-api/app/api/v1/pa.py:259 附近`
- Test: `epms-api/tests/test_pr_scoping.py`(既有,验证零回归)、各服务既有测试

**Interfaces:**
- Consumes: identity `user_roles`(同库直读)。
- Produces: 三处的"角色并集"语义不变——**主角色 ∪ 副角色**,替代原先的"主角色 ∪ role_management 指派"。

- [ ] **Step 1: 摸清三处现状**

```bash
sed -n '100,130p' epms-api/app/core/access_scope.py
sed -n '78,95p' finance-api/app/crud/payment_execute.py
sed -n '250,270p' expense-api/app/api/v1/pa.py
grep -n "role_management" finance-api/app/crud/journal_voucher.py
```
三处的共同形态:读 `cfg.role_management`,判断 `user_id` 是否等于某 `*_user_id` / 在 `finance_bp_user_ids` 里 → 得出该用户"额外拥有"的角色。

- [ ] **Step 2: 写/改测试(先失败)**

epms:`epms-api/tests/test_pr_scoping.py` 里若有基于 role_management 造"该用户是 GM"的用例,改为往 `user_roles` 插行;若没有,新增一个:

```python
async def test_additional_role_widens_scope(test_engine):
    """A dept_manager holding the gm additional role sees what a gm sees."""
    # (按该文件既有 fixture 造 user + PR;给该 user 插 user_roles('gm') 行;
    #  断言 build_scope/visible_pr_subquery 的结果与主角色为 gm 的用户一致)
```
finance:`finance-api/tests/` 里找 can_pay 相关用例(`grep -rn "role_management\|can_pay" finance-api/tests | head`),把造数从 company_config 改成 user_roles。
expense:同法(`grep -rn "role_management" expense-api/tests | head`)。

- [ ] **Step 3: 改 epms `_effective_role_codes`**

```python
async def _effective_role_codes(
    db: AsyncSession,
    base_role: str,
    user_id: uuid.UUID,
) -> set[str]:
    """Return the full set of active role codes for a user.

    The JWT base role plus any ADDITIONAL roles from identity's user_roles
    (same physical DB — read directly, no HTTP). Replaces the retired
    company_config.role_management assignments (phase 3).
    """
    codes: set[str] = {base_role} if base_role else set()
    rows = (await db.execute(sa.text(
        "SELECT role_code FROM user_roles WHERE user_id = :u"), {"u": str(user_id)})).scalars().all()
    codes.update(rows)
    return codes
```
(`CompanyConfig` 的 import 若因此不再使用,一并清掉。)

- [ ] **Step 4: 改 finance / expense 的 can_pay**

三处的改法一致——把"查 role_management 得出额外角色"换成"查 user_roles":

```python
async def _user_role_codes(db, user_id: uuid.UUID, base_role: str) -> set[str]:
    """Primary role + additional roles (identity user_roles, same DB)."""
    codes = {base_role} if base_role else set()
    rows = (await db.execute(sa.text(
        "SELECT role_code FROM user_roles WHERE user_id = :u"), {"u": str(user_id)})).scalars().all()
    codes.update(rows)
    return codes
```
放在各服务已有的合适模块里(finance 放 `app/crud/payment_execute.py` 顶部私有函数;`journal_voucher.py` 若有同样需要则各自实现一份——**三个服务不共享代码**,后端无共享包)。原判断 `actor_id == uuid.UUID(rm["finance_manager_user_id"])` → `"finance_manager" in await _user_role_codes(db, actor_id, actor_role)`。

- [ ] **Step 5: 各服务测试转绿 + 全量**

```bash
docker exec uniops_epms_api python -m pytest tests -q          # 失败集须与基线一致
cd /c/Project/uniops/finance-api && TEST_PG_PASSWORD=... ./.venv/Scripts/python -m pytest tests -q
docker exec uniops_expense_api python -m pytest tests -q
```
epms 基线:实施前先跑一次存 `/tmp/epms_base_p3.txt`,改完 diff 失败集必须**逐条一致**。

- [ ] **Step 6: Commit**

```bash
git add epms-api/app/core/access_scope.py epms-api/tests/test_pr_scoping.py \
  finance-api/app/crud/payment_execute.py finance-api/app/crud/journal_voucher.py \
  expense-api/app/api/v1/pa.py
git commit -m "feat: role unions read identity user_roles instead of epms role_management"
```

---

### Task 6: approval 路由 API + Portal 管理页

**Files:**
- Create: `approval-api/app/api/v1/routing.py`
- Modify: `approval-api/app/main.py`(挂 router,照现有 `include_router` 前缀风格)
- Create: `portal/src/pages/admin/ApprovalRouting.tsx`
- Modify: `portal/src/App.tsx`(加 `/admin/approval-routing` 路由)、`portal/src/components/layout/navConfig.tsx:70 附近`(加导航项)
- Modify: `epms-api/app/api/v1/config.py`(加 `GET/PUT /config/approval-routing` 转发端点)——**执行期修正:浏览器不直连 approval-api**(无公网域名+无 CORS),经 epms 网关,复用已有 `APPROVAL_ENGINE_URL`,零新增 env
- Test: `approval-api/tests/test_routing_api.py`

**Interfaces:**
- Consumes: Task 1 表、Task 4 getter。
- Produces:
  - `GET /approval/v1/routing` → `{"departments": [{"dept_id","dept_code","dept_name","gm_or_opm","director_user_id","supervisor_enabled"}], "backups": {"gm": uid|null, "opm": uid|null}}`(部门按 code 排序,含所有 is_active 部门——缺行的部门用引擎缺省填充:gm/None/false)
  - `PUT /approval/v1/routing` body `{"departments": [{"dept_id","gm_or_opm","director_user_id","supervisor_enabled"}], "backups": {"gm": uid|null, "opm": uid|null}}` → 200 同 GET 形状;system_admin only(403);未知 dept_id / `gm_or_opm` 非 gm|opm → 422
  - 前端路由 `/admin/approval-routing`;navConfig 项 `{ label: 'Approval Routing', icon: GitBranch, href: 'portal:/admin/approval-routing', adminOnly: true }`

- [ ] **Step 1: 写失败测试 `approval-api/tests/test_routing_api.py`**

**⚠️ 计划期核实**:approval-api **没有任何 HTTP endpoint 测试**——现有测试全是直接调 crud 函数,conftest **没有 client fixture**。本任务要新建这套设施(**在本测试文件内自建,不动 conftest**)。已核实的事实:

- 路由前缀:`app.include_router(api_router, prefix="/approval/v1")`(`main.py:59`)
- 鉴权:`app/core/deps.py` 只有 `get_token_payload`(`jwt.decode(..., settings.jwt_secret_key, algorithms=[settings.jwt_algorithm])`)+ `CurrentUser`;**没有 create_access_token 辅助函数**,测试需自己用 `jose.jwt.encode` 造 token(payload 至少含 `sub`、`role`、`type`/`exp` 视 decode 要求而定——照 `settings` 的 secret/algorithm)
- DB 依赖:`app/db/base.py` 或 `deps.py` 里的 get_db 名称以实际为准,用 `app.dependency_overrides[<该依赖>] = lambda: engine_db_session` 接到测试 session

```python
from jose import jwt as jose_jwt
from app.core.config import settings

def _token(role: str, sub: str | None = None) -> str:
    return jose_jwt.encode(
        {"sub": sub or str(uuid.uuid4()), "role": role},
        settings.jwt_secret_key, algorithm=settings.jwt_algorithm)
```
(若 decode 要求 `exp`/`type` 等字段,按报错补齐并在报告中说明。)

```python
"""Routing API: read/write dept rules + backups; admin-gated."""
import uuid

import pytest
import sqlalchemy as sa
from httpx import ASGITransport, AsyncClient

from app.main import app

pytestmark = pytest.mark.asyncio
BASE = "/approval/v1"


def _client(role="system_admin"):
    """Authed client — NEW infrastructure for approval-api (see note above)."""
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test",
                       headers={"Authorization": f"Bearer {_token(role)}"})


async def test_get_lists_all_active_departments_with_defaults(engine_db_session):
    db = engine_db_session
    d = uuid.uuid4()
    await db.execute(sa.text(
        "INSERT INTO departments (id, code, name, is_active) VALUES (:i,'D9','Dept 9',true)"),
        {"i": str(d)})
    await db.flush()
    async with _client() as c:
        r = await c.get(f"{BASE}/routing")
    assert r.status_code == 200
    row = next(x for x in r.json()["departments"] if x["dept_id"] == str(d))
    # No routing row yet -> engine defaults surface: gm, no director, NO supervisor
    assert (row["gm_or_opm"], row["director_user_id"], row["supervisor_enabled"]) == ("gm", None, False)


async def test_put_upserts_and_requires_admin(engine_db_session):
    db = engine_db_session
    d = uuid.uuid4()
    await db.execute(sa.text(
        "INSERT INTO departments (id, code, name, is_active) VALUES (:i,'D8','Dept 8',true)"),
        {"i": str(d)})
    await db.flush()
    body = {"departments": [{"dept_id": str(d), "gm_or_opm": "opm",
                             "director_user_id": None, "supervisor_enabled": True}],
            "backups": {"gm": None, "opm": None}}
    async with _client("requester") as c:
        assert (await c.put(f"{BASE}/routing", json=body)).status_code == 403
    async with _client() as c:
        assert (await c.put(f"{BASE}/routing", json=body)).status_code == 200
        bad = {"departments": [{"dept_id": str(d), "gm_or_opm": "ceo",
                                "director_user_id": None, "supervisor_enabled": False}],
               "backups": {"gm": None, "opm": None}}
        assert (await c.put(f"{BASE}/routing", json=bad)).status_code == 422
    got = (await db.execute(sa.text(
        "SELECT gm_or_opm, supervisor_enabled FROM approval_dept_routing WHERE dept_id=:d"),
        {"d": str(d)})).first()
    assert got == ("opm", True)
```

- [ ] **Step 2: 跑测试确认失败**

- [ ] **Step 3: 实现 `approval-api/app/api/v1/routing.py`**

要点(照该服务既有 router 的鉴权/依赖风格写):
- `GET`:左连 `departments`(is_active)与 `approval_dept_routing`,缺行的部门用 `("gm", None, False)` 填充;`backups` 从 `approval_backups` 读。
- `PUT`:system_admin only;校验每个 `dept_id` 存在且 active、`gm_or_opm in ("gm","opm")`,否则 422;upsert(`ON CONFLICT (dept_id) DO UPDATE`),`backups` 同理 upsert(值为 null 则删行);写 `updated_by`;返回与 GET 同形状。

- [ ] **Step 4: 测试转绿**

- [ ] **Step 5: Portal 页面 `ApprovalRouting.tsx`**

chrome/守卫照 `portal/src/pages/admin/DataMaintenance.tsx` 抄(该 app 无 PortalChromeLayout,是 bare div + "Back to UniOps" 链接 + `user?.role !== 'system_admin'` 守卫)。内容:
- 一张部门表:Department(code — name)/ GM or OPM(单选 gm|opm)/ Director(用户下拉,可空)/ Supervisor(开关);
- 页尾两个下拉:GM backup、OPM backup;
- 脏标记 + 底部 Save/Discard 条;Save 调 `PUT /routing`;错误显示在条上;
- 用户列表翻页取全(`page_size=200` 循环,防 20 条截断);部门与用户数据来源:用户走 epms `GET /users`(既有 admin 端点),部门走 `GET /approval/v1/routing` 返回的 dept_code/dept_name(**避免再引一个部门 API**)。
- 全英文文案。

- [ ] **Step 6: 路由 + 导航 + api 客户端**

App.tsx 加 `<Route path="/admin/approval-routing" element={<ProtectedRoute><ApprovalRouting /></ProtectedRoute>} />`;navConfig 第 70 行邻域加导航项(lucide `GitBranch`);页面用现有 `epmsApi` 调 `/config/approval-routing`(**不新建 approvalApi 客户端**——见上方执行期修正)。

- [ ] **Step 7: typecheck + dev 冒烟**

```bash
cd /c/Project/uniops/portal && npx tsc -p tsconfig.app.json --noEmit --ignoreDeprecations 6.0   # 0 errors
docker restart uniops_portal_frontend && docker logs uniops_portal_frontend --tail 6            # clean vite start
```

- [ ] **Step 8: Commit**

```bash
git add approval-api/app/api/v1/routing.py approval-api/app/main.py \
  approval-api/tests/test_routing_api.py portal/src/pages/admin/ApprovalRouting.tsx \
  portal/src/App.tsx portal/src/components/layout/navConfig.tsx portal/src/lib/api.ts \
  docker-compose.dev.yml docker-compose.prod.yml portal/Dockerfile
git commit -m "feat(approval): routing API + Portal Approval Routing admin page"
```

---

### Task 7: EPMS Role Management 页签下线 + 代班功能删除

**Files:**
- Modify: `epms/src/pages/admin/AdminPanel.tsx`(删 Role Management 页签:tab 项、case、组件、`TEMP_ROLE_OPTIONS`、`DEFAULT_ROLE_MANAGEMENT_CONFIG` 等)
- Modify: `epms/src/services/config.ts`(删 role_management / temp assignment 相关方法与类型)
- Modify: `epms-api/app/api/v1/config.py`(删 temp_assignments 端点与 ConfigResponse 嵌入:`:46-51` 附近)
- Modify: `epms-api/app/crud/config.py`(删 `list_temp_assignments` 等)、`epms-api/app/schemas/config.py`(删 TempAssignment* 与四件套字段)、`epms-api/app/models/config.py`(删 `TempAssignment` 模型 `:117-132`)
- Create: `epms-api/alembic/versions/<next>_drop_temp_assignments.py`(**先 `docker exec uniops_epms_api alembic heads` 查真实链尾**,见 [[feedback_uniops_alembic_new_migration_check_heads]])
- Test: epms 既有测试(`test_config_director_mapping.py` 若测的是被删字段则一并删除/改写)

**Interfaces:**
- Consumes: 无(纯删除)。
- Produces: `company_config` 的四件套 JSONB 列**保留不删**(数据留档,回滚可用;仅无人再读);`temp_assignments` 表删除。

- [ ] **Step 1: 前端删页签**

删 tab 数组里的 role_management 项、`renderTab` 的 case、`RoleManagementSection`(实名以文件为准)、`TEMP_ROLE_OPTIONS`(:2501)、`DEFAULT_ROLE_MANAGEMENT_CONFIG`(:2510 附近)及其 import/类型。删后:
`grep -n "TEMP_ROLE_OPTIONS\|RoleManagement\|role_management\|temp_assignment" epms/src/pages/admin/AdminPanel.tsx` → **零命中**。

- [ ] **Step 2: 后端删 temp_assignments**

端点(`config.py:46-51` 的嵌入 + 相关 POST/DELETE 路由)、crud、schema、model 全删;写迁移 drop 表:

```python
"""drop temp_assignments — dead feature (engine never read it)"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

revision = "<new>"
down_revision = "<真实链尾>"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_table("temp_assignments")


def downgrade() -> None:
    op.create_table(
        "temp_assignments",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("delegate_user_id", UUID(as_uuid=True),
                  sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True),
        sa.Column("role_key", sa.String(50), nullable=False, index=True),
        sa.Column("start_date", sa.Date, nullable=False),
        sa.Column("end_date", sa.Date, nullable=False),
        sa.Column("created_by", UUID(as_uuid=True),
                  sa.ForeignKey("users.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
    )
```
(downgrade 照 `epms-api/app/models/config.py:117-132` 的真实列写;删表前确认生产该表为空:`SELECT count(*) FROM temp_assignments`。)

- [ ] **Step 3: typecheck + 测试**

```bash
cd /c/Project/uniops/epms && npx tsc -p tsconfig.app.json --noEmit          # 基线 69,不得增加
docker exec uniops_epms_api alembic upgrade head
docker exec uniops_epms_api python -m pytest tests -q                        # 失败集与基线一致
```

- [ ] **Step 4: Commit**

```bash
git add epms/src/pages/admin/AdminPanel.tsx epms/src/services/config.ts \
  epms-api/app/api/v1/config.py epms-api/app/crud/config.py \
  epms-api/app/schemas/config.py epms-api/app/models/config.py \
  epms-api/alembic/versions/<new>_drop_temp_assignments.py
git commit -m "feat(epms): retire Role Management tab; drop dead temp_assignments feature"
```

---

### Task 8: 平价断言 + 全量回归 + 发布材料

**Files:**
- Create: `approval-api/scripts/verify_routing_parity.py`
- Create: `docs/superpowers/plans/2026-07-15-approval-routing-phase3-release.md`

**Interfaces:**
- Consumes: 全部前序任务。
- Produces: 可重复运行的平价比对脚本;发布清单。

- [ ] **Step 1: 写平价脚本**

`approval-api/scripts/verify_routing_parity.py`:遍历所有 active 部门 × `("pr","po","pa")`,用**旧口径**(直接读 company_config 四件套 JSONB,脚本内自带一份旧解析逻辑的拷贝——即 `_build_role_map` 的旧输入)与**新口径**(`crud/workflow.py` 的 getter)分别算出每个 step 的 `role → user_id` 映射,逐项比对;任一差异打印 `DIFF dept=<code> doc=<type> step=<role> old=<uid> new=<uid>` 并以非零码退出;全等打印 `PARITY OK (N depts × 3 doc types)`。

它是**一次性验收工具**(迁移后 JSONB 仍在,可对比);发布后可删。

- [ ] **Step 2: dev 跑平价**

```bash
docker exec uniops_approval_api python -m scripts.verify_routing_parity
```
预期 `PARITY OK (12 depts × 3 doc types)`。任何 DIFF 都必须查清再继续——这是本期最重要的门。

- [ ] **Step 3: 全量回归**

```bash
cd /c/Project/uniops/approval-api && TEST_PG_PASSWORD=... ./.venv/Scripts/python -m pytest tests -q
cd /c/Project/uniops/identity-api && TEST_PG_PASSWORD=... ./.venv/Scripts/python -m pytest tests -q
cd /c/Project/uniops/finance-api  && TEST_PG_PASSWORD=... ./.venv/Scripts/python -m pytest tests -q
docker exec uniops_epms_api python -m pytest tests -q       # 失败集 diff 基线,须逐条一致
docker exec uniops_expense_api python -m pytest tests -q
docker exec uniops_vms_api python -m pytest tests -q
cd /c/Project/uniops/portal && npx tsc -p tsconfig.app.json --noEmit --ignoreDeprecations 6.0
cd /c/Project/uniops/epms   && npx tsc -p tsconfig.app.json --noEmit           # 基线 69
```

- [ ] **Step 4: 写发布说明**

`docs/superpowers/plans/2026-07-15-approval-routing-phase3-release.md`,照一期 release 文档的结构,必须写明:
- **新迁移两处**:approval `0001_approval_routing`(首次!`migrate-prod.sh` 已加 approval-api)、identity `0003_post_role_singleton`、epms drop temp_assignments;
- **★ seed 必做**:`docker compose -f docker-compose.prod.yml run --rm approval-api python -m scripts.seed_routing`,顺序 migrate → seed → **平价脚本** → up;
- **不 seed 的后果**:approval 的 getter 会返回空 → **所有审批步骤解析不到审批人**(比一期更严重);
- **零新增基建**(执行期修正):浏览器**不直连 approval-api**——它无公网域名(`Caddyfile:52`:"approval/identity are server-to-server, no subdomain")且生产 compose 未给它 `ALLOWED_ORIGINS`。Portal 经 **epms-api 转发网关**访问(`GET/PUT /config/approval-routing` → approval `GET/PUT /approval/v1/routing`),复用早已配好的 `APPROVAL_ENGINE_URL`(dev/prod compose 均在)。故本次发布**不需要新 env、不需要 DNS、不需要 Caddy 改动**。原计划的 `VITE_APPROVAL_API_URL` 方案已废弃(见 Task 6 修复轮)。
- 回滚:四件套 JSONB 未删,回退 TAG 即恢复旧口径(新表留着无害)。

- [ ] **Step 5: Commit**

```bash
git add approval-api/scripts/verify_routing_parity.py \
  docs/superpowers/plans/2026-07-15-approval-routing-phase3-release.md
git commit -m "test(approval): routing parity verifier + phase 3 release notes"
```
