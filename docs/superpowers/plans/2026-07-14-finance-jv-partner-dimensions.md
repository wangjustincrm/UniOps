# JV 往来维度(供应商/客户) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** JV 行的往来维度落地——供应商经 mdm `erp_suppliers`、客户经新表 `nc_customers`(NC bd_customer 直导);NC 凭证导入器解析供应商/客户辅助核算写 `partner_id/partner_name`;报表注册 supplier/customer 两维;存量靠 NC Sync Full Reload 重灌。

**Architecture:** spec `2026-07-13-finance-jv-partner-dimensions-design.md`。迁移 0020(nc_customers)+ ErpSupplier 镜像 → 客户导入脚本 → 导入器扩展(动态辅助类型 pk 解析+校验、aux 五元组、行 tuple 14→16)→ DIMENSIONS 注册表改 4 元组(带 code/name 属性名)加两维 → dev 全量重灌实测。

**Tech Stack:** 同前(FastAPI/SQLAlchemy/alembic/pytest;oracledb/psycopg2 导入;React 前端仅 DimExpansion label 微调)。

## Global Constraints

- **分支** `feature/finance-jv-subsystem`;开工前 `git branch --show-current` 确认。
- **⚠️ 工作树有用户未提交 WIP**:只 `git add <指定文件>`,禁止 `-A`/`.`/`stash`/`reset`/`checkout --`。
- **后端测试**:`cd c:/Project/uniops/finance-api && TEST_PG_PASSWORD=7c0a03bb8c2afef690d1852f8dc3a0195932db5f0f1670e9 ./.venv/Scripts/python -m pytest <file> -q`;单进程串行。
- **前端 typecheck**:`cd c:/Project/uniops/finance && npx tsc -p tsconfig.app.json --noEmit --ignoreDeprecations 6.0`,显式 exit 0。
- **新迁移前必查 `alembic heads`**(当前唯一 head=`0019_multi_dim_expand`)。
- **dev 库操作一律容器内跑**(宿主 .env 指生产);NC 导入脚本硬拒 `10.10.50.*`。
- **改 finance-api 代码要 `docker restart uniops_finance_api`**;UI 文案纯英文。
- 行 tuple/execute_values 列数两份导入器(services/nc_sync.py 与 scripts/nc_migration/voucher_import.py)必须同步:**14 → 16**(尾部追加 partner_id, partner_name)。
- 供应商与客户同现一行时 **supplier 优先**(AP 科目挂供应商、AR 挂客户,冲突罕见)。

---

## 现状(实现者需知)

- `erp_suppliers`(mdm 拥有,dev 1,148 行)物理列已核对:id/erp_supplier_code/supplier_name/supplier_address/supplier_tel/supplier_fax/supplier_type/raw_payload/erp_rowversion/synced_at/created_at/updated_at。镜像取子集。
- NC:`bd_supplier`(pk_supplier char(20), code varchar(40));`bd_customer`(pk_customer, code varchar(40), name, ename, enablestate int 2=已启用, modifiedtime)。
- 辅助类型 pk 常量(nc_sync.py/voucher_import.py 头部):AUX_DEPT=`0001Z0100000000005CS`、AUX_COSTCENTER=`1003Z31000000000SP6J`、AUX_IOITEM=`0001Z0100000000005CZ`——假设它们 = `BD_ACCASSITEM.pk_accassitem`,本计划动态解析并**运行时校验**该假设。
- `NcExtract.aux` 现为 `{freevalueid: (dept_code, cc_code, io_code)}` 三元组;`transform` 行 tuple 现 14 元(尾 ba_id);`_run_worker`/脚本 `load()` 的 insert SQL 现 14 槽。
- `_mini_extract()`(tests/test_nc_sync.py)造三元组 aux——**本计划改五元组会连带更新它与既有断言**。
- DIMENSIONS 注册表(crud/account_balance.py `_dimensions()`)现为 `{dim: (column, mirror)}`,消费点:`expand_by_dims`(m.code/m.name)、`account_vouchers`、`list_dims`(c in reg)。本计划改 4 元组 `(column, model, code_attr, name_attr)`。
- 前端 `DimExpansion`(AccountBalancePage.tsx)label:`k.code ? ... : '(none)'`——本计划区分 `(unknown)`(id 有值 code 空)。
- `generate_from_event` 业务路径已从 posting lines 带 partner_id/partner_name,不改。

---

### Task 1: 迁移 0020(nc_customers)+ NcCustomer 模型 + ErpSupplier 镜像

**Files:**
- Create: `finance-api/alembic/versions/0020_nc_customers.py`
- Create: `finance-api/app/models/nc_customer.py`
- Modify: `finance-api/app/models/mirrors.py`(加 ErpSupplier)
- Modify: `finance-api/app/main.py`(模型 import 列表加 `nc_customer`)
- Modify: `finance-api/tests/conftest.py`(镜像 import+create_all 加 ErpSupplier)
- Test: `finance-api/tests/test_account_balance.py`(追加)

**Interfaces:**
- Produces: `app.models.nc_customer.NcCustomer`(表 nc_customers:code String(40) unique not null / name String(255) not null / is_active bool not null default True + UUIDPrimaryKey+TimestampMixin);`mirrors.ErpSupplier`(表 erp_suppliers,子集 erp_supplier_code String(50) / supplier_name String(255))。

- [ ] **Step 1: 验证 alembic 链尾**

Run: `cd c:/Project/uniops/finance-api && ./.venv/Scripts/python -m alembic heads`
Expected: 恰好 `0019_multi_dim_expand (head)`。否则 STOP。

- [ ] **Step 2: 写失败测试**(追加 tests/test_account_balance.py 末尾)

```python
# ── partner dimensions foundations ────────────────────────────────────────────────

async def test_nc_customer_roundtrip_and_unique(db_session):
    from sqlalchemy.exc import IntegrityError
    from app.models.nc_customer import NcCustomer
    db_session.add(NcCustomer(code="CRM027", name="Debang Duoling", is_active=True))
    await db_session.flush()
    db_session.add(NcCustomer(code="CRM027", name="dup", is_active=True))
    with pytest.raises(IntegrityError):
        await db_session.flush()
    await db_session.rollback()


async def test_erp_supplier_mirror_readable(db_session):
    from app.models.mirrors import ErpSupplier
    sid = uuid.uuid4()
    db_session.add(ErpSupplier(id=sid, erp_supplier_code="S001", supplier_name="ACME"))
    await db_session.flush()
    got = (await db_session.execute(select(ErpSupplier).where(
        ErpSupplier.id == sid))).scalar_one()
    assert got.supplier_name == "ACME"
```

- [ ] **Step 3: 跑测试确认失败**

Run: `cd c:/Project/uniops/finance-api && TEST_PG_PASSWORD=7c0a03bb8c2afef690d1852f8dc3a0195932db5f0f1670e9 ./.venv/Scripts/python -m pytest tests/test_account_balance.py -q`
Expected: 新增 2 FAIL(ModuleNotFoundError/ImportError),原 16 PASS

- [ ] **Step 4: 实现**

(a) `app/models/nc_customer.py`(完整文件):

```python
"""NC customer master (bd_customer direct import) — partner dimension lookup.

The ERP integration API has no customer endpoint (v1.1 doc verified;
'vendor' there is manufacturers), so customers import straight from NC.
finance owns this table (migration 0020); scripts/nc_migration/customers_import.py
fills it.
"""
from sqlalchemy import Boolean, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin, UUIDPrimaryKey


class NcCustomer(UUIDPrimaryKey, TimestampMixin, Base):
    __tablename__ = "nc_customers"

    code: Mapped[str] = mapped_column(String(40), unique=True, index=True, nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
```

(b) `app/models/mirrors.py` —— `BudgetAccount` 类后加:

```python
class ErpSupplier(UUIDPrimaryKey, TimestampMixin, Base):
    """Read-only mirror of mdm-api's erp_suppliers (integration-API synced).
    Column SUBSET verified against information_schema 2026-07-14 — resolves the
    supplier partner dimension to code/name."""
    __tablename__ = "erp_suppliers"

    erp_supplier_code: Mapped[str] = mapped_column(String(50), nullable=False)
    supplier_name: Mapped[str] = mapped_column(String(255), nullable=False)
```

(c) 迁移 `alembic/versions/0020_nc_customers.py`(完整文件):

```python
"""nc_customers — NC bd_customer direct import (partner dimension)

Revision ID: 0020_nc_customers
Revises: 0019_multi_dim_expand
Create Date: 2026-07-14
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

revision = "0020_nc_customers"
down_revision = "0019_multi_dim_expand"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "nc_customers",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("code", sa.String(40), nullable=False),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
        sa.UniqueConstraint("code", name="uq_nc_customers_code"),
    )
    op.create_index("ix_nc_customers_code", "nc_customers", ["code"])


def downgrade():
    op.drop_index("ix_nc_customers_code", table_name="nc_customers")
    op.drop_table("nc_customers")
```

(d) `app/main.py` 模型 import 列表按字母序加 `nc_customer`(保留既有项)。
(e) `tests/conftest.py`:mirrors import 加 `ErpSupplier`;create_all tables 列表 `BudgetAccount.__table__,` 后加 `ErpSupplier.__table__,`。

- [ ] **Step 5: 跑测试确认通过**

Run: `cd c:/Project/uniops/finance-api && TEST_PG_PASSWORD=7c0a03bb8c2afef690d1852f8dc3a0195932db5f0f1670e9 ./.venv/Scripts/python -m pytest tests/test_account_balance.py -q`
Expected: 18 passed

- [ ] **Step 6: Commit**

```bash
cd c:/Project/uniops
git add finance-api/alembic/versions/0020_nc_customers.py finance-api/app/models/nc_customer.py finance-api/app/models/mirrors.py finance-api/app/main.py finance-api/tests/conftest.py finance-api/tests/test_account_balance.py
git commit -m "feat(finance): nc_customers table + ErpSupplier mirror (partner dimension foundations)"
```

---

### Task 2: 客户导入脚本 `customers_import.py`

**Files:**
- Create: `finance-api/scripts/nc_migration/customers_import.py`
- Test: `finance-api/tests/test_account_balance.py`(追加纯函数测试)

**Interfaces:**
- Produces: `pick_name(ename, name, code) -> str`(英文→中文→code,可独立 import 测试);CLI `--dry-run/--load`。

- [ ] **Step 1: 写失败测试**(追加)

```python
def test_customer_pick_name():
    import importlib.util, os
    p = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                     "scripts", "nc_migration", "customers_import.py")
    spec = importlib.util.spec_from_file_location("customers_import", p)
    mod = importlib.util.module_from_spec(spec); spec.loader.exec_module(mod)
    assert mod.pick_name("Acme Ltd", "阿克梅", "C1") == "Acme Ltd"
    assert mod.pick_name(None, "阿克梅", "C1") == "阿克梅"
    assert mod.pick_name(" ", "", "C1") == "C1"
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd c:/Project/uniops/finance-api && TEST_PG_PASSWORD=7c0a03bb8c2afef690d1852f8dc3a0195932db5f0f1670e9 ./.venv/Scripts/python -m pytest tests/test_account_balance.py::test_customer_pick_name -q`
Expected: FAIL(FileNotFoundError)

- [ ] **Step 3: 写脚本**(完整文件)

```python
"""NC65 bd_customer → nc_customers import (partner dimension master).

The ERP integration API has no customer endpoint, so customers load straight
from NC (read-only). Idempotent: --load clears nc_customers then reloads.
Refuses 10.10.50.* targets like the sibling importers.

Usage:
    python scripts/nc_migration/customers_import.py --dry-run
    python scripts/nc_migration/customers_import.py --load
"""
import argparse
import re
import sys
import uuid

import oracledb
import psycopg2
from psycopg2.extras import execute_values, register_uuid

register_uuid()

NC_ENV = r"C:\Project\nc65_conn.env"
DEV_DSN = "host=localhost port=5432 dbname=epms user=epms " \
          "password=7c0a03bb8c2afef690d1852f8dc3a0195932db5f0f1670e9"


def pick_name(ename, name, code) -> str:
    """English name first, then Chinese, then the code (COA import convention)."""
    for v in (ename, name):
        if v and str(v).strip():
            return str(v).strip()[:255]
    return str(code)


def _nc_cfg() -> dict:
    cfg = {}
    with open(NC_ENV, encoding="utf-8-sig") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            cfg[k.strip()] = re.sub(r"\s+#.*$", "", v).strip().strip('"').strip("'")
    return cfg


def _nc_connect():
    cfg = _nc_cfg()
    dsn = oracledb.makedsn(cfg["NC65_HOST"], int(cfg.get("NC65_PORT", "1521")),
                           service_name=cfg["NC65_SERVICE"])
    return oracledb.connect(user=cfg["NC65_USER"], password=cfg["NC65_PASSWORD"], dsn=dsn)


def fetch(cur) -> list:
    """-> [(code, name)] for enabled customers (enablestate=2), code-deduped."""
    cur.execute("select code, name, ename from NCSC.BD_CUSTOMER where enablestate = 2")
    out, seen = [], set()
    for code, name, ename in cur.fetchall():
        c = (code or "").strip()
        if not c or c in seen:
            continue
        seen.add(c)
        out.append((c, pick_name(ename, name, c)))
    return out


def load(rows, dsn):
    con = psycopg2.connect(dsn)
    cur = con.cursor()
    cur.execute("delete from nc_customers")
    execute_values(cur,
        "insert into nc_customers (id, code, name, is_active, created_at, updated_at) values %s",
        [(uuid.uuid4(), c, n, True) for c, n in rows],
        template="(%s,%s,%s,%s, now(), now())", page_size=2000)
    con.commit(); con.close()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--load", action="store_true")
    ap.add_argument("--database", default=DEV_DSN)
    args = ap.parse_args()
    if args.load and "10.10.50" in args.database:
        sys.exit("REFUSING: target looks like production (10.10.50.*).")
    con = _nc_connect()
    rows = fetch(con.cursor())
    con.close()
    print(f"NC customers (enabled): {len(rows)}; sample: {rows[:3]}")
    if args.load:
        load(rows, args.database)
        print("loaded into nc_customers.")
    else:
        print("[dry-run] no writes.")


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: 跑测试确认通过 + Commit**

Run: `cd c:/Project/uniops/finance-api && TEST_PG_PASSWORD=7c0a03bb8c2afef690d1852f8dc3a0195932db5f0f1670e9 ./.venv/Scripts/python -m pytest tests/test_account_balance.py::test_customer_pick_name -q`
Expected: 1 passed

```bash
cd c:/Project/uniops
git add finance-api/scripts/nc_migration/customers_import.py finance-api/tests/test_account_balance.py
git commit -m "feat(finance): NC bd_customer -> nc_customers importer"
```

---

### Task 3: NC 凭证导入器扩展(动态类型 pk + 往来解析,双份同构)

**Files:**
- Modify: `finance-api/app/services/nc_sync.py`
- Modify: `finance-api/scripts/nc_migration/voucher_import.py`
- Test: `finance-api/tests/test_nc_sync.py`(改 _mini_extract 五元组 + 新断言 + 类型 pk 校验测试)

**Interfaces:**
- Consumes: Task 1 的 nc_customers/erp_suppliers 表。
- Produces:
  - `nc_sync.resolve_aux_type_pks(items: list[tuple]) -> dict`:输入 `[(pk_accassitem, name), ...]`,输出 `{"department": pk, "cost_center": pk, "income_expense_item": pk, "supplier": pk|None, "customer": pk|None}`(按名称含 部门/成本中心/收支项目/供应商/客户 匹配);**校验**——解析出的 department/cost_center/income_expense_item 三个 pk 与常量 AUX_DEPT/AUX_COSTCENTER/AUX_IOITEM 不一致时 `raise RuntimeError`(带两值),证明 typevalue 前缀=pk_accassitem 的假设。
  - `NcExtract.aux` 值改 **五元组** `(dept_code, cc_code, io_code, sup_code, cust_code)`。
  - `_resolve_dims(...)` 返回 7 元 `(cc_id, dept_id, io_code, ba_id, partner_id, partner_name, had_cc_hint)`;新参数 `uni_sup: dict[code -> (id, name)]`、`uni_cust: dict[code -> (id, name)]`;**supplier 优先**,档案缺失→partner_id None、partner_name=NC code 文本兜底(此处无档案名可用,存 code)。
  - `transform(extract, uni_cc, uni_dept, uni_ba, uni_sup, uni_cust, skip_pks)` 行 tuple **16 元**(尾部 `partner_id, partner_name`)。
  - `fetch_from_nc`:开头查 `select pk_accassitem, name from NCSC.BD_ACCASSITEM` → `resolve_aux_type_pks`;加载 `bd_supplier(pk_supplier→code)`/`bd_customer(pk_customer→code)`;FREEVALUE 循环用解析出的 supplier/customer 类型 pk 提码。
  - `_run_worker`:uni_sup/uni_cust 从本库 `select erp_supplier_code, id, supplier_name from erp_suppliers` / `select code, id, name from nc_customers` 构建;totals 解包 16 位;insert SQL 列清单加 `partner_id, partner_name`,模板 16 槽。缺档案码时 `logger.warning` 计数。
  - `voucher_import.py` 同构全部改动(load_aux 返回五元组、resolve_dims、read_details 16 元、load() SQL、main() 加载 uni_sup/uni_cust、动态类型 pk+校验)。

- [ ] **Step 1: 写失败测试** —— tests/test_nc_sync.py:

(a) `_mini_extract` 的 aux 行改为五元组,并加一个带供应商的行(整个 helper 替换):

```python
def _mini_extract():
    from app.services.nc_sync import NcExtract
    # voucher 1: line 1 both-sided w/ cc+ioitem aux; line 2 payable w/ supplier aux
    return NcExtract(
        ccy={"CADPK": "CAD"},
        aux={"ASS1": ("0104", "E01", "CRM004", "", ""),
             "ASS2": ("", "", "", "SUP01", "")},
        vouchers=[("NCPK1", "2026", "07", 12, "test voucher",
                   "2026-07-10 09:00:00", "2026-07-11 08:00:00")],
        details=[
            ("NCPK1", 1, "5101", 150, 50, 150, 50, "CADPK", 1, "expense", "ASS1"),
            ("NCPK1", 2, "2202", 0, 100, 0, 100, "CADPK", 1, "payable", "ASS2"),
        ],
        max_creationtime="2026-07-11 08:00:00",
    )
```

(b) `test_transform_maps_dims_and_nets_sides`:transform 调用加 `uni_sup={"SUP01": (sup_id := object(), "ACME Supplies")}, uni_cust={}`(用普通变量,不用海象也行);断言追加:

```python
    l2 = next(l for l in lines if l[2] == 2)
    assert l2[14] is sup_id and l2[15] == "ACME Supplies"
    assert l1[14] is None                     # no partner aux on line 1
```

(c) `test_transform_skips_existing_and_counts_unmapped` 的两处 transform 调用补 `uni_sup={}, uni_cust={}`;NcExtract 构造里 aux 改五元组(`{"ASS1": ("", "ZZZ", "", "", "")}`)。

(d) worker 测试(`test_start_run_*`/`test_full_*`/failure 测试)无需改调用(fetch 注入 fake extract,worker 内部构建 uni_sup/uni_cust——空表即空 map;SUP01 无档案→partner_id NULL、partner_name='SUP01' 文本兜底)。给第一个 worker 测试追加断言:

```python
    assert _pg("select partner_name from journal_voucher_lines "
               "where account_code = '2202'")[0][0] == "SUP01"
```

(e) 新测试:

```python
def test_resolve_aux_type_pks_validates_constants():
    from app.services.nc_sync import (AUX_COSTCENTER, AUX_DEPT, AUX_IOITEM,
                                      resolve_aux_type_pks)
    items = [(AUX_DEPT, "部门"), (AUX_COSTCENTER, "成本中心"), (AUX_IOITEM, "收支项目"),
             ("SUPPK0000000000000001"[:20], "供应商档案"), ("CUSPK0000000000000001"[:20], "客户档案")]
    got = resolve_aux_type_pks(items)
    assert got["supplier"] and got["customer"]
    assert got["department"] == AUX_DEPT
    # constant mismatch -> hard error (typevalue-prefix == pk_accassitem assumption)
    bad = [("WRONGPK0000000000001"[:20], "部门"), (AUX_COSTCENTER, "成本中心"),
           (AUX_IOITEM, "收支项目")]
    with pytest.raises(RuntimeError):
        resolve_aux_type_pks(bad)
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd c:/Project/uniops/finance-api && TEST_PG_PASSWORD=7c0a03bb8c2afef690d1852f8dc3a0195932db5f0f1670e9 ./.venv/Scripts/python -m pytest tests/test_nc_sync.py -q`
Expected: 多个 FAIL(五元组解包/新函数缺失),定位后进入实现

- [ ] **Step 3: 实现 services/nc_sync.py**

(a) 常量区后加:

```python
_AUX_NAME_SLOTS = {
    "department": "部门", "cost_center": "成本中心", "income_expense_item": "收支项目",
    "supplier": "供应商", "customer": "客户",
}
_AUX_CONSTANTS = {"department": AUX_DEPT, "cost_center": AUX_COSTCENTER,
                  "income_expense_item": AUX_IOITEM}


def resolve_aux_type_pks(items) -> dict:
    """[(pk_accassitem, name)] -> {slot: pk}. Validates the known three against
    the frozen constants — proves GL_FREEVALUE's typevalue prefix IS
    pk_accassitem; a mismatch means the assumption broke: stop, don't guess."""
    out: dict = {}
    for slot, needle in _AUX_NAME_SLOTS.items():
        out[slot] = next((pk for pk, name in items if needle in (name or "")), None)
    for slot, const in _AUX_CONSTANTS.items():
        if out.get(slot) and out[slot] != const:
            raise RuntimeError(
                f"aux type pk mismatch for {slot}: resolved {out[slot]!r} != "
                f"constant {const!r} — typevalue-prefix assumption broke")
    return out
```

(b) `NcExtract.aux` 注释改五元组;`_resolve_dims` 整函数替换:

```python
def _resolve_dims(assid, aux, uni_cc, uni_dept, uni_ba, uni_sup, uni_cust):
    """-> (cc_id, dept_id, io_code, ba_id, partner_id, partner_name, had_cc_hint).
    Supplier wins over customer when both appear (AP accounts carry suppliers,
    AR customers; a clash is NC data noise). Missing master row -> partner_id
    None with the NC code kept as partner_name text."""
    d, c, io, sup, cust = aux.get(assid, ("", "", "", "", ""))
    epms = CC_BY_CODE.get(c) if c else CC_BY_DEPT.get(d)
    partner_id = partner_name = None
    code = sup or cust
    if code:
        hit = (uni_sup.get(sup) if sup else None) or (uni_cust.get(cust) if cust else None)
        if hit:
            partner_id, partner_name = hit
        else:
            partner_name = code
    return (uni_cc.get(epms) if epms else None,
            uni_dept.get(d) if d else None,
            io or None,
            uni_ba.get(io) if io else None,
            partner_id, partner_name,
            bool(c or d))
```

(c) `transform` 签名加 `uni_sup: dict, uni_cust: dict`(在 uni_ba 后、skip_pks 前);行循环:

```python
        cc_id, dept_id, io_code, ba_id, partner_id, partner_name, had_hint = _resolve_dims(
            assid, extract.aux, uni_cc, uni_dept, uni_ba, uni_sup, uni_cust)
```

lines.append 尾部 `cc_id, dept_id, ba_id))` → `cc_id, dept_id, ba_id, partner_id, partner_name))`。

(d) `fetch_from_nc`:aux 段前加:

```python
        cur.execute("select pk_accassitem, name from NCSC.BD_ACCASSITEM")
        type_pks = resolve_aux_type_pks(list(cur.fetchall()))
        aux_sup_pk, aux_cust_pk = type_pks.get("supplier"), type_pks.get("customer")

        cur.execute("select pk_supplier, code from NCSC.BD_SUPPLIER")
        sup_codes = {pk: code for pk, code in cur.fetchall()}
        cur.execute("select pk_customer, code from NCSC.BD_CUSTOMER")
        cust_codes = {pk: code for pk, code in cur.fetchall()}
```

FREEVALUE 循环里 `dcode = ccode = iocode = ""` 改 `dcode = ccode = iocode = supcode = custcode = ""`,分支追加:

```python
                elif aux_sup_pk and tpk == aux_sup_pk:
                    supcode = sup_codes.get(vpk, "")
                elif aux_cust_pk and tpk == aux_cust_pk:
                    custcode = cust_codes.get(vpk, "")
```

`aux[fid] = (dcode, ccode, iocode, supcode, custcode)`。

(e) `_run_worker`:uni maps 段加:

```python
        cur.execute("select erp_supplier_code, id, supplier_name from erp_suppliers")
        uni_sup = {c: (i, n) for c, i, n in cur.fetchall()}
        cur.execute("select code, id, name from nc_customers")
        uni_cust = {c: (i, n) for c, i, n in cur.fetchall()}
```

(erp_suppliers 缺表用与 budget_accounts 相同的 savepoint 降级 + warning——测试库 conftest 已建镜像表,正常命中。)transform 调用传入;totals 解包 16 位(再加两个 `_`);lines insert SQL 列清单 `income_expense_item_id,` 后加 `partner_id, partner_name,`;模板 16 槽。

- [ ] **Step 4: 同构改 voucher_import.py**(load_aux 返回五元组+动态类型 pk+校验[复制 resolve_aux_type_pks 逻辑或从 app.services.nc_sync import——脚本 cwd=finance-api 时可 `from app.services.nc_sync import resolve_aux_type_pks`,用 import 免双份];resolve_dims 7 元;read_details 16 元;load() SQL 16 槽;main() 里 `load_uniops_map` 之外加 uni_sup/uni_cust 两个查询(psycopg2 直查,code→(id,name)))。

- [ ] **Step 5: 跑测试确认通过**

Run: `cd c:/Project/uniops/finance-api && TEST_PG_PASSWORD=7c0a03bb8c2afef690d1852f8dc3a0195932db5f0f1670e9 ./.venv/Scripts/python -m pytest tests/test_nc_sync.py tests/test_journal_voucher.py -q`
Expected: 全 PASS

- [ ] **Step 6: Commit**

```bash
cd c:/Project/uniops
git add finance-api/app/services/nc_sync.py finance-api/scripts/nc_migration/voucher_import.py finance-api/tests/test_nc_sync.py
git commit -m "feat(finance): NC importers resolve supplier/customer partner dims (dynamic aux type pks + validation)"
```

---

### Task 4: 报表注册 supplier/customer + 前端 (unknown) 标签

**Files:**
- Modify: `finance-api/app/crud/account_balance.py`(注册表 4 元组化 + 两新维)
- Modify: `finance/src/pages/finance/AccountBalancePage.tsx`(DimExpansion label)
- Test: `finance-api/tests/test_account_balance.py`(追加)

**Interfaces:**
- Produces: `_dimensions()` 返回 `{dim: (column, model, code_attr, name_attr)}`:
  - cost_center/department/income_expense_item → `(..., "code", "name")`
  - `"supplier": (JournalVoucherLine.partner_id, ErpSupplier, "erp_supplier_code", "supplier_name")`
  - `"customer": (JournalVoucherLine.partner_id, NcCustomer, "code", "name")`
  - 消费点 expand_by_dims 的 `m.code/m.name` 改 `getattr(m, code_attr)/getattr(m, name_attr)`;`account_vouchers`/`list_dims` 的 `reg[d][0]`/`c in reg` 不变。

- [ ] **Step 1: 写失败测试**(追加)

```python
async def test_expand_by_supplier_and_customer(db_session):
    from app.models.mirrors import ErpSupplier
    from app.models.nc_customer import NcCustomer
    sup_id, cust_id, ghost = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    db_session.add(ErpSupplier(id=sup_id, erp_supplier_code="S001", supplier_name="ACME"))
    db_session.add(NcCustomer(id=cust_id, code="CRM027", name="Debang", is_active=True))
    await db_session.flush()

    async def _ev(account, amount, pid):
        occurred = datetime(2026, 7, 15, tzinfo=timezone.utc)
        await emit_event(
            db_session, source_service="finance", source_doc_type="ap_invoice",
            source_doc_id=uuid.uuid4(), source_doc_number="AP-1", event_type="accrual",
            occurred_at=occurred, prepared_by=uuid.uuid4(),
            lines=[{"line_role": "purchase_expense", "account_code": "5000",
                    "debit": Decimal(amount), "currency": "CAD"},
                   {"line_role": "accounts_payable", "account_code": account,
                    "credit": Decimal(amount), "currency": "CAD", "partner_id": pid}])

    await _ev("2202", "100.00", sup_id)
    await _ev("2202", "40.00", ghost)          # no master row -> (unknown)
    await jv_crud.backfill_posted_jvs(db_session)

    exp = await ab.expand_by_dims(db_session, "2202", "2026-07", ["supplier"])
    by_id = {r["keys"][0]["id"]: r for r in exp["rows"]}
    assert by_id[str(sup_id)]["keys"][0]["code"] == "S001"
    assert by_id[str(sup_id)]["keys"][0]["name"] == "ACME"
    assert by_id[str(ghost)]["keys"][0]["code"] is None      # unknown master

    exp_c = await ab.expand_by_dims(db_session, "2202", "2026-07", ["customer"])
    assert str(cust_id) not in {r["keys"][0]["id"] for r in exp_c["rows"]}  # no data yet
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd c:/Project/uniops/finance-api && TEST_PG_PASSWORD=7c0a03bb8c2afef690d1852f8dc3a0195932db5f0f1670e9 ./.venv/Scripts/python -m pytest tests/test_account_balance.py::test_expand_by_supplier_and_customer -q`
Expected: FAIL(BadDims: unknown dims ['supplier'])

- [ ] **Step 3: 实现 crud** —— `_dimensions()` 整函数替换:

```python
def _dimensions():
    """dim_code -> (jv_lines column, master model, code attr, name attr).
    supplier/customer share the partner_id column — coa_aux_items keeps them
    apart per account (AP accounts carry suppliers, AR customers)."""
    from app.models.mirrors import BudgetAccount, CostCenter, Department, ErpSupplier
    from app.models.nc_customer import NcCustomer
    return {
        "cost_center": (JournalVoucherLine.cost_center_id, CostCenter, "code", "name"),
        "department": (JournalVoucherLine.department_id, Department, "code", "name"),
        "income_expense_item": (JournalVoucherLine.income_expense_item_id, BudgetAccount, "code", "name"),
        "supplier": (JournalVoucherLine.partner_id, ErpSupplier, "erp_supplier_code", "supplier_name"),
        "customer": (JournalVoucherLine.partner_id, NcCustomer, "code", "name"),
    }
```

`expand_by_dims` 里 lookups 构建与 keys 构造改用 4 元组:

```python
    for i, d in enumerate(dims):
        ids = {row[i] for row in raw if row[i] is not None}
        model = reg[d][1]
        lookups[d] = ({r.id: r for r in (await db.execute(
            select(model).where(model.id.in_(ids)))).scalars()} if ids else {})
    ...
        for i, d in enumerate(dims):
            vid = row[i]
            m = lookups[d].get(vid)
            _, _, code_attr, name_attr = reg[d]
            keys.append({"dim_code": d, "id": str(vid) if vid else None,
                         "code": getattr(m, code_attr) if m else None,
                         "name": getattr(m, name_attr) if m else None})
```

(`account_vouchers`/`list_dims` 消费 `reg[d][0]` 与 `c in reg`,4 元组不影响。)

- [ ] **Step 4: 前端 (unknown) 标签** —— AccountBalancePage.tsx `DimExpansion` 的 label 行替换:

```tsx
        const label = row.keys.map((k) =>
          k.code ? `${k.code}${k.name ? ' · ' + k.name : ''}`
                 : k.id ? '(unknown)' : '(none)').join('  |  ')
```

- [ ] **Step 5: 跑测试 + typecheck 确认通过**

Run: `cd c:/Project/uniops/finance-api && TEST_PG_PASSWORD=7c0a03bb8c2afef690d1852f8dc3a0195932db5f0f1670e9 ./.venv/Scripts/python -m pytest tests/test_account_balance.py -q`
Expected: 20 passed
Run: `cd c:/Project/uniops/finance && npx tsc -p tsconfig.app.json --noEmit --ignoreDeprecations 6.0`
Expected: exit 0

- [ ] **Step 6: Commit**

```bash
cd c:/Project/uniops
git add finance-api/app/crud/account_balance.py finance/src/pages/finance/AccountBalancePage.tsx finance-api/tests/test_account_balance.py
git commit -m "feat(finance): register supplier/customer dimensions + (unknown) label for unresolved partners"
```

---

### Task 5: dev 执行(控制器亲自)—— 迁移/客户导入/Full Reload/实测/回归

- [ ] **Step 1: 容器内迁移 0020 + 重启**

```bash
docker restart uniops_finance_api && sleep 8
docker exec uniops_finance_api python -m alembic upgrade head
docker restart uniops_finance_api
```

- [ ] **Step 2: 客户导入**(宿主跑,目标本地 DSN;权限拦则交用户)

```bash
cd c:/Project/uniops/finance-api
./.venv/Scripts/python scripts/nc_migration/customers_import.py --dry-run
./.venv/Scripts/python scripts/nc_migration/customers_import.py --load
```

- [ ] **Step 3: NC Sync Full Reload**(UI 点或 API 触发,confirm="FULL RELOAD";39,896 张重灌数分钟,轮询 status 到 success;重灌后逐科目净额应与重灌前一致——沿用对账 SQL 抽查 2024-06 试算合计)

- [ ] **Step 4: 实测**:`GET /gl/account-balance/2202/dims`(supplier supported:true);`/expand?period=2024-06&dims=supplier` 出分组带 S 码与名称;AR 科目(如 1122/6001 挂 customer 的)按 customer 展开;与该科目单维 CC 合计一致。

- [ ] **Step 5: 全量回归 + tsc**(同前命令清单)

---

## Self-Review 记录

- **Spec 覆盖**:§2(T1)、§3(T2)、§4 动态 pk/五元组/16 槽/兜底(T3)、§5 注册+(unknown)(T4)、§6 验证(各任务+T5)。§7 范围外未越界。
- **Placeholder 扫描**:通过。
- **类型一致性**:`resolve_aux_type_pks` 名称/返回(T3 定义与测试);五元组 aux(T3 内部一致);行 tuple 16 元(transform/worker SQL/脚本/测试 l2[14]/l2[15]);`_dimensions()` 4 元组(T4 定义,消费点全列出);`uni_sup/uni_cust` code→(id,name)(T3 worker/脚本/测试注入一致)。
- **已知取舍**:partner 档案缺失时 partner_name 存 NC code(FREEVALUE 侧无档案名可取,拿 code 兜底);supplier/customer 共用 partner_id 列(靠 coa_aux_items 按科目区分);worker 的 erp_suppliers 查询按 budget_accounts 先例做 savepoint 降级。
