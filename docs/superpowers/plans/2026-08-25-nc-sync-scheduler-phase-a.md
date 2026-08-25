# NC Sync 调度（Phase A）Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让 JV 和 MDM 两类同步也能像采购同步那样按间隔自动跑，并把三者的入口与间隔设置合并到 Portal → Admin Panel 的一个 `NC Sync` 菜单下。

**Architecture:** **不发明新机制** —— 照抄 `main` 上已有的 `epms-api/app/tasks/nc_purchase_sync_scheduler.py`：间隔存在共用的 `company_config` 表的一列里、每 tick 重读、`0` 表示关闭、到点从上次 run 的**开始时间**推导。epms-api 只加两列（代码不动），finance-api 与 mdm-api 各加一个同形状的循环，Portal 补上一直缺失的 UI。

**Tech Stack:** Python 3.12 / FastAPI / SQLAlchemy 2.x async / Alembic / Postgres；React 18 + TypeScript + Vite + TanStack Query + Tailwind。

**Spec:** `docs/superpowers/specs/2026-08-25-nc-sync-scheduler-design.md`（Phase A = §4、§6.1、§8、§9.1、§10.1、§11）

---

## Global Constraints

来自 `MEMORY.md` 及其 topic 文件（权威记忆库在
`C:\Users\Justin Wang\.claude\projects\c--Project\memory\`，冲突时以记忆为准）。适用于**每一个** task：

- **前端所有 user-facing 文案一律英文**；代码注释可中文；对话/报告用中文。
- **Alembic revision id 必须 ≤ 32 字符**（`alembic_version.version_num` 是 `VARCHAR(32)`）。
- **新建迁移前必须先跑 `alembic heads`**，`down_revision` 挂真实链尾，不要凭文件名或 mtime 猜。
- **`company_config` 由 epms-api 的 alembic 独家拥有。** finance-api / mdm-api 只加 mirror 模型读它，
  **各自的 alembic 一条迁移都不许加**。
- **新建 mirror 模型必须逐列核对物理表**（本仓库已三踩）。mirror 可以只声明本服务用得着的列。
- **epms-api / mdm-api 的测试库表来自 `Base.metadata.create_all`** → 新模型**必须注册进
  `tests/conftest.py` 的 import 列表**，否则表不存在、测试静默失败。finance-api 跑真实 alembic，不受此限。
- **宿主 `.env` 指向生产库**。跑 alembic / 脚本必须在容器内跑，或覆盖 `POSTGRES_*`。**绝不能打到 10.10.50.20。**
- **验证要正面证据**：「没有输出 = 通过」是假阴性。每个 Run 步骤都要看到期望的字样。
- 调度循环是 lifespan 持有、shutdown 时 cancel 的长生命周期任务，与 `daily_followup_loop` 同构 ——
  这**不是**被禁止的 fire-and-forget 裸 Task。`app.core.background.spawn` 在本仓库**不存在**，勿引用。
- **本地 commit 已获授权，push 未获授权。** 任何 task 都不许 `git push`。
- 工作目录是 worktree `C:\Project\uniops\.worktrees\nc-sync-scheduler`，分支 `feature/nc-sync-scheduler`。
  **禁止 `git stash`** —— stash 是仓库级共享的，会弹出别的会话的 WIP。

### 必须照抄的既有实现

**动手前先读 `epms-api/app/tasks/nc_purchase_sync_scheduler.py`（main 上已有，约 150 行）。**
它是本轮两个新循环的模板。四条刻意的设计必须原样保留：

1. 间隔从库里**每 tick 重读** → 管理员改完一分钟内生效
2. `0` = 关闭；`NULL` = 没人设过 → 回落默认值
3. **只跑 `incremental`，永不自动 `full`**
4. 到点从上次 run 的**开始时间**算（不是完成时间，也不存 `next_run_at`）；
   `started_at` 在未来（时钟回拨）算作**未到点**

单飞由 `start_run` 的 `SyncAlreadyRunning` 解决，不需要额外加锁。

### 三个服务的依赖名（别搞混）

| | epms-api | finance-api | mdm-api |
|---|---|---|---|
| DB session dep | `SessionDep` | `Depends(get_db)` | `Depends(get_db)` |
| 当前用户 dep | `CurrentUserPayload` | `CurrentUser` | `CurrentUser` |
| 角色门禁 | `require_roles(*roles)` | 手写 `if user.get("role") != "system_admin"` | `require_roles(*roles)` |
| session factory | `app.db.session.AsyncSessionLocal` | `app.db.base.AsyncSessionLocal` | `app.db.base.AsyncSessionLocal` |
| router 前缀 | `/api/v1` | `/finance/v1` | `/mdm/v1` |
| `get_db()` 自动 commit | 是 | **否** | 是 |

`start_run` 在 epms 与 finance 两边**签名相同**：
`start_run(mode, started_by, *, fetch=..., pg_dsn=None, run_worker=True)`。

---

## File Structure

| 文件 | 职责 | Task |
|---|---|---|
| `epms-api/alembic/versions/<id>_sync_intervals.py` | `company_config` 加两列 | 1 |
| `epms-api/app/models/config.py` | 模型加两个字段 | 1 |
| `finance-api/app/models/mirrors.py` | `CompanyConfig` mirror 加一列 | 2 |
| `finance-api/app/core/config.py` | `nc_sync_scheduler_enabled` | 2 |
| `finance-api/app/tasks/nc_sync_scheduler.py` | JV 调度循环 | 2 |
| `finance-api/app/api/v1/nc_sync.py` | `PATCH /interval` + `/status` 补两个字段 | 2 |
| `finance-api/app/main.py` | 新建 lifespan | 2 |
| `mdm-api/app/models/company_config.py` | **新建** mirror（只声明用得着的列） | 3 |
| `mdm-api/app/core/config.py` | `nc_sync_scheduler_enabled` | 3 |
| `mdm-api/app/tasks/erp_sync_scheduler.py` | MDM 调度循环（顺序跑三小类） | 3 |
| `mdm-api/app/api/v1/erp_mdm.py` | `GET /sync/schedule` + `PATCH /sync/interval` | 3 |
| `mdm-api/app/main.py` | lifespan（目前是空 `yield`） | 3 |
| `mdm-api/app/crud/erp.py` | `part_status` 别名 | 4 |
| `portal/src/pages/admin/nc-sync/ScheduleCard.tsx` | 共用间隔卡片 | 5 |
| `portal/src/pages/admin/nc-sync/NcSyncSection.tsx` | 三 tab 外壳 + JV 手动区块 | 6 |
| `portal/src/pages/admin/AdminPanel.tsx` | 菜单改名 + 渲染分支 | 6 |

---

## Task 1: epms-api — `company_config` 两个新间隔列

**Files:**
- Create: `epms-api/alembic/versions/<newid>_sync_intervals.py`
- Modify: `epms-api/app/models/config.py`（`nc_purchase_sync_interval_minutes` 那行附近）

**Interfaces:**
- Produces: `company_config.nc_jv_sync_interval_minutes`、`company_config.erp_mdm_sync_interval_minutes`
  （均 `Integer NULL`）。Task 2 与 Task 3 的 mirror 模型读这两列。
- 本 task **不改任何代码逻辑**，只加列。epms-api 的采购调度器一行不动。

---

- [ ] **Step 1: 先读既有实现（后续 task 的模板，本 task 只为确认列的语义）**

```bash
sed -n '1,60p' epms-api/app/tasks/nc_purchase_sync_scheduler.py
```

Expected: 看到 `DEFAULT_INTERVAL_MINUTES = 60` / `MAX_INTERVAL_MINUTES = 1440` / `TICK_SECONDS = 60`
以及 docstring 里「NULL 回落默认、0 关闭」的说明。新加的两列语义与它完全一致。

- [ ] **Step 2: 确认 alembic 真实链尾**

```bash
cd epms-api && python -m alembic heads
```

Expected: **恰好一行**。记下这个 id 作为 `down_revision`。
出现两行 = 双 head，停下来报告，不要自己合并。

- [ ] **Step 3: 写迁移**

Create `epms-api/alembic/versions/ai01_sync_intervals.py`
（若 `ai01` 前缀已被占用就顺延；revision id 保持 ≤32 字符）：

```python
"""company_config: JV / MDM 同步间隔列

采购同步的间隔列(nc_purchase_sync_interval_minutes)早就在这张表上了。
JV 与 MDM 的调度器沿用同一套语义,所以各加一列,而不是另起一张表:
  NULL = 没人设过 -> 各自的默认值
  0    = 关闭自动同步
  >0   = 分钟数(上限由各自的 MAX_INTERVAL_MINUTES 钳制)

Revision ID: ai01_sync_intervals
Revises: <Step 2 里 alembic heads 的输出>
"""
import sqlalchemy as sa
from alembic import op

revision = "ai01_sync_intervals"
down_revision = "<Step 2 里 alembic heads 的输出>"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("company_config",
                  sa.Column("nc_jv_sync_interval_minutes", sa.Integer(), nullable=True))
    op.add_column("company_config",
                  sa.Column("erp_mdm_sync_interval_minutes", sa.Integer(), nullable=True))


def downgrade() -> None:
    op.drop_column("company_config", "erp_mdm_sync_interval_minutes")
    op.drop_column("company_config", "nc_jv_sync_interval_minutes")
```

- [ ] **Step 4: 模型加字段**

Modify `epms-api/app/models/config.py` —— 在 `nc_purchase_sync_interval_minutes` 那一行**之后**插入：

```python
    # JV(凭证)与 ERP 主数据同步的间隔,语义与上面那列完全一致:
    # NULL=回落默认 / 0=关闭 / >0=分钟数。分别由 finance-api 和 mdm-api 的
    # 调度循环读取(它们各有一个 company_config 的 mirror 模型)。
    nc_jv_sync_interval_minutes: Mapped[int | None] = mapped_column(Integer, nullable=True)
    erp_mdm_sync_interval_minutes: Mapped[int | None] = mapped_column(Integer, nullable=True)
```

- [ ] **Step 5: 迁移能真正上下行**

```bash
cd epms-api && python -m alembic upgrade head && python -m alembic downgrade -1 && python -m alembic upgrade head
```

Expected: 三条命令都退出码 0，输出里能看到 `Running upgrade` / `Running downgrade`。

⚠️ 跑之前**必须**确认 `POSTGRES_*` 指向本地 docker 的 `uniops_postgres`，不是宿主 `.env` 里的生产库。
不确定就在容器里跑：`docker compose -f ../docker-compose.dev.yml exec epms-api python -m alembic upgrade head`

- [ ] **Step 6: 正面确认两列真的建出来了**

```bash
docker exec uniops_postgres psql -U epms -d epms -tAc \
  "select column_name from information_schema.columns where table_name='company_config' and column_name like '%_sync_interval_minutes'"
```

Expected: 三行 —— `nc_purchase_sync_interval_minutes`、`nc_jv_sync_interval_minutes`、`erp_mdm_sync_interval_minutes`。

- [ ] **Step 7: revision id 长度守卫**

```bash
cd epms-api && python -m pytest tests/test_alembic_revisions.py -q 2>/dev/null || echo "epms 无此守卫测试,跳过"
```

Expected: passed，或提示无此文件（该守卫在 mdm-api 里，epms 可能没有）。
无论哪种，自己数一遍 revision id 长度 ≤ 32。

- [ ] **Step 8: Commit**

```bash
git add epms-api/alembic/versions/ai01_sync_intervals.py epms-api/app/models/config.py
git commit -m "feat(epms): company_config columns for JV and MDM sync intervals"
```

---

## Task 2: finance-api — JV 同步调度器

**Files:**
- Modify: `finance-api/app/models/mirrors.py`（`CompanyConfig`）
- Modify: `finance-api/app/core/config.py`
- Create: `finance-api/app/tasks/__init__.py`
- Create: `finance-api/app/tasks/nc_sync_scheduler.py`
- Modify: `finance-api/app/api/v1/nc_sync.py`
- Modify: `finance-api/app/main.py`
- Modify: `finance-api/app/models/__init__.py`（若该文件枚举模型）
- Create: `finance-api/tests/test_nc_sync_scheduler.py`

**Interfaces:**
- Consumes: Task 1 的 `company_config.nc_jv_sync_interval_minutes`
- Produces:
  - `app.tasks.nc_sync_scheduler.DEFAULT_INTERVAL_MINUTES = 60`、`MAX_INTERVAL_MINUTES = 1440`、`TICK_SECONDS = 60`
  - `resolve_interval_minutes(raw) -> int`
  - `is_due(*, last_started_at, interval_minutes, now) -> bool`
  - `run_tick() -> str`（`'disabled'|'not_configured'|'not_due'|'already_running'|'synced'|'failed'`）
  - `nc_sync_loop() -> None`
  - HTTP：`PATCH /finance/v1/nc-sync/interval`；`GET /finance/v1/nc-sync/status` 新增
    `interval_minutes` 与 `next_due_at` 两个键
- Task 5 的 Portal 组件读这两个键。

⚠️ finance-api **目前没有 `app/tasks/` 目录，也没有 lifespan**，两样都要新建。

---

- [ ] **Step 1: 读模板**

```bash
cat epms-api/app/tasks/nc_purchase_sync_scheduler.py
```

Expected: 完整读完约 150 行。本 task 是它的 JV 版本，差异只有：读哪一列、
触发哪个 `start_run`、从哪张表取「上次开始时间」。

- [ ] **Step 2: 写失败测试**

Create `finance-api/tests/test_nc_sync_scheduler.py`：

```python
"""JV 同步调度器 —— 纯决策函数的测试,不碰 NC、不碰时钟。

形状对齐 epms-api/tests/test_nc_purchase_sync_scheduler.py。
"""
from datetime import datetime, timedelta, timezone

import pytest

from app.tasks import nc_sync_scheduler as sched

NOW = datetime(2026, 8, 25, 12, 0, 0, tzinfo=timezone.utc)


# ── resolve_interval_minutes ─────────────────────────────────────────────
def test_null_resolves_to_default():
    assert sched.resolve_interval_minutes(None) == sched.DEFAULT_INTERVAL_MINUTES


def test_zero_means_off():
    assert sched.resolve_interval_minutes(0) == 0


def test_negative_clamps_to_zero():
    assert sched.resolve_interval_minutes(-5) == 0


def test_above_max_clamps_to_max():
    assert sched.resolve_interval_minutes(99999) == sched.MAX_INTERVAL_MINUTES


def test_bool_is_not_an_integer():
    # True 在 Python 里 isinstance(True, int) 为真 —— 手改脏值不能被当成 1 分钟
    assert sched.resolve_interval_minutes(True) == sched.DEFAULT_INTERVAL_MINUTES


def test_non_integer_resolves_to_default():
    assert sched.resolve_interval_minutes("60") == sched.DEFAULT_INTERVAL_MINUTES


def test_ordinary_value_passes_through():
    assert sched.resolve_interval_minutes(30) == 30


# ── is_due ───────────────────────────────────────────────────────────────
def test_never_run_is_due():
    assert sched.is_due(last_started_at=None, interval_minutes=60, now=NOW) is True


def test_disabled_is_never_due():
    assert sched.is_due(last_started_at=None, interval_minutes=0, now=NOW) is False


def test_not_due_before_interval_elapses():
    assert sched.is_due(last_started_at=NOW - timedelta(minutes=59),
                        interval_minutes=60, now=NOW) is False


def test_due_exactly_at_interval():
    assert sched.is_due(last_started_at=NOW - timedelta(minutes=60),
                        interval_minutes=60, now=NOW) is True


def test_naive_timestamp_treated_as_utc():
    naive = (NOW - timedelta(minutes=61)).replace(tzinfo=None)
    assert sched.is_due(last_started_at=naive, interval_minutes=60, now=NOW) is True


def test_future_start_is_not_due():
    # 时钟回拨:多等一个 interval 无害,当成逾期会连续同步
    assert sched.is_due(last_started_at=NOW + timedelta(minutes=5),
                        interval_minutes=60, now=NOW) is False


# ── run_tick ─────────────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_run_tick_disabled_when_interval_zero(monkeypatch):
    async def _interval():
        return 0
    monkeypatch.setattr(sched, "load_interval_minutes", _interval)
    assert await sched.run_tick() == "disabled"


@pytest.mark.asyncio
async def test_run_tick_not_configured(monkeypatch):
    async def _interval():
        return 60
    monkeypatch.setattr(sched, "load_interval_minutes", _interval)
    monkeypatch.setattr(sched.svc, "nc_configured", lambda: False)
    assert await sched.run_tick() == "not_configured"


@pytest.mark.asyncio
async def test_run_tick_not_due(monkeypatch):
    async def _interval():
        return 60

    async def _last():
        return NOW  # 刚跑过
    monkeypatch.setattr(sched, "load_interval_minutes", _interval)
    monkeypatch.setattr(sched, "last_run_started_at", _last)
    monkeypatch.setattr(sched.svc, "nc_configured", lambda: True)
    # is_due 用真实 now(),NOW 是"刚才",所以未到点
    assert await sched.run_tick() == "not_due"


@pytest.mark.asyncio
async def test_run_tick_already_running(monkeypatch):
    async def _interval():
        return 60

    async def _last():
        return None
    def _boom(*a, **kw):
        raise sched.svc.SyncAlreadyRunning("busy")
    monkeypatch.setattr(sched, "load_interval_minutes", _interval)
    monkeypatch.setattr(sched, "last_run_started_at", _last)
    monkeypatch.setattr(sched.svc, "nc_configured", lambda: True)
    monkeypatch.setattr(sched.svc, "start_run", _boom)
    assert await sched.run_tick() == "already_running"


@pytest.mark.asyncio
async def test_run_tick_synced(monkeypatch):
    import uuid
    async def _interval():
        return 60

    async def _last():
        return None
    monkeypatch.setattr(sched, "load_interval_minutes", _interval)
    monkeypatch.setattr(sched, "last_run_started_at", _last)
    monkeypatch.setattr(sched.svc, "nc_configured", lambda: True)
    monkeypatch.setattr(sched.svc, "start_run", lambda *a, **kw: uuid.uuid4())
    assert await sched.run_tick() == "synced"


@pytest.mark.asyncio
async def test_run_tick_failed(monkeypatch):
    async def _interval():
        return 60

    async def _last():
        return None
    def _boom(*a, **kw):
        raise RuntimeError("NC exploded")
    monkeypatch.setattr(sched, "load_interval_minutes", _interval)
    monkeypatch.setattr(sched, "last_run_started_at", _last)
    monkeypatch.setattr(sched.svc, "nc_configured", lambda: True)
    monkeypatch.setattr(sched.svc, "start_run", _boom)
    assert await sched.run_tick() == "failed"


@pytest.mark.asyncio
async def test_run_tick_never_runs_full(monkeypatch):
    """自动跑必须永远是 incremental —— full 是删了重建。"""
    import uuid
    captured = {}
    async def _interval():
        return 60

    async def _last():
        return None
    def _capture(mode, started_by, **kw):
        captured["mode"] = mode
        captured["started_by"] = started_by
        return uuid.uuid4()
    monkeypatch.setattr(sched, "load_interval_minutes", _interval)
    monkeypatch.setattr(sched, "last_run_started_at", _last)
    monkeypatch.setattr(sched.svc, "nc_configured", lambda: True)
    monkeypatch.setattr(sched.svc, "start_run", _capture)
    await sched.run_tick()
    assert captured["mode"] == "incremental"
    # 没人按按钮,不该把某个人的名字记到机器跑的这条上
    assert captured["started_by"] is None
```

- [ ] **Step 3: 跑测试确认失败**

```bash
cd finance-api && TEST_FINANCE_DB=finance_test python -m pytest tests/test_nc_sync_scheduler.py -v
```

Expected: 全部 ERROR，`ModuleNotFoundError: No module named 'app.tasks'`。
**不能是 `collected 0 items`**。

- [ ] **Step 4: mirror 加列 + settings 加开关**

Modify `finance-api/app/models/mirrors.py` —— 在 `CompanyConfig` 的
`remittance_config` 之后加：

```python
    # epms-api 拥有这张表。这里只声明 finance-api 用得着的列:JV 同步间隔。
    # NULL=没人设过(回落 DEFAULT_INTERVAL_MINUTES) / 0=关闭 / >0=分钟数。
    nc_jv_sync_interval_minutes: Mapped[int | None] = mapped_column(Integer, nullable=True)
```

⚠️ 确认该文件顶部已 import `Integer`；没有就加进现有的 `from sqlalchemy import ...`。

Modify `finance-api/app/core/config.py` —— 在 `Settings` 类里加：

```python
    # 部署级总开关。关掉 = 这个进程不起调度循环(手动触发不受影响)。
    # 与 epms-api 的同名设置一一对应。
    nc_sync_scheduler_enabled: bool = True
```

- [ ] **Step 5: 写调度循环**

Create `finance-api/app/tasks/__init__.py`（空文件）。

Create `finance-api/app/tasks/nc_sync_scheduler.py`：

```python
"""按间隔自动跑 NC 凭证(JV)同步,而不是靠谁记得去点按钮。

形状照抄 epms-api/app/tasks/nc_purchase_sync_scheduler.py —— 同一套语义,
换成读 nc_jv_sync_interval_minutes、触发 services/nc_sync 的 start_run、
从 nc_sync_runs 取上次开始时间。

四条刻意的设计(与模板一致):
* 间隔每 tick 重读 —— 管理员改完一分钟内生效,不用重启。
* NULL 回落默认;0 关闭整个排期。
* 只跑 incremental。full 是删了重建,API 层专门做了 confirm 门禁,
  没有任何自动的东西应该做它。
* 到点从上次 run 的"开始时间"算 —— NC 连不上时是每个 interval 重试一次,
  而不是每个 tick 都撞一次。
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import select

from app.core.config import settings
from app.db.base import AsyncSessionLocal
from app.models.mirrors import CompanyConfig
from app.models.nc_sync import NcSyncRun
from app.services import nc_sync as svc

logger = logging.getLogger(__name__)

# 每小时。凭证是人在工作日里录的,分钟级新鲜度没有意义。
DEFAULT_INTERVAL_MINUTES = 60
# 一天。比这更长就不是谁在依赖的排期了,那是想关掉 —— 关掉用 0 表达。
MAX_INTERVAL_MINUTES = 1440
# 心跳。远短于 interval,所以改设置一分钟内生效,而且排期是 run 历史的属性、
# 不是这个进程 uptime 的属性 —— 重启不会重置任何人的相位。
TICK_SECONDS = 60


def resolve_interval_minutes(raw: object) -> int:
    """把库里存的值强制成一个能用的分钟数。

    NULL(没人设过)回落默认。超出 0..MAX 的值 —— API 层会拒,但手改的行不会 ——
    是钳制而不是照办:interval=0.5 会变成每 30 秒读一次 NC。
    """
    if raw is None:
        return DEFAULT_INTERVAL_MINUTES
    if isinstance(raw, bool) or not isinstance(raw, int):
        logger.warning("JV sync interval is %r, not an integer — using %d minutes",
                       raw, DEFAULT_INTERVAL_MINUTES)
        return DEFAULT_INTERVAL_MINUTES
    if raw < 0:
        return 0
    return min(raw, MAX_INTERVAL_MINUTES)


def is_due(*, last_started_at: datetime | None, interval_minutes: int,
           now: datetime) -> bool:
    """纯决策,所以排期能脱离 NC 和时钟被测试。

    从上一次的 START 算起,不管它的结果如何:NC 连不上应该是每个 interval
    重试一次,而不是每个 tick。开始时间在未来(时钟回拨)算作未到点 ——
    多等一个 interval 无害,当成逾期会连续同步。
    """
    if interval_minutes <= 0:
        return False
    if last_started_at is None:
        return True
    if last_started_at.tzinfo is None:
        last_started_at = last_started_at.replace(tzinfo=timezone.utc)
    return now - last_started_at >= timedelta(minutes=interval_minutes)


async def load_interval_minutes() -> int:
    """读配置。读库炸了不能杀掉循环 —— 降级成默认值,那也正是未配置的含义。"""
    try:
        async with AsyncSessionLocal() as db:
            raw = (await db.execute(
                select(CompanyConfig.nc_jv_sync_interval_minutes).limit(1)
            )).scalar_one_or_none()
    except Exception as exc:  # noqa: BLE001
        logger.error("JV sync scheduler: failed to read the interval (%s) — using %d minutes",
                     exc, DEFAULT_INTERVAL_MINUTES)
        return DEFAULT_INTERVAL_MINUTES
    return resolve_interval_minutes(raw)


async def last_run_started_at() -> datetime | None:
    async with AsyncSessionLocal() as db:
        return (await db.execute(
            select(NcSyncRun.started_at)
            .order_by(NcSyncRun.started_at.desc()).limit(1)
        )).scalar_one_or_none()


async def run_tick() -> str:
    """一次调度决策。返回它做了什么以及为什么 —— 'disabled' | 'not_configured'
    | 'not_due' | 'already_running' | 'synced' | 'failed'。测试断言的就是这个,
    日志打印的也是这个。"""
    interval = await load_interval_minutes()
    if interval <= 0:
        return "disabled"
    if not svc.nc_configured():
        # NC_* 留空是"这个部署把功能关了",不是每分钟报一次的故障。
        return "not_configured"
    if not is_due(last_started_at=await last_run_started_at(),
                  interval_minutes=interval, now=datetime.now(timezone.utc)):
        return "not_due"

    try:
        # start_run 和加载都是阻塞的 psycopg2 活;放到线程里,免得一次慢的
        # NC 读把这个进程正在服务的 API 也卡住。
        # started_by=None:没人按任何东西,编一个 user id 会把某个人的名字
        # 记到机器跑的这条 run 上。
        run_id = await asyncio.to_thread(
            svc.start_run, "incremental", None, run_worker=True)
    except svc.SyncAlreadyRunning:
        # 管理员按了按钮,或者另一个副本先到了。
        return "already_running"
    except Exception:  # noqa: BLE001
        logger.exception("Scheduled NC voucher sync failed")
        return "failed"
    logger.info("Scheduled NC voucher sync finished (run %s)", run_id)
    return "synced"


async def nc_sync_loop() -> None:
    """无限循环;以 asyncio.create_task(nc_sync_loop()) 启动。"""
    if not settings.nc_sync_scheduler_enabled:
        logger.info("JV sync scheduler disabled (nc_sync_scheduler_enabled=false)")
        return
    logger.info("JV sync scheduler started (tick=%ss)", TICK_SECONDS)
    while True:
        try:
            await run_tick()
        except asyncio.CancelledError:
            logger.info("JV sync scheduler stopping")
            raise
        except Exception:  # noqa: BLE001
            logger.exception("JV sync tick failed")
        await asyncio.sleep(TICK_SECONDS)
```

- [ ] **Step 6: 跑测试确认通过**

```bash
cd finance-api && TEST_FINANCE_DB=finance_test python -m pytest tests/test_nc_sync_scheduler.py -v
```

Expected: `20 passed`。

- [ ] **Step 7: 端点 —— `PATCH /interval` 与 `/status` 补字段**

Modify `finance-api/app/api/v1/nc_sync.py`。顶部 import 补上：

```python
from datetime import timedelta
from app.models.mirrors import CompanyConfig
from app.tasks import nc_sync_scheduler as sched
```

在 `status()` 的 `return {` **之前**插入：

```python
    interval = sched.resolve_interval_minutes((await db.execute(
        select(CompanyConfig.nc_jv_sync_interval_minutes).limit(1))
    ).scalar_one_or_none())
    # 调度器下一次会捡起它的时刻。和 is_due() 一样从上次 run 的 START 量 ——
    # 用别的东西算出来的倒计时会和它所描述的那个循环对不上。
    # 排期关闭时为 null,好让 UI 说"off",而不是画一个永远走不到的倒计时。
    anchor = current or last
    next_due_at = None
    if interval > 0 and anchor is not None and anchor.started_at is not None:
        started = anchor.started_at
        if started.tzinfo is None:
            started = started.replace(tzinfo=timezone.utc)
        next_due_at = (started + timedelta(minutes=interval)).isoformat()
```

并在 `return {` 的字典里加两个键：

```python
        "interval_minutes": interval,
        "next_due_at": next_due_at,
```

在文件末尾追加：

```python
class IntervalIn(BaseModel):
    minutes: int


@router.patch("/interval")
async def set_interval(body: IntervalIn, user: CurrentUser,
                       db: AsyncSession = Depends(get_db)):
    """同步多久自己跑一次。0 关闭排期,只剩按钮这一个触发方式。"""
    if user.get("role") != "system_admin":
        raise HTTPException(status_code=403, detail="system_admin only")
    if isinstance(body.minutes, bool) or not 0 <= body.minutes <= sched.MAX_INTERVAL_MINUTES:
        raise HTTPException(
            status_code=422,
            detail=f"minutes must be between 0 and {sched.MAX_INTERVAL_MINUTES} "
                   f"(0 disables automatic sync)")
    cfg = (await db.execute(select(CompanyConfig).limit(1))).scalars().first()
    if cfg is None:
        raise HTTPException(status_code=404, detail="company config not found")
    cfg.nc_jv_sync_interval_minutes = body.minutes
    # finance-api 的 get_db() 不自动 commit —— 不显式提交这里的改动会被丢弃。
    await db.commit()
    return {"interval_minutes": body.minutes}
```

⚠️ `status()` 端点当前的签名是 `async def status(user: CurrentUser, db: AsyncSession = Depends(get_db))`，
`current` / `last` 两个变量在它里面已经存在，直接用。

- [ ] **Step 8: 新建 lifespan 并挂上循环**

Modify `finance-api/app/main.py`。import 区加：

```python
import asyncio
from contextlib import asynccontextmanager
```

在 `app = FastAPI(` **之前**插入：

```python
@asynccontextmanager
async def lifespan(app: FastAPI):
    from app.tasks.nc_sync_scheduler import nc_sync_loop
    scheduler_task = asyncio.create_task(nc_sync_loop())
    yield
    scheduler_task.cancel()
```

给 `FastAPI(...)` 加 `lifespan=lifespan` 参数。

- [ ] **Step 9: 应用能起来 + 端点真的挂上了**

```bash
cd finance-api && python -c "
import app.main as m
paths=[r.path for r in m.app.routes if 'nc-sync' in r.path]
print(paths)
assert '/finance/v1/nc-sync/interval' in paths, 'interval 端点没挂上'
print('APP OK')"
```

Expected: 打印出含 `/finance/v1/nc-sync/interval` 的路径列表，然后 `APP OK`。

⚠️ **不要用「打一下看返回码」当端点存在的证据** —— 本仓库实测过不存在的路径也返回 403。
看 `app.routes` 才是正面证据。

- [ ] **Step 10: 全量回归对基线**

```bash
cd finance-api && TEST_FINANCE_DB=finance_test python -m pytest -q -k "not qbo" 2>&1 | tail -20
```

Expected: 失败**集合**与改动前一致。只比数字会误判。
⚠️ `-k "not qbo"` 是必须的 —— `POST /qbo/sync` 相关用例会**真连生产 QuickBooks**。
全量约 40 分钟。

- [ ] **Step 11: Commit**

```bash
git add finance-api/app/tasks/ finance-api/app/models/mirrors.py finance-api/app/core/config.py \
        finance-api/app/api/v1/nc_sync.py finance-api/app/main.py \
        finance-api/tests/test_nc_sync_scheduler.py
git commit -m "feat(finance): scheduled incremental NC voucher sync"
```

---

## Task 3: mdm-api — ERP 主数据同步调度器

**Files:**
- Create: `mdm-api/app/models/company_config.py`
- Create: `mdm-api/app/tasks/__init__.py`
- Create: `mdm-api/app/tasks/erp_sync_scheduler.py`
- Modify: `mdm-api/app/core/config.py`
- Modify: `mdm-api/app/api/v1/erp_mdm.py`
- Modify: `mdm-api/app/main.py`
- Modify: `mdm-api/tests/conftest.py`（注册新模型 —— 表来自 `create_all`）
- Create: `mdm-api/tests/test_erp_sync_scheduler.py`

**Interfaces:**
- Consumes: Task 1 的 `company_config.erp_mdm_sync_interval_minutes`
- Produces:
  - `app.models.company_config.CompanyConfig`（**mirror，不进 mdm 的 alembic**）
  - `app.tasks.erp_sync_scheduler` 的 `DEFAULT_INTERVAL_MINUTES = 1440`、`MAX_INTERVAL_MINUTES = 1440`、
    `TICK_SECONDS = 60`、`SUB_KINDS = ("material", "supplier", "person")`
  - `resolve_interval_minutes` / `is_due` / `run_all_kinds(db)` / `run_tick()` / `erp_sync_loop()`
  - HTTP：`GET /mdm/v1/erp/sync/schedule`、`PATCH /mdm/v1/erp/sync/interval`
- Task 5 的 Portal 组件读 `GET /erp/sync/schedule`。

⚠️ **不要改 `GET /erp/sync/status` 的返回形状** —— 它现在返回 `{material:…, supplier:…, person:…}`，
Portal 的 `ErpSyncToolbar` 正在按 `status[kind]` 读。所以间隔另开一个 `/sync/schedule`。

---

- [ ] **Step 1: 先逐列核对物理表，再写 mirror**

```bash
docker exec uniops_postgres psql -U epms -d epms -tAc \
  "select column_name, data_type, is_nullable from information_schema.columns where table_name='company_config' and column_name in ('id','erp_mdm_sync_interval_minutes')"
```

Expected: 两行 —— `id|uuid|NO` 和 `erp_mdm_sync_interval_minutes|integer|YES`。

若 `erp_mdm_sync_interval_minutes` 不在，说明 Task 1 的迁移没跑到这个库 —— 先跑它，不要往下走。
（本仓库因「镜像模型和物理表对不上」已踩过三次。）

- [ ] **Step 2: 写失败测试**

Create `mdm-api/tests/test_erp_sync_scheduler.py`：

```python
"""ERP 主数据同步调度器 —— 纯决策函数 + 三小类顺序执行。"""
from datetime import datetime, timedelta, timezone

import pytest

from app.tasks import erp_sync_scheduler as sched

NOW = datetime(2026, 8, 25, 12, 0, 0, tzinfo=timezone.utc)


def test_null_resolves_to_default():
    assert sched.resolve_interval_minutes(None) == sched.DEFAULT_INTERVAL_MINUTES


def test_zero_means_off():
    assert sched.resolve_interval_minutes(0) == 0


def test_negative_clamps_to_zero():
    assert sched.resolve_interval_minutes(-1) == 0


def test_above_max_clamps_to_max():
    assert sched.resolve_interval_minutes(99999) == sched.MAX_INTERVAL_MINUTES


def test_bool_is_not_an_integer():
    assert sched.resolve_interval_minutes(True) == sched.DEFAULT_INTERVAL_MINUTES


def test_never_run_is_due():
    assert sched.is_due(last_started_at=None, interval_minutes=1440, now=NOW) is True


def test_disabled_is_never_due():
    assert sched.is_due(last_started_at=None, interval_minutes=0, now=NOW) is False


def test_not_due_before_interval_elapses():
    assert sched.is_due(last_started_at=NOW - timedelta(minutes=1439),
                        interval_minutes=1440, now=NOW) is False


def test_due_exactly_at_interval():
    assert sched.is_due(last_started_at=NOW - timedelta(minutes=1440),
                        interval_minutes=1440, now=NOW) is True


def test_future_start_is_not_due():
    assert sched.is_due(last_started_at=NOW + timedelta(minutes=5),
                        interval_minutes=1440, now=NOW) is False


@pytest.mark.asyncio
async def test_run_all_kinds_runs_three_in_order(monkeypatch):
    called = []

    async def _fake(db, kind, **kw):
        called.append(kind)
        return {"total": 0, "inserted": 0, "updated": 0}

    monkeypatch.setattr(sched, "sync_kind", _fake)
    await sched.run_all_kinds(db=None)
    assert called == ["material", "supplier", "person"]


@pytest.mark.asyncio
async def test_run_all_kinds_never_asks_for_full(monkeypatch):
    """自动跑必须永远是增量 —— full 会把整个 ERP 从头拉一遍。"""
    seen = []

    async def _fake(db, kind, **kw):
        seen.append(kw.get("full"))
        return {"total": 0, "inserted": 0, "updated": 0}

    monkeypatch.setattr(sched, "sync_kind", _fake)
    await sched.run_all_kinds(db=None)
    assert seen == [False, False, False]


@pytest.mark.asyncio
async def test_one_failing_kind_does_not_block_the_others(monkeypatch):
    """供应商和人员的数据与物料无关 —— 一类的接口抖动不该废掉整轮。"""
    called = []

    async def _fake(db, kind, **kw):
        called.append(kind)
        if kind == "material":
            raise RuntimeError("ERP down")
        return {"total": 0, "inserted": 0, "updated": 0}

    monkeypatch.setattr(sched, "sync_kind", _fake)
    await sched.run_all_kinds(db=None)
    assert called == ["material", "supplier", "person"]


@pytest.mark.asyncio
async def test_run_tick_disabled(monkeypatch):
    async def _interval():
        return 0
    monkeypatch.setattr(sched, "load_interval_minutes", _interval)
    assert await sched.run_tick() == "disabled"


@pytest.mark.asyncio
async def test_run_tick_not_due(monkeypatch):
    async def _interval():
        return 1440

    async def _last():
        return NOW
    monkeypatch.setattr(sched, "load_interval_minutes", _interval)
    monkeypatch.setattr(sched, "last_sync_started_at", _last)
    assert await sched.run_tick() == "not_due"


@pytest.mark.asyncio
async def test_run_tick_synced(monkeypatch):
    async def _interval():
        return 1440

    async def _last():
        return None

    async def _run(db):
        return None
    monkeypatch.setattr(sched, "load_interval_minutes", _interval)
    monkeypatch.setattr(sched, "last_sync_started_at", _last)
    monkeypatch.setattr(sched, "run_all_kinds", _run)
    assert await sched.run_tick() == "synced"
```

- [ ] **Step 3: 跑测试确认失败**

```bash
cd mdm-api && TEST_DATABASE_URL=postgresql+asyncpg://epms:epms_dev@localhost:5432/mdm_test \
  python -m pytest tests/test_erp_sync_scheduler.py -v
```

Expected: 全部 ERROR，`ModuleNotFoundError: No module named 'app.tasks'`。

⚠️ **`TEST_DATABASE_URL` 必须设**，否则 conftest 直接 `pytest.skip`，
**52 个用例静默跳过、显示全绿**。看到大量 `skipped` 就是没设对。

- [ ] **Step 4: 写 mirror 模型 + settings 开关**

Create `mdm-api/app/models/company_config.py`：

```python
"""company_config 的部分镜像 —— 这张表由 epms-api 的 alembic 拥有。

mdm-api 只需要一列:ERP 主数据同步的间隔。照 finance-api/app/models/mirrors.py
的做法,只声明用得着的列,其余留给拥有者。

⚠️ 绝不要把这张表写进 mdm-api 自己的 alembic —— 它不归这个服务管。
测试库靠 Base.metadata.create_all 建表,所以本模型必须注册进 tests/conftest.py。
"""
from sqlalchemy import Integer
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, UUIDPrimaryKey


class CompanyConfig(UUIDPrimaryKey, Base):
    __tablename__ = "company_config"

    # NULL=没人设过(回落 DEFAULT_INTERVAL_MINUTES) / 0=关闭 / >0=分钟数
    erp_mdm_sync_interval_minutes: Mapped[int | None] = mapped_column(Integer, nullable=True)
```

Modify `mdm-api/app/core/config.py` —— 在 `Settings` 类里加：

```python
    # 部署级总开关。关掉 = 这个进程不起调度循环(手动触发不受影响)。
    nc_sync_scheduler_enabled: bool = True
```

- [ ] **Step 5: 写调度循环**

Create `mdm-api/app/tasks/__init__.py`（空文件）。

Create `mdm-api/app/tasks/erp_sync_scheduler.py`：

```python
"""按间隔自动跑 ERP 主数据同步(物料/供应商/人员)。

形状照抄 epms-api/app/tasks/nc_purchase_sync_scheduler.py。差异只有两处:
* 一次调度顺序跑完三个小类 —— 三张表都很小(合计不到 4000 行),
  没必要给它们三个独立的排期。
* 单个小类失败不让另外两个连坐:供应商和人员的数据与物料无关,
  一类的接口抖动没有理由把整轮同步都废掉。

默认一天一次:主数据变动很少,而且每次都是对外部接口的一串 HTTP 调用。
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select

from app.core.config import settings
from app.db.base import AsyncSessionLocal
from app.models.company_config import CompanyConfig
from app.models.erp_sync_state import ErpSyncState
from app.services.erp_sync import sync_kind

logger = logging.getLogger(__name__)

# 一天一次。主数据变动很少,而且这是对外部接口的调用,不是本地读。
DEFAULT_INTERVAL_MINUTES = 1440
MAX_INTERVAL_MINUTES = 1440
TICK_SECONDS = 60

# 固定顺序:三者互不依赖,但固定顺序让日志可预期。
SUB_KINDS = ("material", "supplier", "person")


def resolve_interval_minutes(raw: object) -> int:
    """把库里存的值强制成一个能用的分钟数。NULL→默认,超界→钳制。"""
    if raw is None:
        return DEFAULT_INTERVAL_MINUTES
    if isinstance(raw, bool) or not isinstance(raw, int):
        logger.warning("ERP MDM sync interval is %r, not an integer — using %d minutes",
                       raw, DEFAULT_INTERVAL_MINUTES)
        return DEFAULT_INTERVAL_MINUTES
    if raw < 0:
        return 0
    return min(raw, MAX_INTERVAL_MINUTES)


def is_due(*, last_started_at: datetime | None, interval_minutes: int,
           now: datetime) -> bool:
    """纯决策。从上次同步的开始时间量;未来时间(时钟回拨)算未到点。"""
    if interval_minutes <= 0:
        return False
    if last_started_at is None:
        return True
    if last_started_at.tzinfo is None:
        last_started_at = last_started_at.replace(tzinfo=timezone.utc)
    return now - last_started_at >= timedelta(minutes=interval_minutes)


async def load_interval_minutes() -> int:
    """读配置。读库炸了降级成默认值,不杀循环。"""
    try:
        async with AsyncSessionLocal() as db:
            raw = (await db.execute(
                select(CompanyConfig.erp_mdm_sync_interval_minutes).limit(1)
            )).scalar_one_or_none()
    except Exception as exc:  # noqa: BLE001
        logger.error("ERP MDM scheduler: failed to read the interval (%s) — using %d minutes",
                     exc, DEFAULT_INTERVAL_MINUTES)
        return DEFAULT_INTERVAL_MINUTES
    return resolve_interval_minutes(raw)


async def last_sync_started_at() -> datetime | None:
    """三个小类里最早的那次 last_synced_at —— 只要有一类还没轮到,整轮就算没跑完。

    某一类从来没同步过时该行不存在,min() 得到 NULL,is_due 因此返回 True。
    """
    async with AsyncSessionLocal() as db:
        rows = (await db.execute(
            select(func.count(ErpSyncState.kind), func.min(ErpSyncState.last_synced_at))
            .where(ErpSyncState.kind.in_(SUB_KINDS))
        )).one()
    count, earliest = rows
    if count < len(SUB_KINDS):
        return None      # 有小类从没跑过 —— 该跑
    return earliest


async def run_all_kinds(db) -> None:
    """顺序跑三小类的增量同步。单类异常只记日志,不中断其余。"""
    for kind in SUB_KINDS:
        try:
            result = await sync_kind(db, kind, full=False)
            logger.info("ERP MDM scheduler: %s ok — %s", kind, result)
        except Exception as exc:  # noqa: BLE001 — 一类失败不连坐
            logger.error("ERP MDM scheduler: %s failed: %s", kind, exc)


async def run_tick() -> str:
    """一次调度决策。'disabled' | 'not_due' | 'synced' | 'failed'。"""
    interval = await load_interval_minutes()
    if interval <= 0:
        return "disabled"
    if not is_due(last_started_at=await last_sync_started_at(),
                  interval_minutes=interval, now=datetime.now(timezone.utc)):
        return "not_due"
    try:
        async with AsyncSessionLocal() as db:
            await run_all_kinds(db)
            await db.commit()
    except Exception:  # noqa: BLE001
        logger.exception("Scheduled ERP MDM sync failed")
        return "failed"
    return "synced"


async def erp_sync_loop() -> None:
    """无限循环;以 asyncio.create_task(erp_sync_loop()) 启动。"""
    if not settings.nc_sync_scheduler_enabled:
        logger.info("ERP MDM sync scheduler disabled (nc_sync_scheduler_enabled=false)")
        return
    logger.info("ERP MDM sync scheduler started (tick=%ss)", TICK_SECONDS)
    while True:
        try:
            await run_tick()
        except asyncio.CancelledError:
            logger.info("ERP MDM sync scheduler stopping")
            raise
        except Exception:  # noqa: BLE001
            logger.exception("ERP MDM sync tick failed")
        await asyncio.sleep(TICK_SECONDS)
```

⚠️ `test_run_tick_synced` 会 monkeypatch `run_all_kinds`，所以 `run_tick` 里必须
**通过模块名调用**（`run_all_kinds(db)` 在同模块内是全局查找，monkeypatch 生效）。不要
把它 import 成别的名字。

- [ ] **Step 6: 跑测试确认通过**

```bash
cd mdm-api && TEST_DATABASE_URL=postgresql+asyncpg://epms:epms_dev@localhost:5432/mdm_test \
  python -m pytest tests/test_erp_sync_scheduler.py -v
```

Expected: `16 passed`，且**没有 `skipped`**。

- [ ] **Step 7: 端点**

Modify `mdm-api/app/api/v1/erp_mdm.py`。顶部 import 补上：

```python
from datetime import timedelta, timezone
from pydantic import BaseModel
from app.models.company_config import CompanyConfig
from app.tasks import erp_sync_scheduler as sched
```

在文件末尾追加：

```python
class IntervalIn(BaseModel):
    minutes: int


@router.get("/sync/schedule")
async def sync_schedule(db: AsyncSession = Depends(get_db), user: CurrentUser = None):
    """自动同步的排期。单开一个端点而不是塞进 /sync/status —— 后者返回的是
    {material:…, supplier:…, person:…},Portal 正按 kind 取值,不该改它的形状。"""
    _require_admin(user)
    interval = sched.resolve_interval_minutes((await db.execute(
        select(CompanyConfig.erp_mdm_sync_interval_minutes).limit(1))
    ).scalar_one_or_none())
    started = await sched.last_sync_started_at()
    next_due_at = None
    if interval > 0 and started is not None:
        if started.tzinfo is None:
            started = started.replace(tzinfo=timezone.utc)
        next_due_at = (started + timedelta(minutes=interval)).isoformat()
    return {"interval_minutes": interval, "next_due_at": next_due_at}


@router.patch("/sync/interval")
async def set_sync_interval(body: IntervalIn,
                            db: AsyncSession = Depends(get_db), user: CurrentUser = None):
    """同步多久自己跑一次。0 关闭排期,只剩按钮这一个触发方式。"""
    _require_admin(user)
    if isinstance(body.minutes, bool) or not 0 <= body.minutes <= sched.MAX_INTERVAL_MINUTES:
        raise HTTPException(
            status_code=422,
            detail=f"minutes must be between 0 and {sched.MAX_INTERVAL_MINUTES} "
                   f"(0 disables automatic sync)")
    cfg = (await db.execute(select(CompanyConfig).limit(1))).scalars().first()
    if cfg is None:
        raise HTTPException(status_code=404, detail="company config not found")
    cfg.erp_mdm_sync_interval_minutes = body.minutes
    return {"interval_minutes": body.minutes}
```

⚠️ 路由顺序：`/sync/schedule` 与 `/sync/interval` 必须**不会**被既有的
`@router.post("/sync/{kind}")` 抢走。前者是 GET、后者是 PATCH，方法不同不会冲突；
但如果有 `@router.get("/sync/{kind}")` 就要把新端点注册在它**之前**。动手前 grep 确认。

- [ ] **Step 8: 挂 lifespan + 注册模型**

Modify `mdm-api/app/main.py` —— 把空的 lifespan 换成：

```python
@asynccontextmanager
async def lifespan(app: FastAPI):
    import asyncio

    from app.tasks.erp_sync_scheduler import erp_sync_loop
    scheduler_task = asyncio.create_task(erp_sync_loop())
    yield
    scheduler_task.cancel()
```

并把 `company_config` 加进顶部那行模型 import 列表。

Modify `mdm-api/tests/conftest.py` —— 把 `company_config` 加进 `from app.models import (...)`。
**这一步不能漏**：测试库表来自 `create_all`。

- [ ] **Step 9: 应用能起来 + 端点真的挂上了**

```bash
cd mdm-api && python -c "
import app.main as m
paths=[(r.path, sorted(getattr(r,'methods',[]) or [])) for r in m.app.routes if '/erp/sync' in r.path]
print(paths)
assert any(p=='/mdm/v1/erp/sync/schedule' for p,_ in paths), 'schedule 端点没挂上'
assert any(p=='/mdm/v1/erp/sync/interval' for p,_ in paths), 'interval 端点没挂上'
print('APP OK')"
```

Expected: 打印路径列表后 `APP OK`。

- [ ] **Step 10: 全量回归对基线**

```bash
cd mdm-api && TEST_DATABASE_URL=postgresql+asyncpg://epms:epms_dev@localhost:5432/mdm_test \
  python -m pytest -q 2>&1 | tail -20
```

Expected: 失败集合与改动前一致，且 **`skipped` 数量没有突然变大**（变大 = 环境变量没生效）。

- [ ] **Step 11: Commit**

```bash
git add mdm-api/app/models/company_config.py mdm-api/app/tasks/ mdm-api/app/core/config.py \
        mdm-api/app/api/v1/erp_mdm.py mdm-api/app/main.py mdm-api/tests/conftest.py \
        mdm-api/tests/test_erp_sync_scheduler.py
git commit -m "feat(mdm): scheduled incremental ERP master-data sync"
```

---

## Task 4: mdm-api — `part_status` 过滤别名（spec §6.1）

**Files:**
- Modify: `mdm-api/app/crud/erp.py`
- Create: `mdm-api/tests/test_erp_part_status_alias.py`

**Interfaces:**
- Produces: `app.crud.erp._STATUS_ALIASES`
- 与 Task 3 无耦合，只是同一个服务。

**背景：** 现网 `erp_materials.part_status` 全库 2573 行都是 NC 的 `ENABLESTATE` 原值 `'2'`，
而两处消费方都按 `'A'` 等值过滤（`crud/erp.py` 用 `==`）：
`epms/src/components/pr/PrLineItems.tsx:234` 的 PR Type 1 选料器 → **永远 0 行**；
Portal 的 ERP MDM Materials tab 默认 `Active (A)` → **默认空表**。

---

- [ ] **Step 1: 先证明缺陷真的存在**

```bash
docker exec uniops_postgres psql -U epms -d epms -tAc \
  "select part_status, count(*) from erp_materials group by 1"
```

Expected: 看到 `2|<数量>` 之类，**没有** `A`。这就是缺陷的正面证据。
若这里已经是 `A`，停下来报告 —— 前提变了，本 task 可能已无必要。

- [ ] **Step 2: 写失败测试**

Create `mdm-api/tests/test_erp_part_status_alias.py`：

```python
"""part_status 别名 —— 向前兼容的读端修复(spec §6.1)。

'A' 同时匹配 'A' 和 NC 的 ENABLESTATE 原值 '2'。这样将来写端归一成
'A'/'B' 之后这段代码不用再改一次,两种口径并存的窗口期也不会失效。
"""
import pytest

from app.crud import erp as erp_crud
from app.models.erp_material import ErpMaterial


@pytest.mark.asyncio
async def test_filter_A_matches_legacy_numeric_2(db_session):
    db_session.add(ErpMaterial(erp_part_no="CR0297", part_status="2"))
    await db_session.commit()
    items, total = await erp_crud.list_materials(db_session, part_status="A")
    assert total == 1
    assert items[0].erp_part_no == "CR0297"


@pytest.mark.asyncio
async def test_filter_A_still_matches_literal_A(db_session):
    db_session.add(ErpMaterial(erp_part_no="CR0298", part_status="A"))
    await db_session.commit()
    items, total = await erp_crud.list_materials(db_session, part_status="A")
    assert total == 1
    assert items[0].erp_part_no == "CR0298"


@pytest.mark.asyncio
async def test_filter_B_matches_legacy_numeric_3(db_session):
    db_session.add(ErpMaterial(erp_part_no="CR0299", part_status="3"))
    await db_session.commit()
    items, total = await erp_crud.list_materials(db_session, part_status="B")
    assert total == 1


@pytest.mark.asyncio
async def test_filter_A_excludes_inactive(db_session):
    db_session.add(ErpMaterial(erp_part_no="CR0300", part_status="3"))
    await db_session.commit()
    items, total = await erp_crud.list_materials(db_session, part_status="A")
    assert total == 0
    assert items == []


@pytest.mark.asyncio
async def test_total_and_items_agree(db_session):
    """count 查询和取行查询必须用同一个过滤条件 —— 只改一条会让分页错乱。"""
    db_session.add(ErpMaterial(erp_part_no="CR0301", part_status="2"))
    db_session.add(ErpMaterial(erp_part_no="CR0302", part_status="3"))
    await db_session.commit()
    items, total = await erp_crud.list_materials(db_session, part_status="A")
    assert total == len(items) == 1


@pytest.mark.asyncio
async def test_no_filter_returns_everything(db_session):
    db_session.add(ErpMaterial(erp_part_no="CR0303", part_status="2"))
    db_session.add(ErpMaterial(erp_part_no="CR0304", part_status="3"))
    await db_session.commit()
    items, total = await erp_crud.list_materials(db_session)
    assert total == 2


@pytest.mark.asyncio
async def test_unknown_status_still_matches_itself(db_session):
    """别名表里没有的值原样比较,不要吞掉。"""
    db_session.add(ErpMaterial(erp_part_no="CR0305", part_status="Z"))
    await db_session.commit()
    items, total = await erp_crud.list_materials(db_session, part_status="Z")
    assert total == 1
```

⚠️ `ErpMaterial(...)` 的构造参数以 `mdm-api/app/models/erp_material.py` 的实际列为准。
若有 NOT NULL 且无默认值的列，补上真实值即可 —— **不要为了让测试通过去改模型**。

- [ ] **Step 3: 跑测试确认失败**

```bash
cd mdm-api && TEST_DATABASE_URL=postgresql+asyncpg://epms:epms_dev@localhost:5432/mdm_test \
  python -m pytest tests/test_erp_part_status_alias.py -v
```

Expected: `test_filter_A_matches_legacy_numeric_2`、`test_filter_B_matches_legacy_numeric_3`、
`test_total_and_items_agree` 三个 FAIL（`total == 0`）；其余 passed。

- [ ] **Step 4: 改过滤**

Modify `mdm-api/app/crud/erp.py` —— 在 `list_materials` **之前**加常量：

```python
# 现网 erp_materials.part_status 存的是 NC 的 ENABLESTATE 原值('2' 启用 / '3' 停用),
# 而前端(PR Type 1 选料器、Admin 的 Materials tab)一直按 'A'/'B' 过滤 —— 等值比较
# 永远不命中,两处都是空列表。这里让两种口径都能命中,所以将来写端归一成 'A'/'B'
# 之后这段不用再改,两种口径并存的窗口期也不会失效。
_STATUS_ALIASES = {"A": ("A", "2"), "B": ("B", "3")}
```

把每一处

```python
    if part_status:
        q = q.where(ErpMaterial.part_status == part_status)
```

换成

```python
    if part_status:
        q = q.where(ErpMaterial.part_status.in_(
            _STATUS_ALIASES.get(part_status, (part_status,))))
```

⚠️ `list_materials` 里通常有**两条** query（取行 + `count`）。**两条都要改**，否则
`total` 与 `items` 对不上、分页错乱。改完自己 grep 确认没有漏网：

```bash
grep -n "part_status ==" mdm-api/app/crud/erp.py
```

Expected: **无输出**。

- [ ] **Step 5: 跑测试确认通过**

```bash
cd mdm-api && TEST_DATABASE_URL=postgresql+asyncpg://epms:epms_dev@localhost:5432/mdm_test \
  python -m pytest tests/test_erp_part_status_alias.py -v
```

Expected: `7 passed`。

- [ ] **Step 6: Commit**

```bash
git add mdm-api/app/crud/erp.py mdm-api/tests/test_erp_part_status_alias.py
git commit -m "fix(mdm): part_status filter matches NC ENABLESTATE values"
```

---

## Task 5: Portal — `ScheduleCard` 共用组件

**Files:**
- Create: `portal/src/pages/admin/nc-sync/ScheduleCard.tsx`

**Interfaces:**
- Consumes: `@/lib/api` 的 `epmsApi` / `financeApi` / `mdmApi`（三个客户端都已存在，
  见 `portal/src/lib/api.ts:123/199` 附近）；`@/lib/utils` 的 `cn`
- Produces:
  - `export interface ScheduleState { interval_minutes: number; next_due_at: string | null }`
  - `export interface ScheduleEndpoint { client: 'epms'|'finance'|'mdm'; readPath: string; writePath: string; queryKey: string }`
  - `export function ScheduleCard(props: { endpoint: ScheduleEndpoint; title: string; maxMinutes?: number })`
- Task 6 引用这两样。

**为什么是 endpoint 描述符而不是 `service` 枚举：** 三个服务的读路径形状不一样 ——
epms 与 finance 把 `interval_minutes`/`next_due_at` 放在各自的 `/status` 里，
mdm 单开了 `/erp/sync/schedule`。写路径也各不相同。用描述符比在组件里写三分支干净。

---

- [ ] **Step 1: 写组件**

Create `portal/src/pages/admin/nc-sync/ScheduleCard.tsx`：

```tsx
/**
 * ScheduleCard — 自动同步的间隔设置,Purchase / JV / MDM 三个 tab 共用。
 *
 * 后端三处的语义完全一致(见 epms-api/app/tasks/nc_purchase_sync_scheduler.py):
 *   0 = 关闭自动同步,只剩手动按钮
 *   >0 = 分钟数,上限 1440(一天)
 * 所以这里只做一层薄的分钟/小时换算,真正的钳制在后端。
 *
 * English-only copy per project convention.
 */
import { useEffect, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Clock, Loader2 } from 'lucide-react'
import { epmsApi, financeApi, mdmApi } from '@/lib/api'
import { cn } from '@/lib/utils'

export interface ScheduleState {
  interval_minutes: number
  next_due_at: string | null
}

export interface ScheduleEndpoint {
  client: 'epms' | 'finance' | 'mdm'
  /** GET 路径,返回体里必须含 interval_minutes 与 next_due_at */
  readPath: string
  /** PATCH 路径,请求体 { minutes } */
  writePath: string
  /** react-query 的 key,与该 tab 的手动区块共用同一份 status 时要保持一致 */
  queryKey: string
}

const CLIENTS = { epms: epmsApi, finance: financeApi, mdm: mdmApi }

const DEFAULT_MAX_MINUTES = 1440

function formatWhen(iso: string | null): string {
  if (!iso) return '—'
  const d = new Date(iso)
  return Number.isNaN(d.getTime()) ? '—' : d.toLocaleString()
}

/** 分钟数 → 便于阅读的 {amount, unit}。整小时用 Hours,否则用 Minutes。 */
function splitInterval(minutes: number): { amount: string; unit: 'minutes' | 'hours' } {
  if (minutes > 0 && minutes % 60 === 0) {
    return { amount: String(minutes / 60), unit: 'hours' }
  }
  return { amount: String(minutes), unit: 'minutes' }
}

export function ScheduleCard({ endpoint, title, maxMinutes = DEFAULT_MAX_MINUTES }: {
  endpoint: ScheduleEndpoint
  title: string
  maxMinutes?: number
}) {
  const qc = useQueryClient()
  const api = CLIENTS[endpoint.client]
  const queryKey = [endpoint.queryKey]

  const { data, isLoading } = useQuery({
    queryKey,
    queryFn: () => api.get<ScheduleState>(endpoint.readPath),
  })

  const [enabled, setEnabled] = useState(false)
  const [amount, setAmount] = useState('1')
  const [unit, setUnit] = useState<'minutes' | 'hours'>('hours')
  const [err, setErr] = useState<string | null>(null)

  const serverMinutes = data?.interval_minutes

  // 服务端值到达/变化时同步进本地编辑态。依赖只放服务端那个数,不放本地
  // enabled/amount —— 否则用户每敲一个字都会被服务端值覆盖回去。
  useEffect(() => {
    if (serverMinutes === undefined) return
    setEnabled(serverMinutes > 0)
    if (serverMinutes > 0) {
      const s = splitInterval(serverMinutes)
      setAmount(s.amount)
      setUnit(s.unit)
    }
  }, [serverMinutes])

  // 关闭 = 提交 0。这是后端的语义,不是前端发明的。
  const minutes = enabled ? (unit === 'hours' ? Number(amount) * 60 : Number(amount)) : 0

  function validate(): string | null {
    if (!enabled) return null
    if (!Number.isFinite(minutes) || !Number.isInteger(minutes) || minutes <= 0) {
      return 'Enter a whole number greater than zero.'
    }
    if (minutes > maxMinutes) {
      return `Maximum interval is ${maxMinutes} minutes (24 hours).`
    }
    return null
  }

  const save = useMutation({
    mutationFn: async () => {
      const problem = validate()
      if (problem) throw new Error(problem)
      return api.patch(endpoint.writePath, { minutes })
    },
    onSuccess: () => { setErr(null); qc.invalidateQueries({ queryKey }) },
    onError: (e: unknown) => setErr(e instanceof Error ? e.message : 'Failed to save schedule'),
  })

  if (isLoading) {
    return (
      <div className="rounded-xl border border-neutral-200 bg-white px-4 py-6 text-center text-sm text-neutral-400">
        Loading schedule…
      </div>
    )
  }

  return (
    <div className="rounded-xl border border-neutral-200 bg-white p-4">
      <div className="mb-3 flex items-center gap-2">
        <Clock className="h-4 w-4 text-neutral-500" />
        <h3 className="text-sm font-semibold text-neutral-900">{title}</h3>
      </div>

      <div className="flex flex-wrap items-center gap-3">
        <label className="flex items-center gap-2 text-sm text-neutral-700">
          <input type="checkbox" checked={enabled}
                 onChange={(e) => setEnabled(e.target.checked)} className="h-4 w-4" />
          Run automatically
        </label>

        <span className="text-sm text-neutral-500">every</span>
        <input type="number" min={1} value={amount} disabled={!enabled}
               onChange={(e) => setAmount(e.target.value)}
               className="h-9 w-20 rounded-lg border border-neutral-300 bg-white px-2 text-sm disabled:bg-neutral-50 disabled:text-neutral-400" />
        <select value={unit} disabled={!enabled}
                onChange={(e) => setUnit(e.target.value as 'minutes' | 'hours')}
                className="h-9 rounded-lg border border-neutral-300 bg-white px-2 text-sm disabled:bg-neutral-50 disabled:text-neutral-400">
          <option value="minutes">Minutes</option>
          <option value="hours">Hours</option>
        </select>

        <button type="button" disabled={save.isPending}
                onClick={() => { if (!save.isPending) save.mutate() }}
                className={cn('ml-auto flex items-center gap-1.5 rounded-lg bg-[#085E5E] px-3 py-2',
                              'text-sm font-medium text-white hover:bg-[#064A4A] disabled:opacity-50')}>
          {save.isPending && <Loader2 className="h-4 w-4 animate-spin" />}
          Save
        </button>
      </div>

      <div className="mt-3 border-t border-neutral-100 pt-3 text-xs text-neutral-500">
        Next run: {enabled ? formatWhen(data?.next_due_at ?? null) : 'Disabled'}
      </div>

      <p className="mt-2 text-[11px] text-neutral-400">
        Scheduled runs are always incremental. A full reload must be started manually.
      </p>

      {err && <div className="mt-2 rounded-md bg-red-50 px-3 py-2 text-sm text-red-700">{err}</div>}
    </div>
  )
}
```

- [ ] **Step 2: 确认 tsc 门禁不是假的，再看有没有报错**

```bash
cd portal && npx tsc --version
```

Expected: 打印 `Version 6.0.3`（Portal 用 TS 6）。

```bash
cd portal && npx tsc --noEmit --listFiles 2>/dev/null | grep -c "ScheduleCard"
```

Expected: `1`。

⚠️ 本仓库踩过「solution 式 tsconfig 编译 0 个文件、永远绿灯」的假门禁。
上面这条 grep 计数**必须是 1**，是 0 就说明 tsc 根本没看这个文件，绿灯无意义。

```bash
cd portal && npx tsc --noEmit
```

Expected: 无输出、退出码 0。

- [ ] **Step 3: Commit**

```bash
git add portal/src/pages/admin/nc-sync/ScheduleCard.tsx
git commit -m "feat(portal): shared ScheduleCard for NC sync intervals"
```

---

## Task 6: Portal — `NC Sync` 三 tab + 菜单改名

**Files:**
- Create: `portal/src/pages/admin/nc-sync/NcSyncSection.tsx`
- Modify: `portal/src/pages/admin/AdminPanel.tsx`

**Interfaces:**
- Consumes: Task 5 的 `ScheduleCard` / `ScheduleEndpoint`；既有的
  `portal/src/pages/admin/NcPurchaseSyncSection.tsx`（**不移动、不改名、不改内容**）
- Produces: `export function NcSyncSection()`，AdminPanel 在 `section === 'nc_sync'` 时渲染

---

- [ ] **Step 1: 确认三个读路径的真实形状**

```bash
grep -n "interval_minutes\|next_due_at" \
  ../epms-api/app/api/v1/nc_purchase_sync.py \
  ../finance-api/app/api/v1/nc_sync.py \
  ../mdm-api/app/api/v1/erp_mdm.py 2>/dev/null | head -20
```

（在仓库根跑就去掉 `../`。）
Expected: 三个文件都命中。epms 与 finance 在各自 `/status` 的返回里，
mdm 在 `/erp/sync/schedule` 里。与 Task 5 的描述符一致。

- [ ] **Step 2: 写 tab 外壳**

Create `portal/src/pages/admin/nc-sync/NcSyncSection.tsx`：

```tsx
/**
 * NC Sync — 把 NC65 的三类同步(采购单 / 凭证 / 主数据)合并到一个 Admin 菜单下,
 * 每类各自一张间隔配置卡 + 各自的手动触发区块。
 *
 * 三个 tab 打三个不同的服务:
 *   Purchase → epms-api    JV → finance-api    MDM → mdm-api
 * Portal 与 Finance 是两个独立的 Vite 应用、没有共享 UI 包,所以 JV 的手动触发
 * 区块在这里另写一份(Finance 页面上那个弹窗保持不动,打的是同一个端点)。
 *
 * English-only copy per project convention.
 */
import { useState } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { AlertTriangle, DatabaseZap, Loader2 } from 'lucide-react'
import { financeApi } from '@/lib/api'
import { cn } from '@/lib/utils'
import { NcPurchaseSyncSection } from '../NcPurchaseSyncSection'
import { ScheduleCard, type ScheduleEndpoint } from './ScheduleCard'

const FULL_CONFIRM = 'FULL RELOAD'
const inputCls = 'h-9 rounded-lg border border-neutral-300 bg-white px-3 text-sm focus:outline-none focus:ring-2 focus:ring-primary-600'
const primaryBtn = 'flex items-center gap-1.5 rounded-lg bg-[#085E5E] px-3 py-2 text-sm font-medium text-white hover:bg-[#064A4A] disabled:opacity-50'

type Tab = 'purchase' | 'jv' | 'mdm'

const TABS: { key: Tab; label: string }[] = [
  { key: 'purchase', label: 'Purchase Sync' },
  { key: 'jv', label: 'JV Sync' },
  { key: 'mdm', label: 'MDM Sync' },
]

// 三个服务的排期端点。epms 与 finance 把排期放在各自的 /status 里,
// mdm 单开了 /erp/sync/schedule(它的 /sync/status 形状是按 kind 分组的,不能动)。
const ENDPOINTS: Record<Tab, ScheduleEndpoint> = {
  purchase: {
    client: 'epms',
    readPath: '/admin/nc-purchase-sync/status',
    writePath: '/admin/nc-purchase-sync/interval',
    queryKey: 'nc-purchase-sync-status',
  },
  jv: {
    client: 'finance',
    readPath: '/nc-sync/status',
    writePath: '/nc-sync/interval',
    queryKey: 'nc-sync-status',
  },
  mdm: {
    client: 'mdm',
    readPath: '/erp/sync/schedule',
    writePath: '/erp/sync/interval',
    queryKey: 'erp-sync-schedule',
  },
}

interface NcSyncRun {
  id: string; mode: string; status: string
  started_at: string | null; watermark_to: string | null
  vouchers_inserted: number; vouchers_deleted: number
  unmapped_cc_count: number; error: string | null
}
interface NcSyncStatus {
  can_sync: boolean; configured: boolean
  current_run: NcSyncRun | null; last_run: NcSyncRun | null
}

function JvRunSummary({ run, label }: { run: NcSyncRun; label: string }) {
  return (
    <div className="rounded-lg bg-neutral-50 px-3 py-2 text-xs text-neutral-600">
      <span className="font-medium text-neutral-700">{label}:</span>{' '}
      {run.mode} · {run.status}
      {run.started_at && ` · ${run.started_at.slice(0, 16).replace('T', ' ')}`}
      {run.status !== 'running' && (
        <> · inserted {Number(run.vouchers_inserted).toLocaleString()} vouchers
          {run.vouchers_deleted > 0 && `, deleted ${Number(run.vouchers_deleted).toLocaleString()}`}
          {run.unmapped_cc_count > 0 && `, ${Number(run.unmapped_cc_count).toLocaleString()} unmapped CC lines`}
        </>
      )}
      {run.watermark_to && <> · watermark {run.watermark_to}</>}
      {run.error && <div className="mt-1 text-red-600">{run.error}</div>}
    </div>
  )
}

function JvManualSync() {
  const qc = useQueryClient()
  const [mode, setMode] = useState<'incremental' | 'full'>('incremental')
  const [confirm, setConfirm] = useState('')
  const [err, setErr] = useState<string | null>(null)
  const [starting, setStarting] = useState(false)

  // 与 ScheduleCard 共用同一个 queryKey —— 排期字段和 run 状态来自同一个端点,
  // 保存间隔后这里的展示也会跟着刷新。
  const { data: status } = useQuery({
    queryKey: ['nc-sync-status'],
    queryFn: () => financeApi.get<NcSyncStatus>('/nc-sync/status'),
    refetchInterval: (q) => (q.state.data?.current_run ? 2000 : false),
  })
  const running = status?.current_run ?? null

  const start = async () => {
    if (starting) return
    setErr(null); setStarting(true)
    try {
      await financeApi.post('/nc-sync', {
        mode, confirm: mode === 'full' ? confirm : undefined,
      })
      setConfirm('')
      await qc.invalidateQueries({ queryKey: ['nc-sync-status'] })
    } catch (e) {
      setErr(e instanceof Error ? e.message : 'Failed to start sync')
    } finally {
      setStarting(false)
    }
  }

  const canStart = !!status?.configured && !!status?.can_sync && !running && !starting &&
    (mode === 'incremental' || confirm === FULL_CONFIRM)

  if (!status) return <div className="py-6 text-center text-sm text-neutral-400">Loading…</div>
  if (!status.configured) {
    return (
      <div className="rounded-lg border border-amber-200 bg-amber-50 px-3 py-2 text-sm text-amber-800">
        NC voucher sync is not configured. Contact an administrator to set up the NC65 connection.
      </div>
    )
  }

  return (
    <div className="space-y-3">
      {status.last_run && !running && <JvRunSummary run={status.last_run} label="Last sync" />}
      {running ? (
        <div className="flex items-center gap-2 rounded-lg border border-neutral-200 px-3 py-3 text-sm text-neutral-700">
          <Loader2 className="h-4 w-4 animate-spin" />
          Sync in progress ({running.mode})…
        </div>
      ) : (
        <>
          <div className="space-y-2 text-sm">
            <label className="flex items-start gap-2">
              <input type="radio" checked={mode === 'incremental'}
                     onChange={() => setMode('incremental')} className="mt-0.5" />
              <span>
                <span className="font-medium">Incremental</span>
                <span className="block text-xs text-neutral-500">
                  Import only vouchers that are new or changed in NC since the last watermark.
                </span>
              </span>
            </label>
            <label className="flex items-start gap-2">
              <input type="radio" checked={mode === 'full'}
                     onChange={() => setMode('full')} className="mt-0.5" />
              <span>
                <span className="font-medium">Full reload</span>
                <span className="block text-xs text-neutral-500">Re-import every NC voucher.</span>
              </span>
            </label>
          </div>
          {mode === 'full' && (
            <div className="rounded-lg border border-red-200 bg-red-50 px-3 py-2">
              <div className="mb-1 flex items-center gap-1.5 text-xs font-medium text-red-700">
                <AlertTriangle className="h-3.5 w-3.5" />
                Destructive: re-imports every NC voucher. Type {FULL_CONFIRM} to enable.
              </div>
              <input value={confirm} onChange={(e) => setConfirm(e.target.value)}
                     placeholder={FULL_CONFIRM} className={cn(inputCls, 'w-full font-mono')} />
            </div>
          )}
          {err && <div className="rounded-md bg-red-50 px-3 py-2 text-sm text-red-700">{err}</div>}
          <div className="flex justify-end border-t border-neutral-100 pt-3">
            <button onClick={start} disabled={!canStart} className={primaryBtn}>
              {starting ? <Loader2 className="h-4 w-4 animate-spin" /> : <DatabaseZap className="h-4 w-4" />}
              Start Sync
            </button>
          </div>
        </>
      )}
    </div>
  )
}

export function NcSyncSection() {
  const [tab, setTab] = useState<Tab>('purchase')

  return (
    <div>
      <div className="mb-6 border-b border-neutral-100 pb-4">
        <h2 className="text-lg font-semibold text-neutral-900">NC Sync</h2>
        <p className="mt-0.5 text-sm text-neutral-500">
          Import purchase orders, journal vouchers and master data from NC65 — on a schedule or on demand.
        </p>
      </div>

      <div className="mb-4 flex gap-1 border-b border-neutral-200">
        {TABS.map((t) => (
          <button key={t.key} onClick={() => setTab(t.key)}
            className={cn('-mb-px border-b-2 px-3 py-2 text-sm font-medium transition-colors',
              tab === t.key
                ? 'border-[#085E5E] text-[#085E5E]'
                : 'border-transparent text-neutral-500 hover:text-neutral-700')}>
            {t.label}
          </button>
        ))}
      </div>

      {tab === 'purchase' && (
        <div className="max-w-lg space-y-4">
          <ScheduleCard endpoint={ENDPOINTS.purchase} title="Automatic purchase sync" />
          <NcPurchaseSyncSection />
        </div>
      )}

      {tab === 'jv' && (
        <div className="max-w-lg space-y-4">
          <ScheduleCard endpoint={ENDPOINTS.jv} title="Automatic voucher sync" />
          <JvManualSync />
        </div>
      )}

      {tab === 'mdm' && (
        <div className="max-w-lg space-y-4">
          <ScheduleCard endpoint={ENDPOINTS.mdm} title="Automatic master-data sync" />
          <p className="text-sm text-neutral-500">
            A scheduled run imports materials, suppliers and persons in that order.
            Browse and manually sync the mirrored records under ERP MDM.
          </p>
        </div>
      )}
    </div>
  )
}
```

- [ ] **Step 3: 改菜单与渲染分支**

Modify `portal/src/pages/admin/AdminPanel.tsx`：

顶部 import：把
```tsx
import { NcPurchaseSyncSection } from './NcPurchaseSyncSection'
```
改成
```tsx
import { NcSyncSection } from './nc-sync/NcSyncSection'
```

SECTIONS 里：把
```tsx
  { key: 'nc_purchase',  label: 'NC Purchase Sync',     icon: DatabaseZap },
```
改成
```tsx
  { key: 'nc_sync',      label: 'NC Sync',              icon: DatabaseZap },
```

渲染分支里：把
```tsx
          {section === 'nc_purchase' && <NcPurchaseSyncSection />}
```
改成
```tsx
          {section === 'nc_sync'     && <NcSyncSection />}
```

⚠️ `NcPurchaseSyncSection` 现在改由 `NcSyncSection` 引用，AdminPanel 不再直接用它。
改完 tsc 会告诉你有没有残留引用。

- [ ] **Step 4: tsc 通过（先验门禁真实性）**

```bash
cd portal && npx tsc --noEmit --listFiles 2>/dev/null | grep -c "NcSyncSection"
```

Expected: `1`。是 0 就说明 tsc 没看这个文件，下一步的绿灯无意义。

```bash
cd portal && npx tsc --noEmit
```

Expected: 无输出、退出码 0。

- [ ] **Step 5: 构建能过**

```bash
cd portal && npm run build 2>&1 | tail -15
```

Expected: 看到 `✓ built in` 字样，无 error。

- [ ] **Step 6: 正面确认新文案真的进了产物**

```bash
cd portal && grep -rl "Run automatically" dist/assets/*.js | head -1
```

Expected: 打印出一个 `dist/assets/index-*.js` 路径。
（本仓库有过「构建过了但改动没进产物」的先例，所以正面 grep 一次。）

- [ ] **Step 7: Commit**

```bash
git add portal/src/pages/admin/nc-sync/NcSyncSection.tsx portal/src/pages/admin/AdminPanel.tsx
git commit -m "feat(portal): merge NC syncs into a single NC Sync admin section"
```

---

## Task 7: 端到端可达性验证

**Files:** 无新文件（可能新增 `screenshots/`）。

**为什么单独成一个 task：** 本仓库同一条分支上出现过三次「建对了但用户走不到」。
后端测试全绿、tsc 全绿，都**不能**证明管理员在 Portal 里点得到。可达性必须进完成定义。

---

- [ ] **Step 1: 起 dev 栈并确认它挂的是这棵 worktree**

```bash
docker compose -f docker-compose.dev.yml ps
```

Expected: postgres / epms-api / finance-api / mdm-api / portal 都 running。

```bash
for c in uniops_epms_api uniops_mdm_api; do
  echo "$c -> $(docker inspect -f '{{range .Mounts}}{{.Source}} {{end}}' $c 2>/dev/null | tr ' ' '\n' | grep -i uniops | head -3 | tr '\n' ' ')"
done
```

⚠️ 同一个栈的不同服务**可能挂在不同的 worktree 上**。必须逐个确认挂的是
`.worktrees/nc-sync-scheduler`。**排查任何「改了没生效」之前，先证明跑的是你改的那份。**
若挂的是主 checkout，用 `docker-compose.worktree-test.yml` 之类的 override 重挂，
并记得把 gitignored 的 `.env` 一起带上（缺了会 500 全模块瘫、且 health 假绿）。

⚠️ `*-web` 容器显示 `unhealthy` 是**长期既有误报**（healthcheck 走 IPv6、nginx 只听 IPv4），
不是故障，不要为它排查。

- [ ] **Step 2: 迁移跑上（本地 dev 库是陈的）**

```bash
docker compose -f docker-compose.dev.yml exec epms-api python -m alembic upgrade head
```

Expected: 退出码 0。**必须在容器内跑** —— 宿主 `.env` 指向生产库。

```bash
docker exec uniops_postgres psql -U epms -d epms -tAc \
  "select column_name from information_schema.columns where table_name='company_config' and column_name like '%_sync_interval_minutes' order by 1"
```

Expected: 三行 —— `erp_mdm_sync_interval_minutes`、`nc_jv_sync_interval_minutes`、
`nc_purchase_sync_interval_minutes`。

- [ ] **Step 3: 三个排期端点都正面应答**

用一个真 system_admin token（从 dev 登录拿），依次 GET：

```
GET  {epms}/api/v1/admin/nc-purchase-sync/status   → 含 interval_minutes / next_due_at
GET  {finance}/finance/v1/nc-sync/status           → 含 interval_minutes / next_due_at
GET  {mdm}/mdm/v1/erp/sync/schedule                → { interval_minutes, next_due_at }
```

Expected: 三个都返回 200 且含这两个键。

⚠️ **不要用「返回 403 说明端点存在」当证据** —— 本仓库实测过不存在的路径也返回 403。

- [ ] **Step 4: 无头浏览器实际走一遍**

按 `reference_uniops_headless_browser_check` 的方式（`playwright-core` 驱动系统 Chrome、
不下载浏览器包，localStorage 塞 system_admin 登录态）：

1. 打开 Portal 的 Admin Panel
2. 左侧菜单**看到 `NC Sync`**，且**看不到 `NC Purchase Sync`**
3. 点进去，三个 tab（Purchase Sync / JV Sync / MDM Sync）都能切换
4. 三个 tab 都渲染出 ScheduleCard（有 "Run automatically" 复选框与 "Save" 按钮）
5. 在 **JV** tab 勾上开关、填 2 Hours、点 Save
6. **刷新页面**，回到 JV tab：开关仍勾上、值仍是 2 Hours、"Next run" 是一个具体时间
7. 把 JV 的开关**取消**、Save、刷新 —— 显示 "Disabled"

Expected: 七步全过。截图存 `screenshots/`。

第 6、7 步是关键：它们同时证明 PATCH 真的落库、GET 真的读回、
以及 `0 = 关闭` 这条语义端到端是通的。

- [ ] **Step 5: 库里确认写的是 0 而不是别的**

```bash
docker exec uniops_postgres psql -U epms -d epms -tAc \
  "select nc_jv_sync_interval_minutes, erp_mdm_sync_interval_minutes from company_config"
```

Expected: 第 7 步之后 JV 那列是 `0`。

- [ ] **Step 6: `part_status` 修复的正面证据**

```bash
docker exec uniops_postgres psql -U epms -d epms -tAc \
  "select part_status, count(*) from erp_materials group by 1"
```

Expected: 仍是 `2|<数量>`（库里还是 NC 原值，别名修复才有意义）。

然后在无头浏览器里打开 EPMS 的 PR Create → Type 1 → 物料选择器，搜 `CR`。

Expected: **列表非空**。修之前这里必然是 0 行。

⚠️ 本地 `erp_materials` 若为空，先灌几行再测，否则测的是「空表」而不是「过滤器」。

- [ ] **Step 7: 定时器活体证明**

把 JV 间隔设成 5 分钟并开启，然后：

```bash
docker compose -f docker-compose.dev.yml logs -f finance-api | grep -i "sync scheduler"
```

Expected: 启动时看到 `JV sync scheduler started (tick=60s)`；
之后看到调度决策的日志。NC 未配置时 `run_tick` 返回 `not_configured` ——
**这同样算通过**，它证明循环醒着、读到了间隔、走到了判定。

跑完把 JV 间隔改回去（或设 0），不要留着。

- [ ] **Step 8: 三次全量回归的失败集合汇总**

把 Task 2 / 3 的全量回归失败**集合**与改动前基线逐一对照。

Expected: 集合相同，或新增失败都能一一解释。**只比数字会误判。**

- [ ] **Step 9: Commit（如有截图）**

```bash
git add screenshots/ 2>/dev/null && git commit -m "test: reachability evidence for NC Sync admin section" || echo "无截图,跳过"
```

---

## Self-Review

**Spec coverage（Phase A）**

| Spec 节 | 对应 task |
|---|---|
| §4.1 main 上已有什么 | Task 1 Step 1（读它）、Task 2 Step 1（读它） |
| §4.2 既有模式的形状 | Task 2 / 3 照抄 |
| §4.3 本轮新增（两列 + 两个循环 + 端点） | Task 1 / 2 / 3 |
| §4.4 上线即生效的行为变化 | Task 7 Step 7 验证；发布说明不在本计划内 |
| §6.1 `part_status` 向前兼容 | Task 4 + Task 7 Step 6 |
| §8 Portal UI | Task 5 / 6 |
| §9.1 测试 | 各 task 的测试步骤 + Task 7 |
| §10.1 迁移（只有一条，在 epms-api） | Task 1 |
| §11 发布 | 不在本计划内 |

**已知的有意取舍：**
- epms-api 的采购调度器**后端一行不改** —— 它已经在 main 上且工作正常，本轮只补它缺的 UI。
- mdm-api 的 `GET /erp/sync/status` 形状**不动**（有 live 消费方），排期另开 `/sync/schedule`。
- 三个服务共用同一个 `nc_sync_scheduler_enabled` 变量名，但**各自读各自的 settings** ——
  部署时可以按服务分别关。

**Type consistency 已核对：** `resolve_interval_minutes` / `is_due` / `run_tick` 三处签名一致；
`ScheduleEndpoint.client` 的三个取值与 `CLIENTS` 的键一致；`ScheduleState` 的两个字段与
三个后端返回的键名一致（`interval_minutes` / `next_due_at`）。

**跨 task 共享文件检查：** Task 3 与 Task 4 都改 `mdm-api/` 但文件不重叠
（Task 3 碰 models/tasks/api/main/conftest，Task 4 只碰 `crud/erp.py`）——
`tests/conftest.py` 只有 Task 3 改。Task 5 与 Task 6 都在 `portal/src/pages/admin/nc-sync/`
但文件不同，且 Task 6 依赖 Task 5 的导出，顺序不能颠倒。
