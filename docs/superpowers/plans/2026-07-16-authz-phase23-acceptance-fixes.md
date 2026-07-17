# 权限重构 ②③ 验收问题修复 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 修掉生产验收报出的 3 个问题:部门配置双源(Issue 1)、Data Maintenance 永久 loading(Issue 2)、三个 admin 页缺 sidebar(Issue 3)。

**Architecture:** 三条互相独立,可任意顺序。Issue 1 动的是**文档可见性**(安全相关且**当前零测试覆盖**)→ 必须先补表征测试(characterization test)锁住现有行为再改。Issue 2 是构建期 env 漏配 + fetch 缺超时。Issue 3 抽公共 layout 消除复制粘贴。

**Tech Stack:** FastAPI + SQLAlchemy async(epms-api)、React + Vite(portal)、Docker Compose。

## Global Constraints

- **UI 文案全英文**(user-facing 字符串;注释可中文)。见 [[feedback_uniops_ui_english_only]]。
- **禁止 `git add -A` / `-a` / `.`,禁止 `git stash` / `git reset`** —— 工作区有用户的未提交 WIP(训练材料 PDF、test_users_import_csv.py 等),只 `git add` 明确点名的文件。
- **所有 DB 脚本必须容器内跑**(`docker exec` / `compose run --rm`)。宿主 `.env` 指向**生产库** 10.10.50.20。见 [[feedback_uniops_host_env_points_at_prod]]。
- **验证要正面证据**:测试必须先看它**失败**再看它通过;"无输出=通过"是假阴性。见 [[feedback_verification_positive_evidence]]。
- 分支:`feature/approval-routing-phase3`(当前 HEAD 6501934)。
- `company_config.dept_*` 三列**留档不删**(回滚 + `reconstruct.py` 仍读 `dept_gm_opm_mapping`)。

---

### Task 1: access_scope 表征测试(锁住现有可见性行为)

**为什么先做**:`_mapped_dept_ids` / `_director_dept_ids` 决定 GM/OPM/Director 能看见哪些部门的 PR/PO/PA,**今天零测试**。先用测试把**现有(读 JSONB 的)行为**钉死,Task 2 换数据源后同一批测试必须仍全绿 —— 这就是可见性平价证据。

**Files:**
- Create: `epms-api/tests/test_access_scope_dept.py`

**Interfaces:**
- Consumes: `app.core.access_scope._mapped_dept_ids(db, role) -> list[uuid.UUID]`、`_director_dept_ids(db, user_id) -> list[uuid.UUID]`
- Produces: 一套 Task 2 复用的测试(**Task 2 不得修改断言语义**,只允许改 fixture 建数据的位置:JSONB → `approval_dept_routing` 表)

**测试环境**:见 [[feedback_uniops_admin_test_db_env]] —— pytest 连不上生产库,须覆盖 `POSTGRES_*` 指向本地 docker `uniops_postgres`。

- [ ] **Step 1: 写表征测试(读现有 JSONB 实现)**

```python
"""access_scope 的部门可见性表征测试。

锁住 _mapped_dept_ids / _director_dept_ids 的现有行为,以便把数据源从
company_config.dept_* JSONB 换成 approval_dept_routing 表时能证明可见性不变。
"""
import uuid
import pytest
from app.core.access_scope import _mapped_dept_ids, _director_dept_ids
from app.models.config import CompanyConfig


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
```

**注意**:上面 import 缺 `from sqlalchemy import select` —— 实现时补齐。`db` fixture 沿用 `epms-api/tests/conftest.py` 现有的(先读 conftest 确认 fixture 名与 CompanyConfig 是否已存在,不存在则测试内建一条)。

- [ ] **Step 2: 运行,确认全绿(表征测试锁的是现状,故应立刻通过)**

Run: `docker exec uniops_epms_api pytest tests/test_access_scope_dept.py -v`
Expected: 4 passed。**若有 FAIL,说明我对现有行为的理解就是错的 —— 停下来报告,不要改测试去迁就。**

- [ ] **Step 3: 变异验证(证明测试不是空的)**

把 `access_scope.py:_mapped_dept_ids` 的 `if mapped_role == role` 临时改成 `if True`,重跑。
Expected: **必须有测试 FAIL**(至少 `test_mapped_dept_ids_returns_only_depts_mapped_to_that_role`)。
若全绿 → 测试是空的,修好再继续。**改回来。**

- [ ] **Step 4: Commit**

```bash
git add epms-api/tests/test_access_scope_dept.py
git commit -m "test(epms): characterize dept visibility scope before data-source switch"
```

---

### Task 2: access_scope 改读 approval_dept_routing(消除双源)

**Files:**
- Modify: `epms-api/app/core/access_scope.py`(`_mapped_dept_ids` :82、`_director_dept_ids` :97)
- Modify: `epms-api/tests/test_access_scope_dept.py`(**仅**改 fixture 建数据的位置,断言语义不动)

**Interfaces:**
- Consumes: approval-api 拥有的表 `approval_dept_routing(dept_id PK, gm_or_opm varchar(3) NOT NULL default 'gm', director_user_id uuid NULL, supervisor_enabled bool NOT NULL default false, ...)`
- Produces: 函数签名与返回类型**不变**(`list[uuid.UUID]`),调用方无需改动

**背景**:同库直读别的服务的表是本项目既定模式(`packages/authz` 就是这么读 identity 矩阵的)。epms **不建 ORM 模型**、**不写**这张表 —— 只用裸 SQL 只读,避免 epms 反过来"拥有"approval 的 schema。

**★ 已知语义变化(必须照此实现并写进注释)**:
旧 JSONB 里**未列出**的部门,`_mapped_dept_ids('gm')` **不**返回它;而 `seed_routing` 给**每个活跃部门都写了一行**,未列出的取默认 `gm_or_opm='gm'` → 直读表会让 GM **多看见**这些部门。
- **今天影响为 0**:dev 与生产均 12 个活跃部门且**全部显式映射**(9 gm / 3 opm),无"未列出"部门。
- **决策:接受这个新语义**(路由本来就把未映射部门默认派给 GM 审 —— `engine.py` 的 `gm_opm.get(dept_id, "gm")`;旧代码"GM 要审却看不见"本身才是 bug)。
- 用 Step 1 的测试把它**显式钉住**,不让它是个悄悄发生的行为。

- [ ] **Step 1: 改测试的建数据位置 + 加一条锁住新语义的测试**

把 4 个测试里写 `cfg.dept_gm_opm_mapping = {...}` / `cfg.dept_director_mapping = {...}` 的地方,改为往 `approval_dept_routing` 插行:

```python
async def _set_routing(db, rows: dict[uuid.UUID, str], directors: dict[uuid.UUID, uuid.UUID] | None = None):
    """rows: {dept_id: 'gm'|'opm'};directors: {dept_id: user_id}"""
    await db.execute(text("DELETE FROM approval_dept_routing"))
    directors = directors or {}
    for dept_id, code in rows.items():
        await db.execute(text(
            "INSERT INTO approval_dept_routing (dept_id, gm_or_opm, director_user_id, supervisor_enabled) "
            "VALUES (:d, :g, :dir, false)"),
            {"d": str(dept_id), "g": code, "dir": str(directors[dept_id]) if dept_id in directors else None})
    await db.commit()
```

`test_mapped_dept_ids_excludes_unmapped_dept` 的断言含义随之演进 —— **替换**成这条,并保留原测试名之外的新名字:

```python
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
```

- [ ] **Step 2: 运行,确认 FAIL(实现还在读 JSONB)**

Run: `docker exec uniops_epms_api pytest tests/test_access_scope_dept.py -v`
Expected: FAIL —— 测试已往新表写数据,而实现仍读 JSONB,返回空列表。**必须先看到这个红,再写实现。**

- [ ] **Step 3: 改实现**

```python
async def _mapped_dept_ids(db: AsyncSession, role: str) -> list[uuid.UUID]:
    """Return department IDs whose gm_or_opm step routes to this role.

    Reads approval-api's approval_dept_routing (same physical DB, read-only —
    epms never writes it; Portal → Approval Routing is the only writer). This is
    the single source of truth for dept routing since the phase-3 migration;
    company_config.dept_gm_opm_mapping is a frozen snapshot kept for rollback.

    NOTE: a department with no explicit mapping still has a routing row with the
    default gm_or_opm='gm', so GM sees it — matching how the engine already
    routes such a department's approval to GM.
    """
    rows = (await db.execute(
        text("SELECT dept_id FROM approval_dept_routing WHERE gm_or_opm = :r"),
        {"r": role},
    )).scalars().all()
    return list(rows)


async def _director_dept_ids(db: AsyncSession, user_id: uuid.UUID) -> list[uuid.UUID]:
    """Return department IDs for which this user is the mapped director.

    Same source/ownership rules as _mapped_dept_ids above.
    """
    rows = (await db.execute(
        text("SELECT dept_id FROM approval_dept_routing WHERE director_user_id = :u"),
        {"u": str(user_id)},
    )).scalars().all()
    return list(rows)
```

补 `from sqlalchemy import text`(若未导入)。若 `CompanyConfig` 至此在本文件已无其他用途,删掉它的 import;**若还有别处用,留着**。

- [ ] **Step 4: 运行,确认全绿**

Run: `docker exec uniops_epms_api pytest tests/test_access_scope_dept.py -v`
Expected: 全部 passed。

- [ ] **Step 5: 跑 epms 全量套件,对基线**

Run: `docker exec uniops_epms_api pytest -q 2>&1 | tail -5`
Expected: 通过数 **≥ 基线 72**,无新增 FAIL。低于基线就是回归。

- [ ] **Step 6: Commit**

```bash
git add epms-api/app/core/access_scope.py epms-api/tests/test_access_scope_dept.py
git commit -m "fix(epms): read dept visibility from approval_dept_routing, not frozen JSONB"
```

---

### Task 3: 删 EPMS Admin 的三个部门页签

**Files:**
- Modify: `epms/src/pages/admin/AdminPanel.tsx`(:39 类型联合、:54-56 页签定义、及对应渲染区块与 state/handler)
- Modify: `epms/src/services/config.ts`(若有仅服务这三个页签的写接口)

**Interfaces:**
- Consumes: 无(纯删除)
- Produces: 无

**为什么现在能删**:Task 2 已让**可见性**改读新表;**路由**在 ③ 期就已迁走。这三个页签写的 `company_config.dept_*` 至此**无人再读** → 是纯粹的误导性 UI(管理员改了不生效)。Portal → Approval Routing 成为唯一入口。
`dept_supervisor` 页签**本来就是死设置**(只有 approval 读,③ 期已迁)。

- [ ] **Step 1: 删页签定义与渲染**

删 `:54-56` 三行页签定义、`:39` 类型联合里的 `'dept_mapping' | 'dept_director_mapping' | 'dept_supervisor'`,以及三个 `activeTab === '...'` 的渲染区块和**只被它们使用**的 state / handler / import。

**★ 删之前先确认每个待删符号无其他引用**:`grep -rn "<符号名>" epms/src/`。有别处引用就留着,并在报告里说明。

- [ ] **Step 2: 后端写接口处理**

查 `epms-api` 里写 `dept_gm_opm_mapping` / `dept_director_mapping` / `dept_supervisor_enabled` 的 config 端点:
Run: `grep -rn "dept_gm_opm_mapping\|dept_director_mapping\|dept_supervisor_enabled" epms-api/app/`
- **读**的地方:Task 2 之后应只剩 `reconstruct.py`(PMS 导入工具,读快照)→ **保留**。
- **写**的地方(config update schema/endpoint 的这三个字段):删除写入路径,防止再有东西改这份冻结快照。
- **列本身不删**(Global Constraints)。

- [ ] **Step 3: 前端 typecheck**

Run: `cd epms && npx tsc -p tsconfig.app.json --noEmit --ignoreDeprecations 6.0`
Expected: 0 errors。见 [[reference_uniops_frontend_tsc6]] —— **别用 `tsc -b` / `npm run build`**(TS 6.0.3 因 baseUrl 弃用直接报错)。

- [ ] **Step 4: 后端测试**

Run: `docker exec uniops_epms_api pytest -q 2>&1 | tail -5`
Expected: ≥ 基线,无新增 FAIL。

- [ ] **Step 5: Commit**

```bash
git add epms/src/pages/admin/AdminPanel.tsx epms-api/app/<改到的文件>
git commit -m "refactor(epms): retire dept mapping/director/supervisor tabs (moved to Portal Approval Routing)"
```

---

### Task 4: 修 Data Maintenance 永久 loading(Issue 2)

**Files:**
- Modify: `portal/Dockerfile`(ARG + ENV)
- Modify: `docker-compose.prod.yml`(portal-web build args,:303-313)
- Modify: `docker-compose.dev.yml`(portal-frontend environment,:453-463)
- Modify: `portal/src/services/adminApi.ts`(`request()` 加超时)

**Interfaces:**
- Consumes: 环境变量 `FINANCE_API_URL`(生产 `/tmp/uniops-domain.env` 已有 = `https://finance-api.canadaroyalmilk.com`;`finance-web` 已在用)
- Produces: 无

**根因**:`portal/Dockerfile` 有 `VITE_EPMS_API_URL` 等 5 个 ARG,**独缺 `VITE_FINANCE_API_URL`** → Vite 静默丢弃 → `adminApi.ts:34` 回落 `http://localhost:8004` → 浏览器连自己机器 → 永久 pending(非拒绝,故无报错)→ `Promise.allSettled` 永不 resolve → 整页 Loading。
这是 [[feedback_uniops_dockerfile_missing_build_arg]] 的**第二次**(上次补了跳转用的 `VITE_FINANCE_URL`,漏了 API 用的 `VITE_FINANCE_API_URL`)。

- [ ] **Step 1: Dockerfile 补 ARG + ENV**

`portal/Dockerfile` 在 `ARG VITE_MDM_API_URL`(:18)后加:
```dockerfile
ARG VITE_FINANCE_API_URL
```
并在 `ENV` 那串里加一行(照现有 `\` 续行风格,注意**最后一行不带 `\`**):
```dockerfile
    VITE_FINANCE_API_URL=$VITE_FINANCE_API_URL \
```

- [ ] **Step 2: compose 补 build arg / env**

`docker-compose.prod.yml` portal-web 的 `args:`(:313 `VITE_MDM_API_URL` 后)加:
```yaml
        VITE_FINANCE_API_URL: ${FINANCE_API_URL}
```
`docker-compose.dev.yml` portal-frontend 的 `environment:`(:463 后)加(dev 里 finance-api 就在本机 8004,此前靠 fallback 碰巧能用,现在显式化):
```yaml
      VITE_FINANCE_API_URL: http://localhost:8004
```

- [ ] **Step 3: adminApi 加 fetch 超时(治本)**

`portal/src/services/adminApi.ts` 的 `request()`,给 `fetch` 加:
```ts
signal: AbortSignal.timeout(15000),
```
(若已有 `signal` 则合并;15s 足够跨系统慢查询,又远短于"永远")。

**为什么这才是治本**:`entities()` 用 `Promise.allSettled` across 4 个系统,本意是"某个系统挂了就跳过它"。但 `fetch` 无超时 → 挂起的请求永不 settle → allSettled 永不 resolve → **容错设计形同虚设,一个系统拖死整页**。加超时后该意图才真正成立:超时 → reject → allSettled settle → 该系统被跳过,其余正常渲染。

- [ ] **Step 4: 加一条测试锁住超时(防止 Step 3 被无意删掉)**

若 `portal` 有测试基建(先查 `ls portal/src/**/*.test.ts*` 与 package.json 的 test script):加一条断言 —— mock 一个永不 resolve 的 fetch,`entities()` 应在超时后 resolve(而非挂起),且返回其余系统的结果。
**若 portal 无测试基建 → 跳过此步,在报告里明确写"portal 无测试基建,超时行为未被测试覆盖"**,不要为这一条现搭整套基建。

- [ ] **Step 5: typecheck**

Run: `cd portal && npx tsc -p tsconfig.app.json --noEmit --ignoreDeprecations 6.0`
Expected: 0 errors。

- [ ] **Step 6: 正面验证 build arg 真的进了产物**

```bash
docker compose -f docker-compose.prod.yml --env-file /tmp/uniops-domain.env build portal-web
docker run --rm --entrypoint sh $(grep -m1 REGISTRY /tmp/uniops-domain.env | cut -d= -f2)/uniops-portal-web:$(grep -m1 '^TAG=' /tmp/uniops-domain.env | cut -d= -f2) -c "grep -rlo 'finance-api.canadaroyalmilk.com' /usr/share/nginx/html/assets/ | head -2"
```
Expected: **至少一个 assets/*.js 命中**。命中 = 域名真被烤进产物;无命中 = ARG 仍没生效(别放过)。
再反向确认 `localhost:8004` 已不在产物里:
```bash
docker run --rm --entrypoint sh <同上镜像> -c "grep -rlo 'localhost:8004' /usr/share/nginx/html/assets/ | head -2"
```
Expected: **无输出**。⚠️ 此处"无输出"是有效证据,**仅因为**上一条正向 grep 已证明该 grep 命令本身能在此产物里命中东西。

- [ ] **Step 7: Commit**

```bash
git add portal/Dockerfile docker-compose.prod.yml docker-compose.dev.yml portal/src/services/adminApi.ts
git commit -m "fix(portal): wire VITE_FINANCE_API_URL build arg + add fetch timeout to adminApi"
```

---

### Task 5: 三个 admin 页补系统标准 chrome(Issue 3)

**Files:**
- Create: `portal/src/components/layout/PortalPageLayout.tsx`
- Modify: `portal/src/pages/admin/AccessControl.tsx`、`portal/src/pages/admin/ApprovalRouting.tsx`、`portal/src/pages/admin/DataMaintenance.tsx`

**Interfaces:**
- Consumes: `PortalSidebar`(`portal/src/components/layout/PortalSidebar.tsx`),用法见 `portal/src/pages/PortalHome.tsx:785-791`
- Produces: `<PortalPageLayout activeKey={string} title={string}>{children}</PortalPageLayout>`

**根因**:三页都是裸 div,没渲染 `PortalSidebar` → 点进去侧边栏消失,与系统其他页面断裂。违反 [[feedback_uniops_portal_page_chrome]]。
**抽公共 layout 而非三处各补一遍**:照抄正是本次一错错两页的成因(我让代理"照 DataMaintenance 抄",而它本身不合规)。

- [ ] **Step 1: 读参照实现**

读 `portal/src/pages/PortalHome.tsx` 的 `PortalSidebar` 用法(:785-791)及其外层容器/`mobileOpen`/`collapsed` state、`perms` 与 `session` 的取得方式。**照它的既有模式**,不要自创。

- [ ] **Step 2: 写 PortalPageLayout**

```tsx
// Portal 页面的标准外壳:sidebar + header + content。
// 每个 Portal 页面都必须用它(见 feedback_uniops_portal_page_chrome),
// 别再各页自己渲染裸 div —— AccessControl/ApprovalRouting 就是那样漏掉 sidebar 的。
export function PortalPageLayout({ activeKey, title, children }: {
  activeKey: string
  title: string
  children: React.ReactNode
}) { /* PortalSidebar + header + <main>{children}</main> */ }
```
`session` / `perms` / `userRole` / 各模块 href / `mobileOpen` / `collapsed` 全在 layout 内部取得与管理,**页面不该关心这些** —— 页面只给 `activeKey` 和 `title`。

- [ ] **Step 3: 三页接入**

各自包起来,`activeKey` 用 navConfig 里的 href 值(`navConfig.tsx:69-71`):
- `DataMaintenance.tsx` → `portal:/admin/data-maintenance`
- `AccessControl.tsx` → `portal:/admin/access-control`
- `ApprovalRouting.tsx` → `portal:/admin/approval-routing`

**保留各页现有的 system_admin 守卫逻辑**(守卫在 layout 之内还是之外,照 PortalHome 的既有模式)。各页原有的 "Back to UniOps" 链接删掉(sidebar 已提供导航)。

- [ ] **Step 4: typecheck**

Run: `cd portal && npx tsc -p tsconfig.app.json --noEmit --ignoreDeprecations 6.0`
Expected: 0 errors。

- [ ] **Step 5: 正面验证三页都真的用了 layout**

Run: `grep -c "PortalPageLayout" portal/src/pages/admin/AccessControl.tsx portal/src/pages/admin/ApprovalRouting.tsx portal/src/pages/admin/DataMaintenance.tsx`
Expected: 三个文件**各 ≥ 2**(import + 使用)。

- [ ] **Step 6: Commit**

```bash
git add portal/src/components/layout/PortalPageLayout.tsx portal/src/pages/admin/AccessControl.tsx portal/src/pages/admin/ApprovalRouting.tsx portal/src/pages/admin/DataMaintenance.tsx
git commit -m "fix(portal): wrap admin pages in standard sidebar chrome via PortalPageLayout"
```

---

## 发布注意(执行完后写进发布说明)

- **必须重建 portal-web 镜像**:`VITE_*` 是构建期烤入的。按 [[reference_uniops_prod_release_workflow]] 仍是**全部 15 个镜像**都 build+push `:<新sha>`。
- **无新迁移、无新 seed**:Task 2 只是改读已存在的表(③ 期的 `seed_routing` 已把 12 行写好)。
- **★ 生产预检(Task 2 的前提)**:确认生产无"未列出部门",否则 GM 可见性会扩大。容器内跑:
  ```bash
  sudo docker compose -f docker-compose.prod.yml run --rm epms-api python -c "
  import asyncio, json
  from sqlalchemy import text
  from app.db.session import AsyncSessionLocal
  async def main():
      async with AsyncSessionLocal() as db:
          cfg = (await db.execute(text('SELECT dept_gm_opm_mapping FROM company_config LIMIT 1'))).scalar()
          m = json.loads(cfg) if isinstance(cfg, str) else (cfg or {})
          depts = (await db.execute(text('SELECT id::text, code FROM departments WHERE is_active'))).all()
          unlisted = [c for i, c in depts if i not in set(m.keys())]
          print('未列出(改后 GM 将新看见):', len(unlisted), unlisted)
  asyncio.run(main())"
  ```
  期望 `0 []`(dev 实测为 0,生产发布时 parity 也是 12 depts)。非 0 → 先在 Portal → Approval Routing 里把这些部门配好再发。
- 回滚:全部 additive / 纯前端,回退 TAG 即恢复。
