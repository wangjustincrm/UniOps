# MRP Phase 0 — 主数据与镜像地基 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 为 MRP 子系统打好数据地基：mdm-api 落地正式物料主数据 + NC65 BOM 只读镜像与规范化 + 单位转换 + 供应参数；新建 mrp-api 服务骨架并落地 WMS(Flux) 库存批次镜像与状态映射。

**Architecture:** 遵循设计文档 `docs/superpowers/specs/2026-08-03-mrp-subsystem-design.md`（V1.7，方案 A）。制造主数据归 mdm-api（NC65 Oracle 直连镜像 → 规范化表）；计划域数据归新服务 mrp-api（8011，共享 epms 库 + `alembic_version_mrp`）；WMS 为富勒 Flux WMS（Oracle ≤11g，须 thick 模式 + Instant Client 19c）。所有外部库只读，绝不回写。

**Tech Stack:** FastAPI + SQLAlchemy 2.0 async (asyncpg) + Alembic + python-oracledb（NC=thin / WMS=thick）+ pytest。

## Global Constraints

- 分支纪律（R1-R3）：本计划在独立 worktree 的新分支 `feature/mrp-phase0-foundations` 上执行；按逻辑单元 commit；**push 前须用户同意**。
- 新迁移前先跑 `alembic heads`，`down_revision` 挂真实链尾（mdm 链、mrp 新链各自独立）。
- 镜像模型忠于物理表：NC/WMS 镜像表建表前以调研实测列为准，逐列核对。
- 外部库连接只读：NC65（thin，`NC65_*` env）、WMS（thick，`WMS_*` env，值见 `c:/Project/wms_conn.env`，**密码不得写进任何 repo 文件**）。
- 服务间数据消费走 HTTP + 转发 Bearer token（MdmClient 模式），不直读他服务表。
- 测试连本地 docker `uniops_postgres`（conftest 覆盖 `POSTGRES_*`，防宿主 .env 直连生产库）；mdm 套件与 epms 套件不并发跑。
- Decimal 经 Pydantic 序列化为字符串——前端（Phase 1）须 `Number()`；本期 API 返回保持 Decimal 原样。
- 验证要正面证据：每个任务以"测试通过计数 + 实际数据行数"为完成证据，不以"无输出"当通过。
- 生产部署不在本计划内（Phase 0 结束统一走发布流程 [[reference_uniops_prod_release_workflow]]）。

---

## File Structure（本计划新增/修改总览）

```
mdm-api/
  app/models/material.py            # 新: materials 正式主数据
  app/models/nc_bom.py              # 新: nc_bom / nc_bom_b 原始镜像(列以Task 1调研为准)
  app/models/bom.py                 # 新: boms / bom_lines / bom_substitutes 规范化
  app/models/material_supplier.py   # 新: material_suppliers
  app/models/uom_conversion.py      # 新: uom_conversions
  app/services/material_sync.py     # 新: erp_material → materials 升格同步
  app/services/nc_bom_sync/         # 新: reader.py / transform.py / service.py
  app/services/erp_client.py        # 改: 增 unitTranf 拉取; "Firmus"注释改 NC ERP
  app/api/v1/materials.py           # 新: GET /materials, POST /materials/sync
  app/api/v1/boms.py                # 新: GET /boms/effective, POST /boms/sync
  app/api/v1/material_suppliers.py  # 新: CRUD
  app/api/v1/uom_conversions.py     # 新: GET + POST /sync
  app/core/config.py                # 改: NC65_* 设置
  alembic/versions/mdm_xx_*.py      # 新迁移(挂链尾)
  requirements.txt                  # 改: +oracledb
mrp-api/                            # 新服务(骨架仿 budget-api)
  app/main.py, app/core/{config,authz,deps}.py, app/db/base.py
  app/models/{wms_inventory.py,status_mapping.py,sync_state.py}
  app/services/wms_sync/{reader.py,transform.py,service.py}
  app/api/v1/{admin_sync.py,inventory.py}
  alembic/  (version_table="alembic_version_mrp")
  tests/  (库 mrp_test)
  Dockerfile                        # 打入 Instant Client 19c
docker-compose.dev.yml / docker-compose.prod.yml   # 改: +mrp-api; mdm-api +NC65_* env
identity-api/scripts/seed_authz.py  # 改: +module="mrp" 权限键
docs/superpowers/specs/2026-08-03-nc-bom-survey.md # 新: Task 1 调研产出
```

---

### Task 1: NC65 BOM 表结构调研（阻塞后续 BOM 任务）

**Files:**
- Create: `docs/superpowers/specs/2026-08-03-nc-bom-survey.md`（调研结论）
- Create（临时，不提交）: scratchpad `nc_bom_survey.py`

**Interfaces:**
- Produces: 调研文档，内容至少包含——BOM 表头/子表实际表名与全列清单、版本/生效日期/用量/损耗字段落点、样本 3 行、行数、与 `BD_MATERIAL.pk_material` 的关联方式。Task 4/5 的 reader 与镜像 DDL 以此文档为准。

- [ ] **Step 1: 写调研脚本**（连接值来自 `c:/Project/nc65_conn.env`；只读查询。⚠️ 本步曾被权限分类器拦截，执行时如再被拦，暂停并请用户放行/手工运行）

```python
# scratchpad/nc_bom_survey.py — read-only survey of NC65 BOM tables
import oracledb
oracledb.defaults.fetch_decimals = True
dsn = oracledb.makedsn("10.10.95.67", 1521, service_name="ORCL")
con = oracledb.connect(user="ncsc", password="<NC65_PASSWORD from nc65_conn.env>", dsn=dsn)
cur = con.cursor()
print("== candidate BOM tables ==")
cur.execute("""select owner, table_name, num_rows from all_tables
               where table_name like '%BOM%' order by owner, table_name""")
for r in cur.fetchall(): print(" ", r)
# NC65 标准 BOM 档案通常是 NCSC.BD_BOM(表头)/NCSC.BD_BOM_B(子件行)。逐列打印：
for t in ("BD_BOM", "BD_BOM_B"):
    try:
        cur.execute("""select column_name, data_type from all_tab_columns
                       where owner='NCSC' and table_name=:t order by column_id""", {"t": t})
        cols = cur.fetchall()
        print(f"\n== NCSC.{t} ({len(cols)} cols) ==")
        print("; ".join(f"{c}:{d}" for c, d in cols))
        cur.execute(f"select * from NCSC.{t} where rownum<=3")
        names = [c[0] for c in cur.description]
        for row in cur.fetchall():
            print({k: v for k, v in zip(names, row) if v is not None})
    except Exception as e:
        print(t, "->", str(e).splitlines()[0])
con.close()
```

- [ ] **Step 2: 运行并核对**

Run: `python scratchpad/nc_bom_survey.py`
Expected: 打印候选表清单 + BD_BOM/BD_BOM_B（或实际表）的全列与样本。重点确认字段：母件 pk（关联 BD_MATERIAL）、版本号、生效/失效日期、审批状态、子件 pk、基本用量（如 baseNum）、损耗率（如 wastagerate）、替代料标记。
**级联结构确认（用户已说明业务形态）**：一个产品的 BOM 级联三层——制粉 BOM → 干混 BOM（可选）→ 包装 BOM；检索成品只见包装 BOM，其组件含半成品粉，半成品粉再挂干混/制粉 BOM。调研必须回答：NC 里三类 BOM 靠什么字段/类型码区分（还是仅靠母件物料类型隐式区分）？取一个真实成品，把三层链路（成品→包装 BOM→半成品粉→干混/制粉 BOM→原料）各层样本打印出来验证连通。

- [ ] **Step 3: 写调研文档**

将 Step 2 结果整理进 `docs/superpowers/specs/2026-08-03-nc-bom-survey.md`：表名、列清单（原名+类型）、字段→规范化模型映射表（对齐 Task 5 的 `boms/bom_lines` 列）、样本行、行数、增量水位字段（如 ts/modifiedtime）。若 NC 中无 BOM 数据（行数=0），**停止并上报用户**——这改变 BOM 来源决策。

- [ ] **Step 4: Commit**

```bash
git add docs/superpowers/specs/2026-08-03-nc-bom-survey.md
git commit -m "docs(mrp): NC65 BOM table survey for phase0 mirror design"
```

---

### Task 2: mdm-api `materials` 正式主数据 + erp_material 升格同步

**Files:**
- Create: `mdm-api/app/models/material.py`
- Create: `mdm-api/app/services/material_sync.py`
- Create: `mdm-api/app/api/v1/materials.py`
- Modify: `mdm-api/app/api/v1/__init__.py`（或 router 注册处，先 Read 确认注册方式）
- Create: `mdm-api/alembic/versions/<rev>_add_materials.py`
- Test: `mdm-api/tests/test_materials.py`

**Interfaces:**
- Consumes: 既有 `mdm-api/app/models/erp_material.py`（**先 Read 拿到真实列名**，下述映射按其列名调整）、`app/db/base.py` 的 `UUIDPrimaryKey`/`TimestampMixin`。
- Produces: 表 `materials`；`GET /mdm/v1/materials?page=&page_size=&q=&item_type=`（分页响应 `{items, total, page, page_size}`）；`POST /mdm/v1/materials/sync` 返回 `{created, updated, skipped}`。后续任务以 `materials.code`（String(50) 唯一）为全局物料键。

- [ ] **Step 1: 写失败测试**

```python
# mdm-api/tests/test_materials.py
import pytest
from httpx import AsyncClient

@pytest.mark.anyio
async def test_material_sync_upserts_from_erp_mirror(client: AsyncClient, db_session):
    # seed 一行 erp_material 镜像（列名以 models/erp_material.py 为准）
    from app.models.erp_material import ErpMaterial
    from app.models.material import Material
    db_session.add(ErpMaterial(part_no="CF0086", name="Skim Milk Powder",
                               unit_meas="KGM", exp=24, item_type="10",
                               dim_quality="25kg", part_product_family="BASE"))
    await db_session.commit()
    resp = await client.post("/mdm/v1/materials/sync")
    assert resp.status_code == 200
    assert resp.json()["created"] == 1
    m = (await db_session.execute(
        __import__("sqlalchemy").select(Material).where(Material.code == "CF0086"))).scalar_one()
    assert m.base_uom == "KGM" and m.shelf_life_months == 24
    # 幂等：再跑一次 → updated 而非重复 created
    resp2 = await client.post("/mdm/v1/materials/sync")
    assert resp2.json()["created"] == 0

@pytest.mark.anyio
async def test_materials_list_pagination(client: AsyncClient):
    resp = await client.get("/mdm/v1/materials", params={"page": 1, "page_size": 10})
    assert resp.status_code == 200
    body = resp.json()
    assert {"items", "total", "page", "page_size"} <= body.keys()
```

- [ ] **Step 2: 跑测试确认失败**

Run: `pytest mdm-api/tests/test_materials.py -v`（用 mdm 既有 conftest 的本地测试库 env）
Expected: FAIL（Material 模型/端点不存在）

- [ ] **Step 3: 写模型 + 迁移**

```python
# mdm-api/app/models/material.py
from sqlalchemy import String, Integer, Boolean
from sqlalchemy.orm import Mapped, mapped_column
from app.db.base import Base, UUIDPrimaryKey, TimestampMixin

class Material(Base, UUIDPrimaryKey, TimestampMixin):
    __tablename__ = "materials"
    code: Mapped[str] = mapped_column(String(50), unique=True, index=True)
    name: Mapped[str | None] = mapped_column(String(255))
    spec: Mapped[str | None] = mapped_column(String(255))          # ERP dim_QUALITY
    item_type: Mapped[str | None] = mapped_column(String(20))      # raw/aux/packaging/semi/finished，可后补
    erp_item_type: Mapped[str | None] = mapped_column(String(20))  # ERP 原始 itemType，保底追溯
    base_uom: Mapped[str | None] = mapped_column(String(20))
    shelf_life_months: Mapped[int | None] = mapped_column(Integer)  # ERP exp
    procurement_type: Mapped[str] = mapped_column(String(20), default="purchase")  # purchase/manufacture
    product_family: Mapped[str | None] = mapped_column(String(100))
    factory_code: Mapped[str | None] = mapped_column(String(50))
    erp_id: Mapped[str | None] = mapped_column(String(50), index=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
```

迁移：`cd mdm-api && alembic heads`（确认唯一链尾）→ `alembic revision -m "add materials"`，`down_revision` 填链尾；upgrade 建表+索引，downgrade drop。

- [ ] **Step 4: 写同步服务与端点**

```python
# mdm-api/app/services/material_sync.py
"""erp_material(原始镜像) → materials(正式主数据) 升格同步。ERP 为源；本地治理字段(item_type/procurement_type)不被覆盖。"""
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from app.models.erp_material import ErpMaterial
from app.models.material import Material

_ERP_FIELDS = ("name", "spec", "base_uom", "shelf_life_months", "product_family", "erp_item_type")

def _from_erp(row: ErpMaterial) -> dict:
    # 列名以 models/erp_material.py 实际为准（Task 前置 Read）
    return {
        "code": row.part_no, "name": row.name, "spec": row.dim_quality,
        "base_uom": row.unit_meas, "shelf_life_months": row.exp,
        "product_family": row.part_product_family, "erp_item_type": row.item_type,
        "erp_id": row.part_no,
    }

async def sync_materials(db: AsyncSession) -> dict:
    created = updated = 0
    existing = {m.code: m for m in (await db.execute(select(Material))).scalars()}
    for row in (await db.execute(select(ErpMaterial))).scalars():
        data = _from_erp(row)
        m = existing.get(data["code"])
        if m is None:
            db.add(Material(**data)); created += 1
        else:
            for f in _ERP_FIELDS:
                setattr(m, f, data[f])
            updated += 1
    await db.commit()
    return {"created": created, "updated": updated, "skipped": 0}
```

端点 `mdm-api/app/api/v1/materials.py`：仿既有 `parts.py` 写法（Read 后对齐依赖注入/分页习惯）——`GET /materials` 分页+`q`(code/name ilike)+`item_type` 过滤；`POST /materials/sync` 调 `sync_materials`，权限依 parts.py 同款门禁（若 parts 无写权限门禁则用 `require_permission("data_maintenance")`）。router 注册进 `/mdm/v1`。

- [ ] **Step 5: 跑测试通过 + Commit**

Run: `pytest mdm-api/tests/test_materials.py -v` → PASS（2 passed）；再跑全套 `pytest mdm-api/tests -q` 对基线无新增失败。

```bash
git add mdm-api
git commit -m "feat(mdm): materials master table promoted from erp_material mirror"
```

---

### Task 3: mdm-api `uom_conversions`（ERP unitTranf 镜像）

**Files:**
- Create: `mdm-api/app/models/uom_conversion.py`
- Modify: `mdm-api/app/services/erp_client.py`（`_PATHS` 增 `"unit_tranf": "/firmusData/touch_mdm_mes/unitTranf/getUnitTranfInfo"`；顺手把文件头 `"""HTTP client for the external ERP (Firmus) MDM API."""` 改为 `"""HTTP client for the NC ERP master-data HTTP interface."""`）
- Modify: `mdm-api/app/services/erp_sync.py`（增 conversions 同步，仿 material 同步既有写法——先 Read）
- Create: `mdm-api/app/api/v1/uom_conversions.py`
- Create: 迁移 `<rev>_add_uom_conversions.py`（挂 Task 2 之后的链尾）
- Test: `mdm-api/tests/test_uom_conversions.py`

**Interfaces:**
- Produces: 表 `uom_conversions(from_uom, to_uom, rate)`（unique(from_uom,to_uom)）；`GET /mdm/v1/uom-conversions` 返回全量列表；`POST /mdm/v1/uom-conversions/sync`。Phase 1 引擎按 `rate` 做 BOM 跨单位换算。

- [ ] **Step 1: 失败测试**

```python
# mdm-api/tests/test_uom_conversions.py
import pytest
from decimal import Decimal

@pytest.mark.anyio
async def test_uom_conversion_upsert_and_list(client, db_session, monkeypatch):
    from app.services import erp_sync
    async def fake_fetch(*a, **k):
        return [{"unit_CODE": "GRM", "unit_TYPE": "KGM", "unit_RATE": "0.001"}]
    monkeypatch.setattr(erp_sync, "fetch_unit_tranf", fake_fetch, raising=False)
    resp = await client.post("/mdm/v1/uom-conversions/sync")
    assert resp.status_code == 200
    body = (await client.get("/mdm/v1/uom-conversions")).json()
    assert any(r["from_uom"] == "GRM" and r["to_uom"] == "KGM"
               and Decimal(str(r["rate"])) == Decimal("0.001") for r in body["items"])
```

- [ ] **Step 2: 跑测试失败** → Run: `pytest mdm-api/tests/test_uom_conversions.py -v`，Expected: FAIL
- [ ] **Step 3: 模型+迁移+同步+端点**

```python
# mdm-api/app/models/uom_conversion.py
from sqlalchemy import String, Numeric, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column
from app.db.base import Base, UUIDPrimaryKey, TimestampMixin

class UomConversion(Base, UUIDPrimaryKey, TimestampMixin):
    __tablename__ = "uom_conversions"
    __table_args__ = (UniqueConstraint("from_uom", "to_uom", name="uq_uom_conv"),)
    from_uom: Mapped[str] = mapped_column(String(20))
    to_uom: Mapped[str] = mapped_column(String(20))
    rate: Mapped[object] = mapped_column(Numeric(18, 8))
```

同步：拉 `unit_tranf` 全量，按 (from,to) upsert。端点仿 Task 2。

- [ ] **Step 4: 测试通过 + Commit** → `git commit -m "feat(mdm): uom_conversions mirrored from NC ERP unitTranf"`

---

### Task 4: mdm-api NC65 BOM 原始镜像（reader thin 模式）

**Files:**
- Create: `mdm-api/app/models/nc_bom.py`（表 `nc_bom`/`nc_bom_b`，**列=Task 1 调研文档逐列照抄**，全部 nullable String/Numeric/Date + `nc_source_pk` 唯一索引）
- Create: `mdm-api/app/services/nc_bom_sync/reader.py` + `service.py`
- Modify: `mdm-api/app/core/config.py`（增 `nc_host/nc_port/nc_service/nc_user/nc_password`，env 名 `NC65_HOST` 等——与 epms-api settings 命名对齐，先 Read epms 的 config 确认）
- Modify: `mdm-api/requirements.txt`（+`oracledb`）
- Modify: `docker-compose.dev.yml` / `docker-compose.prod.yml`（mdm-api 段加 `NC_HOST: ${NC65_HOST:-}` 等 5 项，照抄 epms-api 段现成写法）
- Create: 迁移 `<rev>_add_nc_bom_mirror.py`
- Test: `mdm-api/tests/test_nc_bom_sync.py`

**Interfaces:**
- Consumes: Task 1 调研文档的表名/列名/水位字段。
- Produces: `nc_bom`/`nc_bom_b` 原始镜像表；`sync_nc_bom(db) -> {"headers": n, "lines": m}`（reader 失败抛异常、不半写——单事务提交）。`nc_configured() -> bool`（无 NC env 时功能隐藏，惰性安全，仿 nc_purchase_sync）。

- [ ] **Step 1: 失败测试**（reader 打桩，测 transform/写入幂等）

```python
# mdm-api/tests/test_nc_bom_sync.py
import pytest

FAKE_HEADERS = [{"pk_bom": "PK1", "pk_invmandoc": "MAT1", "version": "V1.0",
                 "hstate": "1", "dbegindate": "2026-01-01", "denddate": None}]
FAKE_LINES = [{"pk_bom_b": "PKB1", "pk_bom": "PK1", "pk_invmandoc_b": "MAT2",
               "basenum": "1.05", "wastagerate": "2.5"}]
# 字段名以 Task 1 调研为准——执行本任务前先改此 fixture 对齐真实列名

@pytest.mark.anyio
async def test_nc_bom_sync_idempotent(db_session, monkeypatch):
    from app.services.nc_bom_sync import service
    monkeypatch.setattr(service, "fetch_nc_bom", lambda: {"headers": FAKE_HEADERS, "lines": FAKE_LINES})
    r1 = await service.sync_nc_bom(db_session)
    assert r1 == {"headers": 1, "lines": 1}
    r2 = await service.sync_nc_bom(db_session)   # 再跑不重复插入
    assert r2 == {"headers": 1, "lines": 1}
    from sqlalchemy import select, func
    from app.models.nc_bom import NcBom
    n = (await db_session.execute(select(func.count()).select_from(NcBom))).scalar()
    assert n == 1
```

- [ ] **Step 2: 跑测试失败** → Expected: FAIL（模块不存在）
- [ ] **Step 3: 实现**

```python
# mdm-api/app/services/nc_bom_sync/reader.py
"""Read-only NC65 Oracle reader for BOM headers/lines (thin mode)."""
from app.core.config import settings

def nc_configured() -> bool:
    return all([settings.nc_host, settings.nc_service, settings.nc_user, settings.nc_password])

def fetch_nc_bom() -> dict:
    import oracledb
    oracledb.defaults.fetch_decimals = True
    dsn = oracledb.makedsn(settings.nc_host, settings.nc_port, service_name=settings.nc_service)
    con = oracledb.connect(user=settings.nc_user, password=settings.nc_password, dsn=dsn)
    try:
        cur = con.cursor()
        def rows(sql):
            cur.execute(sql)
            names = [c[0].lower() for c in cur.description]
            return [dict(zip(names, r)) for r in cur.fetchall()]
        # 表名/列清单以 docs/superpowers/specs/2026-08-03-nc-bom-survey.md 为准
        headers = rows("select * from NCSC.BD_BOM")
        lines = rows("select * from NCSC.BD_BOM_B")
        return {"headers": headers, "lines": lines}
    finally:
        con.close()
```

`service.py`：按 `nc_source_pk`（=pk_bom / pk_bom_b）upsert 两表，单事务；行数返回。模型 `nc_bom.py` 逐列照调研文档。迁移挂链尾。

- [ ] **Step 4: 测试通过**；再手工对真库跑一次冒烟（dev 容器内或本机）：`python -c "...sync_nc_bom..."` 记录实际行数作为证据。
- [ ] **Step 5: Commit** → `git commit -m "feat(mdm): NC65 BOM raw mirror (read-only thin-mode sync)"`

---

### Task 5: mdm-api 规范化 `boms`/`bom_lines`/`bom_substitutes` + 转换同步 + 生效版本查询

**Files:**
- Create: `mdm-api/app/models/bom.py`
- Create: `mdm-api/app/services/nc_bom_sync/transform.py`
- Create: `mdm-api/app/api/v1/boms.py`
- Create: 迁移 `<rev>_add_boms.py`
- Test: `mdm-api/tests/test_bom_transform.py`

**Interfaces:**
- Consumes: `nc_bom`/`nc_bom_b`（Task 4）、`materials.code`（Task 2）。NC pk_invmandoc → 物料 code 的解法以调研文档为准（若 nc_bom 只有 pk 需 join NC BD_MATERIAL——则 Task 4 的 reader 需同时拉 `select pk_material, code from NCSC.BD_MATERIAL` 做映射，参照 nc_purchase_sync/reader.py 的 lookup 写法）。
- Produces: 表 `boms(product_material_code, version, status, effective_from/to, yield_rate, nc_source_pk)`、`bom_lines(bom_id, line_no, component_material_code, qty_per, uom, scrap_rate)`、`bom_substitutes(bom_line_id, substitute_material_code, priority, mode='suggest')`；`GET /mdm/v1/boms/effective?product=<code>&date=<YYYY-MM-DD>` 返回单个生效 BOM（头+行嵌套）；`POST /mdm/v1/boms/sync`（先跑 Task 4 原始镜像再转换）。Phase 1 引擎只消费本接口。

- [ ] **Step 1: 失败测试**（transform 纯函数）

```python
# mdm-api/tests/test_bom_transform.py
from app.services.nc_bom_sync.transform import transform
from decimal import Decimal

def test_transform_maps_header_and_lines():
    raw = {"headers": [{"pk_bom": "PK1", "pk_invmandoc": "M1", "version": "V1.0",
                        "hstate": "1", "dbegindate": "2026-01-01", "denddate": None}],
           "lines": [{"pk_bom_b": "PKB1", "pk_bom": "PK1", "pk_invmandoc_b": "M2",
                      "basenum": Decimal("1.05"), "wastagerate": Decimal("2.5")}],
           "material_codes": {"M1": "CF0086", "M2": "CM0040"}}
    out = transform(raw)
    b = out["boms"][0]
    assert b["product_material_code"] == "CF0086" and b["version"] == "V1.0"
    ln = out["lines"][0]
    assert ln["component_material_code"] == "CM0040"
    assert ln["qty_per"] == Decimal("1.05") and ln["scrap_rate"] == Decimal("0.025")  # 2.5% → 0.025

def test_transform_skips_unresolvable_material():
    raw = {"headers": [{"pk_bom": "PK1", "pk_invmandoc": "MISSING", "version": "V1",
                        "hstate": "1", "dbegindate": "2026-01-01", "denddate": None}],
           "lines": [], "material_codes": {}}
    out = transform(raw)
    assert out["boms"] == [] and out["skipped"] == ["PK1"]
```

- [ ] **Step 2: 跑测试失败** → Expected: FAIL
- [ ] **Step 3: 实现模型/转换/端点**

```python
# mdm-api/app/models/bom.py（节选，全列见下）
from sqlalchemy import String, Numeric, Date, Integer, ForeignKey, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship
from app.db.base import Base, UUIDPrimaryKey, TimestampMixin

class Bom(Base, UUIDPrimaryKey, TimestampMixin):
    __tablename__ = "boms"
    __table_args__ = (UniqueConstraint("nc_source_pk", name="uq_boms_nc_pk"),)
    product_material_code: Mapped[str] = mapped_column(String(50), index=True)
    bom_type: Mapped[str | None] = mapped_column(String(20))  # milling/drymix/packaging，NC 区分方式按 Task 1 调研落映射
    version: Mapped[str] = mapped_column(String(30))
    factory_code: Mapped[str | None] = mapped_column(String(50))
    status: Mapped[str] = mapped_column(String(20), default="approved")  # NC hstate 映射
    effective_from: Mapped[object | None] = mapped_column(Date)
    effective_to: Mapped[object | None] = mapped_column(Date)
    yield_rate: Mapped[object] = mapped_column(Numeric(10, 4), default=1)
    nc_source_pk: Mapped[str] = mapped_column(String(50))
    lines: Mapped[list["BomLine"]] = relationship(back_populates="bom", cascade="all, delete-orphan")

class BomLine(Base, UUIDPrimaryKey, TimestampMixin):
    __tablename__ = "bom_lines"
    bom_id: Mapped[str] = mapped_column(ForeignKey("boms.id", ondelete="CASCADE"), index=True)
    line_no: Mapped[int] = mapped_column(Integer, default=0)
    component_material_code: Mapped[str] = mapped_column(String(50), index=True)
    qty_per: Mapped[object] = mapped_column(Numeric(18, 6))
    uom: Mapped[str | None] = mapped_column(String(20))
    scrap_rate: Mapped[object] = mapped_column(Numeric(10, 4), default=0)  # 已归一为小数
    nc_source_pk: Mapped[str | None] = mapped_column(String(50))
    bom: Mapped[Bom] = relationship(back_populates="lines")

class BomSubstitute(Base, UUIDPrimaryKey, TimestampMixin):
    __tablename__ = "bom_substitutes"
    bom_line_id: Mapped[str] = mapped_column(ForeignKey("bom_lines.id", ondelete="CASCADE"), index=True)
    substitute_material_code: Mapped[str] = mapped_column(String(50))
    priority: Mapped[int] = mapped_column(Integer, default=1)
    mode: Mapped[str] = mapped_column(String(10), default="suggest")
```

transform 纯函数：header 按 material_codes 解析物料码（解析不到→skipped 并计数，同步结果暴露 skipped 供异常可见）；`wastagerate` 百分数归一小数；`hstate` → status 映射（调研确认取值后固化，未知值→'inactive'）。service 端 upsert 按 `nc_source_pk`。`GET /boms/effective`：`status='approved' AND effective_from<=date AND (effective_to IS NULL OR effective_to>=date)`，多版本命中取 `effective_from` 最新一条。

- [ ] **Step 4: 测试通过 + 真库冒烟**（行数证据）**+ Commit** → `git commit -m "feat(mdm): canonical BOM tables + NC transform sync + effective-version API"`

---

### Task 6: mdm-api `material_suppliers`（供应参数，手工维护）

**Files:**
- Create: `mdm-api/app/models/material_supplier.py`、`mdm-api/app/api/v1/material_suppliers.py`、迁移
- Test: `mdm-api/tests/test_material_suppliers.py`

**Interfaces:**
- Produces: 表 `material_suppliers(material_code, partner_code, lead_time_days, moq, order_multiple, is_primary, notes)` unique(material_code, partner_code)；CRUD `GET/POST/PATCH/DELETE /mdm/v1/material-suppliers`（写操作 `require_permission("data_maintenance")`）。Phase 1 采购建议按 `is_primary` 取默认供应商与提前期。

- [ ] **Step 1: 失败测试**：POST 建一条(material_code="CM0040", partner_code="0000131", lead_time_days=45, moq=500, is_primary=True) → GET 按 material_code 过滤能查回；同物料重复 partner POST → 409。
- [ ] **Step 2: 跑失败** → **Step 3: 模型（列同上，Numeric(18,4) for moq/order_multiple）+ CRUD 实现（仿既有 partners.py 风格）** → **Step 4: 测试通过 + Commit** → `git commit -m "feat(mdm): material_suppliers supply parameters CRUD"`

---

### Task 7: mrp-api 服务骨架（8011 + alembic_version_mrp + authz + compose + Instant Client）

**Files:**
- Create: `mrp-api/`（目录骨架照抄 `budget-api/`：`app/main.py`, `app/core/config.py`, `app/core/authz.py`, `app/api/deps.py`, `app/db/base.py`, `alembic/`, `tests/conftest.py`, `requirements.txt`, `pytest.ini`, `Dockerfile`）
- Modify: `docker-compose.dev.yml`、`docker-compose.prod.yml`（新增 mrp-api 服务段）
- Test: `mrp-api/tests/test_health.py`

**Interfaces:**
- Produces: 服务骨架——`/api/v1/health` 200；`require_permission` 可用（packages/authz bind）；`alembic -c mrp-api/alembic.ini upgrade head` 使用 `version_table="alembic_version_mrp"`；容器内 oracledb thick 模式可初始化。后续任务在此骨架上加模型/端点。

- [ ] **Step 1: 复制骨架并适配**。以 budget-api 为模板逐文件复制修改：
  - `config.py`：`PROJECT_NAME="UniOps MRP API"`，port 8011，`DATABASE_URL` 用 `POSTGRES_*` `@property` 计算（照抄 budget，含注释）；新增 `wms_host/wms_port/wms_service/wms_user/wms_password`（env `WMS_HOST` 等）与 `oracle_client_lib: str = "/opt/oracle/instantclient_19_28"`（env `ORACLE_CLIENT_LIB`）。
  - `alembic/env.py`：`version_table="alembic_version_mrp"`（照抄 budget 的 `alembic_version_budget` 写法改名）。
  - `main.py`：CORS 用 `ALLOWED_ORIGINS`；路由前缀 `/api/v1`。
  - `tests/conftest.py`：照抄 budget 的测试库模式，库名 **mrp_test**，覆盖 `POSTGRES_*` 指向本地 docker `uniops_postgres`。
- [ ] **Step 2: Dockerfile（含 Instant Client 19c）**

```dockerfile
# mrp-api/Dockerfile —— 构建上下文=仓库根（需要 packages/authz），仿 budget-api/Dockerfile 再加 Oracle 客户端
FROM python:3.12-slim
WORKDIR /app
RUN apt-get update && apt-get install -y --no-install-recommends libaio1 unzip curl \
    && rm -rf /var/lib/apt/lists/*
# WMS(Flux) Oracle ≤11g：python-oracledb 必须 thick 模式，19c 客户端向下兼容 11g
RUN curl -fsSL -o /tmp/ic.zip https://download.oracle.com/otn_software/linux/instantclient/1928000/instantclient-basiclite-linux.x64-19.28.0.0.0dbru.zip \
    && unzip -q /tmp/ic.zip -d /opt/oracle && rm /tmp/ic.zip \
    && echo /opt/oracle/instantclient_19_28 > /etc/ld.so.conf.d/oracle.conf && ldconfig
ENV ORACLE_CLIENT_LIB=/opt/oracle/instantclient_19_28
COPY packages/authz /packages/authz
COPY mrp-api/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt /packages/authz
COPY mrp-api/app ./app
COPY mrp-api/alembic ./alembic
COPY mrp-api/alembic.ini .
EXPOSE 8011
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8011"]
```

（budget-api Dockerfile 若结构不同，以其为准融合上面 Oracle 段。）
- [ ] **Step 3: compose 两份加服务段**：dev 照抄 budget-api 段改名/端口 8011，env 加 `WMS_HOST: ${WMS_HOST:-}` 等 5 项 + `POSTGRES_PASSWORD: ${DB_PASSWORD}`；prod 段用 `x-db-env` anchor + `image: uniops-mrp-api:${TAG}`。根 `.env`（dev）加 WMS_*（值抄 `c:/Project/wms_conn.env`，该文件不进 git）。
- [ ] **Step 4: 健康测试**

```python
# mrp-api/tests/test_health.py
import pytest
@pytest.mark.anyio
async def test_health(client):
    resp = await client.get("/api/v1/health")
    assert resp.status_code == 200
```

Run: `pytest mrp-api/tests -v` → PASS。dev 起容器验证：`docker compose -f docker-compose.dev.yml up -d mrp-api` → `curl localhost:8011/api/v1/health` 200，容器内 `python -c "import oracledb,os;oracledb.init_oracle_client(lib_dir=os.environ['ORACLE_CLIENT_LIB']);print('thick ok')"`。
- [ ] **Step 5: Commit** → `git commit -m "feat(mrp): mrp-api service skeleton with alembic_version_mrp + Oracle thick-mode client"`

---

### Task 8: mrp-api WMS 库存镜像（wms_inventory_lots + 状态映射 + 同步状态）

**Files:**
- Create: `mrp-api/app/models/wms_inventory.py`、`app/models/status_mapping.py`、`app/models/sync_state.py`
- Create: `mrp-api/app/services/wms_sync/reader.py`、`transform.py`、`service.py`
- Create: `mrp-api/app/api/v1/admin_sync.py`（POST 手动触发）、`app/api/v1/inventory.py`（GET 查询镜像）
- Create: 迁移 `mrp01_wms_inventory.py`（mrp 链首）
- Test: `mrp-api/tests/test_wms_transform.py`、`tests/test_wms_sync_service.py`

**Interfaces:**
- Consumes: 设计文档附录 A 的实测源结构：`INV_LOT join INV_LOT_ATT on (ORGANIZATIONID, LOTNUM, CUSTOMERID, SKU)`；LOTATT01=生产日期、LOTATT02=失效日期（'YYYY-MM-DD' 字符串）、LOTATT03=入库日期、LOTATT05=供应商批次、LOTATT08=质量状态、LOTATT13=供应商、LOTATT14=来源单号；QLT_STS 字典 01=Block/02=Release/04=Under Inspection。
- Produces: 表 `wms_inventory_lots`（unique(warehouse_id, material_code, lot_no)）、`mrp_status_mapping`（seed 3 行）、`mrp_sync_state`；`run_wms_sync(db) -> {"lots": n, "synced_at": ...}`；`POST /api/v1/admin/wms-sync`（`require_permission("mrp.param.write")`）；`GET /api/v1/inventory/lots?material_code=&mapped_status=`（`require_permission("mrp.report.view")`，分页）。Phase 1 引擎的可用量口径基于 `mapped_status='available'` 且未过期批次的 `qty - qty_onhold`。

- [ ] **Step 1: 失败测试（transform 纯函数）**

```python
# mrp-api/tests/test_wms_transform.py
from datetime import date
from decimal import Decimal
from app.services.wms_sync.transform import transform_lot

MAPPING = {"01": "hold", "02": "available", "04": "hold"}

def _raw(**over):
    base = {"warehouseid": "CANADA", "sku": "CF0086", "lotnum": "HGC1976532",
            "qty": Decimal("420"), "qtyallocated": Decimal("0"), "qtyonhold": Decimal("0"),
            "lotatt01": "2025-01-03", "lotatt02": "2027-01-02", "lotatt03": "2025-01-20",
            "lotatt05": "20250103 291041001", "lotatt08": "02", "lotatt13": "0000131",
            "lotatt14": "CASN2502100006*189", "edittime": None}
    base.update(over); return base

def test_release_lot_maps_available():
    row = transform_lot(_raw(), MAPPING, today=date(2026, 8, 3))
    assert row["mapped_status"] == "available"
    assert row["expiry_date"] == date(2027, 1, 2) and row["material_code"] == "CF0086"

def test_expired_lot_overrides_to_expired():
    row = transform_lot(_raw(lotatt02="2026-01-01"), MAPPING, today=date(2026, 8, 3))
    assert row["mapped_status"] == "expired"

def test_unknown_status_defaults_hold():
    row = transform_lot(_raw(lotatt08="99"), MAPPING, today=date(2026, 8, 3))
    assert row["mapped_status"] == "hold"

def test_blank_dates_tolerated():
    row = transform_lot(_raw(lotatt01=None, lotatt02=None), MAPPING, today=date(2026, 8, 3))
    assert row["expiry_date"] is None and row["mapped_status"] == "available"
```

- [ ] **Step 2: 跑测试失败** → Expected: FAIL
- [ ] **Step 3: 实现**

```python
# mrp-api/app/services/wms_sync/reader.py
"""Read-only Flux WMS Oracle reader (thick mode — server is Oracle <=11g)."""
import os
from app.core.config import settings

_client_ready = False

def _ensure_thick():
    global _client_ready
    if not _client_ready:
        import oracledb
        oracledb.init_oracle_client(lib_dir=settings.oracle_client_lib)
        _client_ready = True

def wms_configured() -> bool:
    return all([settings.wms_host, settings.wms_service, settings.wms_user, settings.wms_password])

def fetch_inventory() -> list[dict]:
    import oracledb
    _ensure_thick()
    oracledb.defaults.fetch_decimals = True
    dsn = oracledb.makedsn(settings.wms_host, settings.wms_port, service_name=settings.wms_service)
    con = oracledb.connect(user=settings.wms_user, password=settings.wms_password, dsn=dsn)
    try:
        cur = con.cursor()
        cur.execute("""
            select l.warehouseid, l.sku, l.lotnum, l.qty, l.qtyallocated, l.qtyonhold,
                   a.lotatt01, a.lotatt02, a.lotatt03, a.lotatt05, a.lotatt08,
                   a.lotatt13, a.lotatt14, l.edittime
            from INV_LOT l
            join INV_LOT_ATT a
              on a.organizationid = l.organizationid and a.lotnum = l.lotnum
             and a.customerid = l.customerid and a.sku = l.sku
            where l.qty > 0""")
        names = [c[0].lower() for c in cur.description]
        return [dict(zip(names, r)) for r in cur.fetchall()]
    finally:
        con.close()
```

```python
# mrp-api/app/services/wms_sync/transform.py
"""Pure mapping: Flux WMS raw rows -> wms_inventory_lots payloads."""
from datetime import date, datetime

def _d(s):
    if not s:
        return None
    try:
        return datetime.strptime(str(s)[:10], "%Y-%m-%d").date()
    except ValueError:
        return None

def transform_lot(raw: dict, mapping: dict[str, str], today: date) -> dict:
    expiry = _d(raw.get("lotatt02"))
    status = mapping.get(raw.get("lotatt08") or "", "hold")
    if expiry is not None and expiry < today:
        status = "expired"
    return {
        "warehouse_id": raw["warehouseid"], "material_code": raw["sku"], "lot_no": raw["lotnum"],
        "qty": raw["qty"], "qty_allocated": raw.get("qtyallocated") or 0,
        "qty_onhold": raw.get("qtyonhold") or 0,
        "wms_status": raw.get("lotatt08"), "mapped_status": status,
        "production_date": _d(raw.get("lotatt01")), "expiry_date": expiry,
        "inbound_date": _d(raw.get("lotatt03")),
        "supplier_batch": raw.get("lotatt05"), "supplier_code": raw.get("lotatt13"),
        "source_doc": raw.get("lotatt14"), "wms_edit_time": raw.get("edittime"),
    }
```

`service.py`：全量拉取 → 逐行 transform（映射来自 `mrp_status_mapping` 表）→ **整表替换式 upsert**（delete-all-insert 单事务，快照语义，附 `sync_batch_id`=run 时间戳字符串）→ 更新 `mrp_sync_state(source='wms')` 成功水位；异常时写 `last_error` 并抛出。模型：附录 A 列清单（Numeric(18,4) 数量、Date 日期、String 状态）。迁移 `mrp01`（链首，`down_revision=None`）+ seed `mrp_status_mapping` 三行（01→hold, 02→available, 04→hold）。端点两个（权限键先按字符串写好，Task 9 seed 后生效；system_admin 恒通过便于联调）。

- [ ] **Step 4: service 测试**（monkeypatch `fetch_inventory` 返回 2 行 fixture → 断言表行数=2、sync_state 更新、再跑一次仍=2）。Run: `pytest mrp-api/tests -v` → 全部 PASS。
- [ ] **Step 5: dev 冒烟对真库**：容器内 `POST /api/v1/admin/wms-sync`（system_admin token）→ 响应 lots 数与 WMS 实际活跃批次同量级（调研时约 3.5k）；`GET /api/v1/inventory/lots?mapped_status=available` 有数据。记录行数作为证据。
- [ ] **Step 6: Commit** → `git commit -m "feat(mrp): Flux WMS inventory lot mirror with status mapping (read-only thick-mode)"`

---

### Task 9: 权限键 seed（identity，module="mrp"）

**Files:**
- Modify: `identity-api/scripts/seed_authz.py`（**先 Read 全文**，照既有结构加键）
- Test: 手工验证（seed 脚本无既有测试则不新增框架）

**Interfaces:**
- Produces: `permission_defs` 新 8 键（`mrp.demand.write / mrp.run.execute / mrp.proposal.confirm / mrp.proposal.export / mrp.exception.handle / mrp.param.write / mrp.report.view / mdm.bom.write`，前 7 个 module="mrp"，最后一个 module="mdm"），label 英文；不预授任何角色（Portal Access Control 矩阵里勾）。

- [ ] **Step 1**: Read `seed_authz.py`，在 `MODULE_BY_KEY` 与 label 表按现有格式补 8 键。
- [ ] **Step 2**: dev 容器内跑 seed：`docker compose -f docker-compose.dev.yml exec identity-api python scripts/seed_authz.py`；验证：`select key, module from permission_defs where module in ('mrp') or key='mdm.bom.write'` 返回 8 行。
- [ ] **Step 3**: Portal Access Control 页面能看到 MRP 分组（人工确认一眼即可，无前端代码改动——矩阵 UI 是数据驱动的）。
- [ ] **Step 4: Commit** → `git commit -m "feat(identity): seed mrp module permission keys"`

---

### Task 10: 出口验收 + 文档收尾

**Files:**
- Modify: `docs/superpowers/specs/2026-08-03-mrp-subsystem-design.md`（状态标注 Phase 0 完成情况）

**Interfaces:** 无新接口；本任务是设计文档 Phase 0 出口标准的正面验证。

- [ ] **Step 1: 全量测试基线核对**：依次跑 `pytest mdm-api/tests -q`、`pytest mrp-api/tests -q`（不与 epms 套件并发），记录 passed/failed 计数，对比任务开始前基线——不允许新增失败。
- [ ] **Step 2: 出口标准逐条验证（dev 环境，真数据）**：
  1. **级联链路验证**：取一个真实成品 code，`GET /mdm/v1/boms/effective?product=<成品>` 返回包装 BOM（组件含半成品粉+包材）；再以其中的半成品粉 code 查 `GET /boms/effective?product=<半成品粉>` 返回干混或制粉 BOM（组件到原料）——三层链路逐层可跟随 →「成品→包材→原料 BOM 能按日期取版浏览」✅
  2. `GET /api/v1/inventory/lots` 有当日 synced_at 的批次数据，`mapped_status` 分布合理（有 available 也有 hold/expired）→「库存快照进镜像表且时效可见」✅
  3. `GET /mdm/v1/materials?q=` 能查到 NC 物料（行数≈erp_material 镜像量）。
  记录三条证据（截取响应片段/行数）。
- [ ] **Step 3: 设计文档标注**：在 V1.7 修订行后追加一行「Phase 0 实施完成（分支 feature/mrp-phase0-foundations，日期），出口标准验证记录见本任务 commit message」。
- [ ] **Step 4: Commit** → `git commit -m "docs(mrp): phase0 exit criteria verified"`；**汇报用户**：展示证据，征求是否合并 main / push（须用户同意）。

---

## Self-Review 记录

- **Spec 覆盖**：设计文档 Phase 0 五项——NC65 BOM 调研+镜像（Task 1/4/5）、materials 升格（Task 2）、WMS 镜像+状态映射（Task 7/8）、供应参数（Task 6）、单位转换（Task 3）✅；需求 Excel 模板定稿**移入 Phase 1 计划**（与导入向导同任务实现更合理，设计文档 Phase 0 行保留的该项在 Phase 1 计划中落地）。权限键（Task 9）为 Phase 1 前置。
- **类型一致性**：全局物料键 `materials.code: String(50)`；`boms.product_material_code`/`bom_lines.component_material_code`/`material_suppliers.material_code`/`wms_inventory_lots.material_code` 均为 String(50) code 引用 ✅。`transform_lot(raw, mapping, today)` 与测试签名一致 ✅。
- **BOM 级联（用户 2026-08-03 补充）**：制粉→干混（可选）→包装三层级联，成品只挂包装 BOM；`boms.bom_type` 承载层类型；Task 1 调研须验证三层链路连通与类型区分方式；Phase 1 引擎按组件递归展开（不在本计划范围）。
- **已知不确定点（非占位符，有明确决议路径）**：NC BOM 真实表/列名以 Task 1 调研文档为准，Task 4/5 的 fixture 与 reader SQL 在执行时按调研文档对齐——这是镜像纪律的要求，不是缺口。
