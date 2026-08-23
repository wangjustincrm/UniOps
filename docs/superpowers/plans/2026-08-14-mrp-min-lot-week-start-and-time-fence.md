# MRP 最小批量 + 周起始日 + 锁定区 实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让排产引擎按「开一次工的最小经济批量」排产、按工厂真实的周六→周五周网格计算、并把当月起 3 个月的计划锁死不动。

**Architecture:** 三块改动都收敛在 mrp-api 的三个纯函数模块（`week_calendar.py` / `mps_engine.py` / `capacity.py`）+ 一层 API 编排（`mps.py` / `params.py` / `capacity.py`）+ mrp 前端两页。周起始日与锁定区月数是 **run 快照项**（回放历史计划必须字节一致）；产品级最小批量**不快照**（与产能规则同待遇）。一次迁移 `mrp11` 加齐全部新列，纯增量。

**Tech Stack:** FastAPI + SQLAlchemy 2.x async + Alembic（mrp-api，独立 `alembic_version_mrp`）；React + TS + Vite（mrp 前端，端口 5179）；pytest。

**Spec:** `docs/superpowers/specs/2026-08-14-mrp-min-lot-week-start-and-time-fence-design.md`

## Global Constraints

- **分支**：`feature/mrp-min-lot-and-time-fence`，worktree `c:/Project/uniops-mrp-phase0`，基线 `origin/main` = `1460f48`。
- **后端测试基线：mrp-api 318 passed**。每个任务结束必须仍是 `318 + 本任务新增` 且 **0 failed**。
- **跑测命令（宿主机，必须 `cd mrp-api`，否则 `ModuleNotFoundError: app`）**：
  ```bash
  cd /c/Project/uniops-mrp-phase0/mrp-api && JWT_SECRET_KEY=test-secret \
    TEST_PG_PASSWORD="$(docker inspect uniops_postgres --format '{{range .Config.Env}}{{println .}}{{end}}' | grep POSTGRES_PASSWORD | cut -d= -f2)" \
    ALLOWED_ORIGINS='["http://localhost:5179"]' \
    python -m pytest tests -q
  ```
  `TEST_PG_HOST/USER/PORT`、`TEST_MRP_DB` 有默认值（localhost/epms/5432/mrp_test）。**同一时刻只跑一个套件**（测试库会 drop_all）。
- **「skipped」不算通过**。验证一律要正面证据：新用例必须先跑出 FAIL、再跑出 PASS。
- **前端 tsc 基线自己量，不照抄任何数字**：`cd mrp && npx tsc -p tsconfig.app.json --noEmit`，先在**未改动的 HEAD** 上跑一次记下条数，改完对比。命令首行必须打印出 TS 版本存在的证据（`npx tsc --version`），否则可能回落全局版罢工。
- **UI 文案全英文**（注释可中文）。
- **Decimal 序列化成字符串**：前端读数值一律 `Number(...)`。
- **KG 是唯一规划单位**：所有产能/批量/结转在后端恒为 KG，吨只是前端显示层。
- **迁移**：新迁移 `down_revision` 挂 `mrp10b_weekly_columns`，**动手前先 `alembic heads` 确认真实链尾**。禁止手工 INSERT `alembic_version_mrp`。
- **每个任务独立 commit**，消息用英文祈使式，带 `Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>`。**不 push**（发布由用户单点汇合）。

---

## 阶段 A：周起始日（T1–T5）

### Task 1: `week_calendar` 支持任意周起始日

**Files:**
- Modify: `mrp-api/app/services/week_calendar.py`
- Test: `mrp-api/tests/test_week_calendar.py`
- Create: `mrp-api/tests/test_week_calendar_call_sites.py`

**Interfaces:**
- Produces: 五个公开函数全部新增关键字参数 `start_dow: int = 0`：
  `week_start_of(day, mode, *, start_dow=0)`、`owning_month(week_start, mode, *, start_dow=0)`、
  `weeks_of_month(month, mode, *, start_dow=0)`、`shift_weeks(week_start, delta, mode, *, start_dow=0)`、
  `week_label(week_start, mode, *, start_dow=0)`。`start_dow` 取值 `0..6`（0=周一），越界抛 `ValueError`。
  `month_fixed` 模式忽略 `start_dow`。

- [ ] **Step 1: 写失败测试（周边界 + 归属月 + 标签 + 越界）**

```python
# tests/test_week_calendar.py 追加
from datetime import date
import pytest
from app.services.week_calendar import (
    week_start_of, owning_month, weeks_of_month, shift_weeks, week_label)


def test_saturday_start_week_boundaries():
    # 2026-08-15 是周六 -> 它自己就是周起点；8/21 周五仍属同一周
    assert week_start_of(date(2026, 8, 15), "iso_thursday", start_dow=5) == date(2026, 8, 15)
    assert week_start_of(date(2026, 8, 21), "iso_thursday", start_dow=5) == date(2026, 8, 15)
    assert week_start_of(date(2026, 8, 22), "iso_thursday", start_dow=5) == date(2026, 8, 22)


def test_owning_month_uses_fourth_day_of_the_week():
    # 周六起算：周起点 8/29，第 4 天 = 9/1 -> 归 9 月
    assert owning_month(date(2026, 8, 29), "iso_thursday", start_dow=5) == "2026-09"
    # 周起点 8/22，第 4 天 = 8/25 -> 归 8 月
    assert owning_month(date(2026, 8, 22), "iso_thursday", start_dow=5) == "2026-08"


def test_weeks_of_month_saturday_start_are_contiguous_and_owned():
    weeks = weeks_of_month("2026-09", "iso_thursday", start_dow=5)
    assert weeks == sorted(weeks)
    assert all(w.weekday() == 5 for w in weeks)          # 全是周六
    assert all(owning_month(w, "iso_thursday", start_dow=5) == "2026-09" for w in weeks)
    assert all((b - a).days == 7 for a, b in zip(weeks, weeks[1:]))


def test_shift_weeks_saturday_start_crosses_month():
    assert shift_weeks(date(2026, 9, 5), -2, "iso_thursday", start_dow=5) == date(2026, 8, 22)


def test_week_label_drops_iso_number_when_start_is_not_monday():
    mon = week_label(date(2026, 8, 17), "iso_thursday", start_dow=0)
    sat = week_label(date(2026, 8, 15), "iso_thursday", start_dow=5)
    assert "W" in mon                      # 周一起算保留 ISO 周号
    assert "W" not in sat                  # 周六起算只给日期区间
    assert "08-15" in sat and "08-21" in sat


def test_month_fixed_ignores_start_dow():
    assert (weeks_of_month("2026-09", "month_fixed", start_dow=5)
            == weeks_of_month("2026-09", "month_fixed", start_dow=0))


@pytest.mark.parametrize("bad", [-1, 7, 99])
def test_start_dow_out_of_range_raises(bad):
    with pytest.raises(ValueError):
        week_start_of(date(2026, 8, 15), "iso_thursday", start_dow=bad)


def test_default_start_dow_reproduces_monday_behaviour():
    for day in (date(2026, 8, 1), date(2026, 8, 17), date(2026, 12, 31)):
        assert week_start_of(day, "iso_thursday") == week_start_of(day, "iso_thursday", start_dow=0)
        assert week_start_of(day, "iso_thursday").weekday() == 0
```

- [ ] **Step 2: 跑测确认失败**

Run: `cd mrp-api && ... python -m pytest tests/test_week_calendar.py -q`
Expected: FAIL — `week_start_of() got an unexpected keyword argument 'start_dow'`

- [ ] **Step 3: 实现**

要点（写进 `week_calendar.py`，并更新模块 docstring 说明周起始日是与 `mode` 正交的第二维）：

```python
def _validate_start_dow(start_dow: int) -> None:
    if not isinstance(start_dow, int) or isinstance(start_dow, bool) or not 0 <= start_dow <= 6:
        raise ValueError(
            f"start_dow must be an int 0..6 (0=Monday), got {start_dow!r}. "
            "Refusing to silently fall back to Monday."
        )


def _iso_week_start(day: date, start_dow: int = 0) -> date:
    """Start of the week containing `day` when weeks begin on `start_dow`."""
    return day - timedelta(days=(day.weekday() - start_dow) % 7)
```

- `owning_month` 的 `iso_thursday` 分支：把写死的「+3 天 = 周四」改成 `week_start + timedelta(days=3)`（本来就是这么算的，泛化后语义变成「这周的第 4 天」）—— 确认现有实现是 `week_start + timedelta(days=3)` 而不是 `weekday()==3` 的查找；若是后者，改成前者。
- `weeks_of_month` 的扫描窗口用 `_iso_week_start(first, start_dow) - timedelta(days=7)` 起，逻辑不变。
- `shift_weeks` 的 ISO 分支是 `week_start + timedelta(weeks=delta)`，与 `start_dow` 无关，不用改；`month_fixed` 分支不动。
- `week_label`：`start_dow == 0` 时保留现有 `f"{iso_year}-W{iso_week:02d} · {date_range}"`；否则只给 `date_range`，并把区间格式化成 `08-15 Sat → 08-21 Fri`。
- 每个公开函数入口调用 `_validate_start_dow(start_dow)`（`month_fixed` 也校验——传了非法值仍是调用方的 bug）。

- [ ] **Step 4: 跑测确认通过**

Run: `python -m pytest tests/test_week_calendar.py -q`
Expected: PASS，且原有 50 处调用（不传 `start_dow`）全部照旧通过。

- [ ] **Step 5: 写 AST 守卫测试，杜绝「新调用点忘了传 dow 静默回落周一」**

```python
# tests/test_week_calendar_call_sites.py  (新建)
"""app/ 里对 week_calendar 五个函数的每一次调用都必须显式传 start_dow。

默认值 0 是给测试和历史 run 回放用的便利；生产代码路径上漏传 = 整个周网格
静默回落成周一制，且没有任何报错。这条守卫用 AST 检查（不是 grep），新增调用
点忘了传会在 CI 直接红。
"""
import ast
from pathlib import Path

_GUARDED = {"week_start_of", "owning_month", "weeks_of_month", "shift_weeks", "week_label"}
_APP = Path(__file__).resolve().parents[1] / "app"


def test_every_app_call_passes_start_dow_explicitly():
    offenders = []
    for path in _APP.rglob("*.py"):
        if path.name == "week_calendar.py":
            continue                      # 模块内部调用自己的私有实现，不受此约束
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            name = (node.func.id if isinstance(node.func, ast.Name)
                    else node.func.attr if isinstance(node.func, ast.Attribute) else None)
            if name in _GUARDED and not any(k.arg == "start_dow" for k in node.keywords):
                offenders.append(f"{path.relative_to(_APP.parent)}:{node.lineno} {name}()")
    assert not offenders, (
        "these calls would silently fall back to Monday-start weeks:\n  "
        + "\n  ".join(offenders))
```

- [ ] **Step 6: 跑守卫测试，确认它现在是红的**

Run: `python -m pytest tests/test_week_calendar_call_sites.py -q`
Expected: FAIL，列出 `app/api/v1/mps.py`、`app/services/mps_engine.py`、`app/services/mps_export.py` 里的全部调用点（约 41 处）。**这条红灯要保留到 Task 4 修完**——它就是 Task 4 的验收条件。

- [ ] **Step 7: 提交**

```bash
git add mrp-api/app/services/week_calendar.py mrp-api/tests/test_week_calendar.py mrp-api/tests/test_week_calendar_call_sites.py
git commit -m "feat(mrp): support configurable week start day in week_calendar

Weeks may now begin on any weekday (the factory runs Saturday to Friday).
The month-ownership rule generalises from 'the week's Thursday' to 'the
week's fourth day'; ISO week numbers are dropped from labels whenever the
week does not start on Monday, since those numbers are Monday-based and
would point at the wrong week.

Adds an AST guard asserting every call site under app/ passes start_dow
explicitly, so a new call path cannot silently fall back to Monday."
```

**注**：Step 6 的守卫测试此刻是红的，本次提交后套件不是全绿。这是刻意的（计划内的红灯，Task 4 转绿）。若执行者不接受中途红灯，可把 `test_every_app_call_passes_start_dow_explicitly` 先标 `@pytest.mark.xfail(strict=True)`，Task 4 里去掉标记。

---

### Task 2: 迁移 `mrp11` + 模型新列

**Files:**
- Create: `mrp-api/alembic/versions/mrp11_min_lot_week_start_frozen.py`
- Modify: `mrp-api/app/models/mps.py`
- Test: `mrp-api/tests/test_alembic_revisions.py`（已有链检查，确认它覆盖新迁移）

**Interfaces:**
- Produces: `MrpMpsLine` 新增 `surplus_qty`、`carry_in_qty`（`Numeric(18,3)`，默认 0）、`late_production`、`surplus_expiry_risk`、`below_min_lot`（Boolean，默认 false）；`MrpMpsRun` 新增 `week_start_dow`（SmallInteger，默认 0）、`frozen_months`（SmallInteger，默认 3）。

- [ ] **Step 1: 确认真实链尾**

Run: `cd mrp-api && ... python -m alembic heads`
Expected: 单 head `mrp10b_weekly_columns`。**不是就停下来查清楚再动**（双 head 会让迁移在生产中止）。

- [ ] **Step 2: 写失败测试**

```python
# tests/test_mps_api.py 追加（或新建 tests/test_mps_line_columns.py）
async def test_new_planning_columns_exist_with_defaults(db_session):
    from app.models.mps import MrpMpsLine, MrpMpsRun
    assert MrpMpsRun.__table__.c.week_start_dow.default.arg == 0
    assert MrpMpsRun.__table__.c.frozen_months.default.arg == 3
    for col in ("surplus_qty", "carry_in_qty", "late_production",
                "surplus_expiry_risk", "below_min_lot"):
        assert col in MrpMpsLine.__table__.c
```

- [ ] **Step 3: 跑测确认失败** — Expected: `AttributeError` / `KeyError`。

- [ ] **Step 4: 写迁移 + 模型**

```python
# alembic/versions/mrp11_min_lot_week_start_frozen.py
"""Minimum lot size, week start day and frozen-zone columns.

Purely additive with server defaults, so it runs on a populated production
database without touching existing rows: every existing run replays as
Monday-start weeks (week_start_dow=0), which is exactly how it was planned.
"""
import sqlalchemy as sa
from alembic import op

revision = "mrp11_min_lot_week_start_frozen"
down_revision = "mrp10b_weekly_columns"
branch_labels = None
depends_on = None

_LINE_NUMERIC = ("surplus_qty", "carry_in_qty")
_LINE_FLAGS = ("late_production", "surplus_expiry_risk", "below_min_lot")


def upgrade() -> None:
    for name in _LINE_NUMERIC:
        op.add_column("mrp_mps_lines",
                      sa.Column(name, sa.Numeric(18, 3), nullable=False, server_default="0"))
    for name in _LINE_FLAGS:
        op.add_column("mrp_mps_lines",
                      sa.Column(name, sa.Boolean(), nullable=False, server_default="false"))
    op.add_column("mrp_mps_runs",
                  sa.Column("week_start_dow", sa.SmallInteger(), nullable=False, server_default="0"))
    op.add_column("mrp_mps_runs",
                  sa.Column("frozen_months", sa.SmallInteger(), nullable=False, server_default="3"))


def downgrade() -> None:
    op.drop_column("mrp_mps_runs", "frozen_months")
    op.drop_column("mrp_mps_runs", "week_start_dow")
    for name in reversed(_LINE_FLAGS + _LINE_NUMERIC):
        op.drop_column("mrp_mps_lines", name)
```

模型侧照抄同名列（`Mapped[Decimal]` / `Mapped[bool]` / `Mapped[int]`，都带 `default=` 与 `server_default=`），注释说明 `week_start_dow`/`frozen_months` 是**快照项**、其余是引擎产出的标记。

- [ ] **Step 5: 跑测确认通过** — Run 全套 `python -m pytest tests -q`，Expected: `318 + 1 passed`（Task 1 的守卫测试若未 xfail 则仍红）。

- [ ] **Step 6: 提交**

```bash
git add mrp-api/alembic/versions/mrp11_min_lot_week_start_frozen.py mrp-api/app/models/mps.py mrp-api/tests
git commit -m "feat(mrp): add mrp11 columns for lot size, week start and frozen zone"
```

---

### Task 3: `week_start_dow` 参数 + 检修周平移

**Files:**
- Modify: `mrp-api/app/api/v1/params.py`
- Modify: `mrp-api/app/services/capacity.py`（新增平移函数）
- Test: `mrp-api/tests/test_params.py`

**Interfaces:**
- Consumes: Task 1 的 `week_start_of(..., start_dow=)`。
- Produces: `PUT /params/week_start_dow` 接受 `0..6`；响应 `{"week_start_dow": 5, "exceptions_shifted": 3}`。
  新函数 `shift_capacity_exceptions(db, *, mode, old_dow, new_dow) -> int`（冲突时抛 `ExceptionShiftConflict(weeks=[...])`）。

- [ ] **Step 1: 写失败测试**

```python
# tests/test_params.py 追加
async def test_put_week_start_dow_accepts_saturday(client, auth_headers):
    r = await client.put("/params/week_start_dow", json={"value": 5}, headers=auth_headers)
    assert r.status_code == 200, r.text
    assert r.json()["week_start_dow"] == 5


@pytest.mark.parametrize("bad", [-1, 7, "sat", 1.5, None])
async def test_put_week_start_dow_rejects_bad_values(client, auth_headers, bad):
    r = await client.put("/params/week_start_dow", json={"value": bad}, headers=auth_headers)
    assert r.status_code == 422


async def test_changing_week_start_shifts_capacity_exceptions(client, auth_headers, db_session):
    # 检修周：周一制下的 2026-08-17（周一）
    await client.post("/capacity/exceptions", json={
        "week_start": "2026-08-17", "scope_type": "factory",
        "constraint_type": "max_output_qty", "limit_value": 0}, headers=auth_headers)
    r = await client.put("/params/week_start_dow", json={"value": 5}, headers=auth_headers)
    assert r.status_code == 200, r.text
    assert r.json()["exceptions_shifted"] == 1
    rows = (await client.get("/capacity/exceptions", headers=auth_headers)).json()
    # 8/17 落在周六起算的 8/15 那一周
    assert [x["week_start"] for x in rows] == ["2026-08-15"]


async def test_exception_shift_conflict_is_409_and_changes_nothing(client, auth_headers):
    # 周一制的 8/17 与 8/24 在周六制下仍是两周，改用会撞在一起的一对：
    # 8/13(周四) 与 8/17(周一) 在周六制下都落进 8/8~8/14 / 8/15~8/21？——用真实
    # 冲突对：周一制的 8/17 与 8/20 都落进周六制的 8/15 周。
    for day in ("2026-08-17", "2026-08-20"):
        await client.post("/capacity/exceptions", json={
            "week_start": day, "scope_type": "factory",
            "constraint_type": "max_output_qty", "limit_value": 0}, headers=auth_headers)
    r = await client.put("/params/week_start_dow", json={"value": 5}, headers=auth_headers)
    assert r.status_code == 409
    assert "2026-08-15" in r.text
    # 冲突时整个请求回滚：参数没改、例外没动
    assert (await client.get("/params", headers=auth_headers)).json().get("week_start_dow") in (None, 0)
```

> 写测试前**先手工验一遍日期**：`date(2026,8,17).weekday()==0`（周一）、`date(2026,8,15).weekday()==5`（周六）。若 `POST /capacity/exceptions` 的既有校验不允许非当前网格的 `week_start`，改用直接 `db_session.add(MrpCapacityException(...))` 造数据。

- [ ] **Step 2: 跑测确认失败** — Expected: 404（`unknown parameter key 'week_start_dow'`）。

- [ ] **Step 3: 实现**

```python
# app/services/capacity.py
class ExceptionShiftConflict(Exception):
    def __init__(self, weeks: list[date]):
        self.weeks = weeks
        super().__init__(f"exception rows would collide on {[w.isoformat() for w in weeks]}")


async def shift_capacity_exceptions(db, *, mode: str, old_dow: int, new_dow: int) -> int:
    """Move every capacity exception onto the new week grid.

    Exceptions are keyed by `week_start`. Changing the week start day shifts
    the whole grid, so a row keyed to a Monday no longer matches any week --
    a maintenance shutdown would silently stop applying. Each row moves to
    the new-grid week that CONTAINS its old start date.

    Raises `ExceptionShiftConflict` (and writes nothing) when two rows would
    land on the same (week, scope, constraint) -- dropping one silently is
    exactly the failure this function exists to prevent.
    """
```

- 实现里先算出全部 `(row, new_start)`，用 `(new_start, scope_type, scope_ref, constraint_type)` 做冲突检测，冲突就抛；否则逐行赋值。**不 commit**，由调用方的事务收口。
- `params.py`：`_validate_week_start_dow` 加进 `_WRITABLE_PARAMS`（拒 `bool`，`isinstance(v, bool)` 要单独挡，Python 里 `True == 1`）；`update_param` 对 `week_start_dow` 特殊处理：读旧值 → `set_param` → `shift_capacity_exceptions` → 一起 commit；捕获 `ExceptionShiftConflict` 转 409 并 `await db.rollback()`。响应多带 `exceptions_shifted`。
- ★ `set_param` 内部现在自己 `commit()`。为了让平移与参数写在同一事务里，把这条路径改成先 `db.add`/赋值、平移完再统一 commit（或给 `set_param` 加 `commit: bool = True` 形参，本路径传 False）。**不要**让参数先落库、平移再失败——那正是「检修周静默失效」的入口。

- [ ] **Step 4: 跑测确认通过** — Run: `python -m pytest tests/test_params.py tests/test_capacity.py -q`，Expected: 全绿。

- [ ] **Step 5: 提交**

```bash
git add mrp-api/app/api/v1/params.py mrp-api/app/services/capacity.py mrp-api/tests/test_params.py
git commit -m "feat(mrp): add week_start_dow setting and shift capacity exceptions with it"
```

---

### Task 4: 把 `week_start_dow` 贯通到 run 快照与全部读端点

**Files:**
- Modify: `mrp-api/app/api/v1/mps.py`（生成/读取/重算/调整/导出全路径）
- Modify: `mrp-api/app/services/mps_engine.py`（`generate_mps` 新增 `start_dow` 形参并向下透传）
- Modify: `mrp-api/app/services/mps_export.py`
- Test: `mrp-api/tests/test_mps_api.py`、`mrp-api/tests/test_week_calendar_call_sites.py`（转绿）

**Interfaces:**
- Consumes: Task 1 的 `start_dow=`、Task 2 的 `MrpMpsRun.week_start_dow`。
- Produces: `generate_mps(..., mode: str, start_dow: int)`；`GET /runs/{id}` 响应新增 `week_start_dow`；`_compute_week_grid(run, lines, mode, start_dow)`。

- [ ] **Step 1: 写失败测试**

```python
# tests/test_mps_api.py 追加
async def test_generate_snapshots_current_week_start_dow(client, auth_headers, seeded_outlook):
    await client.put("/params/week_start_dow", json={"value": 5}, headers=auth_headers)
    run = (await client.post("/mps/runs", json={"forecast_version_id": seeded_outlook},
                             headers=auth_headers)).json()
    assert run["week_start_dow"] == 5
    assert all(date.fromisoformat(w["week_start"]).weekday() == 5 for w in run["week_grid"])


async def test_existing_run_replays_on_its_own_grid_after_setting_changes(
        client, auth_headers, seeded_outlook):
    run = (await client.post("/mps/runs", json={"forecast_version_id": seeded_outlook},
                             headers=auth_headers)).json()          # 周一制
    before = [w["week_start"] for w in run["week_grid"]]
    await client.put("/params/week_start_dow", json={"value": 5}, headers=auth_headers)
    after = (await client.get(f"/mps/runs/{run['id']}", headers=auth_headers)).json()
    assert [w["week_start"] for w in after["week_grid"]] == before
    assert after["week_start_dow"] == 0
```

- [ ] **Step 2: 跑测确认失败** — Expected: `KeyError: 'week_start_dow'`。

- [ ] **Step 3: 实现（把 41 处调用一次改完）**

- `mps.py`：新增 `_resolve_week_start_dow(db)`（读 param，缺省 0），与 `_resolve_week_mode` 并列，**只在 `POST /mps/runs` 里读一次**；其余端点一律 `run.week_start_dow`。
- `generate_mps` 增加 `start_dow: int` 形参（**必填，不给默认**——引擎是纯函数，默认值等于给自己留一个静默回落）；内部所有 `week_start_of/weeks_of_month/owning_month/shift_weeks` 调用透传。
- `mps_export.py` 的 `week_label` 调用改读 `run.week_start_dow`。
- 响应模型 `MpsRunDetail` 加 `week_start_dow: int`。
- 建 run 时写 `week_start_dow=dow`。
- 改完跑 Task 1 的 AST 守卫，靠它证明没漏（若漏了它会直接指出文件行号）。

- [ ] **Step 4: 跑测确认通过**

Run: `python -m pytest tests -q`
Expected: **全绿**（守卫测试转绿，若之前打了 `xfail(strict=True)` 记得去掉标记），总数 = 318 + 新增。

- [ ] **Step 5: 提交**

```bash
git add mrp-api/app/api/v1/mps.py mrp-api/app/services/mps_engine.py mrp-api/app/services/mps_export.py mrp-api/tests
git commit -m "feat(mrp): snapshot week_start_dow on each run and read it everywhere"
```

---

### Task 5: 前端 —— 周起始日设置项

**Files:**
- Modify: `mrp/src/pages/capacity/PlanningCalendarSection.tsx`
- Modify: `mrp/src/pages/capacity/capacityApi.ts`（或 params 的 api 模块，跟现有 `week_calendar_mode` 走同一处）
- Modify: `mrp/src/pages/mps/mpsApi.ts`（run 类型加 `week_start_dow`）

- [ ] **Step 1: 量前端基线**

Run: `cd mrp && npx tsc --version && npx tsc -p tsconfig.app.json --noEmit | tee /tmp/tsc-before.txt | wc -l`
记下版本与条数（**不要照抄记忆里的数字**）。

- [ ] **Step 2: 实现**

- 在现有「周模式」下拉旁加 **Week starts on** 下拉：`Monday … Sunday`（value 0..6），保存打 `PUT /params/week_start_dow`。
- 保存成功后若响应带 `exceptions_shifted > 0`，toast：`Week grid changed — N maintenance week(s) were moved onto the new grid.`
- 409 时弹出冲突周列表：`Two exceptions would land on the same week (2026-08-15). Remove one first.`
- 下拉旁加静态说明：`Only affects ISO week modes; the fixed 4/5-week mode always starts on the 1st.`
- run 类型补 `week_start_dow: number`（暂不渲染，Task 11 的矩阵会用）。

- [ ] **Step 3: 验证**

Run: `cd mrp && npx tsc -p tsconfig.app.json --noEmit | wc -l`
Expected: 与 Step 1 基线**相同**（不允许新增诊断）。

- [ ] **Step 4: 提交**

```bash
git add mrp/src
git commit -m "feat(mrp-web): add the week start day setting to Planning Calendar"
```

---

## 阶段 B：最小生产批量（T6–T12）

### Task 6: 产品级最小批量的解析与写入校验

**Files:**
- Modify: `mrp-api/app/services/capacity.py`
- Modify: `mrp-api/app/api/v1/capacity.py`
- Test: `mrp-api/tests/test_capacity.py`

**Interfaces:**
- Produces: `resolve_min_lots(db, week_start: date) -> dict[str, Decimal]`（键=material_code，只含**有产品级规则**的产品）与 `resolve_default_min_lot(db, week_start) -> Decimal | None`（全厂值）。
  `scope_type='product'` 进 `_KNOWN_SCOPE_TYPES`；`scope_ref` 对 product 级**必填**。

- [ ] **Step 1: 写失败测试**

```python
# tests/test_capacity.py 追加
async def test_product_scope_min_output_qty_round_trips(client, auth_headers):
    r = await client.post("/capacity/rules", json={
        "scope_type": "product", "scope_ref": "FG-001",
        "constraint_type": "min_output_qty", "limit_value": 20000, "uom": "KG",
        "effective_from": "2026-01-01"}, headers=auth_headers)
    assert r.status_code == 201, r.text


async def test_product_scope_requires_scope_ref(client, auth_headers):
    r = await client.post("/capacity/rules", json={
        "scope_type": "product", "scope_ref": None,
        "constraint_type": "min_output_qty", "limit_value": 20000, "uom": "KG",
        "effective_from": "2026-01-01"}, headers=auth_headers)
    assert r.status_code == 422


async def test_product_min_lot_above_factory_max_is_rejected(client, auth_headers):
    await client.post("/capacity/rules", json={
        "scope_type": "factory", "scope_ref": None, "constraint_type": "max_output_qty",
        "limit_value": 40000, "uom": "KG", "effective_from": "2026-01-01"}, headers=auth_headers)
    r = await client.post("/capacity/rules", json={
        "scope_type": "product", "scope_ref": "FG-001",
        "constraint_type": "min_output_qty", "limit_value": 50000, "uom": "KG",
        "effective_from": "2026-01-01"}, headers=auth_headers)
    assert r.status_code == 422
    assert "max_output_qty" in r.text


async def test_resolve_min_lots_prefers_product_over_factory(db_session):
    from app.services.capacity import resolve_min_lots, resolve_default_min_lot
    # 建全厂 min 10000 + 产品 FG-001 min 20000（直接 db_session.add 两条规则）
    lots = await resolve_min_lots(db_session, date(2026, 8, 17))
    assert lots["FG-001"] == Decimal("20000")
    assert "FG-002" not in lots                       # 没有产品级规则的不出现
    assert await resolve_default_min_lot(db_session, date(2026, 8, 17)) == Decimal("10000")
```

- [ ] **Step 2: 跑测确认失败**

- [ ] **Step 3: 实现**

- `_KNOWN_SCOPE_TYPES` 加 `product`；create/update 校验 `scope_type == 'product'` 时 `scope_ref` 非空且非空串。
- 新增 `_validate_product_min_lot_against_factory_max(...)`：读同生效期内的全厂 `max_output_qty`（无则跳过），大于就 422，消息里点名两个数字。
- `resolve_min_lots`：查 `scope_type='product' AND constraint_type='min_output_qty'` 且生效期覆盖 `week_start` 的 `is_active` 行，同 `scope_ref` 多行时 `order_by(id)` 取最后一条（与现有 factory 解析同口径）。
- **不动** `resolve_limits_for_week`。

- [ ] **Step 4: 跑测确认通过**

- [ ] **Step 5: 提交**

```bash
git commit -am "feat(mrp): resolve per-product minimum lot sizes from capacity rules"
```

---

### Task 7: 引擎约束 G —— 产品级下限、尾槽回摊、产能优先例外

**Files:**
- Modify: `mrp-api/app/services/mps_engine.py`（`CapacityLimits` 之外新增按产品的下限入参，`_spread_ceiling`、`_pack_tight`、`pack_bucket`）
- Test: `mrp-api/tests/test_mps_engine.py`

**Interfaces:**
- Produces: `pack_bucket(items, weeks, limits, preloaded=None, min_lots: dict[str, Decimal] | None = None)`；
  `WeeklyLine` 新增字段 `below_min_lot: bool = False`。
  `min_lots` 缺失的产品落回 `CapacityLimits.min_output_qty`（全厂值），两者都无 = 无下限。

- [ ] **Step 1: 写失败测试**

```python
# tests/test_mps_engine.py 追加
def test_golden_case_is_unchanged_by_minimum_lot_sizes():
    """加了最小批量后，黄金用例的答案一个字都不许变。"""
    items = [BucketItem("A", "2026-09", Decimal("60")), BucketItem("B", "2026-09", Decimal("20")),
             BucketItem("C", "2026-09", Decimal("30")), BucketItem("D", "2026-09", Decimal("30"))]
    weeks = [date(2026, 9, 5), date(2026, 9, 12), date(2026, 9, 19), date(2026, 9, 26)]
    limits = CapacityLimits(None, Decimal("40"), min_output_qty=Decimal("20"))
    lines = pack_bucket(items, weeks, limits, min_lots={"A": Decimal("20"), "B": Decimal("20"),
                                                        "C": Decimal("20"), "D": Decimal("20")})
    got = {(l.material_code, l.plan_week_start): l.qty for l in lines}
    assert got == {("A", weeks[0]): Decimal("40"), ("A", weeks[1]): Decimal("20"),
                   ("B", weeks[1]): Decimal("20"), ("C", weeks[2]): Decimal("30"),
                   ("D", weeks[3]): Decimal("30")}


def test_tight_regime_relevels_a_tail_below_the_lot_size():
    """90 / cap 40 / L 20 -> 30+30+30，不许出 40+40+10。"""
    weeks = [date(2026, 9, 5), date(2026, 9, 12), date(2026, 9, 19)]
    lines = pack_bucket([BucketItem("A", "2026-09", Decimal("90"))], weeks,
                        CapacityLimits(None, Decimal("40")), min_lots={"A": Decimal("20")})
    assert sorted(l.qty for l in lines) == [Decimal("30")] * 3
    assert not any(l.below_min_lot for l in lines)


def test_capacity_wins_when_no_week_can_reach_the_lot_size():
    """21 / cap 20 / L 20 -> 10.5+10.5 且标 below_min_lot，绝不顶到 40。"""
    weeks = [date(2026, 9, 5), date(2026, 9, 12)]
    lines = pack_bucket([BucketItem("A", "2026-09", Decimal("21"))], weeks,
                        CapacityLimits(None, Decimal("20")), min_lots={"A": Decimal("20")})
    assert sum(l.qty for l in lines) == Decimal("21")
    assert all(l.below_min_lot for l in lines if not l.capacity_gap)


def test_product_lot_size_overrides_the_factory_floor():
    weeks = [date(2026, 9, 5), date(2026, 9, 12), date(2026, 9, 19), date(2026, 9, 26)]
    limits = CapacityLimits(None, Decimal("100"), min_output_qty=Decimal("10"))
    lines = pack_bucket([BucketItem("A", "2026-09", Decimal("80"))], weeks, limits,
                        min_lots={"A": Decimal("40")})
    # 全厂下限 10 会允许摊 4 周(20 each)；产品下限 40 只允许摊 2 周
    assert len([l for l in lines if l.qty > 0]) == 2
```

- [ ] **Step 2: 跑测确认失败**

- [ ] **Step 3: 实现**

- `pack_bucket` 新增 `min_lots` 形参，内部解析出 `lot_of(code) -> Decimal | None`（产品级 → `limits.min_output_qty` → None）。**每周的 `CapacityLimits` 可能不同，取该 bucket 内第一周的全厂值即可**（全厂下限本就是软的；产品级才是本次的一等约束）。
- `_spread_ceiling` 的 `min_out` 改成按产品取。
- `_pack_tight`：产品的一段连续 run 排完后，若**最后一个槽 < lot** 且该产品在本 bucket 的槽数 > 1，则在该产品已占的槽内 `_level` 重摊；重摊后仍有槽 < lot（即 `qty / span < lot` 且 span 已是物理最小 `need_weeks`）→ 保留结果并给这些行打 `below_min_lot=True`。
- `WeeklyLine` 加 `below_min_lot: bool = False`，`_line()` 透传。
- **不要**在本任务里做「顶到 lot」（那是 Task 8）——本任务只保证「已有的量」符合约束 G。

- [ ] **Step 4: 跑测确认通过** — 全套 `python -m pytest tests -q`，**原有 318 一条不许掉**。

- [ ] **Step 5: 提交**

```bash
git commit -am "feat(mrp): honour per-product minimum lot sizes when packing a bucket"
```

---

### Task 8: 顶批量 + 三段落位搜索（含往后排）

**Files:**
- Modify: `mrp-api/app/services/mps_engine.py`（`generate_mps` 的 bucket 循环 + overflow walk）
- Test: `mrp-api/tests/test_mps_engine.py`

**Interfaces:**
- Consumes: Task 7 的 `min_lots`。
- Produces: `generate_mps(..., min_lots: dict[str, Decimal] | None = None)`；`WeeklyLine` 新增 `late_production: bool = False`、`surplus_qty: Decimal = Decimal("0")`。

- [ ] **Step 1: 写失败测试**

```python
def test_a_month_below_the_lot_size_is_rounded_up_to_it():
    lines = generate_mps(
        [DemandItem("A", "2026-09", Decimal("5"))],
        limits_for_week=lambda w: CapacityLimits(None, Decimal("40")),
        shelf_life_months={"A": 24}, safety_margin_fraction=Decimal("0.3333"),
        lead_weeks=0, current_week=date(2026, 8, 31), mode="iso_thursday", start_dow=0,
        min_lots={"A": Decimal("20")})
    produced = [l for l in lines if not l.capacity_gap]
    assert len(produced) == 1
    assert produced[0].qty == Decimal("20")
    assert produced[0].surplus_qty == Decimal("15")


def test_when_the_month_cannot_host_the_lot_it_looks_backwards_first():
    """当月周被别的产品占满 -> 落到更早的月，且不是 late_production。"""
    # 用 preloaded/locked 把 2026-09 的每周都占到只剩 8，L=20
    ...
    assert placed.plan_week_month < "2026-09"
    assert placed.late_production is False


def test_only_when_earlier_weeks_are_gone_does_it_go_late_and_flag_it():
    ...
    assert placed.plan_week_month > "2026-09"
    assert placed.late_production is True


def test_a_lot_that_fits_nowhere_at_all_is_still_a_capacity_gap():
    ...
    assert any(l.capacity_gap for l in lines)
```

> 后三条的「占满」构造：用 `locked=[WeeklyLine(...)]` 把目标周填到只剩 `< L` 的余量（`locked` 会 preload 进 ledger）。写的时候先跑一次打印 `lines` 确认构造真的把周占满了——**构造没生效会让断言假通过**。

- [ ] **Step 2: 跑测确认失败**

- [ ] **Step 3: 实现**

- bucket 循环里，`merged` 构造之后、`pack_bucket` 之前：对 `q < lot_of(code)` 的产品，把 `BucketItem.qty` 顶到 `lot`，并记下 `surplus = lot - q`（**顶起后的整批不许被 `_pack_tight` 拆散**：`need_weeks` 用整批算，`lot <= 周产能` 时天然是 1 周）。
- 顶起后若本 bucket 无周能容纳整批 → **不排**，整批进 `bucket_overflow`（走既有 backward walk）。
- backward walk 里对「顶起批」要求 `room >= lot`（不接受部分放置），走完仍未安置 → **新增 forward walk**：从 bucket 最后一周之后逐周往未来找（上界 = 计划窗口最后一周，用 `_planning_weeks` 已算出的列表末尾），命中即排并置 `late_production=True`；forward walk **不受保质期限制**（往后排只会更新鲜）。
- 仍未安置 → 现有 `capacity_gap` 分支，理由文案加一句「minimum lot size N could not be placed anywhere in the horizon」。
- `surplus_qty` 写在实际落位的那条 line 上。

- [ ] **Step 4: 跑测确认通过**

- [ ] **Step 5: 提交**

```bash
git commit -am "feat(mrp): round a month up to the minimum lot size and search for a slot"
```

---

### Task 9: 超产结转、covered 行、守恒守卫、保质期警告

**Files:**
- Modify: `mrp-api/app/services/mps_engine.py`
- Test: `mrp-api/tests/test_mps_engine.py`

**Interfaces:**
- Produces: `WeeklyLine` 新增 `carry_in_qty: Decimal = Decimal("0")`、`covered_by_carry: bool = False`、`surplus_expiry_risk: bool = False`。
  守恒式改为 `Σ产出 + Σ缺口 == Σ输入 + Σ超产`。

- [ ] **Step 1: 写失败测试**

```python
def test_surplus_covers_the_following_months_and_only_one_run_is_opened():
    demands = [DemandItem("A", m, Decimal("5")) for m in ("2026-09", "2026-10", "2026-11")]
    lines = generate_mps(demands, lambda w: CapacityLimits(None, Decimal("40")),
                         {"A": 24}, Decimal("0.3333"), lead_weeks=0,
                         current_week=date(2026, 8, 31), mode="iso_thursday", start_dow=0,
                         min_lots={"A": Decimal("20")})
    produced = [l for l in lines if l.qty > 0 and not l.capacity_gap]
    assert len(produced) == 1 and produced[0].qty == Decimal("20")
    covered = sorted((l.demand_month, l.carry_in_qty) for l in lines if l.covered_by_carry)
    assert covered == [("2026-10", Decimal("15")), ("2026-11", Decimal("10"))]


def test_covered_rows_carry_zero_quantity_and_never_book_capacity():
    ...
    assert all(l.qty == Decimal("0") for l in lines if l.covered_by_carry)


def test_conservation_now_includes_the_surplus():
    ...
    assert (sum(l.qty for l in lines if not l.capacity_gap)
            + sum(l.qty for l in lines if l.capacity_gap)
            == sum(d.qty for d in demands) + sum(l.surplus_qty for l in lines))


def test_surplus_that_outlives_shelf_life_is_flagged_but_still_planned():
    # A 每月只要 1，L=20 -> 超产 19 要 19 个月才吃完，保质期 6 个月
    ...
    assert produced[0].qty == Decimal("20")            # 照顶
    assert produced[0].surplus_expiry_risk is True     # 只标警告
```

- [ ] **Step 2: 跑测确认失败**

- [ ] **Step 3: 实现**

- 引擎顶部建 `carry: dict[str, Decimal]`；bucket 按 `sorted(buckets)` 升序（现状已是），**每个 bucket 开工前**对其 `contributions` 逐产品抵扣：`take = min(carry[code], qty)`，`qty -= take`，`carry[code] -= take`，并记 `carry_in[(code, demand_month)] += take`。
- 抵成 0 的 (code, demand_month) 从 `merged` 里移除，同时产出 `WeeklyLine(qty=0, covered_by_carry=True, carry_in_qty=take, plan_week_start=targets[demand_month], ...)`。
- 顶批量产生的 `surplus` 累进 `carry[code]`。
- **covered 行必须排除在 ledger、`_merge_same_slot`、`_plan_shortfall` 之外**（qty=0 不占产能、不参与合并）。
- `surplus_expiry_risk`：用后续各月该产品的净需求估算消化月数 `months_to_consume`，若 `months_to_consume > floor(shelf_life * (1 - safety_margin))` 则置位；`shelf_life` 为 None 时也置位（无保质期记录 = 不能证明躺得住）。
- 改守恒守卫的 `RuntimeError` 条件与消息。

- [ ] **Step 4: 跑测确认通过** — 全套跑，确认原 318 无损。

- [ ] **Step 5: 提交**

```bash
git commit -am "feat(mrp): carry minimum-lot surplus into later months and flag its risks"
```

---

### Task 10: 持久化新列 + 发布口径 + 发布摘要

**Files:**
- Modify: `mrp-api/app/api/v1/mps.py`（写 line 的三处：generate / recalculate / adjust；`confirm_release`；`_compute_stats`）
- Test: `mrp-api/tests/test_mps_api.py`

**Interfaces:**
- Consumes: Task 9 的 `WeeklyLine` 新字段、Task 6 的 `resolve_min_lots`。
- Produces: `GET /runs/{id}` 的 line 响应带 `surplus_qty`/`carry_in_qty`/`late_production`/`surplus_expiry_risk`/`below_min_lot`/`covered_by_carry`；`stats` 新增 `total_surplus`、`unconsumed_surplus`。

- [ ] **Step 1: 写失败测试**

```python
async def test_release_writes_the_actual_production_quantity_not_the_net_requirement(
        client, auth_headers, seeded_outlook):
    """顶到最小批量后，喂给 1C 的必须是实际产量（含超产），否则原料会少买。"""
    ...
    demands = await fetch_mrp_demands(db_session)
    assert sum(d.qty for d in demands) == Decimal("20")     # 而不是净需求 5


async def test_covered_months_are_not_released_as_demand(client, auth_headers, seeded_outlook):
    assert all(d.qty > 0 for d in demands)


async def test_stats_report_surplus(client, auth_headers, seeded_outlook):
    run = ...
    assert run["stats"]["total_surplus"] == "15"
    assert run["stats"]["unconsumed_surplus"] == "5"
```

- [ ] **Step 2: 跑测确认失败**

- [ ] **Step 3: 实现**

- `POST /mps/runs` 里解析 `min_lots`：`await resolve_min_lots(db, current_week)`（**按当前周解析一次**，与产能规则同口径），传给 `generate_mps`。`recalculate` 同样重新解析（重算就该按最新规则）。
- 三处 `MrpMpsLine(...)` 构造补齐新列。
- `confirm_release`：写 `mrp_demands` 时**用 line.qty（含超产）**，并跳过 `qty <= 0` 的 covered 行。
- `_compute_stats` 加 `total_surplus`（Σ `surplus_qty`）与 `unconsumed_surplus`（窗口末未被抵扣的 carry —— 由引擎返回，不要在 API 层重算）。
  → 为此让 `generate_mps` 返回值保持 `list[WeeklyLine]` 不变，把未消化 carry 放进**最后一条 covered 行的字段**过于隐晦；改为引擎额外提供纯函数 `unconsumed_surplus(lines) -> Decimal = Σsurplus_qty - Σcarry_in_qty`，API 调用它。

- [ ] **Step 4: 跑测确认通过**

- [ ] **Step 5: 提交**

```bash
git commit -am "feat(mrp): persist lot-size fields and release actual production quantities"
```

---

### Task 11: 前端矩阵 + xlsx 导出

**Files:**
- Modify: `mrp/src/pages/mps/ProductionMatrix.tsx`、`mrp/src/pages/mps/mpsApi.ts`
- Modify: `mrp-api/app/services/mps_export.py`
- Test: `mrp-api/tests/test_mps_api.py`（导出用例）

- [ ] **Step 1: 前端基线**：`cd mrp && npx tsc -p tsconfig.app.json --noEmit | wc -l`

- [ ] **Step 2: 后端导出先写失败测试**

```python
async def test_export_available_column_includes_carried_surplus(client, auth_headers, ...):
    """xlsx 的 Available 与前端矩阵是两套实现，口径必须一致。"""
```

- [ ] **Step 3: 实现**

前端 `ProductionMatrix.tsx`：
- Available 单元格值改为 `Number(line.opening_stock) + Number(line.carry_in_qty)`（沿用现有「按 (产品, 需求月) 去重」的规则，**不是逐行相加**）；`title` 里补 `incl. N t carried from minimum-lot surplus`。
- covered 行（`covered_by_carry`）：Planned 显示为空并灰化，`title` = `Covered by minimum-lot surplus produced earlier`。
- 计划格：`surplus_qty > 0` 时 `title` = `Net requirement X t + Y t minimum-lot surplus`。
- 徽章优先级：`capacity_gap`（红）> `late_production`（琥珀）> `surplus_expiry_risk`（弱琥珀）> `below_min_lot`（弱琥珀）。**文案全英文**。

`mps_export.py`：同样三处（Available 加 carry_in、covered 行、超产 tooltip 改成单元格批注或额外列），与前端逐条对齐。

- [ ] **Step 4: 验证**：后端全套绿；`npx tsc -p tsconfig.app.json --noEmit | wc -l` 与基线相同。

- [ ] **Step 5: 提交**

```bash
git commit -am "feat(mrp): show carried surplus and lot-size warnings in the plan matrix"
```

---

### Task 12: 前端 Capacity Rules —— 产品级最小批量

**Files:**
- Modify: `mrp/src/pages/capacity/RuleDrawer.tsx`、`mrp/src/pages/capacity/CapacityRulesPage.tsx`、`capacityApi.ts`

- [ ] **Step 1: 实现**

- scope 下拉加 `Product`；选中后出现物料选择器（复用现有 MaterialPicker 的**成品过滤 + 客户端全量加载**，别自己再写一个分页拉取）。
- 列表页 scope 列显示 `Product · <code>`。
- 422 的错误消息原样弹出（后端已给出「产品级下限 50000 超过全厂上限 40000」这种可读文案）。
- 约束类型标签：`min_output_qty` 的显示文案从 `Min output / week` 改为 **`Minimum lot size`**（语义已变，旧文案会误导）。

- [ ] **Step 2: 验证**：`npx tsc -p tsconfig.app.json --noEmit | wc -l` 与基线相同。

- [ ] **Step 3: 提交**

```bash
git commit -am "feat(mrp-web): configure minimum lot size per product"
```

---

## 阶段 C：锁定区（T13–T14）

### Task 13: `frozen_months` 参数 + 锁定区照抄

**Files:**
- Modify: `mrp-api/app/api/v1/params.py`（白名单加 `frozen_months`）
- Modify: `mrp-api/app/services/mps_engine.py`（`generate_mps` 新增 `frozen_lines` / `frozen_until_month`）
- Modify: `mrp-api/app/api/v1/mps.py`（找生效 run、切片锁定区、快照 `frozen_months`）
- Test: `mrp-api/tests/test_mps_api.py`、`tests/test_mps_engine.py`、`tests/test_params.py`

**Interfaces:**
- Produces: `PUT /params/frozen_months` 接受 `0..24`；`generate_mps(..., frozen_lines: list[WeeklyLine] | None = None, frozen_until_month: str | None = None)`；
  `MrpMpsRun.frozen_months` 快照；`GET /runs/{id}` 响应新增 `frozen_months` 与 `frozen_until_month`。

- [ ] **Step 1: 写失败测试**

```python
def test_frozen_zone_lines_are_copied_verbatim_and_book_capacity():
    frozen = [WeeklyLine("A", "2026-09", date(2026, 9, 5), Decimal("30"))]
    lines = generate_mps([DemandItem("A", "2026-09", Decimal("50"))], ...,
                         frozen_lines=frozen, frozen_until_month="2026-10")
    kept = [l for l in lines if l.plan_week_start == date(2026, 9, 5)]
    assert kept[0].qty == Decimal("30")                     # 原样保留，不因需求变成 50 而改


def test_extra_demand_inside_the_frozen_zone_lands_in_the_first_liquid_week():
    ...
    assert extra.plan_week_month >= "2026-11"
    assert extra.late_production is True


def test_frozen_zone_surplus_carries_across_the_boundary():
    """照抄进来的锁定区行也会产生超产，自由区必须扣掉，否则重复生产。"""
    ...
    assert liquid_planned == Decimal("0")


def test_frozen_months_zero_reproduces_current_behaviour():
    assert generate_mps(..., frozen_months=0 的等价调用) == generate_mps(...不传 frozen 的调用)
```

```python
# tests/test_mps_api.py
async def test_generate_copies_the_frozen_zone_from_the_live_released_run(...):
    ...
async def test_run_snapshots_frozen_months(...):
    assert run["frozen_months"] == 3
```

- [ ] **Step 2: 跑测确认失败**

- [ ] **Step 3: 实现**

- `params.py` 白名单加 `frozen_months`（int 0..24，拒 bool）。
- `mps.py` 新增 `_live_released_run(db, horizon_start_month)`：`status='released'` 按 `created_at DESC` 取一（**临时定义**，注释里写明「版本模型 A 做完后换成 Default run」并留 `TODO(plan-versioning)`）。
- 算 `frozen_until_month = current_month + frozen_months - 1`（`frozen_months=0` → `None`）。
- 把生效 run 里 `plan_week_month <= frozen_until_month` 的行取出，作为 `frozen_lines` 传进引擎。
- 引擎：`frozen_lines` 与现有 `locked` 走**同一条 preload/ledger 路径**（它们的语义一致：已承诺的产量），但**不参与 `held_by_demand` 的需求抵扣**——锁定区的量确实覆盖了那部分需求，所以仍要抵扣；差别在于**锁定区行不许被重算改动**，而 `locked` 本来就不会。→ 结论：复用 `locked` 通道，额外记录 `frozen=True` 以便前端只读渲染。
- 自由区首周 = `frozen_until_month` 之后的第一周；锁定区内新增需求的差额落位从那里开始搜索，置 `late_production=True`。
- 建 run 时写 `frozen_months`。

- [ ] **Step 4: 跑测确认通过**

- [ ] **Step 5: 提交**

```bash
git commit -am "feat(mrp): freeze the first N months of the plan and copy them forward"
```

---

### Task 14: 锁定区只读（后端 422 + 前端）+ 全分支回归

**Files:**
- Modify: `mrp-api/app/api/v1/mps.py`（`adjust` 端点）
- Modify: `mrp/src/pages/mps/ProductionMatrix.tsx`、`AdjustDrawer.tsx`、`ProductionPlanPage.tsx`
- Test: `mrp-api/tests/test_mps_api.py`

- [ ] **Step 1: 写失败测试**

```python
async def test_adjusting_a_frozen_line_is_rejected(client, auth_headers, ...):
    r = await client.post(f"/mps/runs/{run_id}/adjust", json={...frozen line...},
                          headers=auth_headers)
    assert r.status_code == 422
    assert "frozen" in r.text.lower()
```

- [ ] **Step 2: 跑测确认失败**（现在会 200 —— 只靠前端挡等于没挡）

- [ ] **Step 3: 实现**

- 后端 `adjust`：目标行 `plan_week_month <= run.frozen_until_month` → 422 `This week is inside the frozen zone (materials already purchased) and cannot be changed.`
- 前端：锁定区列灰底 + 锁图标（表头），格子 `cursor: not-allowed` 且不触发 AdjustDrawer；页面顶部一行说明 `Weeks up to <month> are frozen — materials are already purchased.`

- [ ] **Step 4: 全分支回归（正面证据）**

```bash
cd mrp-api && ... python -m pytest tests -q          # 期望：318 + 本分支新增，0 failed
cd ../mrp && npx tsc --version && npx tsc -p tsconfig.app.json --noEmit | wc -l   # 与基线相同
cd ../mrp-api && ... python -m alembic heads         # 期望：单 head = mrp11_min_lot_week_start_frozen
```

把三条命令的**实际输出**贴进提交说明或交付报告，不写「应该通过」。

- [ ] **Step 5: 提交**

```bash
git commit -am "feat(mrp): make the frozen zone read-only end to end"
```

---

## 自查（写完计划后的核对结果）

- **spec 覆盖**：§2.1→T7；§2.2→T6；§2.3→T7；§2.4→T8；§2.5→T9；§2.6→T9；§2.7→T9/T10；§3.1-3.3→T1/T3/T5；§3.4→T3；§4.1-4.3→T11；§4.4→T2；§5.1-5.3→T13；§5.4→T14；§5.5→T13；§6→各任务的测试步骤；§7 上线注意→交付报告。
- **命名一致性**：引擎字段全程 `surplus_qty` / `carry_in_qty` / `late_production` / `surplus_expiry_risk` / `below_min_lot` / `covered_by_carry`；解析函数全程 `resolve_min_lots` / `resolve_default_min_lot`；周参数全程 `start_dow`（模型列名 `week_start_dow`，两者不同是刻意的：列名要自解释，函数参数在 `week_calendar` 语境下已无歧义）。
- **已知留白（不是占位符，是刻意的临时定义）**：T13 的「当前生效计划 = 最近一个 released run」带 `TODO(plan-versioning)`，由下一轮版本模型 A 收口。
