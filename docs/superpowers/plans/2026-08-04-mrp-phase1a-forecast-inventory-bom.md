# MRP Phase 1A — 需求与库存底账 + BOM Explorer Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 建成 MRP 的需求与库存底账——18 个月销售预测（系统内建网格表单 + Excel 粘贴 + 导入导出）、代储仓库存周度录入、逐月净需求计算——外加 BOM Explorer（多层级联展开 + 反查 + NC 同步按钮），其中展开逻辑将被 Phase 1C 的 MRP 引擎直接复用。

**Architecture:** 依据 `docs/superpowers/specs/2026-08-03-mrp-subsystem-design.md` V2.0 第六部分。后端：预测/代储仓/净需求归 mrp-api（8011），BOM 展开与反查归 mdm-api（BOM 是主数据）。前端：新建 `mrp/` Vite app（5179），复用 `@uniops/shell`，矩阵网格以 `epms/src/pages/budget/BreakdownMatrixModal.tsx` 为蓝本。

**Tech Stack:** FastAPI + SQLAlchemy 2.0 async + Alembic + openpyxl（Excel）+ pytest；React 18 + TS + Tailwind + Vite + @uniops/shell。

## Global Constraints

- 分支 `feature/mrp-phase0-foundations`（沿用，worktree `c:/Project/uniops-mrp-phase0`）；按逻辑单元 commit；**push 前须用户同意**。
- **alembic 修订号 ≤ 32 字符**（`version_num` 是 varchar(32)，超长会让 `upgrade head` 直接失败——已踩过）。新迁移前跑 `alembic heads` 挂真实链尾：mdm 当前 `0014_bom_line_secondary_uom`，mrp 当前 `mrp01`。守卫测试已存在，勿删。
- 测试基线：**mdm-api 99 passed / mrp-api 16 passed**，不允许新增失败。测试库 `mdm_test` / `mrp_test`（本地 docker `uniops_postgres`，conftest 覆盖 `POSTGRES_*`），两套不并发跑。
- 计划口径**统一 KG**；`EA` ≡ `PIECES` 已在 Phase 0 归一为 `EA`。
- Decimal 经 Pydantic 序列化为字符串，前端一律 `Number()` 转换。
- 前端 **user-facing 文案全英文**；样式对照 EPMS；浮层用 `createPortal`；状态徽章用统一 `StatusBadge`；页面包 chrome。
- 外部库只读（NC65 thin / WMS thick），绝不回写；凭据不入 repo（`c:/Project/nc65_conn.env`、`c:/Project/wms_conn.env`）。
- 验证要正面证据：每个任务给出测试计数 + 真实数据行数/响应片段。

---

## File Structure

```
mrp-api/
  app/models/forecast.py            # 新: mrp_forecast_versions / mrp_forecast_lines
  app/models/consignment.py         # 新: mrp_consignment_stock
  app/services/forecast_io.py       # 新: Excel 模板/导入解析/导出
  app/services/net_requirement.py   # 新: 净需求计算（纯函数为主）
  app/services/wms_lot_lookup.py    # 新: 按批次号反查 WMS 效期
  app/api/v1/forecast.py            # 新: 版本/网格读写/导入导出
  app/api/v1/consignment.py         # 新: 代储仓录入 CRUD
  app/api/v1/net_requirement.py     # 新: 逐月净需求查询
  alembic/versions/mrp02_*.py       # 新: 三张表
mdm-api/
  app/services/bom_explode.py       # 新: ★多层展开纯服务（Phase 1C 复用）
  app/models/sync_state.py          # 新/改: BOM 同步状态记录
  app/api/v1/boms.py                # 改: +/explode +/where-used +/sync-state；sync 加 advisory lock
  alembic/versions/0015_*.py        # 新: bom 同步状态表
mrp/                                # 新 Vite app（端口 5179）
  src/main.tsx, src/App.tsx, src/routes.tsx
  src/components/MatrixGrid.tsx     # 矩阵网格（粘贴+undo，蓝本 BreakdownMatrixModal）
  src/pages/ForecastPage.tsx
  src/pages/ConsignmentStockPage.tsx
  src/pages/BomExplorerPage.tsx
  src/lib/api.ts, Dockerfile, nginx.conf, vite.config.ts
portal/src/components/layout/navConfig.tsx   # 改: MRP 分组入口
docker-compose.dev.yml / docker-compose.prod.yml  # 改: +mrp-web；全服务 ALLOWED_ORIGINS +5179
Caddyfile                           # 改: mrp 子域
```

---

### Task 1: 预测数据模型 + 网格读写 API

**Files:** Create `mrp-api/app/models/forecast.py`、`mrp-api/app/api/v1/forecast.py`、迁移 `mrp02_forecast_consignment.py`；Test `mrp-api/tests/test_forecast.py`

**Interfaces:**
- Produces: 表 `mrp_forecast_versions(version_no unique, status draft/confirmed/superseded, horizon_start_month CHAR(7) 'YYYY-MM', horizon_months default 18, note, created_by, confirmed_at)`、`mrp_forecast_lines(version_id FK cascade, material_code String(50), month CHAR(7), qty Numeric(18,3), uom default 'KG', freeze_flag bool)` unique(version_id, material_code, month)。
- API：`POST /api/v1/forecast/versions`（建版，可 `copy_from_version_id` 复制上版数据）、`GET /api/v1/forecast/versions`（列表）、`GET /api/v1/forecast/versions/{id}/grid`（**网格形态**返回 `{months: [...], rows: [{material_code, name, cells: {month: qty}, total}], column_totals: {month: qty}, grand_total}`）、`PUT /api/v1/forecast/versions/{id}/cells`（**批量 upsert**，body `{cells: [{material_code, month, qty}]}`，一次事务）、`POST /api/v1/forecast/versions/{id}/confirm`。写操作 `require_permission("mrp.demand.write")`，读 `mrp.report.view`。
- 冻结：`freeze_flag=true` 的单元格**批量 upsert 时跳过并在响应里回报 `skipped_frozen: [...]`**，不静默改。

- [ ] **Step 1: 写失败测试**

```python
# mrp-api/tests/test_forecast.py
import pytest

@pytest.mark.anyio
async def test_create_version_and_upsert_cells(client):
    v = (await client.post("/api/v1/forecast/versions",
         json={"horizon_start_month": "2026-09", "note": "初版"})).json()
    assert v["horizon_months"] == 18 and v["status"] == "draft"
    r = await client.put(f"/api/v1/forecast/versions/{v['id']}/cells", json={"cells": [
        {"material_code": "S0093", "month": "2026-09", "qty": "20000"},
        {"material_code": "S0093", "month": "2026-10", "qty": "18000"},
    ]})
    assert r.status_code == 200 and r.json()["upserted"] == 2
    grid = (await client.get(f"/api/v1/forecast/versions/{v['id']}/grid")).json()
    assert len(grid["months"]) == 18 and grid["months"][0] == "2026-09"
    row = next(x for x in grid["rows"] if x["material_code"] == "S0093")
    assert float(row["total"]) == 38000
    assert float(grid["column_totals"]["2026-09"]) == 20000

@pytest.mark.anyio
async def test_frozen_cells_are_not_overwritten(client, db_session):
    from app.models.forecast import ForecastVersion, ForecastLine
    v = (await client.post("/api/v1/forecast/versions",
         json={"horizon_start_month": "2026-09"})).json()
    await client.put(f"/api/v1/forecast/versions/{v['id']}/cells", json={"cells": [
        {"material_code": "S0060", "month": "2026-09", "qty": "100"}]})
    # 冻结该格
    import sqlalchemy as sa
    await db_session.execute(sa.update(ForecastLine).where(
        ForecastLine.material_code == "S0060").values(freeze_flag=True))
    await db_session.commit()
    r = await client.put(f"/api/v1/forecast/versions/{v['id']}/cells", json={"cells": [
        {"material_code": "S0060", "month": "2026-09", "qty": "999"}]})
    body = r.json()
    assert body["upserted"] == 0
    assert {"material_code": "S0060", "month": "2026-09"} in body["skipped_frozen"]
```

- [ ] **Step 2: 跑测试确认失败** — `pytest mrp-api/tests/test_forecast.py -v`，Expected: FAIL（模块/端点不存在）
- [ ] **Step 3: 实现模型 + 迁移 + 端点**（`horizon_start_month` 起连续 18 个 `YYYY-MM` 由服务端生成，不信任前端传月份；物料名从 mdm-api 拉取或留空由前端补）
- [ ] **Step 4: 跑通** — `pytest mrp-api/tests -q`，Expected: 16 baseline + 新增全过
- [ ] **Step 5: Commit** — `git commit -m "feat(mrp): sales forecast versions and grid cell API"`

---

### Task 2: 预测 Excel 导入/导出/模板

**Files:** Create `mrp-api/app/services/forecast_io.py`；Modify `mrp-api/app/api/v1/forecast.py`；Test `mrp-api/tests/test_forecast_io.py`

**Interfaces:**
- Consumes: Task 1 的 `ForecastVersion`/`ForecastLine`、批量 upsert 语义（冻结跳过）。
- Produces: `GET /api/v1/forecast/versions/{id}/template`（下载空模板 xlsx：首列 Material Code、次列 Name、其后 18 个月列）、`POST /api/v1/forecast/versions/{id}/import?dry_run=true|false`（上传 xlsx，返回 `{ok_rows, error_rows: [{row, column, reason}], skipped_frozen, would_upsert}`；`dry_run=true` 只校验不落库——**前端"预览校验报告"依赖它**）、`GET /api/v1/forecast/versions/{id}/export`（导出当前网格 xlsx）。
- 校验规则：物料码必须存在于 mdm `materials`（HTTP 查询，转发 token，批量拉一次做集合判定，别逐行调）；数量非负数值，容忍千分位与 `$`；月份列头须匹配该版本的 18 个月；**单行出错不影响其余行**。

- [ ] **Step 1: 失败测试**

```python
# mrp-api/tests/test_forecast_io.py
import io, pytest
from openpyxl import Workbook

def _xlsx(rows, months):
    wb = Workbook(); ws = wb.active
    ws.append(["Material Code", "Name"] + months)
    for r in rows: ws.append(r)
    buf = io.BytesIO(); wb.save(buf); buf.seek(0); return buf

@pytest.mark.anyio
async def test_import_dry_run_reports_errors_without_writing(client, monkeypatch):
    from app.services import forecast_io
    monkeypatch.setattr(forecast_io, "fetch_valid_material_codes",
                        lambda *a, **k: {"S0093"})
    v = (await client.post("/api/v1/forecast/versions",
         json={"horizon_start_month": "2026-09"})).json()
    months = (await client.get(f"/api/v1/forecast/versions/{v['id']}/grid")).json()["months"]
    buf = _xlsx([["S0093", "x", "20,000"] + [""] * 17,
                 ["BOGUS", "y", "5"] + [""] * 17,
                 ["S0093", "x", "abc"] + [""] * 17], months)
    r = await client.post(f"/api/v1/forecast/versions/{v['id']}/import?dry_run=true",
                          files={"file": ("f.xlsx", buf, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")})
    body = r.json()
    assert body["ok_rows"] == 1                      # 千分位被正确解析
    reasons = {(e["row"], e["reason"]) for e in body["error_rows"]}
    assert any("BOGUS" in str(e) or e["row"] == 3 for e in body["error_rows"])
    assert len(body["error_rows"]) == 2              # 未知物料 + 非数值
    grid = (await client.get(f"/api/v1/forecast/versions/{v['id']}/grid")).json()
    assert grid["grand_total"] in (0, "0", "0.000")  # dry_run 未落库
```

- [ ] **Step 2: 跑失败** → **Step 3: 实现**（openpyxl；仓库已有 `nc-ap-export` 的 xlsx 先例可参考写法）→ **Step 4: 跑通** → **Step 5: Commit** `feat(mrp): forecast excel template, import with validation report, export`

---

### Task 3: 代储仓库存录入 + 批次反查 WMS 效期

**Files:** Create `mrp-api/app/models/consignment.py`（并入 Task 1 的 mrp02 迁移）、`mrp-api/app/services/wms_lot_lookup.py`、`mrp-api/app/api/v1/consignment.py`；Test `mrp-api/tests/test_consignment.py`

**Interfaces:**
- Produces: 表 `mrp_consignment_stock(warehouse_code String(50) default 'MAIN', material_code, lot_no String(50), qty Numeric(18,3), count_date Date, expiry_date Date nullable, expiry_source String(20) 'wms'/'manual'/null, entered_by)` unique(warehouse_code, material_code, lot_no, count_date)。
- API：`POST/GET/PATCH/DELETE /api/v1/consignment/stock`（写 `mrp.demand.write`，读 `mrp.report.view`）；`GET /api/v1/consignment/lot-lookup?lot_no=&material_code=` → `{found, production_date, expiry_date}`。
- `lot_lookup` 查 **WMS `INV_LOT_ATT`**（不是 `INV_LOT`——属性表 23973 行 > 在库 3532 行，发出去的历史批次属性仍在），join 键 `(ORGANIZATIONID, LOTNUM, CUSTOMERID, SKU)`，取 `LOTATT01` 生产日期 / `LOTATT02` 失效日期。thick 模式（`settings.oracle_client_lib`），只读，连接 finally 关闭。**WMS 不可达时返回 `found=false` 并记 warning，绝不阻断保存**。
- 创建库存记录时若未显式给 `expiry_date`，自动 lot_lookup 补齐并置 `expiry_source='wms'`。

- [ ] **Step 1: 失败测试**（monkeypatch `lookup_lot` 返回固定值，验证自动补齐 + WMS 挂掉不阻断 + 唯一约束 409）
- [ ] **Step 2: 跑失败** → **Step 3: 实现** → **Step 4: 跑通** → **Step 5: Commit** `feat(mrp): consignment stock entry with WMS lot expiry lookup`

---

### Task 4: 逐月净需求计算

**Files:** Create `mrp-api/app/services/net_requirement.py`、`mrp-api/app/api/v1/net_requirement.py`；Test `mrp-api/tests/test_net_requirement.py`

**Interfaces:**
- Consumes: `ForecastLine`（Task 1）、`ConsignmentStock`（Task 3）、Phase 0 的 `WmsInventoryLot`（`mapped_status='available'` 且未过期）。
- Produces: 纯函数 `compute_net_requirements(forecast_by_month: dict[str, Decimal], opening_stock: Decimal) -> list[NetRow]`，逐月滚动：`可用期初 = 上月结转`，`净需求 = max(0, 预测 - 可用期初)`，`结转 = max(0, 可用期初 - 预测)`。API `GET /api/v1/net-requirement?version_id=&material_code=` 返回逐月 `{month, forecast_qty, opening_stock, net_requirement, closing_stock}` + 库存来源明细 `{wms_qty, consignment_qty, consignment_count_date}`（**新鲜度可见**）。
- 口径写死在 docstring：**期初库存 = WMS available 未过期 + 代储仓最近一次盘点**；在途/已排产未入库本期为 0（1B 发布计划后才有）。

- [ ] **Step 1: 失败测试**

```python
# mrp-api/tests/test_net_requirement.py
from decimal import Decimal
from app.services.net_requirement import compute_net_requirements

def test_stock_is_consumed_before_generating_net_requirement():
    rows = compute_net_requirements(
        {"2026-09": Decimal("100"), "2026-10": Decimal("100"), "2026-11": Decimal("100")},
        opening_stock=Decimal("250"))
    assert [r.net_requirement for r in rows] == [Decimal("0"), Decimal("0"), Decimal("50")]
    assert [r.closing_stock for r in rows] == [Decimal("150"), Decimal("50"), Decimal("0")]

def test_zero_stock_passes_forecast_through():
    rows = compute_net_requirements({"2026-09": Decimal("80")}, opening_stock=Decimal("0"))
    assert rows[0].net_requirement == Decimal("80")
```

- [ ] **Step 2-5:** 同前四步；Commit `feat(mrp): monthly net requirement calculation with stock consumption`

---

### Task 5: ★BOM 多层展开服务 + 端点（Phase 1C 复用）

**Files:** Create `mdm-api/app/services/bom_explode.py`；Modify `mdm-api/app/api/v1/boms.py`；Test `mdm-api/tests/test_bom_explode.py`

**Interfaces:**
- Consumes: Phase 0 的 `boms`/`bom_lines`/`bom_substitutes`、`boms.py` 既有 `_version_key`（数值版本比较，**必须复用，禁止字符串比较**）与 `_covers`（行级生效窗口）。
- Produces: **纯服务函数** `explode_bom(db, product_code, on_date, max_depth=10) -> ExplodeNode`，返回树；`GET /mdm/v1/boms/explode?product=&date=&max_depth=`。节点字段：`material_code, name, bom_type, level, qty_per(本层), qty_accumulated(相对顶层1单位), uom, qty_per_secondary, uom_secondary, scrap_rate, version, version_candidates_count, missing_bom(bool), cycle_detected(bool), children[]`。
- **硬要求**：① 环检测——沿路径维护已访问 code 集合，命中即置 `cycle_detected=true` 并停止下探（禁止无限递归）；② `qty_accumulated = 父累计 × qty_per × (1 + scrap_rate)`（Phase 0 实测 NC 本实例 scrap 全为 0，公式仍须正确）；③ 组件本身应有 BOM 却查不到 → `missing_bom=true`（如 `-R` 返工变体），不当叶子静默略过；④ `version_candidates_count` = 该物料在该日期下的已审版本数（选版透明）。

- [ ] **Step 1: 失败测试**

```python
# mdm-api/tests/test_bom_explode.py
import pytest
from datetime import date
from decimal import Decimal

@pytest.mark.anyio
async def test_explode_walks_three_levels_and_accumulates(db_session, seed_cascade):
    from app.services.bom_explode import explode_bom
    root = await explode_bom(db_session, "S0093", date(2026, 8, 4))
    assert root.material_code == "S0093" and root.level == 0
    powder = next(c for c in root.children if c.material_code == "CW0001")
    assert powder.bom_type == "drymix" and powder.level == 1
    silo = next(c for c in powder.children if c.material_code == "CS0026")
    assert silo.level == 2
    raw = silo.children[0]
    # 累计 = 各层 qty_per 连乘
    assert raw.qty_accumulated == pytest.approx(
        Decimal(powder.qty_per) * Decimal(silo.qty_per) * Decimal(raw.qty_per), rel=1e-9)

@pytest.mark.anyio
async def test_cycle_is_detected_not_infinite(db_session, seed_cyclic_bom):
    from app.services.bom_explode import explode_bom
    root = await explode_bom(db_session, "CYC_A", date(2026, 8, 4))
    node = root.children[0].children[0]      # A -> B -> A
    assert node.cycle_detected is True and node.children == []

@pytest.mark.anyio
async def test_component_without_bom_is_flagged_missing(db_session, seed_missing_bom):
    from app.services.bom_explode import explode_bom
    root = await explode_bom(db_session, "S_MISS", date(2026, 8, 4))
    child = root.children[0]
    assert child.missing_bom is True
```

- [ ] **Step 2: 跑失败** → **Step 3: 实现**（递归带 visited 集合；`db` 查询按层批量取避免 N+1）→ **Step 4: 跑通** → **Step 5: 真实数据冒烟**：对 `S0093` 调用 explode，输出树深度、节点数、CR 原料累计用量，记入报告 → **Step 6: Commit** `feat(mdm): multi-level BOM explosion service and endpoint`

---

### Task 6: BOM 反查（where-used）

**Files:** Modify `mdm-api/app/services/bom_explode.py`（加 `find_where_used`）、`mdm-api/app/api/v1/boms.py`；Test 追加到 `test_bom_explode.py`

**Interfaces:** `GET /mdm/v1/boms/where-used?component=&date=` → `[{top_product, path: [code...], qty_accumulated}]`。向上反查同样要防环、限深。

- [ ] **Step 1-5:** 失败测试（`CR0031` 反查应命中 `S0093`，路径为 `CR0031 → CS0026 → CW0001 → S0093`）→ 实现 → 跑通 → 真实数据冒烟 → Commit `feat(mdm): BOM where-used reverse lookup`

---

### Task 7: BOM 同步状态记录 + 同步并发守卫

**Files:** Create `mdm-api/app/models/sync_state.py`（若已有 `erp_sync_state` 则复用扩展，先 Read 确认）；Modify `mdm-api/app/services/nc_bom_sync/service.py`、`mdm-api/app/api/v1/boms.py`；迁移 `0015_*`（**名称 ≤32 字符**）；Test `mdm-api/tests/test_bom_sync_state.py`

**Interfaces:**
- Produces: 同步状态记录（source='nc_bom'，last_success_at, last_error, last_stats jsonb）；`GET /mdm/v1/boms/sync-state`（读，`mrp.report.view`）；`POST /boms/sync` 成功/失败都写状态。
- **并发守卫**：sync 入口取 `pg_advisory_xact_lock(hashtext('nc_bom_sync'))`，**非阻塞尝试**（`pg_try_advisory_xact_lock`），拿不到锁立即返回 409 + 明确提示，不让第二个请求干等。

- [ ] **Step 1-5:** 失败测试（同步后状态有 last_success_at + stats；失败时写 last_error 且异常不被吞；并发第二次调用返回 409）→ 实现 → 跑通 → Commit `feat(mdm): BOM sync state tracking and concurrency guard`

---

### Task 8: mrp 前端应用骨架 + 接线

**Files:** Create `mrp/`（vite.config.ts, package.json, tsconfig, Dockerfile, nginx.conf, src/main.tsx, src/App.tsx, src/routes.tsx, src/lib/api.ts）；Modify `portal/src/components/layout/navConfig.tsx`、`docker-compose.dev.yml`、`docker-compose.prod.yml`、`Caddyfile`

**Interfaces:**
- Produces: 可访问的 mrp 前端（dev 5179），登录态经 portal 会话交接（`#__session=`，照抄既有模块做法），`src/lib/api.ts` 提供带 Bearer 的 fetch 封装（**照抄 oa 的 api client 绝对 URL 写法**——Vite proxy 在 Docker 下不生效，已踩过）。
- 以 `oa/` 或 `vms/` app 为模板复制结构（选与 shell 集成最完整的那个，先 Read 对比）。
- **Dockerfile 的 `VITE_*` 必须 `ARG` + `ENV` 成对声明**（漏了会回落 localhost，已三犯）。
- **全部后端服务的 `ALLOWED_ORIGINS` 加 5179 / 新域名**（两份 compose 都要）。
- portal `navConfig` 加 MRP 分组：Forecast(`mrp.demand.write`)、Consignment Stock(`mrp.demand.write`)、BOM Explorer(`mrp.report.view`)，分组 `anyPermission` 为三者并集；`resolveNavHref` 加分支。

- [ ] **Step 1:** 复制模板 app 并改名/改端口；`npm install --install-links` 起 dev 容器
- [ ] **Step 2:** 接 portal 会话交接 + api client；页面先放三个占位路由
- [ ] **Step 3:** compose/Caddyfile/ALLOWED_ORIGINS 接线
- [ ] **Step 4: 验证** — dev 起容器，浏览器（或 curl）访问 5179 得到应用；从 portal 点 MRP 入口能带登录态跳入；`npx tsc -p tsconfig.app.json` **0 error**（新 app 基线为 0）
- [ ] **Step 5: Commit** `feat(mrp): frontend app scaffold, portal nav entry, compose wiring`

---

### Task 9: MatrixGrid 组件（Excel 粘贴 + undo/redo）

**Files:** Create `mrp/src/components/MatrixGrid.tsx`；Test `mrp/src/components/MatrixGrid.test.tsx`（若该 app 无测试框架则以 Task 10 的页面级手工验证替代，并在报告中说明）

**Interfaces:**
- **蓝本：`epms/src/pages/budget/BreakdownMatrixModal.tsx`——先完整 Read 该文件的第 116–151 行（Snapshot/undo/redo）与第 327–370 行（粘贴），照搬模式，不要重新发明，也不要引入 ag-grid/handsontable。**
- Produces: 受控组件 `<MatrixGrid rows cols value onChange frozenKeys readOnly />`，能力：单元格编辑（点击/Tab/方向键/Enter 下移）、**矩形块粘贴**、**Ctrl+Z/Ctrl+Y undo/redo**、区域选择 + Ctrl+C 复制为 TSV、行/列合计、首列与表头 sticky、行虚拟化。
- 粘贴规则（照搬 + 本模块补充）：只拦截多格粘贴（含 `\t` 或 `\n`），单格放行原生；解析剥 `\r`、剔尾部空行、`\n` 分行 `\t` 分列；数值 `trim()` 后剥 `,`/`$`，`parseFloat` + `Number.isFinite` 校验；越界裁剪；**空格跳过不清零**；**冻结格跳过并汇总 `N cells skipped (frozen)`**；非数值格标红计入报告但不中断；**影响 >100 格先弹确认摘要**；整块粘贴为一次 undo snapshot。

- [ ] **Step 1:** Read 蓝本文件相关两段
- [ ] **Step 2:** 实现组件（状态用稀疏 Map，不用全量二维数组）
- [ ] **Step 3: 验证** — 至少覆盖：从 Excel 复制 3×4 块粘贴到锚点后数值正确落位且千分位被解析；粘到边界外被裁剪；冻结格跳过并给出提示；Ctrl+Z 一次撤回整块。**手工验证须截图或逐条记录实际操作与结果**，不接受"应该可以"。
- [ ] **Step 4: Commit** `feat(mrp): matrix grid with excel paste and undo history`

---

### Task 10: Sales Forecast 页面

**Files:** Create `mrp/src/pages/ForecastPage.tsx`；Modify `mrp/src/routes.tsx`

**Interfaces:** Consumes Task 1/2 的 API 与 Task 9 的 `MatrixGrid`。行=产品、列=18 个月；版本切换器；`Save Draft`/`Confirm`；`Import Excel`（上传 → **调 `dry_run=true` 展示校验报告** → 确认后 `dry_run=false` 落库）、`Export`、`Download Template`。

- [ ] **Step 1:** 页面骨架 + 网格接数据 + 行列合计
- [ ] **Step 2:** 保存（批量 upsert）+ 冻结跳过提示 + 三态反馈（loading→success toast/error）
- [ ] **Step 3:** 导入向导（预览校验报告：哪一行哪一列错、错在哪；错行不阻断其余）+ 导出 + 模板下载
- [ ] **Step 4: 验证** — 真实操作：建版 → 粘贴一块数据 → 保存 → 刷新数据仍在 → 导出的 xlsx 能再导回；`tsc` 0 error。记录每步实际结果。
- [ ] **Step 5: Commit** `feat(mrp): sales forecast page with grid, import wizard and export`

---

### Task 11: Consignment Stock 页面

**Files:** Create `mrp/src/pages/ConsignmentStockPage.tsx`；Modify `mrp/src/routes.tsx`

**Interfaces:** Consumes Task 3 的 API。表单字段：产品（带搜索下拉，数据来自 mdm materials）、批次号、数量、盘点日期。**批次号 onBlur 即调 `lot-lookup`**：命中则自动带出生产/失效日期并置灰 + 标注 `from WMS`；未命中提示"批次未在 WMS 找到，效期将留空"但**不阻断保存**。列表顶部显示数据新鲜度 `Last counted: … (N days ago)`，超过 7 天变黄。

- [ ] **Step 1-4:** 页面 + 反查联动 + 新鲜度提示 → 真实操作验证（用一个真实存在的批次号验证自动带出效期，用一个瞎编的验证不阻断）→ `tsc` 0 error → Commit `feat(mrp): consignment stock entry page with WMS lot lookup`

---

### Task 12: BOM Explorer 页面

**Files:** Create `mrp/src/pages/BomExplorerPage.tsx`；Modify `mrp/src/routes.tsx`

**Interfaces:** Consumes Task 5/6/7 的三个端点。树形展开/折叠；`bom_type` 用 `StatusBadge`；**两列用量**（本层 + 累计）；as-of 日期选择器；版本标注 `v1.6 (7 approved)` 可展开候选；`missing_bom` 与 `cycle_detected` 用警示样式且给可读原因；Where-used 模式切换；导出 Excel（含缩进层级与累计用量）。

**Sync 按钮**：右上角 `Sync from NC`，仅 `mdm.bom.write` 可见；点击进 loading 并禁用（防重复提交）；完成后 toast + 可展开明细展示 `boms/lines/substitutes/skipped/warnings/tombstoned`（**warnings 与 skipped 必须可见**）；并发时后端返 409 → 前端提示"另一个同步正在进行"；`nc_configured` 为假时按钮置灰并说明原因。标题旁常驻 `Last synced: … (N hours ago)`，超 24 小时变黄。

- [ ] **Step 1-4:** 页面 + 树渲染 + where-used + sync 按钮 → 真实操作验证（对 S0093 展开出三层树并核对累计用量与后端一致；点 Sync 后同步时间更新且统计可见）→ `tsc` 0 error → Commit `feat(mrp): BOM explorer with cascade tree, where-used and NC sync`

---

### Task 13: 1A 出口验收

**Files:** Modify `docs/superpowers/specs/2026-08-03-mrp-subsystem-design.md`（追加一行 1A 完成记录）

- [ ] **Step 1:** 跑 `pytest mdm-api/tests -q`、`pytest mrp-api/tests -q`（串行），记录计数，对基线（99 / 16）无新增失败
- [ ] **Step 2:** 三条出口标准逐条取真实证据：① 导入一份 18 个月预测（可用导出的模板造数）→ `GET /net-requirement` 能算出逐月净需求且库存来源三项可追溯、代储仓盘点日期可见；② BOM Explorer 对真实成品展开完整级联树、累计用量与手算一致；③ Sync 按钮可用、同步时间可见、warnings/skipped 可见
- [ ] **Step 3:** 前端 `tsc` 全 0 error；截图或逐条记录关键页面实际状态
- [ ] **Step 4: Commit** `docs(mrp): phase 1A exit criteria verified` 并向用户汇报证据

---

## Self-Review 记录

- **Spec 覆盖**：V2.0 §6.2 ①②③（预测/库存/净需求）→ Task 1-4；§6.6 页面 1（Forecast 含粘贴）→ Task 9-10；页面 4（代储仓）→ Task 3+11；页面 6（BOM Explorer 含 Sync）→ Task 5-7+12；§6.4 三张新表 → Task 1+3。§6.4 的 `mrp_capacity_rules`/`mrp_mps_*`/`mrp_actual_output` 属 1B/1C，本计划不含。
- **类型一致性**：`material_code` 全链 String(50)；月份统一 `CHAR(7) 'YYYY-MM'` 字符串（跨表一致，便于按字典序排且无时区问题）；数量 Numeric(18,3) KG；`explode_bom(db, product_code, on_date, max_depth)` 与测试签名一致。
- **已知风险**：Task 9 的网格是本期最复杂前端件，蓝本存在但需适配 18 列固定月份场景；若该 app 无前端测试框架，验证以逐条手工记录为准（已在任务内写明要求）。
