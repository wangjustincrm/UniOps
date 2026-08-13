# MRP 周排产 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把 MPS 从月计划改成周计划——需求仍按月，排产落到具体周，产能规则与生产提前期同步改周口径，并按三条业务原则（单品连续生产 / 月不满时周周有产 / 每周尽量单品）排布。

**Architecture:** 新增纯函数 `week_calendar.py` 独占三种周定义的差异，排产引擎只吃周列表；`mps_engine.py` 重写为两体制排布（周数紧张=降序装箱、周数富余=按最低产能摊平）；`plan_month` 换成 `plan_week_start DATE`（三种周模式下唯一无歧义的键）+ 派生归属月列。存量月口径计划一刀切清空。

**Tech Stack:** FastAPI + SQLAlchemy 2.0 async + Alembic（mrp-api，端口 8011）；React 19 + TS + Tailwind + `@uniops/shell`（mrp 前端，端口 5179）；openpyxl（导出）

**Spec:** `docs/superpowers/specs/2026-08-12-mrp-weekly-planning-and-intent-products-design.md`（§2 引擎、§3 周日历、§4 数据模型、§5.1/5.2/5.4 前端）

**依赖**：本计划与 `2026-08-12-mrp-intent-products.md` 相互独立，可任意先后。若意向产品先落地，本计划的迁移编号从 `mrp10` 起；若本计划先落地，则本计划用 `mrp09`、意向产品用 `mrp10`。**动手第一步必看 `alembic heads` 的真实链尾，别照抄本文的编号。**

## Global Constraints

- **计划口径恒为 KG**：`PLANNING_UOM='KG'`。产能规则的 `uom` 已经钉死 KG（Phase 1B T0 的教训：KG/MT 选择器造成过 1000× 静默错），周化后继续钉死
- **金额/数量一律 `Decimal`**，禁止 float；Pydantic 把 Decimal 序列化成字符串，前端必须 `Number()`
- **周的规范键是 `plan_week_start`（`datetime.date`，周首日）**，不是 `'YYYY-Www'` 字符串——`month_fixed` 模式编不出 ISO 周号
- **保质期校验按真实日期差**，禁止用「4.33 周/月」近似（18 个月窗口上会累积到整周级偏差）
- **前端 user-facing 文案全英文**；UI 样式以 EPMS 为模板，用 `@uniops/shell` 原语，浮层 `createPortal` 到 body
- **权限**：不新增权限键。参数与周例外走 `mrp.param.write`，排产走 `mrp.run.execute`，读走 `mrp.report.view`
- **`auth_headers` fixture**：本计划测试都用它，但 conftest 现在只有 `admin_token`。第一个任务先补：`@pytest_asyncio.fixture async def auth_headers(admin_token): return {"Authorization": f"Bearer {admin_token}"}`（若意向产品计划已先加过则跳过）
- **测试库禁止并发**：本计划专用 `mrp_weekly_test`（★库名必须**以 `_test` 结尾**——conftest 有 DROP SCHEMA 安全护栏，`mrp_test_xxx` 这种命名会被直接拒绝）
- **跑测命令**（宿主机，容器内无 pytest）：
  ```bash
  PGPW=$(docker inspect uniops_postgres --format '{{range .Config.Env}}{{println .}}{{end}}' | grep POSTGRES_PASSWORD | cut -d= -f2)
  cd mrp-api && JWT_SECRET_KEY=test-secret TEST_PG_PASSWORD="$PGPW" \
    TEST_MRP_DB=mrp_weekly_test ALLOWED_ORIGINS='["http://localhost:5179"]' \
    python -m pytest tests -q
  ```
- **基线**：mrp-api 现有 **167 passed**。本计划会**重写** `test_mps_engine.py` 与 `test_mps_api.py` 里的月口径用例，所以总数会变——**Task 6 之后以「0 failed」+「月口径用例已全部改写为周口径且无一被删空」为准，不再以 167 为数字门槛**
- **前端门禁**：`cd mrp && npx tsc -p tsconfig.app.json --noEmit` 只许剩 baseUrl 一条；`--listFiles` 正面确认改动文件被编译

---

## File Structure

| 文件 | 职责 |
| --- | --- |
| `mrp-api/app/services/week_calendar.py` | **唯一**知道三种周定义差异的地方：月→周列表、周→归属月、周位移、显示标签 |
| `mrp-api/app/services/mps_engine.py` | 重写：两体制周排布 + lead 周 + pre-build + 保质期。纯函数，无 DB |
| `mrp-api/app/models/params.py` | `MrpPlanningParam`（key-value 规划参数） |
| `mrp-api/app/models/capacity.py` | 加 `MrpCapacityException` |
| `mrp-api/app/api/v1/params.py` | `/params` 读写（本期只有 `week_calendar_mode`） |
| `mrp-api/app/api/v1/capacity.py` | 加 `min_output_qty` 约束类型 + 周例外 CRUD |
| `mrp-api/app/api/v1/mps.py` | 改写为周口径（generate / get / recalculate / adjust / release） |
| `mrp-api/app/services/mps_export.py` | 导出改周列 + 月分组表头 |
| `mrp-api/alembic/versions/mrp10a_planning_params_and_exceptions.py` | 纯新增两张表（非破坏性，可先合） |
| `mrp-api/alembic/versions/mrp10b_weekly_columns.py` | 破坏性迁移：清月口径计划 + 改列 |
| `mrp/src/pages/mps/ProductionMatrix.tsx` | 双层表头 + 月折叠 |
| `mrp/src/pages/mps/AdjustDrawer.tsx` | 改生产周 + 合并到相邻周 |
| `mrp/src/pages/capacity/CapacityRulesPage.tsx` | 周文案 + 最低产能 + Planning Calendar + Week Exceptions |

---

### Task 1: 周日历纯函数模块

**Files:**
- Create: `mrp-api/app/services/week_calendar.py`
- Test: `mrp-api/tests/test_week_calendar.py`

**Interfaces:**
- Consumes: 无
- Produces:
  - `WEEK_MODES = ('iso_thursday', 'iso_first_day', 'month_fixed')`
  - `weeks_of_month(month: str, mode: str) -> list[date]`（该月归属的周首日列表，升序）
  - `owning_month(week_start: date, mode: str) -> str`（`'YYYY-MM'`）
  - `week_label(week_start: date, mode: str) -> str`
  - `shift_weeks(week_start: date, delta: int, mode: str) -> date`
  - `week_start_of(day: date, mode: str) -> date`

- [ ] **Step 1: 写失败测试**

`mrp-api/tests/test_week_calendar.py`：

```python
from datetime import date

import pytest

from app.services.week_calendar import (
    owning_month, shift_weeks, week_label, week_start_of, weeks_of_month,
)


class TestIsoThursday:
    """跨月周归【周四所在月】。2026-07-27(周一)~08-02(周日) 的周四是 07-30 → 归 7 月。"""

    def test_straddling_week_goes_to_the_month_holding_its_thursday(self):
        assert owning_month(date(2026, 7, 27), "iso_thursday") == "2026-07"
        # 2026-08-31(周一)~09-06 的周四是 09-03 → 归 9 月
        assert owning_month(date(2026, 8, 31), "iso_thursday") == "2026-09"

    def test_weeks_of_month_are_mondays_in_order(self):
        weeks = weeks_of_month("2026-08", "iso_thursday")
        assert all(w.weekday() == 0 for w in weeks)
        assert weeks == sorted(weeks)
        assert all(owning_month(w, "iso_thursday") == "2026-08" for w in weeks)

    def test_every_month_has_four_or_five_weeks(self):
        for m in range(1, 13):
            assert len(weeks_of_month(f"2026-{m:02d}", "iso_thursday")) in (4, 5)

    def test_weeks_tile_the_year_without_gap_or_overlap(self):
        """一年 52/53 周，逐月拼接后不重不漏。"""
        all_weeks = [w for m in range(1, 13)
                     for w in weeks_of_month(f"2026-{m:02d}", "iso_thursday")]
        assert len(all_weeks) == len(set(all_weeks))
        for a, b in zip(all_weeks, all_weeks[1:]):
            assert (b - a).days == 7


class TestIsoFirstDay:
    """跨月周归【含月初那一天】的月：2026-07-27~08-02 含 8/1 → 归 8 月。"""

    def test_straddling_week_goes_to_the_new_month(self):
        assert owning_month(date(2026, 7, 27), "iso_first_day") == "2026-08"

    def test_non_straddling_week_is_unambiguous(self):
        assert owning_month(date(2026, 8, 10), "iso_first_day") == "2026-08"


class TestMonthFixed:
    """每月 1 号起每 7 天一周，永不跨月。"""

    def test_weeks_start_on_the_first_and_step_seven_days(self):
        assert weeks_of_month("2026-08", "month_fixed") == [
            date(2026, 8, 1), date(2026, 8, 8), date(2026, 8, 15),
            date(2026, 8, 22), date(2026, 8, 29),
        ]

    def test_last_week_is_the_short_remainder(self):
        # 2026-02 有 28 天 → 正好 4 周，无零头
        assert len(weeks_of_month("2026-02", "month_fixed")) == 4

    def test_owning_month_never_crosses(self):
        for w in weeks_of_month("2026-08", "month_fixed"):
            assert owning_month(w, "month_fixed") == "2026-08"


class TestShiftWeeks:
    def test_iso_shift_is_plain_seven_day_arithmetic(self):
        assert shift_weeks(date(2026, 8, 10), -4, "iso_thursday") == date(2026, 7, 13)
        assert shift_weeks(date(2026, 8, 10), 0, "iso_thursday") == date(2026, 8, 10)

    def test_month_fixed_shift_lands_on_a_real_week_start(self):
        """周长不恒为 7 天，所以不能直接减 7×n —— 必须落在该模式真实存在的周首日上。"""
        got = shift_weeks(date(2026, 8, 1), -1, "month_fixed")
        assert got in weeks_of_month("2026-07", "month_fixed")
        assert got == weeks_of_month("2026-07", "month_fixed")[-1]


class TestLabels:
    def test_iso_label_carries_week_number_and_date_range(self):
        assert week_label(date(2026, 8, 3), "iso_thursday") == "2026-W32 · Aug 3–9"

    def test_month_fixed_label_is_ordinal_within_month(self):
        assert week_label(date(2026, 8, 8), "month_fixed") == "Aug W2 · Aug 8–14"


def test_unknown_mode_is_rejected_not_silently_defaulted():
    with pytest.raises(ValueError):
        weeks_of_month("2026-08", "fiscal_445")
```

- [ ] **Step 2: 运行，确认失败**

Run: `python -m pytest tests/test_week_calendar.py -q`
Expected: FAIL —— `ModuleNotFoundError: No module named 'app.services.week_calendar'`

- [ ] **Step 3: 实现模块**

`mrp-api/app/services/week_calendar.py`。实现要点：

- `iso_thursday`：`owning_month(w)` = `(w + 3 days).strftime('%Y-%m')`；`weeks_of_month(m)` = 扫该月首日所在周到月末所在周，保留 `owning_month == m` 的
- `iso_first_day`：`owning_month(w)` = 若 `w` 与 `w+6` 不同月则取 `w+6` 的月，否则取 `w` 的月
- `month_fixed`：`weeks_of_month(m)` = `[1, 8, 15, 22, 29...]` 中 ≤ 该月天数的日期；`owning_month(w)` = `w` 自己的月
- `shift_weeks`：ISO 两模式 = `w + timedelta(weeks=delta)`；`month_fixed` 必须**在真实周序列上走步**（跨月时取上/下个月的周列表尾/首），不能做 7×n 算术
- 未知 mode 一律 `raise ValueError`，**不要静默回落默认值**——静默回落会让一个拼错的配置值悄悄改变全厂排产

模块 docstring 里写明：本模块是三种模式差异的唯一所在地，引擎与 API 只接收周列表。

- [ ] **Step 4: 运行测试，确认通过**

Run: `python -m pytest tests/test_week_calendar.py -q`
Expected: PASS（全部）

- [ ] **Step 5: Commit**

```bash
git add mrp-api/app/services/week_calendar.py mrp-api/tests/test_week_calendar.py
git commit -m "feat(mrp): week calendar module with three configurable week definitions"
```

---

### Task 2: 规划参数表与端点

**Files:**
- Create: `mrp-api/app/models/params.py`
- Create: `mrp-api/app/api/v1/params.py`
- Modify: `mrp-api/app/models/__init__.py`、`mrp-api/app/api/v1/__init__.py`
- Test: `mrp-api/tests/test_params.py`
- 迁移在 Task 6 统一写（本任务只加模型，测试暂时会因缺表失败——**所以本任务的迁移建表部分提前放进本任务的临时迁移不可取**，见下）

**关于顺序**：为避免"模型有表没有"的中间态，本任务**连同建表一起做**，但只建 `mrp_planning_params` 与 `mrp_capacity_exceptions` 两张**纯新增**表，放进迁移 `mrp10a_planning_params_and_exceptions`。Task 6 的破坏性改列另起一支 `mrp10b`。这样非破坏性的部分可以先合、先测。

**Interfaces:**
- Consumes: Task 1 的 `WEEK_MODES`
- Produces:
  - `MrpPlanningParam`（`key` PK, `value` JSONB, `updated_by`, `updated_at`）
  - `get_param(db, key, default)` / `set_param(db, key, value, actor)`
  - `GET /api/v1/params` → `{"week_calendar_mode": "iso_thursday"}`
  - `PUT /api/v1/params/week_calendar_mode` body `{"value": "month_fixed"}`

- [ ] **Step 1: 写失败测试**

```python
import pytest


@pytest.mark.asyncio
async def test_week_calendar_mode_defaults_to_iso_thursday(client, auth_headers):
    r = await client.get("/api/v1/params", headers=auth_headers)
    assert r.status_code == 200
    assert r.json()["week_calendar_mode"] == "iso_thursday"


@pytest.mark.asyncio
async def test_week_calendar_mode_round_trips(client, auth_headers):
    r = await client.put("/api/v1/params/week_calendar_mode",
                         json={"value": "month_fixed"}, headers=auth_headers)
    assert r.status_code == 200
    assert (await client.get("/api/v1/params",
                             headers=auth_headers)).json()["week_calendar_mode"] == "month_fixed"


@pytest.mark.asyncio
async def test_unknown_week_mode_is_rejected(client, auth_headers):
    r = await client.put("/api/v1/params/week_calendar_mode",
                         json={"value": "fiscal_445"}, headers=auth_headers)
    assert r.status_code == 422


@pytest.mark.asyncio
async def test_params_write_requires_param_write_permission(client, non_admin_token, monkeypatch):
    # 必须照 tests/test_permission_gates.py 的 `_deny_everything` 惯例：mrp_test 库里
    # 没有 identity 的 role_permissions/role_defs/user_roles 表，直接拿 non_admin_token
    # 打真实 gate 会炸在查表而不是 403；而 admin_token 走 system_admin 快速通道，
    # 根本不会触到权限键，用它压根测不出门禁。
    _deny_everything(monkeypatch)
    r = await client.put("/api/v1/params/week_calendar_mode", json={"value": "month_fixed"},
                         headers={"Authorization": f"Bearer {non_admin_token}"})
    assert r.status_code == 403
```

`_deny_everything` 从 `tests/test_permission_gates.py` 导入或照抄。

- [ ] **Step 2: 运行，确认失败**

Run: `python -m pytest tests/test_params.py -q`
Expected: FAIL —— 404

- [ ] **Step 3: 模型 + 迁移**

`mrp-api/app/models/params.py`：

```python
"""Planning parameters (key-value).

Deliberately generic: Phase 1C's raw_material_loss_rate / packaging_loss_rate
land in this same table rather than growing another one-row config table.
"""
import uuid
from datetime import datetime

from sqlalchemy import DateTime, String
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class MrpPlanningParam(Base):
    __tablename__ = "mrp_planning_params"

    key: Mapped[str] = mapped_column(String(50), primary_key=True)
    value: Mapped[dict] = mapped_column(JSONB)
    updated_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
```

迁移 `mrp10a_planning_params_and_exceptions.py`（`down_revision` = 实测链尾）建两张表：`mrp_planning_params`，以及 `mrp_capacity_exceptions`（`id, week_start DATE NOT NULL, scope_type, scope_ref, constraint_type, limit_value NUMERIC(18,3), uom, reason TEXT, is_active BOOL DEFAULT true`，唯一约束 `(week_start, scope_type, scope_ref, constraint_type)`）。同时 `INSERT` 默认参数行 `week_calendar_mode = '"iso_thursday"'::jsonb`。

- [ ] **Step 4: 端点**

`mrp-api/app/api/v1/params.py`：`GET /params` 返回全部参数扁平字典；`PUT /params/{key}` 只接受白名单 key，`week_calendar_mode` 的值必须 ∈ `WEEK_MODES`（否则 422）。读用 `mrp.report.view`，写用 `mrp.param.write`。

- [ ] **Step 5: 运行测试，确认通过**

Run: `python -m pytest tests/test_params.py tests/test_alembic_revisions.py -q`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add mrp-api/app/models/params.py mrp-api/app/api/v1/params.py \
        mrp-api/app/models/__init__.py mrp-api/app/api/v1/__init__.py \
        mrp-api/alembic/versions/mrp10a_planning_params_and_exceptions.py \
        mrp-api/tests/test_params.py
git commit -m "feat(mrp): planning params table + week calendar mode setting"
```

---

### Task 3: 最低产能约束与周例外

**Files:**
- Modify: `mrp-api/app/models/capacity.py`（加 `MrpCapacityException`）
- Modify: `mrp-api/app/api/v1/capacity.py`（`min_output_qty` + 例外 CRUD）
- Modify: `mrp-api/app/services/capacity.py`（生效规则解析时套用周例外）
- Test: `mrp-api/tests/test_capacity.py`

**Interfaces:**
- Consumes: Task 2 建的 `mrp_capacity_exceptions` 表
- Produces:
  - `resolve_limits_for_week(db, week_start) -> CapacityLimits`（例外覆盖常规规则）
  - `CapacityLimits` 扩展为 `(max_sku_count, max_output_qty, min_output_qty)`
  - `GET/POST/PATCH/DELETE /api/v1/capacity/exceptions`

- [ ] **Step 1: 写失败测试**

```python
@pytest.mark.asyncio
async def test_min_output_qty_is_an_accepted_constraint_type(client, auth_headers):
    r = await client.post("/api/v1/capacity/rules", headers=auth_headers, json={
        "scope_type": "factory", "constraint_type": "min_output_qty",
        "limit_value": "20000", "uom": "KG", "effective_from": "2026-01-01",
    })
    assert r.status_code == 201, r.text


@pytest.mark.asyncio
async def test_min_output_above_max_output_is_rejected(client, auth_headers):
    await client.post("/api/v1/capacity/rules", headers=auth_headers, json={
        "scope_type": "factory", "constraint_type": "max_output_qty",
        "limit_value": "40000", "uom": "KG", "effective_from": "2026-01-01",
    })
    r = await client.post("/api/v1/capacity/rules", headers=auth_headers, json={
        "scope_type": "factory", "constraint_type": "min_output_qty",
        "limit_value": "50000", "uom": "KG", "effective_from": "2026-01-01",
    })
    assert r.status_code == 422
    assert "min" in r.json()["detail"].lower()


@pytest.mark.asyncio
async def test_week_exception_overrides_the_standing_rule(client, auth_headers, db_session):
    from datetime import date
    from app.services.capacity import resolve_limits_for_week
    await client.post("/api/v1/capacity/rules", headers=auth_headers, json={
        "scope_type": "factory", "constraint_type": "max_output_qty",
        "limit_value": "40000", "uom": "KG", "effective_from": "2026-01-01",
    })
    await client.post("/api/v1/capacity/exceptions", headers=auth_headers, json={
        "week_start": "2026-08-10", "scope_type": "factory",
        "constraint_type": "max_output_qty", "limit_value": "0", "uom": "KG",
        "reason": "annual maintenance",
    })
    normal = await resolve_limits_for_week(db_session, date(2026, 8, 3))
    shut = await resolve_limits_for_week(db_session, date(2026, 8, 10))
    assert str(normal.max_output_qty) == "40000.000"
    assert str(shut.max_output_qty) == "0.000"


@pytest.mark.asyncio
async def test_inactive_exception_is_ignored(client, auth_headers, db_session):
    from datetime import date
    from app.services.capacity import resolve_limits_for_week
    await client.post("/api/v1/capacity/rules", headers=auth_headers, json={
        "scope_type": "factory", "constraint_type": "max_output_qty",
        "limit_value": "40000", "uom": "KG", "effective_from": "2026-01-01",
    })
    exc = (await client.post("/api/v1/capacity/exceptions", headers=auth_headers, json={
        "week_start": "2026-09-07", "scope_type": "factory",
        "constraint_type": "max_output_qty", "limit_value": "0", "uom": "KG",
        "reason": "cancelled",
    })).json()
    await client.patch(f"/api/v1/capacity/exceptions/{exc['id']}",
                       json={"is_active": False}, headers=auth_headers)
    assert str((await resolve_limits_for_week(db_session,
                                              date(2026, 9, 7))).max_output_qty) == "40000.000"
```

- [ ] **Step 2: 运行，确认失败**

Run: `python -m pytest tests/test_capacity.py -q -k "min_output or exception"`
Expected: FAIL

- [ ] **Step 3: 实现**

1. `models/capacity.py` 加 `MrpCapacityException`（列同 Task 2 建的表）
2. `api/v1/capacity.py`：`constraint_type` 白名单加 `min_output_qty`；创建/更新时做跨规则校验（同 scope 下生效期重叠的 `min_output_qty` 不得大于 `max_output_qty`，否则 422，detail 写明白）；加例外 CRUD 四个端点
3. `services/capacity.py` 新增 `resolve_limits_for_week(db, week_start)`：先取该周生效的常规规则，再用同周同约束的 active 例外覆盖

**注意**：`uom` 继续钉死 `'KG'`——Phase 1B 已因为 KG/MT 选择器踩过 1000× 静默错，周化不是放开它的时机。

- [ ] **Step 4: 运行测试，确认通过**

Run: `python -m pytest tests/test_capacity.py -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add mrp-api/app/models/capacity.py mrp-api/app/api/v1/capacity.py \
        mrp-api/app/services/capacity.py mrp-api/tests/test_capacity.py
git commit -m "feat(mrp): minimum weekly output constraint + per-week capacity exceptions"
```

---

### Task 4: 周排产引擎（两体制核心）

**Files:**
- Modify: `mrp-api/app/services/mps_engine.py`（重写排布部分）
- Test: `mrp-api/tests/test_mps_engine.py`（月口径用例改写为周口径）

**Interfaces:**
- Consumes: Task 1 的 `weeks_of_month`、Task 3 的 `CapacityLimits(max_sku_count, max_output_qty, min_output_qty)`
- Produces:
  - `pack_bucket(items: list[BucketItem], weeks: list[date], limits: CapacityLimits) -> list[WeeklyLine]`
  - `BucketItem(material_code: str, demand_month: str, qty: Decimal)`
  - `WeeklyLine(material_code, demand_month, plan_week_start, qty, ...)`

**这是整个计划的心脏。** 先只做单桶内排布（不含 lead / pre-build / 保质期，那是 Task 5），这样黄金用例能在最小面上验证。

- [ ] **Step 1: 写失败测试——先写黄金用例**

```python
from datetime import date
from decimal import Decimal

from app.services.mps_engine import BucketItem, CapacityLimits, pack_bucket

WEEKS = [date(2026, 8, 3), date(2026, 8, 10), date(2026, 8, 17), date(2026, 8, 24)]


def _items(**kv):
    return [BucketItem(code, "2026-08", Decimal(str(q))) for code, q in kv.items()]


def _by_week(lines):
    out = {}
    for l in lines:
        out.setdefault(l.plan_week_start, []).append((l.material_code, str(l.qty)))
    return {w: sorted(v) for w, v in out.items()}


def test_golden_case_from_the_business_owner():
    """A60 B20 C30 D30, cap 40/week, 4 weeks — the plan the planner drew by hand.

    W1 A40 | W2 A20+B20 | W3 C30 | W4 D30
    """
    limits = CapacityLimits(max_sku_count=None, max_output_qty=Decimal("40"),
                            min_output_qty=Decimal("20"))
    lines = pack_bucket(_items(A=60, B=20, C=30, D=30), WEEKS, limits)
    assert _by_week(lines) == {
        WEEKS[0]: [("A", "40")],
        WEEKS[1]: [("A", "20"), ("B", "20")],
        WEEKS[2]: [("C", "30")],
        WEEKS[3]: [("D", "30")],
    }


def test_a_product_never_splits_when_it_fits_in_one_week():
    """朴素贪心会把 C 切成 20+10 —— 那既多一次换线又让 W2 变三个品。"""
    limits = CapacityLimits(None, Decimal("40"), Decimal("20"))
    lines = pack_bucket(_items(A=60, C=30), WEEKS, limits)
    c_weeks = {l.plan_week_start for l in lines if l.material_code == "C"}
    assert len(c_weeks) == 1


class TestSpreadRegime:
    """周数富余：摊到最低产能就停，剩下的周空着（D6：别为了填日历烧能源）。"""

    limits = CapacityLimits(None, Decimal("40"), Decimal("20"))

    def test_single_product_at_exactly_min_output_stays_in_one_week(self):
        lines = pack_bucket(_items(A=20), WEEKS, self.limits)
        assert len(lines) == 1
        assert str(lines[0].qty) == "20"

    def test_single_product_below_min_output_is_clamped_to_one_week(self):
        lines = pack_bucket(_items(A=10), WEEKS, self.limits)
        assert len(lines) == 1
        assert str(lines[0].qty) == "10"

    def test_single_product_at_twice_min_output_spreads_over_two_weeks(self):
        lines = pack_bucket(_items(A=40), WEEKS, self.limits)
        assert sorted(str(l.qty) for l in lines) == ["20", "20"]
        assert len({l.plan_week_start for l in lines}) == 2

    def test_capacity_floor_wins_over_min_output(self):
        """q=100, cap=40, min=50 → need_weeks=3 胜过 floor(100/50)=2。"""
        limits = CapacityLimits(None, Decimal("40"), Decimal("50"))
        lines = pack_bucket(_items(A=100), WEEKS, limits)
        assert len({l.plan_week_start for l in lines}) == 3
        assert all(Decimal(str(l.qty)) <= Decimal("40") for l in lines)

    def test_spare_weeks_go_to_the_heaviest_product(self):
        lines = pack_bucket(_items(A=60, B=20), WEEKS, self.limits)
        a_weeks = {l.plan_week_start for l in lines if l.material_code == "A"}
        b_weeks = {l.plan_week_start for l in lines if l.material_code == "B"}
        assert len(a_weeks) == 3 and len(b_weeks) == 1
        assert all(str(l.qty) == "20" for l in lines)


class TestPrinciples:
    """P1/P2/P3 的性质断言，跑一批构造输入。"""

    limits = CapacityLimits(max_sku_count=2, max_output_qty=Decimal("40"),
                            min_output_qty=Decimal("20"))
    CASES = [
        {"A": 60, "B": 20, "C": 30, "D": 30},
        {"A": 100, "B": 15},
        {"A": 20},
        {"A": 35, "B": 35, "C": 35},
        {"A": 5, "B": 5, "C": 5, "D": 5},
        {"A": 160},
    ]

    def test_p1_each_product_occupies_a_contiguous_run(self):
        for case in self.CASES:
            lines = pack_bucket(_items(**case), WEEKS, self.limits)
            for code in case:
                idx = sorted(WEEKS.index(l.plan_week_start)
                             for l in lines if l.material_code == code)
                assert idx == list(range(idx[0], idx[0] + len(idx))), (case, code, idx)

    def test_p2_no_empty_week_while_an_earlier_week_could_have_been_thinned(self):
        for case in self.CASES:
            lines = pack_bucket(_items(**case), WEEKS, self.limits)
            used = {l.plan_week_start for l in lines}
            if len(used) == len(WEEKS):
                continue
            for l in lines:
                assert Decimal(str(l.qty)) < 2 * self.limits.min_output_qty, (case, l)

    def test_p3_weekly_sku_count_never_exceeds_the_hard_limit(self):
        for case in self.CASES:
            lines = pack_bucket(_items(**case), WEEKS, self.limits)
            per_week = {}
            for l in lines:
                per_week.setdefault(l.plan_week_start, set()).add(l.material_code)
            assert all(len(v) <= 2 for v in per_week.values()), case

    def test_nothing_is_silently_lost(self):
        for case in self.CASES:
            lines = pack_bucket(_items(**case), WEEKS, self.limits)
            for code, q in case.items():
                planned = sum((Decimal(str(l.qty)) for l in lines
                               if l.material_code == code), Decimal("0"))
                gap = sum((Decimal(str(l.qty)) for l in lines
                           if l.material_code == code and l.capacity_gap), Decimal("0"))
                assert planned == Decimal(str(q)), (case, code, planned)
                del gap  # 缺口行也计入总量，只是带标记
```

- [ ] **Step 2: 运行，确认失败**

Run: `python -m pytest tests/test_mps_engine.py -q -k "golden or Spread or Principles"`
Expected: FAIL —— `ImportError: cannot import name 'pack_bucket'`

- [ ] **Step 3: 实现 `pack_bucket`**

按 spec §2.1–2.3：

```
need_weeks(p) = ceil(q / cap)          # cap 为 None 时视作 1
if Σ need_weeks >= len(weeks):   紧张体制
    按 q 降序（同量按 code 稳定排序）：
        q > cap  → 从最早未满周起逐周填满 cap，余量落紧邻下一周
        q <= cap → 找最早一个「剩余容量 >= q 且该周品种数 < max_sku_count」的周整体放入；
                    找不到 → 另起空周；再找不到 → 标 capacity_gap
else:                            富余体制
    weeks(p)      = need_weeks(p)
    cap_weeks(p)  = max(need_weeks(p), floor(q / min_out), 1)      # min_out 为 None → cap_weeks = need_weeks
    按周均负荷降序把剩余空闲周逐个 +1 给产品，直到 cap_weeks 或周用完
    每个产品在其连续周块内均分 q（末周吸收除不尽的尾差，保证求和精确等于 q）
```

**均分的尾差必须落在最后一周**（`q - 已分配` 而不是四舍五入每周），否则 `test_nothing_is_silently_lost` 会因为分币误差挂——这正是它存在的理由。

- [ ] **Step 4: 运行测试，确认通过**

Run: `python -m pytest tests/test_mps_engine.py -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add mrp-api/app/services/mps_engine.py mrp-api/tests/test_mps_engine.py
git commit -m "feat(mrp): weekly bucket packing engine (tight + spread regimes)"
```

---

### Task 5: lead 周、pre-build 与保质期

**Files:**
- Modify: `mrp-api/app/services/mps_engine.py`
- Test: `mrp-api/tests/test_mps_engine.py`

**Interfaces:**
- Consumes: Task 4 的 `pack_bucket`、Task 1 的 `weeks_of_month`/`shift_weeks`/`owning_month`、`mps_engine` 现有的 `DemandItem(material_code, demand_month, qty)`（沿用不改）
- Produces: `generate_mps(demands, limits_for_week, shelf_life_months, safety_margin_fraction, lead_weeks, current_week, mode) -> list[WeeklyLine]`
  - `WeeklyLine` 字段：`material_code, demand_month, plan_week_start, plan_week_month, qty, is_prebuild, weeks_early, prebuild_reason, shelf_life_ok, capacity_gap, locked, lead_shortfall`

- [ ] **Step 1: 写失败测试**

```python
def test_lead_zero_reproduces_no_shift():
    """lead=0 时目标周就是需求月末周，一步都不许提前。"""
    lines = generate_mps(
        demands=[DemandItem("A", "2026-10", Decimal("30"))],
        limits_for_week=lambda w: CapacityLimits(None, Decimal("40"), Decimal("20")),
        shelf_life_months={"A": 24}, safety_margin_fraction=Decimal("0.3333"),
        lead_weeks=0, current_week=date(2026, 8, 3), mode="iso_thursday",
    )
    assert {l.plan_week_start for l in lines} == {weeks_of_month("2026-10", "iso_thursday")[-1]}
    assert all(l.weeks_early == 0 for l in lines)


def test_lead_four_weeks_moves_the_target_back_four_weeks():
    target = shift_weeks(weeks_of_month("2026-10", "iso_thursday")[-1], -4, "iso_thursday")
    lines = generate_mps(
        demands=[DemandItem("A", "2026-10", Decimal("30"))],
        limits_for_week=lambda w: CapacityLimits(None, Decimal("40"), Decimal("20")),
        shelf_life_months={"A": 24}, safety_margin_fraction=Decimal("0.3333"),
        lead_weeks=4, current_week=date(2026, 8, 3), mode="iso_thursday",
    )
    assert {l.plan_week_start for l in lines} == {target}


def test_lead_clamped_to_current_week_flags_shortfall():
    lines = generate_mps(
        demands=[DemandItem("A", "2026-08", Decimal("30"))],
        limits_for_week=lambda w: CapacityLimits(None, Decimal("40"), Decimal("20")),
        shelf_life_months={"A": 24}, safety_margin_fraction=Decimal("0.3333"),
        lead_weeks=12, current_week=date(2026, 8, 24), mode="iso_thursday",
    )
    assert all(l.plan_week_start >= date(2026, 8, 24) for l in lines)
    assert any(l.lead_shortfall for l in lines)


def test_weeks_early_counts_prebuild_only_not_the_lead_itself():
    """lead 造成的提前不算 weeks_early，否则每行都写着"提前 4 周"，告警就废了。"""
    lines = generate_mps(
        demands=[DemandItem("A", "2026-10", Decimal("30"))],
        limits_for_week=lambda w: CapacityLimits(None, Decimal("40"), Decimal("20")),
        shelf_life_months={"A": 24}, safety_margin_fraction=Decimal("0.3333"),
        lead_weeks=4, current_week=date(2026, 8, 3), mode="iso_thursday",
    )
    assert all(l.weeks_early == 0 and not l.is_prebuild for l in lines)


def test_overflow_moves_earlier_and_marks_prebuild():
    """需求超过目标月总产能 → 向更早的周溢出，标 is_prebuild 与 weeks_early。"""
    lines = generate_mps(
        demands=[DemandItem("A", "2026-10", Decimal("300"))],
        limits_for_week=lambda w: CapacityLimits(None, Decimal("40"), Decimal("20")),
        shelf_life_months={"A": 24}, safety_margin_fraction=Decimal("0.3333"),
        lead_weeks=0, current_week=date(2026, 6, 1), mode="iso_thursday",
    )
    assert sum(Decimal(str(l.qty)) for l in lines) == Decimal("300")
    assert any(l.is_prebuild and l.weeks_early > 0 for l in lines)


def test_shelf_life_uses_real_date_difference_not_4_33_weeks_per_month():
    """保质期 3 个月、安全余量 1/3 → 允许提前 ≈ 61 天。
    62 天前的那一周必须被判为不可用，而"3×4.33=13 周=91 天"的近似会放它过去。"""
    lines = generate_mps(
        demands=[DemandItem("A", "2026-10", Decimal("400"))],
        limits_for_week=lambda w: CapacityLimits(None, Decimal("40"), Decimal("20")),
        shelf_life_months={"A": 3}, safety_margin_fraction=Decimal("0.3333"),
        lead_weeks=0, current_week=date(2026, 1, 5), mode="iso_thursday",
    )
    demand_start = date(2026, 10, 1)
    for l in lines:
        if not l.capacity_gap:
            assert (demand_start - l.plan_week_start).days <= 62, l


def test_missing_shelf_life_means_never_movable():
    lines = generate_mps(
        demands=[DemandItem("A", "2026-10", Decimal("300"))],
        limits_for_week=lambda w: CapacityLimits(None, Decimal("40"), Decimal("20")),
        shelf_life_months={}, safety_margin_fraction=Decimal("0.3333"),
        lead_weeks=0, current_week=date(2026, 1, 5), mode="iso_thursday",
    )
    assert any(l.capacity_gap for l in lines)
    assert all(not l.is_prebuild for l in lines)
```

- [ ] **Step 2: 运行，确认失败**

Run: `python -m pytest tests/test_mps_engine.py -q -k "lead or shelf or overflow or weeks_early"`
Expected: FAIL

- [ ] **Step 3: 实现流水线**

按 spec §2.0 的六步：位移 → 分桶（按目标周归属月）→ 桶内 `pack_bucket` → 溢出向前 → 保质期校验 → 缺口标记。

保质期判定写成独立纯函数并加 docstring 说明为什么不用近似：

```python
def _prebuild_allowed(plan_week_start: date, demand_month: str,
                      shelf_life_months: int | None,
                      safety_margin_fraction: Decimal) -> bool:
    """Real calendar-day comparison, deliberately not 4.33 weeks/month.

    Over an 18-month horizon the approximation drifts by whole weeks, which
    is exactly the resolution this whole feature works at — an off-by-one
    week on a shelf-life gate is the difference between shippable stock and
    a write-off.
    """
    if shelf_life_months is None:
        return False
    demand_start = date(int(demand_month[:4]), int(demand_month[5:7]), 1)
    horizon_days = (demand_start - _minus_months(demand_start, shelf_life_months)).days
    allowed_days = int(Decimal(horizon_days) * (Decimal("1") - safety_margin_fraction))
    return (demand_start - plan_week_start).days <= allowed_days
```

- [ ] **Step 4: 运行测试，确认通过**

Run: `python -m pytest tests/test_mps_engine.py -q`
Expected: PASS（Task 4 的用例一并保持通过）

- [ ] **Step 5: Commit**

```bash
git add mrp-api/app/services/mps_engine.py mrp-api/tests/test_mps_engine.py
git commit -m "feat(mrp): weekly lead time, pre-build overflow, date-exact shelf-life gate"
```

---

### Task 6: 破坏性迁移

**Files:**
- Create: `mrp-api/alembic/versions/mrp10b_weekly_columns.py`
- Modify: `mrp-api/app/models/mps.py`、`mrp-api/app/models/demand.py`
- Test: `mrp-api/tests/test_alembic_revisions.py`（加一条列存在性断言）

**Interfaces:**
- Consumes: Task 2 的 `mrp10a`
- Produces: `MrpMpsRun.production_lead_weeks/week_calendar_mode`、`MrpMpsLine.plan_week_start/plan_week_month/weeks_early`、`MrpDemand.plan_week_start`

- [ ] **Step 1: 写失败测试**

```python
@pytest.mark.asyncio
async def test_weekly_columns_exist_and_monthly_ones_are_gone(db_session):
    from sqlalchemy import text

    async def cols(table):
        return set((await db_session.execute(text(
            "select column_name from information_schema.columns where table_name = :t"
        ), {"t": table})).scalars().all())

    runs = await cols("mrp_mps_runs")
    assert "production_lead_weeks" in runs and "week_calendar_mode" in runs
    assert "production_lead_months" not in runs

    lines = await cols("mrp_mps_lines")
    assert {"plan_week_start", "plan_week_month", "weeks_early"} <= lines
    assert "plan_month" not in lines

    assert "plan_week_start" in await cols("mrp_demands")
```

- [ ] **Step 2: 运行，确认失败**

Run: `python -m pytest tests/test_alembic_revisions.py -q`
Expected: FAIL

- [ ] **Step 3: 写迁移**

`mrp10b_weekly_columns.py`，docstring 必须写明**这是破坏性迁移，downgrade 只还原结构、被删的计划数据不可复原**。

```python
def upgrade() -> None:
    # Decision D9: monthly plans are wiped rather than converted — converting
    # would write system-invented numbers into already-released history.
    op.execute("DELETE FROM mrp_demands")
    op.execute("DELETE FROM mrp_mps_lines")
    op.execute("DELETE FROM mrp_mps_runs")
    # Standing rules keep monthly VALUES; leaving them active would silently
    # read a monthly ceiling as a weekly one (~4x the real capacity).
    op.execute("UPDATE mrp_capacity_rules SET is_active = false")

    op.alter_column("mrp_mps_runs", "production_lead_months",
                    new_column_name="production_lead_weeks",
                    server_default="4")
    op.add_column("mrp_mps_runs", sa.Column("week_calendar_mode", sa.String(20),
                                            nullable=False, server_default="iso_thursday"))
    op.drop_column("mrp_mps_lines", "plan_month")
    op.add_column("mrp_mps_lines", sa.Column("plan_week_start", sa.Date(), nullable=False))
    op.add_column("mrp_mps_lines", sa.Column("plan_week_month", sa.CHAR(7), nullable=False))
    op.add_column("mrp_mps_lines", sa.Column("weeks_early", sa.Integer(),
                                             nullable=False, server_default="0"))
    op.create_index("ix_mrp_mps_lines_plan_week", "mrp_mps_lines", ["plan_week_start"])
    op.add_column("mrp_demands", sa.Column("plan_week_start", sa.Date(), nullable=False))
    op.create_index("ix_mrp_demands_plan_week", "mrp_demands", ["plan_week_start"])
```

`NOT NULL` 无默认值能加上，正是因为前面三张表已经清空——顺序不能调。

- [ ] **Step 4: 同步模型**

`models/mps.py`、`models/demand.py` 改成与迁移一致的列。

- [ ] **Step 5: 运行测试**

Run: `python -m pytest tests/test_alembic_revisions.py -q && python -m alembic heads`
Expected: PASS；`heads` 只有一行

- [ ] **Step 6: Commit**

```bash
git add mrp-api/alembic/versions/mrp10b_weekly_columns.py mrp-api/app/models/mps.py \
        mrp-api/app/models/demand.py mrp-api/tests/test_alembic_revisions.py
git commit -m "feat(mrp)!: weekly columns on MPS runs/lines/demands (clears monthly plans)"
```

---

### Task 7: MPS API 改周口径

**Files:**
- Modify: `mrp-api/app/api/v1/mps.py`
- Test: `mrp-api/tests/test_mps_api.py`（月口径用例全部改写）

**Interfaces:**
- Consumes: Task 1/2/3/5
- Produces: `POST /mps/runs` 接受 `production_lead_weeks`（0–52，`Field(ge=0, le=52)`）；响应行含 `plan_week_start`、`plan_week_month`、`weeks_early`、`week_label`

- [ ] **Step 1: 写失败测试**

覆盖：①生成时把当前 `week_calendar_mode` 快照进 run；②之后改设置，**已有 run 的 mode 不变**、`recalculate` 仍用 run 存的 mode；③`production_lead_weeks` 边界 53 被拒（422）；④`adjust` 能把一行改到别的周并置 `manual_adjusted`；⑤锁定行在 `recalculate` 后周与量都不变；⑥`release` 写 `mrp_demands` 前无条件清 `demand_type='mps'`（现有不变量，周化后必须保住），且写入行带 `plan_week_start`。

- [ ] **Step 2: 运行，确认失败**

Run: `python -m pytest tests/test_mps_api.py -q`
Expected: FAIL

- [ ] **Step 3: 实现**

- generate：读 `week_calendar_mode` 参数 → 存进 run → 调 `generate_mps(..., mode=run.week_calendar_mode)`；`limits_for_week` 传 Task 3 的 `resolve_limits_for_week` 偏函数
- get/recalculate：**一律用 `run.week_calendar_mode`**，不读当前参数（已发布计划不随设置漂移）
- adjust：周选择器给的是 `plan_week_start`，服务端重算 `plan_week_month = owning_month(week, run.mode)`、`weeks_early`，并跑保质期校验
- release：保持"先无条件清 `demand_type='mps'` 再写"的既有不变量
- **生成摘要报缺保质期**（spec §7）：`stats["no_shelf_life"] = [{"code": ..., "name": ...}]`，收集 `shelf_life_months` 里查不到值的产品。这把"ERP `exp` 字段可能全空、pre-build 静默退化成不提前"这条已知风险从看不见变成看得见。对应测试：

```python
@pytest.mark.asyncio
async def test_generate_reports_products_without_shelf_life(client, auth_headers, monkeypatch):
    from app.api.v1 import mps as mps_api
    async def _no_shelf_life(_token):
        return {}
    monkeypatch.setattr(mps_api, "resolve_shelf_life", _no_shelf_life)
    ...
    run = (await client.post("/api/v1/mps/runs",
                             json={"forecast_version_id": version["id"]},
                             headers=auth_headers)).json()
    assert [x["code"] for x in run["stats"]["no_shelf_life"]] == ["S0093"]
```

- [ ] **Step 4: 运行测试**

Run: `python -m pytest tests/test_mps_api.py -q`
Expected: PASS

- [ ] **Step 5: 跑整套**

Run: `python -m pytest tests -q`
Expected: **0 failed**。总数会低于/高于 167（月口径用例被改写），逐个确认没有用例被删空

- [ ] **Step 6: Commit**

```bash
git add mrp-api/app/api/v1/mps.py mrp-api/tests/test_mps_api.py
git commit -m "feat(mrp): MPS API plans by week"
```

---

### Task 8: 周口径导出

**Files:**
- Modify: `mrp-api/app/services/mps_export.py`
- Test: `mrp-api/tests/test_mps_api.py`

- [ ] **Step 1: 写失败测试**

```python
@pytest.mark.asyncio
async def test_export_has_month_header_row_over_week_columns(client, auth_headers, released_run):
    import io
    from openpyxl import load_workbook

    r = await client.get(f"/api/v1/mps/runs/{released_run['id']}/export?unit=t",
                         headers=auth_headers)
    assert r.status_code == 200
    ws = load_workbook(io.BytesIO(r.content)).active

    # 第 1 行 = 月分组（合并单元格），第 2 行 = 周标签
    merged = [str(rng) for rng in ws.merged_cells.ranges]
    assert merged, "month header cells must be merged across their weeks"
    week_header = [c.value for c in ws[2] if c.value]
    assert any("W" in str(v) for v in week_header)


@pytest.mark.asyncio
async def test_export_in_tonnes_divides_by_1000(client, auth_headers, released_run):
    import io
    from openpyxl import load_workbook

    kg = load_workbook(io.BytesIO((await client.get(
        f"/api/v1/mps/runs/{released_run['id']}/export?unit=kg", headers=auth_headers)).content)).active
    t = load_workbook(io.BytesIO((await client.get(
        f"/api/v1/mps/runs/{released_run['id']}/export?unit=t", headers=auth_headers)).content)).active

    kg_vals = [c.value for row in kg.iter_rows(min_row=3) for c in row if isinstance(c.value, (int, float))]
    t_vals = [c.value for row in t.iter_rows(min_row=3) for c in row if isinstance(c.value, (int, float))]
    assert kg_vals and len(kg_vals) == len(t_vals)
    assert all(abs(k / 1000 - v) < 0.001 for k, v in zip(kg_vals, t_vals))
```

`released_run` fixture 若不存在，照 `tests/test_mps_api.py` 里现有的"生成→发布"流程造一个。

- [ ] **Step 2: 运行，确认失败**

Run: `python -m pytest tests/test_mps_api.py -q -k export`
Expected: FAIL —— 表头仍是月列，没有合并单元格

- [ ] **Step 3: 实现**

`mps_export.py`：列由 `weeks_of_month` 按 run 的 `week_calendar_mode` 逐月展开生成；第 1 行用 `ws.merge_cells(start_row=1, start_column=c0, end_row=1, end_column=c1)` 把月名跨列合并，第 2 行写 `week_label`；数据行从第 3 行起。`unit=t` 时除以 1000（**除法只在这一层做，落库恒 KG**）。

- [ ] **Step 4: 运行测试，确认通过**

Run: `python -m pytest tests/test_mps_api.py -q -k export`
Expected: PASS

- [ ] **Step 5: Commit** — `feat(mrp): weekly Excel export with month grouping`

---

### Task 9: Production Plan 周矩阵

**Files:**
- Modify: `mrp/src/pages/mps/ProductionMatrix.tsx`、`mrp/src/pages/mps/mpsApi.ts`、`mrp/src/pages/mps/ProductionPlanPage.tsx`
- Create: `mrp/src/pages/mps/weekColumns.ts`（列模型的纯逻辑：月分组、折叠展开、汇总）
- Test: `mrp/src/pages/mps/weekColumns.verify.ts`（照 `matrixGrid/verify.ts` 的断言脚本模式）

**Interfaces:**
- Consumes: Task 7 的响应字段
- Produces: `buildWeekColumns(runLines, expandedMonths) -> Column[]`

- [ ] **Step 1: 写 `weekColumns.verify.ts` 断言**

```ts
import { buildWeekColumns } from './weekColumns'

let failures = 0
function check(name: string, cond: boolean) {
  if (cond) console.log(`  ok  ${name}`)
  else { failures++; console.log(`FAIL  ${name}`) }
}

const WEEKS = [
  { week_start: '2026-08-03', month: '2026-08' },
  { week_start: '2026-08-10', month: '2026-08' },
  { week_start: '2026-08-17', month: '2026-08' },
  { week_start: '2026-08-24', month: '2026-08' },
  { week_start: '2026-09-07', month: '2026-09' },
]

check('an expanded month contributes one column per week', (() => {
  const cols = buildWeekColumns(WEEKS, new Set(['2026-08', '2026-09']))
  return cols.filter((c) => c.month === '2026-08' && c.kind === 'week').length === 4
})())

check('a collapsed month contributes exactly one summary column', (() => {
  const cols = buildWeekColumns(WEEKS, new Set(['2026-09']))
  const aug = cols.filter((c) => c.month === '2026-08')
  return aug.length === 1 && aug[0].kind === 'monthSummary'
})())

check('a month with no planned week still occupies a column', (() => {
  // 时间轴不能断档：10 月没有任何计划行，仍要出现在表头
  const cols = buildWeekColumns([...WEEKS, { week_start: '2026-10-05', month: '2026-10' }],
                                new Set())
  return cols.some((c) => c.month === '2026-10')
})())

check('collapsing then expanding returns the original column set', (() => {
  const a = buildWeekColumns(WEEKS, new Set(['2026-08', '2026-09'])).map((c) => c.id).join()
  const b = buildWeekColumns(WEEKS, new Set(['2026-09']))
  const c = buildWeekColumns(WEEKS, new Set(['2026-08', '2026-09'])).map((x) => x.id).join()
  return a === c && b.length < WEEKS.length
})())

console.log('')
if (failures > 0) { console.log(`${failures} check(s) FAILED`); process.exit(1) }
else console.log('All checks passed.')
```

- [ ] **Step 2: 运行，确认失败** — `npx tsx src/pages/mps/weekColumns.verify.ts`
- [ ] **Step 3: 实现 `weekColumns.ts`**
- [ ] **Step 4: 运行，确认通过**
- [ ] **Step 5: 改 `ProductionMatrix.tsx`**：双层表头（月跨列 + 周），点月表头折叠/展开；Demand/Available 行只在该月首列（展开）或汇总列（折叠）渲染并跨列居中；Planned 行落到具体周。保留单滚动容器 + sticky 表头/首列/Total、缺口红格、No-BOM 徽章、KG/吨切换
- [ ] **Step 6: tsc 门禁** — `npx tsc -p tsconfig.app.json --noEmit` + `--listFiles | grep weekColumns`
- [ ] **Step 7: Commit** — `feat(mrp): weekly production matrix with collapsible months`

---

### Task 10: 调整抽屉与 Capacity Rules 页

**Files:**
- Modify: `mrp/src/pages/mps/AdjustDrawer.tsx`
- Modify: `mrp/src/pages/capacity/CapacityRulesPage.tsx`、`mrp/src/pages/capacity/RuleDrawer.tsx`、`mrp/src/pages/capacity/capacityApi.ts`
- Create: `mrp/src/pages/capacity/WeekExceptionsSection.tsx`、`mrp/src/pages/capacity/PlanningCalendarSection.tsx`

- [ ] **Step 1: AdjustDrawer**：月选择器换周选择器（列出当月各周 + 允许跨月），保存后显示保质期校验结果；新增 **Merge into adjacent week** 按钮（并入上一周/下一周，置 `manual_adjusted`）
- [ ] **Step 2: Capacity Rules 文案改周**：`Max output / week`、`Max SKUs / week`、新增 `Min output / week`；`min > max` 时后端 422 的 detail 原样显示在表单里（`role="alert"`）
- [ ] **Step 3: Planning Calendar 区块**：三种周定义下拉 + 每种一行说明 + 提示"改动只影响之后新生成的计划，已发布计划沿用生成时的模式"
- [ ] **Step 4: Week Exceptions 区块**：列表 + 新增/停用（选周、选约束、填值、填原因）
- [ ] **Step 5: 一次性横幅**：页面顶部提示"Capacity rules now read per week. Existing rules were deactivated by the weekly migration — please re-enter them."（对应迁移里的 `is_active=false`）
- [ ] **Step 6: tsc 门禁** + `--listFiles` 正面确认新文件
- [ ] **Step 7: Commit** — `feat(mrp): weekly capacity rules, week exceptions, planning calendar`

---

## 手工验收（用户，E2E 自动化被安全分类器拦）

1. 真实一个月数据复现 spec §2.2 的黄金用例（A60 B20 C30 D30 / 40t·周 / 4 周）
2. 最低产能三档：单品 10t / 20t / 40t
3. 某周设 `max_output_qty=0`（检修）→ 计划自动绕开该周
4. 调整抽屉把两周 20t 合成一周 40t，重算后不被冲掉（锁定生效）
5. 切换三种周定义 → 新 run 表头随之变化，**已发布的 run 保持不变**
6. 生成摘要出现"N 个产品无保质期数据，已按不可提前处理"（若生产数据确实缺 `exp`）
7. 导出 Excel：月分组表头 + 周列，吨/KG 切换数值正确

## 上线注意

- 迁移 `mrp10a` + `mrp10b` 都要跑；`mrp10b` **会清空生产上的 MPS 计划与已发布需求**，发布前须向用户再次确认（D9 已同意，但清库动作值得二次确认）
- 无新权限键，`seed_authz` 不需要重跑
- 只重建 mrp-api / mrp-web 两个镜像，其余 retag
- **产能规则被停用后必须由计划员按周重填，否则生成的计划会没有产能约束**——发布清单里要写这一条
