# MRP 生产计划版本模型 实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让「哪一版计划在生效」有唯一答案，让历史计划可枚举、可回放、刷新不丢。

**Architecture:** `mrp_mps_runs` 增加 `is_default`（**全局恰好一条**，用部分唯一索引在数据库层保证）与 `released_at`。发布与切换生效版共用一个 `mrp_demands` 重写函数。计划组（`horizon_start_month`）只能往前走：发布新组会把所有更早组置 `superseded`，跨组切换与旧组发布一律 422。前端把 `runId` 放进 URL 并加版本选择器。

**Tech Stack:** FastAPI + SQLAlchemy 2.x async + Alembic（mrp-api）；React + TS + Vite（mrp 前端，端口 5179）；pytest。

**Spec:** `docs/superpowers/specs/2026-08-14-mrp-production-plan-versioning-design.md`

## Global Constraints

- **分支**：`feature/mrp-plan-versioning`，worktree `c:/Project/uniops-mrp-phase0`，基线是 `feature/mrp-min-lot-and-time-fence`（**mrp-api 401 passed**）。
- **跑测命令**（必须 `cd mrp-api`；同一时刻只跑一个套件，conftest 会 drop_all）：
  ```bash
  cd /c/Project/uniops-mrp-phase0/mrp-api && JWT_SECRET_KEY=test-secret \
    TEST_PG_PASSWORD="$(docker inspect uniops_postgres --format '{{range .Config.Env}}{{println .}}{{end}}' | grep POSTGRES_PASSWORD | cut -d= -f2)" \
    ALLOWED_ORIGINS='["http://localhost:5179"]' \
    python -m pytest tests -q
  ```
  全量约 10 分半。单文件跑法相同，末尾换成 `tests/test_mps_api.py`。
- **前端**：`cd mrp && npx tsc --version && npx tsc -p tsconfig.app.json --noEmit`，**先在改动前量一次基线**（当前为 0 条诊断、74 个 src 文件），改完对比。
- **新迁移 `down_revision` 挂 `mrp11_min_lot_week_start_frozen`**，动手前先 `python -m alembic heads` 确认。
- **UI 文案全英文**；Decimal 过线是字符串，前端读数值一律 `Number()`。
- **每个任务独立 commit**，英文祈使式消息 + `Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>`。**不 push**。

---

### Task 1: 迁移 `mrp12` + 模型列 + 存量收敛

**Files:**
- Create: `mrp-api/alembic/versions/mrp12_plan_versioning.py`
- Modify: `mrp-api/app/models/mps.py`
- Test: `mrp-api/tests/test_plan_versioning.py`（新建）

**Interfaces:**
- Produces: `MrpMpsRun.is_default: bool`（默认 False）、`MrpMpsRun.released_at: datetime | None`；
  部分唯一索引 `uq_mrp_mps_runs_single_default ON mrp_mps_runs (is_default) WHERE is_default`。

- [ ] **Step 1: 确认链尾**

Run: `cd mrp-api && JWT_SECRET_KEY=test-secret python -m alembic heads`
Expected: 单 head `mrp11_min_lot_week_start_frozen`。不是就停下来查清楚。

- [ ] **Step 2: 写失败测试**

```python
# tests/test_plan_versioning.py
"""生产计划版本模型：全局恰好一个生效版，计划组只能往前走。"""
import pytest
import sqlalchemy as sa

from app.models.mps import MrpMpsRun


def test_versioning_columns_exist_with_defaults():
    cols = MrpMpsRun.__table__.c
    assert cols.is_default.default.arg is False
    assert str(cols.is_default.server_default.arg) == "false"
    assert cols.released_at.nullable is True


@pytest.mark.anyio
async def test_database_refuses_a_second_default(db_session):
    """★应用层的先清后置在并发下不可靠 —— 这条不变量由部分唯一索引兜底。"""
    from datetime import datetime, timezone

    def _run(no):
        return MrpMpsRun(
            run_no=no, forecast_version_id=uuid.uuid4(), horizon_start_month="2026-09",
            horizon_months=18, status="released", safety_margin_fraction=0,
            is_default=True, released_at=datetime.now(timezone.utc),
        )

    db_session.add(_run("MPS-DUP-1"))
    await db_session.commit()
    db_session.add(_run("MPS-DUP-2"))
    with pytest.raises(sa.exc.IntegrityError):
        await db_session.commit()
    await db_session.rollback()
```

（文件顶部需要 `import uuid`。）

- [ ] **Step 3: 跑测确认失败**

Run: `python -m pytest tests/test_plan_versioning.py -q`
Expected: FAIL —— `AttributeError: is_default`。

- [ ] **Step 4: 写迁移**

```python
# alembic/versions/mrp12_plan_versioning.py
"""Production plan versioning: an explicit default plus a released_at stamp.

Additive, then a one-off convergence of existing data: before this
migration `confirm-release` never cleared the previous release, so a live
database can hold several runs all claiming status='released' with no way
to say which one feeds mrp_demands. The upgrade picks the most recently
created released run as the default and supersedes every released run in an
OLDER horizon group, which is the state the new rules assume.

Revision chain: mrp11_min_lot_week_start_frozen -> mrp12_plan_versioning.
"""
import sqlalchemy as sa
from alembic import op

revision = "mrp12_plan_versioning"
down_revision = "mrp11_min_lot_week_start_frozen"
branch_labels = None
depends_on = None

_INDEX = "uq_mrp_mps_runs_single_default"


def upgrade() -> None:
    op.add_column("mrp_mps_runs",
                  sa.Column("is_default", sa.Boolean(), nullable=False,
                            server_default="false"))
    op.add_column("mrp_mps_runs",
                  sa.Column("released_at", sa.DateTime(timezone=True), nullable=True))

    # Stamp a release time for rows released before the column existed. It is
    # created_at, not a guess: the real publish time was never recorded, and
    # created_at is both the closest available value and enough to order by.
    op.execute("""
        UPDATE mrp_mps_runs SET released_at = created_at WHERE status = 'released'
    """)
    # The most recently created released run becomes the one in force.
    op.execute("""
        UPDATE mrp_mps_runs SET is_default = true
        WHERE id = (
            SELECT id FROM mrp_mps_runs WHERE status = 'released'
            ORDER BY created_at DESC LIMIT 1
        )
    """)
    # Every released run in an older horizon group is history now: the plan
    # group only moves forward, so those can never be made active again.
    op.execute("""
        UPDATE mrp_mps_runs SET status = 'superseded'
        WHERE status = 'released' AND horizon_start_month < (
            SELECT horizon_start_month FROM mrp_mps_runs WHERE is_default LIMIT 1
        )
    """)

    op.create_index(_INDEX, "mrp_mps_runs", ["is_default"], unique=True,
                    postgresql_where=sa.text("is_default"))


def downgrade() -> None:
    op.drop_index(_INDEX, table_name="mrp_mps_runs")
    op.drop_column("mrp_mps_runs", "released_at")
    op.drop_column("mrp_mps_runs", "is_default")
```

模型侧加两列并注释：`is_default` 是**全局**单例（索引保证），`released_at` 只在发布时写。

- [ ] **Step 5: 跑测确认通过**

Run: `python -m pytest tests/test_plan_versioning.py tests/test_alembic_revisions.py -q`
Expected: PASS；`alembic heads` 变成 `mrp12_plan_versioning`。

- [ ] **Step 6: 提交**

```bash
git add mrp-api/alembic/versions/mrp12_plan_versioning.py mrp-api/app/models/mps.py mrp-api/tests/test_plan_versioning.py
git commit -m "feat(mrp): add plan versioning columns and converge existing releases"
```

---

### Task 2: 抽出 `publish_demands_from_run`

**Files:**
- Modify: `mrp-api/app/api/v1/mps.py`（`confirm-release` 内联的清空+重写逻辑）
- Test: 现有 `tests/test_mps_api.py` 的发布用例即回归网

**Interfaces:**
- Produces: `async def publish_demands_from_run(db: SessionDep, run: MrpMpsRun) -> None`
  —— 清空 `demand_type='mps'` 全部行，再从该 run 的 `capacity_gap=False AND qty > 0` 行重写。**不 commit**（调用方收口事务）。

- [ ] **Step 1: 先跑一遍现有发布用例，记下绿的基线**

Run: `python -m pytest tests/test_mps_api.py -q -k "release"`
Expected: 全绿（这一步是纯重构，之后必须还是同样的结果）。

- [ ] **Step 2: 抽函数**

把 `confirm_release` 里从 `await db.execute(delete(MrpDemand)...)` 到 `db.add(MrpDemand(...))` 循环整段搬进新函数，原处改为调用。**注释里写明为什么只能有一份实现**：两处若各写一份，一旦漂移就是采购量翻倍或漏买。

- [ ] **Step 3: 跑测确认行为不变**

Run: `python -m pytest tests/test_mps_api.py -q -k "release"`
Expected: 与 Step 1 完全一致。

- [ ] **Step 4: 提交**

```bash
git commit -am "refactor(mrp): extract publish_demands_from_run so release and switch share it"
```

---

### Task 3: 发布规则（生效版转移 / 旧组 superseded / 旧组发布 422 / 幂等）

**Files:**
- Modify: `mrp-api/app/api/v1/mps.py`
- Test: `mrp-api/tests/test_plan_versioning.py`

**Interfaces:**
- Consumes: Task 1 的列、Task 2 的 `publish_demands_from_run`。
- Produces: `async def _default_run(db) -> MrpMpsRun | None`（`is_default` 那条）；
  `confirm-release` 的新语义（spec §2.2 / §2.4）。

- [ ] **Step 1: 写失败测试**

```python
@pytest.mark.anyio
async def test_releasing_a_second_version_moves_the_default_and_keeps_the_first_released(
    client, db_session, admin_token, monkeypatch,
):
    """同组第二版发布后：新版生效，旧版仍 released（可切回去），不是 superseded。"""
    ...  # 用 _confirmed_version 造同一 horizon_start_month 的两个 version，各生成并发布
    assert first_after["status"] == "released"
    assert first_after["is_default"] is False
    assert second["is_default"] is True


@pytest.mark.anyio
async def test_releasing_a_newer_group_supersedes_every_older_run(
    client, db_session, admin_token, monkeypatch,
):
    """旧组的 released 与 draft 一并 superseded —— 旧组从此不可再生效。"""
    ...
    assert old_released["status"] == "superseded"
    assert old_draft["status"] == "superseded"


@pytest.mark.anyio
async def test_releasing_an_older_group_draft_is_refused(
    client, db_session, admin_token, monkeypatch,
):
    """★发布即生效，所以放行旧组发布就等于让生效组倒退，绕过「只能往前走」。"""
    ...
    assert r.status_code == 422
    assert "forward" in r.text.lower() or "older" in r.text.lower()
    assert (await _get_run(client, headers, old_id))["status"] == "draft"


@pytest.mark.anyio
async def test_releasing_the_current_default_again_is_a_no_op(
    client, db_session, admin_token, monkeypatch,
):
    ...
    assert again.status_code == 200
    assert again.json()["is_default"] is True
    assert demand_rows_before == demand_rows_after


@pytest.mark.anyio
async def test_the_first_release_needs_no_group_comparison(
    client, db_session, admin_token, monkeypatch,
):
    """全新库没有生效版，任何 draft 都能发布并成为第一个生效版。"""
    ...
    assert released["is_default"] is True
```

> 每条的 `...` 在实现时写成真实构造：沿用 `tests/test_mps_api.py` 里的 `_confirmed_version` / `_factory_rule` / `_no_shelf_life` 三个夹具（同目录，直接 import 或复制其调用方式），`_get_run` 就是 `GET /api/v1/mps/runs/{id}`。**每条断言前先打印一次响应确认构造真的产生了预期的前置状态** —— 构造没生效会让断言假通过。

- [ ] **Step 2: 跑测确认失败** — Expected: `KeyError: 'is_default'`。

- [ ] **Step 3: 实现**

`confirm_release` 内，在 `_require_not_released(run)` 之后（幂等分支要在它之前放行生效版）：

```python
current = await _default_run(db)
if current is not None and run.horizon_start_month < current.horizon_start_month:
    raise HTTPException(422, detail=(
        f"this plan covers {run.horizon_start_month}, which is older than the plan "
        f"currently in force ({current.horizon_start_month}); the plan group only "
        f"moves forward. Generate a new plan from the current horizon instead."))
```

然后同一事务里：清掉更早组的 `is_default`/状态、置本 run 为 `released`+`is_default`+`released_at`、`publish_demands_from_run`。**先把旧的 `is_default` 置 false 再 flush，再置新的** —— 否则部分唯一索引会在中途报错。

- [ ] **Step 4: 跑测确认通过**

Run: `python -m pytest tests/test_plan_versioning.py tests/test_mps_api.py -q`
Expected: 全绿（`test_mps_api.py` 的既有发布用例不许挂）。

- [ ] **Step 5: 提交**

```bash
git commit -am "feat(mrp): a release becomes the one plan in force and supersedes older groups"
```

---

### Task 4: `GET /mps/runs` 列表

**Files:**
- Modify: `mrp-api/app/api/v1/mps.py`
- Test: `mrp-api/tests/test_plan_versioning.py`

**Interfaces:**
- Produces: `GET /api/v1/mps/runs` → `list[MpsRunSummaryResponse]`，字段
  `id`/`run_no`/`horizon_start_month`/`horizon_months`/`status`/`is_default`/`released_at`/`created_at`/`stats`。**不含 lines。**

- [ ] **Step 1: 写失败测试**

```python
@pytest.mark.anyio
async def test_run_list_is_ordered_newest_group_first(client, db_session, admin_token, monkeypatch):
    ...
    months = [row["horizon_start_month"] for row in rows]
    assert months == sorted(months, reverse=True)


@pytest.mark.anyio
async def test_run_list_carries_no_lines(client, db_session, admin_token, monkeypatch):
    """列表是导航用的；带上几千行会把版本选择器拖垮。"""
    ...
    assert all("lines" not in row for row in rows)


@pytest.mark.anyio
async def test_run_list_requires_report_permission(client, non_admin_token, monkeypatch):
    ...
    assert r.status_code == 403
```

- [ ] **Step 2: 跑测确认失败** — Expected: 404（路由不存在）。

- [ ] **Step 3: 实现**

新响应模型 `MpsRunSummaryResponse`；端点排序 `ORDER BY horizon_start_month DESC, COALESCE(released_at, created_at) DESC`。权限依赖用现有的 `ReportDep`。

★**路由顺序**：`@router.get("/runs")` 必须写在 `@router.get("/runs/{run_id}")` **之前**，否则 FastAPI 会把 `runs` 当成 `run_id` 去解析并 422。

- [ ] **Step 4: 跑测确认通过**

- [ ] **Step 5: 提交**

```bash
git commit -am "feat(mrp): list production plan runs for the version picker"
```

---

### Task 5: `POST /mps/runs/{id}/set-default`

**Files:**
- Modify: `mrp-api/app/api/v1/mps.py`
- Test: `mrp-api/tests/test_plan_versioning.py`

**Interfaces:**
- Consumes: Task 2 的 `publish_demands_from_run`、Task 3 的 `_default_run`。
- Produces: `POST /api/v1/mps/runs/{run_id}/set-default` → 与 `GET /runs/{id}` 同结构。

- [ ] **Step 1: 写失败测试**

```python
@pytest.mark.anyio
async def test_switching_within_a_group_rewrites_the_live_demand(
    client, db_session, admin_token, monkeypatch,
):
    """切回同组旧版 = 采购拿到的是那一版的需求，逐行比对，不只看行数。"""
    ...
    assert switched["is_default"] is True
    assert rows_after == expected_rows_of_first_version


@pytest.mark.anyio
async def test_switching_across_groups_is_refused_and_changes_nothing(
    client, db_session, admin_token, monkeypatch,
):
    ...
    assert r.status_code == 422
    assert rows_after == rows_before        # 需求集一行未动


@pytest.mark.anyio
async def test_a_draft_cannot_be_made_active(client, db_session, admin_token, monkeypatch):
    ...
    assert r.status_code == 422
```

- [ ] **Step 2: 跑测确认失败**

- [ ] **Step 3: 实现**（校验 → 转移 `is_default` → `publish_demands_from_run` → 一次 commit）

- [ ] **Step 4: 跑测确认通过**

- [ ] **Step 5: 提交**

```bash
git commit -am "feat(mrp): switch the plan in force within a horizon group"
```

---

### Task 6: 锁定区改读生效版（收口 `TODO(plan-versioning)`）

**Files:**
- Modify: `mrp-api/app/api/v1/mps.py`（`_live_released_run` → `_default_run`）
- Test: `mrp-api/tests/test_plan_versioning.py`

- [ ] **Step 1: 写失败测试**

```python
@pytest.mark.anyio
async def test_the_frozen_zone_follows_the_plan_in_force(
    client, db_session, admin_token, monkeypatch,
):
    """同组发布 v1、v2 后切回 v1，再生成新计划 —— 锁定区必须继承 v1 的行，
    而不是「最近一次 released」的 v2。这就是上一轮那条 TODO 的差别所在。"""
    ...
    assert frozen_lines_of_new_run == frozen_lines_of_v1
```

- [ ] **Step 2: 跑测确认失败**（现在会继承 v2）

- [ ] **Step 3: 实现**：删除 `_live_released_run`，改调 `_default_run`，把 `TODO(plan-versioning)` 注释一并删掉（**留着比没有更糟**：下一个人会以为还没做）。

- [ ] **Step 4: 跑测确认通过**

Run: `python -m pytest tests/test_plan_versioning.py tests/test_mps_api.py -q`

- [ ] **Step 5: 提交**

```bash
git commit -am "feat(mrp): the frozen zone inherits from the plan in force"
```

---

### Task 7: 前端 —— URL 里的 runId + 版本选择器 + 历史版只读

**Files:**
- Modify: `mrp/src/pages/mps/ProductionPlanPage.tsx`、`mrp/src/pages/mps/mpsApi.ts`
- Create: `mrp/src/pages/mps/RunPicker.tsx`

- [ ] **Step 1: 量前端基线**

Run: `cd mrp && npx tsc --version && npx tsc -p tsconfig.app.json --noEmit | wc -l`

- [ ] **Step 2: 实现**

- `mpsApi`：加 `list()` → `MpsRunSummary[]`；`setDefault(id)`；`MpsRun` 类型加 `is_default: boolean`、`released_at: string | null`、`status` 加 `'superseded'`。
- `ProductionPlanPage`：`runId` 改由 `useSearchParams()` 的 `run` 参数驱动（react-router v7，仓库已在用）。首次进入无参数 → 取列表里 `is_default` 那条；没有则取最新一条；都没有才空状态。
- `RunPicker.tsx`：按 `horizon_start_month` 分节的下拉，行内 `run_no` + 状态徽章 + `Active` + 发布时间。选中即改 URL 参数。
- **历史版只读**：`status === 'superseded'` 时不渲染 Recalculate / Confirm & Release，矩阵 `readOnly`，顶部提示 `A newer plan group has taken over — this version is read-only.`
- **Set as active**：`status === 'released' && !is_default && horizon_start_month === activeGroup` 时可点；跨组时**渲染为禁用**并给 title 说明 `The plan group only moves forward.`（按钮消失会让计划员以为功能坏了）。
- 点击前 `ConfirmDialog`：`This rewrites the demand purchasing works from.`

- [ ] **Step 3: 验证**：`npx tsc -p tsconfig.app.json --noEmit | wc -l` 与基线相同。

- [ ] **Step 4: 提交**

```bash
git commit -am "feat(mrp-web): pick a plan version from the URL and switch the active one"
```

---

### Task 8: 全分支回归

- [ ] **Step 1: 后端全量**

```bash
cd mrp-api && ... python -m pytest tests -q
```
Expected: `401 + 本分支新增`，0 failed。

- [ ] **Step 2: 前端 tsc**：与基线相同条数（并打印 `npx tsc --version` 作为版本证据）。

- [ ] **Step 3: 迁移链**：`python -m alembic heads` → 单 head `mrp12_plan_versioning`。

- [ ] **Step 4: 把三条命令的实际输出贴进交付报告**，不写「应该通过」。

---

## 自查（写完计划后的核对结果）

- **spec 覆盖**：§2.1→T1；§2.2→T3；§2.3→T5；§2.4→T3；§2.5→T1（部分唯一索引）；§3.1→T4；§3.2→T5；§3.3→T3；§4.1→T2；§4.2→T6；§4.3→T1；§4.4→T7；§5→各任务测试步骤 + T8。
- **命名一致性**：全程 `is_default` / `released_at` / `publish_demands_from_run` / `_default_run` / `MpsRunSummaryResponse`。
- **已知取舍**：T3/T4/T5 的测试骨架里用 `...` 标注构造部分，实现时必须写成真实夹具调用并**先验证前置状态**；这是本计划里唯一需要执行者补全的地方，其余步骤都给了可直接落地的代码。
