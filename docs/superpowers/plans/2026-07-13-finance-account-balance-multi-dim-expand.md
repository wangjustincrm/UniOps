# 科目余额表通用多维展开 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 科目余额表按「该科目在 NC 挂的辅助核算项」任选子集动态展开(CC/部门/收支项目纯列 GROUP BY)+ 组合钻取;收支项目提列到 `journal_voucher_lines`;`coa_aux_items` 从 NC BD_ACCASS 导入。

**Architecture:** spec `2026-07-13-finance-account-balance-multi-dim-expand-design.md`。迁移 0019(列+回填+coa_aux_items)→ 写路径三处同步提列 → `crud/account_balance.py` 维度注册表 + expand/vouchers 泛化 + dims 端点 → NC 挂接导入脚本 → 前端维度勾选弹层与通用展开。Budget Actual 不动。

**Tech Stack:** FastAPI + SQLAlchemy async + alembic + pytest;React 19 + react-query;oracledb/psycopg2(导入脚本)。

## Global Constraints

- **分支** `feature/finance-jv-subsystem`;开工前 `git branch --show-current` 确认。
- **⚠️ 工作树有用户未提交 WIP**(epms/、epms-api/、expense-api/、portal/):**只 `git add <指定文件>`,禁止 `-A`/`.`/`stash`/`reset`/`checkout --`**。
- **后端测试**:`cd c:/Project/uniops/finance-api && TEST_PG_PASSWORD=7c0a03bb8c2afef690d1852f8dc3a0195932db5f0f1670e9 ./.venv/Scripts/python -m pytest <file> -q`;单进程串行,禁止后台/并发。
- **前端 typecheck**:`cd c:/Project/uniops/finance && npx tsc -p tsconfig.app.json --noEmit --ignoreDeprecations 6.0`,显式确认 exit 0。
- **新迁移前必查 `alembic heads`**(当前唯一 head=`0018_nc_sync_runs`;Task 1 内含验证步)。
- **⚠️ dev 库操作(迁移/导入)一律容器内跑**——宿主 finance-api/.env 指向生产;NC 导入脚本硬拒 `10.10.50.*`。
- **改 finance-api 代码要 `docker restart uniops_finance_api`**;UI 文案纯英文;金额字符串前端 Number() 强转。
- 报表只统计 `status='posted'` JV,本位币 local_debit/local_credit。
- 浮层弹层遵循 portal 规范(createPortal 到 body / 或居中 modal)。

---

## 现状(实现者需知)

- `crud/account_balance.py`:`account_balance`(①)、`expand_by_cost_center`(旧②,将被 `expand_by_dims` 取代)、`account_vouchers`(③,现只支持 cost_center_id)、`budget_actual`(④,内部用 `_by_cost_center`,**保留不动**)。API 在 `api/v1/account_balance.py`(4 端点)。
- JV 行维度现状:`cost_center_id`/`department_id` 是 `journal_voucher_lines` 列;收支项目在 `jv_line_dimensions`(dim_code='income_expense_item', value_id=budget_account id, value_text=CRM code,dev 库 66,315 行)。
- 镜像:`mirrors.CostCenter`(code/name)、`mirrors.Department`(code/name)已存在并在 conftest 注册;**BudgetAccount 镜像不存在**(budget_accounts 物理列 2026-07-13 核对:id/code/name/is_active/l1_id/created_at/updated_at/description/decomposition_enabled/sort_order/created_by/updated_by;镜像取子集 code/name/is_active)。
- `emit_event`(services/posting.py)行支持 `aux` dict → posting_line_dimensions;`generate_from_event`(services/journal_voucher.py 116-123 行)把 posting_line_dimensions 拷成 jv_line_dimensions。
- NC 导入器两份:`services/nc_sync.py` 的 `transform`(行 13-tuple:`(lid, jid, line_no, acct, summary, odr, ocr, ldr, lcr, ccy, rate, cc_id, dept_id)`,`resolve` 已算出 `ba_id` 但只进 dims)+ `_run_worker` 的 insert SQL;`scripts/nc_migration/voucher_import.py` 同构。
- NC 挂接链(字典已核对):`BD_ACCASS(pk_coveraccasoa→BD_ACCASOA.pk_accasoa; BD_ACCASOA.pk_account→科目; pk_entity→BD_ACCASSITEM(pk_accassitem/name/code)`;科目表 chart=`1001A1100000003CG6GD`;NC 连接 env 文件 `C:\Project\nc65_conn.env`(照抄 voucher_import.py 的 `_nc_cfg/_nc_connect`)。
- 现测试:`tests/test_account_balance.py`(9 个;其中 `test_expand_by_cost_center`/`test_account_vouchers_drilldown` 在 Task 3 改造)。

---

### Task 1: 迁移 0019(提列+回填+coa_aux_items)+ 模型 + BudgetAccount 镜像

**Files:**
- Create: `finance-api/alembic/versions/0019_multi_dim_expand.py`
- Modify: `finance-api/app/models/journal_voucher.py`(行模型加列)
- Modify: `finance-api/app/models/coa.py`(加 `CoaAuxItem`)
- Modify: `finance-api/app/models/mirrors.py`(加 `BudgetAccount`)
- Modify: `finance-api/tests/conftest.py`(镜像 import+create_all 加 BudgetAccount)
- Test: `finance-api/tests/test_account_balance.py`(追加)

**Interfaces:**
- Produces: `JournalVoucherLine.income_expense_item_id: uuid|None`;`app.models.coa.CoaAuxItem`(表 coa_aux_items:account_code String(10)/dim_code String(40)/seq int,UNIQUE(account_code, dim_code));`mirrors.BudgetAccount`(表 budget_accounts,列 code String(50)/name String(255)/is_active bool + UUIDPrimaryKey+TimestampMixin)。

- [ ] **Step 1: 验证 alembic 链尾**

Run: `cd c:/Project/uniops/finance-api && ./.venv/Scripts/python -m alembic heads`
Expected: 恰好 `0018_nc_sync_runs (head)`。否则 STOP 上报。

- [ ] **Step 2: 写失败测试**(追加到 tests/test_account_balance.py 末尾)

```python
# ── multi-dim expand foundations (Task 1) ─────────────────────────────────────────

async def test_jv_line_income_expense_item_column(db_session):
    from app.models.journal_voucher import JournalVoucher, JournalVoucherLine
    from datetime import date
    ba = uuid.uuid4()
    jv = JournalVoucher(jv_number="JV-209901-0001", voucher_word="JV",
                        voucher_date=date(2099, 1, 1), fiscal_period="2099-01",
                        status="posted")
    db_session.add(jv); await db_session.flush()
    ln = JournalVoucherLine(jv_id=jv.id, line_no=1, account_code="5101",
                            local_debit=Decimal("1.00"), currency="CAD",
                            fx_rate=Decimal("1"), income_expense_item_id=ba)
    db_session.add(ln); await db_session.flush()
    got = (await db_session.execute(select(JournalVoucherLine).where(
        JournalVoucherLine.id == ln.id))).scalar_one()
    assert got.income_expense_item_id == ba


async def test_coa_aux_item_roundtrip_and_unique(db_session):
    from sqlalchemy.exc import IntegrityError
    from app.models.coa import CoaAuxItem
    db_session.add(CoaAuxItem(account_code="5101", dim_code="cost_center", seq=1))
    await db_session.flush()
    db_session.add(CoaAuxItem(account_code="5101", dim_code="cost_center", seq=2))
    with pytest.raises(IntegrityError):
        await db_session.flush()
    await db_session.rollback()
```

- [ ] **Step 3: 跑测试确认失败**

Run: `cd c:/Project/uniops/finance-api && TEST_PG_PASSWORD=7c0a03bb8c2afef690d1852f8dc3a0195932db5f0f1670e9 ./.venv/Scripts/python -m pytest tests/test_account_balance.py -q`
Expected: 新增 2 个 FAIL(TypeError: unexpected keyword `income_expense_item_id` / ImportError CoaAuxItem),原 9 个 PASS

- [ ] **Step 4: 模型改动**

(a) `app/models/journal_voucher.py` —— `JournalVoucherLine` 的 `item_id` 行后加:

```python
    income_expense_item_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), nullable=True)  # 收支项目提列 (multi-dim expand)
```

(b) `app/models/coa.py` 末尾加(该文件已有 Base/UUIDPrimaryKey/TimestampMixin/String/Integer imports,缺则补):

```python
class CoaAuxItem(UUIDPrimaryKey, TimestampMixin, Base):
    """科目→辅助核算项挂接(从 NC BD_ACCASS 导入,只读;报表据此列可勾维度)。"""
    __tablename__ = "coa_aux_items"
    __table_args__ = (UniqueConstraint("account_code", "dim_code",
                                       name="uq_coa_aux_items_acct_dim"),)

    account_code: Mapped[str] = mapped_column(String(10), nullable=False, index=True)
    dim_code: Mapped[str] = mapped_column(String(40), nullable=False)
    seq: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
```

(c) `app/models/mirrors.py` —— `Department` 类后加:

```python
class BudgetAccount(UUIDPrimaryKey, TimestampMixin, Base):
    """Read-only mirror of budget_accounts (budget-api owns schema). Column
    SUBSET verified against information_schema 2026-07-13 — resolves the
    income_expense_item dimension (CRM code) to a display name."""
    __tablename__ = "budget_accounts"

    code: Mapped[str] = mapped_column(String(50), nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
```

(d) `tests/conftest.py`:mirrors import 列表加 `BudgetAccount`;create_all tables 列表 `Department.__table__,` 后加 `BudgetAccount.__table__,`。

- [ ] **Step 5: 写迁移**(`alembic/versions/0019_multi_dim_expand.py`,完整文件)

```python
"""multi-dim expand: promote income_expense_item to jv_lines + coa_aux_items

Revision ID: 0019_multi_dim_expand
Revises: 0018_nc_sync_runs
Create Date: 2026-07-13
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

revision = "0019_multi_dim_expand"
down_revision = "0018_nc_sync_runs"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("journal_voucher_lines",
                  sa.Column("income_expense_item_id", UUID(as_uuid=True), nullable=True))
    op.create_index("ix_jv_lines_ioitem", "journal_voucher_lines", ["income_expense_item_id"])
    # data backfill: KV side-table -> the new column (KV rows stay, audit value_text)
    op.execute(
        "update journal_voucher_lines l set income_expense_item_id = d.value_id "
        "from jv_line_dimensions d "
        "where d.jv_line_id = l.id and d.dim_code = 'income_expense_item' "
        "and d.value_id is not null")

    op.create_table(
        "coa_aux_items",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("account_code", sa.String(10), nullable=False),
        sa.Column("dim_code", sa.String(40), nullable=False),
        sa.Column("seq", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
        sa.UniqueConstraint("account_code", "dim_code", name="uq_coa_aux_items_acct_dim"),
    )
    op.create_index("ix_coa_aux_items_account_code", "coa_aux_items", ["account_code"])


def downgrade():
    op.drop_index("ix_coa_aux_items_account_code", table_name="coa_aux_items")
    op.drop_table("coa_aux_items")
    op.drop_index("ix_jv_lines_ioitem", table_name="journal_voucher_lines")
    op.drop_column("journal_voucher_lines", "income_expense_item_id")
```

- [ ] **Step 6: 跑测试确认通过**

Run: `cd c:/Project/uniops/finance-api && TEST_PG_PASSWORD=7c0a03bb8c2afef690d1852f8dc3a0195932db5f0f1670e9 ./.venv/Scripts/python -m pytest tests/test_account_balance.py -q`
Expected: 11 passed

- [ ] **Step 7: Commit(surgical add)**

```bash
cd c:/Project/uniops
git add finance-api/alembic/versions/0019_multi_dim_expand.py finance-api/app/models/journal_voucher.py finance-api/app/models/coa.py finance-api/app/models/mirrors.py finance-api/tests/conftest.py finance-api/tests/test_account_balance.py
git commit -m "feat(finance): promote income_expense_item to jv_lines + coa_aux_items table (migration 0019)"
```

(⚠️ 本 Task **不**对 dev 库跑迁移——Task 6 容器内统一执行。)

---

### Task 2: 写路径提列(generate_from_event + NC 导入器双份)

**Files:**
- Modify: `finance-api/app/services/journal_voucher.py`(dims 拷贝处)
- Modify: `finance-api/app/services/nc_sync.py`(transform 行 tuple + worker insert SQL + totals 解包)
- Modify: `finance-api/scripts/nc_migration/voucher_import.py`(同构两处)
- Test: `finance-api/tests/test_journal_voucher.py` + `finance-api/tests/test_nc_sync.py`(改断言)

**Interfaces:**
- Consumes: Task 1 的 `income_expense_item_id` 列。
- Produces: nc_sync `transform` 行 tuple 变 **14 元**:`(lid, jid, line_no, acct, summary, odr, ocr, ldr, lcr, ccy, rate, cc_id, dept_id, ioitem_id)`(ioitem_id=ba_id 追加在尾);`_run_worker`/脚本 insert SQL 列清单加 `income_expense_item_id`。KV dims 照旧双写。

- [ ] **Step 1: 写失败测试**

(a) `tests/test_journal_voucher.py` 末尾追加:

```python
async def test_generate_from_event_promotes_income_expense_item(db_session):
    """aux dim 'income_expense_item' lands BOTH in jv_line_dimensions (audit)
    and in the promoted jv_lines column (queries read the column)."""
    from app.services.posting import emit_event
    from app.models.journal_voucher import JournalVoucher, JournalVoucherLine

    ba_id = uuid.uuid4()
    ev_id = await emit_event(
        db_session, source_service="finance", source_doc_type="ap_invoice",
        source_doc_id=uuid.uuid4(), source_doc_number="AP-9", event_type="accrual",
        prepared_by=uuid.uuid4(),
        lines=[{"line_role": "purchase_expense", "account_code": "5101",
                "debit": Decimal("10.00"), "currency": "CAD",
                "aux": {"income_expense_item": {"value_id": ba_id, "value_text": "CRM004"}}},
               {"line_role": "accounts_payable", "account_code": "2000",
                "credit": Decimal("10.00"), "currency": "CAD"}])
    jv = (await db_session.execute(select(JournalVoucher).where(
        JournalVoucher.posting_event_id == ev_id))).scalar_one()
    ln = (await db_session.execute(select(JournalVoucherLine).where(
        JournalVoucherLine.jv_id == jv.id, JournalVoucherLine.account_code == "5101"
    ))).scalar_one()
    assert ln.income_expense_item_id == ba_id
```

(b) `tests/test_nc_sync.py` 的 `test_transform_maps_dims_and_nets_sides` 里,`assert l1[11] is cc_id and l1[12] is dept_id` 行后加:

```python
    assert l1[13] is ba_id            # promoted income/expense item column
```

`test_start_run_incremental_inserts_and_sets_watermark` 的第一个 run 断言后追加一行(验证 worker 真把列写进库):

```python
    assert _pg("select count(*) from journal_voucher_lines "
               "where income_expense_item_id is not null")[0][0] == 1
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd c:/Project/uniops/finance-api && TEST_PG_PASSWORD=7c0a03bb8c2afef690d1852f8dc3a0195932db5f0f1670e9 ./.venv/Scripts/python -m pytest tests/test_journal_voucher.py tests/test_nc_sync.py -q`
Expected: 新增断言 FAIL(列为 None / IndexError l1[13]),其余 PASS

- [ ] **Step 3: 实现**

(a) `services/journal_voucher.py` —— dims 拷贝循环(约 116-123 行)整体替换:

```python
    # copy long-tail dimensions; income_expense_item is ALSO promoted onto the
    # line column (queries read the column; the KV row keeps the code text).
    for jl, pl_id in line_map:
        dims = (await db.execute(
            select(PostingLineDimension).where(PostingLineDimension.posting_line_id == pl_id)
        )).scalars().all()
        for d in dims:
            db.add(JvLineDimension(jv_line_id=jl.id, dim_code=d.dim_code,
                                   value_id=d.value_id, value_text=d.value_text))
            if d.dim_code == "income_expense_item" and d.value_id is not None:
                jl.income_expense_item_id = d.value_id
```

(b) `services/nc_sync.py` `transform` —— lines.append 的 tuple 尾部 `cc_id, dept_id))` 改为 `cc_id, dept_id, ba_id))`。

(c) `services/nc_sync.py` `_run_worker`:
- totals 解包 `for _, jid, _, _, _, dr, crr, ldr, lcr, _, _, _, _ in lines:` → 末尾加一个 `_`(14 个位置)。
- lines 的 execute_values SQL 列清单 `... cost_center_id, department_id,` 后加 `income_expense_item_id,`;template `(%s,...,%s, now(), now())` 相应加一个 `%s`(共 14 个)。

(d) `scripts/nc_migration/voucher_import.py`:
- `read_details` 里 `lines.append((..., cc_id, dept_id))` → `..., cc_id, dept_id, ba_id))`。
- `load()` totals 解包加一个 `_`;insert SQL 列清单加 `income_expense_item_id`;template 加一个 `%s`。

- [ ] **Step 4: 跑测试确认通过**

Run: `cd c:/Project/uniops/finance-api && TEST_PG_PASSWORD=7c0a03bb8c2afef690d1852f8dc3a0195932db5f0f1670e9 ./.venv/Scripts/python -m pytest tests/test_journal_voucher.py tests/test_nc_sync.py -q`
Expected: 全 PASS(原有 + 新断言)

- [ ] **Step 5: Commit**

```bash
cd c:/Project/uniops
git add finance-api/app/services/journal_voucher.py finance-api/app/services/nc_sync.py finance-api/scripts/nc_migration/voucher_import.py finance-api/tests/test_journal_voucher.py finance-api/tests/test_nc_sync.py
git commit -m "feat(finance): write income_expense_item to the promoted jv_lines column (emit + NC importers)"
```

---

### Task 3: 报表后端 —— 维度注册表 + expand/vouchers 泛化 + dims 端点

**Files:**
- Modify: `finance-api/app/crud/account_balance.py`
- Modify: `finance-api/app/api/v1/account_balance.py`
- Test: `finance-api/tests/test_account_balance.py`(改造 2 个 + 新增 5 个)

**Interfaces:**
- Consumes: Task 1 模型;`mirrors.CostCenter/Department/BudgetAccount`。
- Produces(前端 Task 5 依赖):
  - crud 常量:`DIMENSIONS: dict[str, tuple[column, mirror_model]]`(cost_center/department/income_expense_item)与 `DIM_LABELS: dict[str, str]`(含 supplier/customer/employee/project 的英文名,供 dims 端点 label)。
  - `class BadDims(ValueError)`(非法维度;API 层转 422)。
  - `expand_by_dims(db, account_code, period, dims: list[str]) -> dict`:`{account_code, period, dims, rows: [{keys: [{dim_code, id: str|None, code: str|None, name: str|None}], amount: str}]}`。
  - `account_vouchers(db, account_code, period, dims_values: dict[str, uuid.UUID|None]) -> dict`(签名改造,原 cost_center_id 参数删除;value None=该维为 NULL)。
  - `list_dims(db, account_code) -> dict`:`{account_code, dims: [{dim_code, label, supported}]}`(coa_aux_items 按 seq;无配置行 → DIMENSIONS 全集 fallback)。
  - API:`GET /gl/account-balance/{code}/dims`;`GET .../expand?period&dims=a,b`(缺省 cost_center;BadDims→422);`GET .../vouchers?period&dims_values=dim:val,...`(val=`none` → NULL;缺省=不过滤;BadDims→422)。
  - 旧 `expand_by_cost_center` 删除(`budget_actual` 的内部 `_by_cost_center` 保留)。

- [ ] **Step 1: 改造+新增测试** —— tests/test_account_balance.py:

(a) 把 `test_expand_by_cost_center` 整个替换为:

```python
async def test_expand_single_dim_matches_old_behavior(db_session):
    cc1 = await _cc(db_session, "MOH-01")
    cc2 = await _cc(db_session, "MOH-02")
    await _posted_cc_event(db_session, "5101", cc1, "100.00")
    await _posted_cc_event(db_session, "5101", cc2, "50.00")
    await jv_crud.backfill_posted_jvs(db_session)
    exp = await ab.expand_by_dims(db_session, "5101", "2026-07", ["cost_center"])
    by = {r["keys"][0]["code"]: r for r in exp["rows"]}
    assert by["MOH-01"]["amount"] == "100.00"
    assert by["MOH-02"]["amount"] == "50.00"
    assert by["MOH-01"]["keys"][0]["dim_code"] == "cost_center"
```

(b) 把 `test_account_vouchers_drilldown` 整个替换为:

```python
async def test_account_vouchers_drilldown_by_dims(db_session):
    cc1 = await _cc(db_session, "MOH-01")
    await _posted_cc_event(db_session, "5101", cc1, "77.00")
    await jv_crud.backfill_posted_jvs(db_session)
    v = await ab.account_vouchers(db_session, "5101", "2026-07",
                                  dims_values={"cost_center": cc1})
    assert len(v["rows"]) == 1
    assert v["rows"][0]["local_debit"] == "77.00"
    assert v["rows"][0]["jv_number"].startswith("JV-")
```

(c) 末尾追加(helper `_posted_dim_event` 造带三维的行;`BudgetAccount` 镜像种子):

```python
# ── generic multi-dim expansion (Task 3) ──────────────────────────────────────────
from app.models.mirrors import BudgetAccount, Department


async def _posted_dim_event(db, account, amount, cc_id=None, dept_id=None, ba_id=None,
                            period="2026-07"):
    occurred = datetime(int(period[:4]), int(period[5:7]), 15, tzinfo=timezone.utc)
    line = {"line_role": "purchase_expense", "account_code": account,
            "debit": Decimal(amount), "currency": "CAD"}
    if cc_id:
        line["cost_center_id"] = cc_id
    if dept_id:
        line["department_id"] = dept_id
    if ba_id:
        line["aux"] = {"income_expense_item": {"value_id": ba_id, "value_text": "X"}}
    await emit_event(
        db, source_service="finance", source_doc_type="ap_invoice",
        source_doc_id=uuid.uuid4(), source_doc_number="AP-1", event_type="accrual",
        occurred_at=occurred, prepared_by=uuid.uuid4(),
        lines=[line, {"line_role": "accounts_payable", "account_code": "2000",
                      "credit": Decimal(amount), "currency": "CAD"}])


async def test_expand_two_dims_and_none_group(db_session):
    cc = await _cc(db_session, "MOH-01")
    ba = uuid.uuid4()
    db_session.add(BudgetAccount(id=ba, code="CRM004", name="Depreciation", is_active=True))
    await db_session.flush()
    await _posted_dim_event(db_session, "5101", "100.00", cc_id=cc, ba_id=ba)
    await _posted_dim_event(db_session, "5101", "40.00", cc_id=cc)          # no ioitem
    await jv_crud.backfill_posted_jvs(db_session)
    exp = await ab.expand_by_dims(db_session, "5101", "2026-07",
                                  ["cost_center", "income_expense_item"])
    assert len(exp["rows"]) == 2
    rows = {tuple((k["dim_code"], k["code"]) for k in r["keys"]): r["amount"]
            for r in exp["rows"]}
    assert rows[(("cost_center", "MOH-01"), ("income_expense_item", "CRM004"))] == "100.00"
    assert rows[(("cost_center", "MOH-01"), ("income_expense_item", None))] == "40.00"
    # name resolution
    named = next(r for r in exp["rows"]
                 if r["keys"][1]["code"] == "CRM004")
    assert named["keys"][1]["name"] == "Depreciation"


async def test_expand_rejects_unknown_dim(db_session):
    with pytest.raises(ab.BadDims):
        await ab.expand_by_dims(db_session, "5101", "2026-07", ["bananas"])


async def test_vouchers_filter_none_and_combo(db_session):
    cc = await _cc(db_session, "MOH-01")
    ba = uuid.uuid4()
    await _posted_dim_event(db_session, "5101", "100.00", cc_id=cc, ba_id=ba)
    await _posted_dim_event(db_session, "5101", "40.00", cc_id=cc)
    await jv_crud.backfill_posted_jvs(db_session)
    v_none = await ab.account_vouchers(db_session, "5101", "2026-07",
                                       dims_values={"cost_center": cc,
                                                    "income_expense_item": None})
    assert [r["local_debit"] for r in v_none["rows"]] == ["40.00"]
    v_hit = await ab.account_vouchers(db_session, "5101", "2026-07",
                                      dims_values={"income_expense_item": ba})
    assert [r["local_debit"] for r in v_hit["rows"]] == ["100.00"]


async def test_dims_endpoint_config_and_fallback(client, db_session):
    from app.models.coa import CoaAuxItem
    db_session.add_all([
        CoaAuxItem(account_code="5101", dim_code="cost_center", seq=1),
        CoaAuxItem(account_code="5101", dim_code="supplier", seq=2),
    ])
    await db_session.flush()
    r = await client.get("/finance/v1/gl/account-balance/5101/dims", headers=_h())
    dims = r.json()["dims"]
    assert [d["dim_code"] for d in dims] == ["cost_center", "supplier"]
    assert dims[0]["supported"] is True and dims[1]["supported"] is False
    # unconfigured account falls back to the full supported registry
    r2 = await client.get("/finance/v1/gl/account-balance/9999/dims", headers=_h())
    assert {d["dim_code"] for d in r2.json()["dims"]} == {
        "cost_center", "department", "income_expense_item"}


async def test_expand_endpoint_dims_param(client, db_session):
    cc = await _cc(db_session, "MOH-01")
    await _posted_cc_event(db_session, "5101", cc, "60.00")
    await jv_crud.backfill_posted_jvs(db_session)
    r = await client.get(
        "/finance/v1/gl/account-balance/5101/expand?period=2026-07&dims=cost_center",
        headers=_h())
    assert r.status_code == 200, r.text
    assert r.json()["rows"][0]["amount"] == "60.00"
    r422 = await client.get(
        "/finance/v1/gl/account-balance/5101/expand?period=2026-07&dims=bananas",
        headers=_h())
    assert r422.status_code == 422
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd c:/Project/uniops/finance-api && TEST_PG_PASSWORD=7c0a03bb8c2afef690d1852f8dc3a0195932db5f0f1670e9 ./.venv/Scripts/python -m pytest tests/test_account_balance.py -q`
Expected: 新/改测试 FAIL(`AttributeError: expand_by_dims` 等),test_budget_actual* 仍 PASS

- [ ] **Step 3: crud 实现** —— `crud/account_balance.py`:

删除 `expand_by_cost_center`,在 `_cc_map` 附近替换/新增(保留 `_by_cost_center`/`budget_actual`/`_net`/`account_balance` 不动;`account_vouchers` 整函数替换):

```python
# ── generic multi-dim expansion (能力② full) ────────────────────────────────────

class BadDims(ValueError):
    """Unknown/unsupported dimension code in a request."""


def _dimensions():
    """dim_code -> (jv_lines column, mirror model). Registry — adding a future
    dimension (supplier/customer) is one line here once its column+mirror exist."""
    from app.models.mirrors import BudgetAccount, CostCenter, Department
    return {
        "cost_center": (JournalVoucherLine.cost_center_id, CostCenter),
        "department": (JournalVoucherLine.department_id, Department),
        "income_expense_item": (JournalVoucherLine.income_expense_item_id, BudgetAccount),
    }


DIM_LABELS = {
    "cost_center": "Cost Center", "department": "Department",
    "income_expense_item": "Income/Expense Item", "supplier": "Supplier",
    "customer": "Customer", "employee": "Employee", "project": "Project",
}


def _check_dims(dims: list[str]) -> dict:
    reg = _dimensions()
    bad = [d for d in dims if d not in reg]
    if bad or not dims:
        raise BadDims(f"unknown or empty dims: {bad or dims}")
    return reg


async def expand_by_dims(db: AsyncSession, account_code: str, period: str,
                         dims: list[str]) -> dict:
    """② dynamic expansion: GROUP BY the chosen dimension columns (all promoted
    columns — no KV join), resolve each id to code/name via its mirror."""
    reg = _check_dims(dims)
    cols = [reg[d][0] for d in dims]
    q = (select(*cols,
                func.coalesce(func.sum(JournalVoucherLine.local_debit), 0),
                func.coalesce(func.sum(JournalVoucherLine.local_credit), 0))
         .join(JournalVoucher, JournalVoucherLine.jv_id == JournalVoucher.id)
         .where(JournalVoucher.status == POSTED,
                JournalVoucher.fiscal_period == period,
                JournalVoucherLine.account_code == account_code)
         .group_by(*cols))
    raw = (await db.execute(q)).all()

    # batch-load mirror rows per dimension
    lookups: dict[str, dict] = {}
    for i, d in enumerate(dims):
        ids = {row[i] for row in raw if row[i] is not None}
        model = reg[d][1]
        lookups[d] = ({r.id: r for r in (await db.execute(
            select(model).where(model.id.in_(ids)))).scalars()} if ids else {})

    rows = []
    for row in raw:
        keys = []
        for i, d in enumerate(dims):
            vid = row[i]
            m = lookups[d].get(vid)
            keys.append({"dim_code": d, "id": str(vid) if vid else None,
                         "code": m.code if m else None, "name": m.name if m else None})
        rows.append({"keys": keys, "amount": _s(_net(row[len(dims)], row[len(dims) + 1]))})
    rows.sort(key=lambda r: tuple(k["code"] or "￿" for k in r["keys"]))
    return {"account_code": account_code, "period": period, "dims": dims, "rows": rows}


async def list_dims(db: AsyncSession, account_code: str) -> dict:
    """Dims checkable for an account: coa_aux_items config (NC BD_ACCASS import),
    falling back to the full supported registry for unconfigured accounts."""
    from app.models.coa import CoaAuxItem
    reg = _dimensions()
    items = (await db.execute(
        select(CoaAuxItem).where(CoaAuxItem.account_code == account_code)
        .order_by(CoaAuxItem.seq))).scalars().all()
    codes = [i.dim_code for i in items] if items else list(reg.keys())
    return {"account_code": account_code, "dims": [
        {"dim_code": c, "label": DIM_LABELS.get(c, c), "supported": c in reg}
        for c in codes]}


async def account_vouchers(db: AsyncSession, account_code: str, period: str,
                           dims_values: dict | None = None) -> dict:
    """③ drill-down: posted JV lines for an account, optionally filtered by a
    dimension-value combo ({dim_code: uuid | None}; None = IS NULL)."""
    q = (select(JournalVoucherLine, JournalVoucher)
         .join(JournalVoucher, JournalVoucherLine.jv_id == JournalVoucher.id)
         .where(JournalVoucher.status == POSTED,
                JournalVoucher.fiscal_period == period,
                JournalVoucherLine.account_code == account_code)
         .order_by(JournalVoucher.voucher_date))
    if dims_values:
        reg = _check_dims(list(dims_values.keys()))
        for d, v in dims_values.items():
            col = reg[d][0]
            q = q.where(col.is_(None) if v is None else col == v)
    rows = []
    for ln, jv in (await db.execute(q)).all():
        rows.append({
            "jv_id": str(jv.id), "jv_number": jv.jv_number,
            "voucher_date": jv.voucher_date.isoformat(),
            "summary": ln.summary or jv.summary,
            "local_debit": str(ln.local_debit), "local_credit": str(ln.local_credit),
            "cost_center_id": str(ln.cost_center_id) if ln.cost_center_id else None,
            "source_doc_type": jv.source_doc_type,
            "source_doc_id": str(jv.source_doc_id) if jv.source_doc_id else None,
            "source_doc_number": jv.source_doc_number,
        })
    return {"account_code": account_code, "period": period, "rows": rows}
```

- [ ] **Step 4: API 实现** —— `api/v1/account_balance.py` 的 expand/vouchers 两端点整体替换 + 新增 dims 端点(`HTTPException` 进 import):

```python
@router.get("/account-balance/{account_code}/dims")
async def dims(account_code: str, _: CurrentUser, db: AsyncSession = Depends(get_db)):
    """Checkable aux dimensions for an account (coa_aux_items ∩ registry)."""
    return await crud.list_dims(db, account_code)


@router.get("/account-balance/{account_code}/expand")
async def expand(account_code: str, _: CurrentUser, db: AsyncSession = Depends(get_db),
                 period: str = Query(...),
                 dims: str = Query(default="cost_center")):
    """② dynamic expansion by a comma-separated dimension subset."""
    try:
        return await crud.expand_by_dims(db, account_code, period,
                                         [d.strip() for d in dims.split(",") if d.strip()])
    except crud.BadDims as e:
        raise HTTPException(status_code=422, detail=str(e))


@router.get("/account-balance/{account_code}/vouchers")
async def vouchers(account_code: str, _: CurrentUser, db: AsyncSession = Depends(get_db),
                   period: str = Query(...),
                   dims_values: str | None = Query(default=None)):
    """③ drill-down; dims_values = 'dim:uuid,dim:none' combo filter."""
    parsed: dict = {}
    if dims_values:
        for pair in dims_values.split(","):
            if not pair.strip():
                continue
            if ":" not in pair:
                raise HTTPException(status_code=422, detail=f"bad dims_values pair: {pair!r}")
            d, v = pair.split(":", 1)
            try:
                parsed[d.strip()] = None if v.strip() == "none" else uuid.UUID(v.strip())
            except ValueError:
                raise HTTPException(status_code=422, detail=f"bad uuid in dims_values: {v!r}")
    try:
        return await crud.account_vouchers(db, account_code, period, dims_values=parsed)
    except crud.BadDims as e:
        raise HTTPException(status_code=422, detail=str(e))
```

- [ ] **Step 5: 跑测试确认通过**

Run: `cd c:/Project/uniops/finance-api && TEST_PG_PASSWORD=7c0a03bb8c2afef690d1852f8dc3a0195932db5f0f1670e9 ./.venv/Scripts/python -m pytest tests/test_account_balance.py -q`
Expected: 16 passed(11 + 5 新)

- [ ] **Step 6: Commit**

```bash
cd c:/Project/uniops
git add finance-api/app/crud/account_balance.py finance-api/app/api/v1/account_balance.py finance-api/tests/test_account_balance.py
git commit -m "feat(finance): generic multi-dim expansion + combo drill-down + per-account dims endpoint"
```

---

### Task 4: NC BD_ACCASS 挂接导入脚本

**Files:**
- Create: `finance-api/scripts/nc_migration/aux_items_import.py`
- Test: `finance-api/tests/test_account_balance.py`(追加映射纯函数测试)

**Interfaces:**
- Consumes: NC 连接惯例(照抄 voucher_import.py 的 `_nc_cfg/_nc_connect/NC_ENV/DEV_DSN`);`coa_aux_items` 表。
- Produces: `map_assitem_name(name: str, code: str) -> str`(可独立 import 测试);CLI `--dry-run`/`--load`。

- [ ] **Step 1: 写失败测试**(追加到 tests/test_account_balance.py 末尾)

```python
def test_aux_item_name_mapping():
    import importlib.util, os
    p = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                     "scripts", "nc_migration", "aux_items_import.py")
    spec = importlib.util.spec_from_file_location("aux_items_import", p)
    mod = importlib.util.module_from_spec(spec); spec.loader.exec_module(mod)
    f = mod.map_assitem_name
    assert f("部门", "bm") == "department"
    assert f("成本中心", "cbzx") == "cost_center"
    assert f("收支项目", "szxm") == "income_expense_item"
    assert f("供应商", "gys") == "supplier"
    assert f("客户", "kh") == "customer"
    assert f("神秘档案", "SomeCode") == "somecode"     # unknown -> code slug
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd c:/Project/uniops/finance-api && TEST_PG_PASSWORD=7c0a03bb8c2afef690d1852f8dc3a0195932db5f0f1670e9 ./.venv/Scripts/python -m pytest tests/test_account_balance.py::test_aux_item_name_mapping -q`
Expected: FAIL(FileNotFoundError)

- [ ] **Step 3: 写脚本**(完整文件)

```python
"""NC65 BD_ACCASS → coa_aux_items import (which aux dims each account carries).

Chain: BD_ACCASS.pk_coveraccasoa -> BD_ACCASOA.pk_accasoa (-> pk_account)
       BD_ACCASS.pk_entity      -> BD_ACCASSITEM (name/code)
Account codes come from BD_ACCOUNT within chart 1001A1100000003CG6GD.
Idempotent: --load clears coa_aux_items then reloads (small table).
Read-only on NC; refuses 10.10.50.* targets like the sibling importers.

Usage:
    python scripts/nc_migration/aux_items_import.py --dry-run
    python scripts/nc_migration/aux_items_import.py --load
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
CHART = "1001A1100000003CG6GD"
DEV_DSN = "host=localhost port=5432 dbname=epms user=epms " \
          "password=7c0a03bb8c2afef690d1852f8dc3a0195932db5f0f1670e9"

# NC 辅助项名称 -> our dim_code (curated); unknown names fall back to a slug of
# the NC item code so nothing silently disappears.
NAME_MAP = {
    "部门": "department", "成本中心": "cost_center", "收支项目": "income_expense_item",
    "供应商": "supplier", "客户": "customer", "人员": "employee", "职员": "employee",
    "项目": "project",
}


def map_assitem_name(name: str, code: str) -> str:
    for key, dim in NAME_MAP.items():
        if key in (name or ""):
            return dim
    return re.sub(r"[^a-z0-9_]+", "_", (code or "unknown").strip().lower()).strip("_")


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


def fetch(cur) -> tuple[list, dict]:
    """-> (rows [(account_code, dim_code, seq)], distinct {assitem name: dim_code})."""
    cur.execute(
        "select acc.code, item.name, item.code "
        "from NCSC.BD_ACCASS a "
        "join NCSC.BD_ACCASOA soa on soa.pk_accasoa = a.pk_coveraccasoa "
        "join NCSC.BD_ACCOUNT acc on acc.pk_account = soa.pk_account "
        "join NCSC.BD_ACCASSITEM item on item.pk_accassitem = a.pk_entity "
        "where acc.pk_accchart = :c", c=CHART)
    seen: dict[tuple, int] = {}
    names: dict[str, str] = {}
    rows = []
    for acct_code, iname, icode in cur.fetchall():
        dim = map_assitem_name(iname, icode)
        names[f"{iname} ({icode})"] = dim
        key = (acct_code, dim)
        if key in seen:
            continue
        seen[key] = 1
        rows.append((acct_code, dim, len([r for r in rows if r[0] == acct_code]) + 1))
    return rows, names


def load(rows, dsn):
    con = psycopg2.connect(dsn); con.autocommit = False
    cur = con.cursor()
    cur.execute("delete from coa_aux_items")
    execute_values(cur,
        "insert into coa_aux_items (id, account_code, dim_code, seq, created_at, updated_at) "
        "values %s",
        [(uuid.uuid4(), a, d, s) for a, d, s in rows],
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
    rows, names = fetch(con.cursor())
    con.close()
    print(f"NC aux links: {len(rows)} (account, dim) pairs across "
          f"{len({r[0] for r in rows})} accounts")
    print("assitem name -> dim_code mapping observed:")
    for k, v in sorted(names.items()):
        print(f"  {k} -> {v}")
    if args.load:
        load(rows, args.database)
        print("loaded into coa_aux_items.")
    else:
        print("[dry-run] no writes.")


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: 跑测试确认通过**

Run: `cd c:/Project/uniops/finance-api && TEST_PG_PASSWORD=7c0a03bb8c2afef690d1852f8dc3a0195932db5f0f1670e9 ./.venv/Scripts/python -m pytest tests/test_account_balance.py::test_aux_item_name_mapping -q`
Expected: 1 passed

- [ ] **Step 5: Commit**

```bash
cd c:/Project/uniops
git add finance-api/scripts/nc_migration/aux_items_import.py finance-api/tests/test_account_balance.py
git commit -m "feat(finance): NC BD_ACCASS -> coa_aux_items importer (per-account aux dims)"
```

---

### Task 5: 前端 —— 维度勾选弹层 + 通用展开 + 钻取组合过滤

**Files:**
- Modify: `finance/src/pages/finance/AccountBalancePage.tsx`
- Modify: `finance/src/pages/finance/AccountVouchersModal.tsx`(costCenterId → dimsValues)
- Modify: `finance/src/pages/finance/BudgetActualPage.tsx`(钻取参数适配)

**Interfaces:**
- Consumes: Task 3 的 `/dims`、`/expand?dims=`、`/vouchers?dims_values=` 契约。
- Produces: `AccountVouchersModal({ accountCode, period, dimsValues, title, onClose, onOpenJv }: { accountCode: string; period: string; dimsValues?: string | null; title: string; onClose: () => void; onOpenJv: (jvId: string) => void })`。

- [ ] **Step 1: 改 `AccountVouchersModal.tsx`** —— props 与查询串两处:

```tsx
export function AccountVouchersModal({ accountCode, period, dimsValues, title, onClose, onOpenJv }: {
  accountCode: string; period: string; dimsValues?: string | null
  title: string; onClose: () => void; onOpenJv: (jvId: string) => void
}) {
  const qs = dimsValues
    ? `?period=${period}&dims_values=${encodeURIComponent(dimsValues)}`
    : `?period=${period}`
  const { data, isLoading } = useQuery({
    queryKey: ['ab-vouchers', accountCode, period, dimsValues ?? ''],
    queryFn: () => financeApi.get<VouchersResp>(`/gl/account-balance/${accountCode}/vouchers${qs}`),
  })
```

(其余渲染不变。)

- [ ] **Step 2: 改 `BudgetActualPage.tsx`** —— `Drill` 接口与两处传参:

`interface Drill { accountCode: string; costCenterId?: string | null; title: string }` 改为:

```tsx
interface Drill { accountCode: string; dimsValues?: string | null; title: string }
```

行内 Vouchers 按钮 onClick 的 `costCenterId: r.cost_center_id,` 改为:

```tsx
                                      dimsValues: `cost_center:${r.cost_center_id ?? 'none'}`,
```

页尾 `<AccountVouchersModal ... costCenterId={drill.costCenterId} ...>` 改为 `dimsValues={drill.dimsValues}`。

- [ ] **Step 3: 改 `AccountBalancePage.tsx`**(整文件替换;在 Plan 4 版本基础上:展开状态从 Set<code> 变 Map<code, dims[]>,新增 DimPicker 弹层与通用 DimExpansion;缓存失效 handler 保留)

```tsx
/**
 * Account Balance report (科目余额表) — 能力①②③ with generic multi-dim expansion.
 * Rows expand by any subset of the account's configured aux dimensions
 * (/dims → checkbox picker → /expand?dims=a,b → grouped rows), and every
 * expansion row drills into its composing vouchers with a dims_values combo.
 */
import { Fragment, useState } from 'react'
import { Navigate } from 'react-router-dom'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { ChevronDown, ChevronRight, Loader2, Scale, X } from 'lucide-react'
import { useAuthStore } from '@/store/auth'
import { financeApi } from '@/lib/api'
import { cn } from '@/lib/utils'
import { PortalChromeLayout } from '@/components/layout/PortalChromeLayout'
import { AccountVouchersModal } from './AccountVouchersModal'
import { JvDetailModal } from './JvDetailModal'

const inputCls = 'h-9 rounded-lg border border-neutral-300 bg-white px-3 text-sm focus:outline-none focus:ring-2 focus:ring-primary-600'
const primaryBtn = 'flex items-center gap-1.5 rounded-lg bg-[#085E5E] px-3 py-2 text-sm font-medium text-white hover:bg-[#064A4A] disabled:opacity-50'
const secondaryBtn = 'flex items-center gap-1.5 rounded-lg border border-neutral-300 bg-white px-3 py-2 text-sm font-medium text-neutral-700 hover:bg-neutral-50 disabled:opacity-50'
const linkBtn = 'text-xs font-medium text-[#085E5E] hover:underline'

interface AbRow {
  account_code: string; account_name: string; account_type: string | null
  opening: string; period_debit: string; period_credit: string; closing: string
}
interface AbResp {
  period: string; rows: AbRow[]
  totals: { period_debit: string; period_credit: string; closing: string }
  balanced: boolean
}
interface DimOption { dim_code: string; label: string; supported: boolean }
interface ExpandKey { dim_code: string; id: string | null; code: string | null; name: string | null }
interface ExpandResp { account_code: string; period: string; dims: string[]; rows: { keys: ExpandKey[]; amount: string }[] }

function money(v: string) {
  const n = Number(v)
  return n ? n.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 }) : '—'
}
function thisMonth() { return new Date().toISOString().slice(0, 7) }

interface Drill { accountCode: string; dimsValues?: string | null; title: string }

export default function AccountBalancePage() {
  const { user } = useAuthStore()
  const qc = useQueryClient()
  const [period, setPeriod] = useState(thisMonth())
  const [expanded, setExpanded] = useState<Record<string, string[]>>({})
  const [picker, setPicker] = useState<string | null>(null)   // account_code being configured
  const [drill, setDrill] = useState<Drill | null>(null)
  const [jvId, setJvId] = useState<string | null>(null)

  const { data: perms } = useQuery({
    queryKey: ['jv-permissions'],
    queryFn: () => financeApi.get<{ can_act: boolean }>('/journal-vouchers/permissions'),
  })

  const { data, isFetching } = useQuery({
    queryKey: ['account-balance', period],
    queryFn: () => financeApi.get<AbResp>(`/gl/account-balance?period=${period}`),
  })

  const onJvActed = () => {
    qc.invalidateQueries({ queryKey: ['account-balance'] })
    qc.invalidateQueries({ queryKey: ['ab-expand'] })
    qc.invalidateQueries({ queryKey: ['ab-vouchers'] })
    qc.invalidateQueries({ queryKey: ['budget-actual'] })
  }

  const toggle = (code: string) => {
    if (expanded[code]) {
      setExpanded((p) => { const n = { ...p }; delete n[code]; return n })
    } else {
      setPicker(code)   // choose dims before expanding
    }
  }

  if (!user) return <Navigate to="/login" replace />

  return (
    <PortalChromeLayout
      activeKey="portal:/finance/account-balance"
      title="Account Balance"
      subtitle="Opening / period movement / closing per account over posted journal vouchers (CAD)"
    >
      <div className="mx-auto max-w-6xl">
        <div className="mb-4 flex items-center gap-2">
          <input type="month" value={period}
                 onChange={(e) => { setPeriod(e.target.value); setExpanded({}) }}
                 className={cn(inputCls, 'w-40')} />
          {data && (
            <span className={cn('inline-flex items-center gap-1 rounded-full px-2 py-0.5 text-xs font-medium',
              data.balanced ? 'bg-green-50 text-green-700' : 'bg-red-50 text-red-700')}>
              <Scale className="h-3 w-3" /> {data.balanced ? 'Balanced' : 'Out of balance'}
            </span>
          )}
        </div>

        {isFetching && !data ? (
          <div className="py-10 text-center"><Loader2 className="mx-auto h-5 w-5 animate-spin text-neutral-400" /></div>
        ) : data && (
          <div className="overflow-hidden rounded-lg border border-neutral-200">
            <table className="w-full text-sm">
              <thead className="bg-neutral-50 text-left text-xs text-neutral-500">
                <tr>
                  <th className="w-8 px-2 py-2" />
                  <th className="px-3 py-2 w-24">Code</th>
                  <th className="px-3 py-2">Account</th>
                  <th className="px-3 py-2 w-28 text-right">Opening</th>
                  <th className="px-3 py-2 w-28 text-right">Period Dr</th>
                  <th className="px-3 py-2 w-28 text-right">Period Cr</th>
                  <th className="px-3 py-2 w-28 text-right">Closing</th>
                  <th className="px-3 py-2 w-24" />
                </tr>
              </thead>
              <tbody>
                {data.rows.length === 0 && (
                  <tr><td colSpan={8} className="px-3 py-6 text-center text-neutral-400">No posted activity up to {data.period}.</td></tr>
                )}
                {data.rows.map((r, i) => (
                  <Fragment key={r.account_code}>
                    <tr className={cn('border-t border-neutral-100 hover:bg-primary-50/40', i % 2 && 'bg-neutral-50/40')}>
                      <td className="px-2 py-2">
                        <button onClick={() => toggle(r.account_code)}
                                className="rounded p-0.5 text-neutral-400 hover:text-neutral-700"
                                title="Expand by auxiliary dimensions">
                          {expanded[r.account_code] ? <ChevronDown className="h-4 w-4" /> : <ChevronRight className="h-4 w-4" />}
                        </button>
                      </td>
                      <td className="px-3 py-2 font-mono text-xs">{r.account_code}</td>
                      <td className="px-3 py-2">{r.account_name}</td>
                      <td className="px-3 py-2 text-right font-mono text-neutral-500">{money(r.opening)}</td>
                      <td className="px-3 py-2 text-right font-mono">{money(r.period_debit)}</td>
                      <td className="px-3 py-2 text-right font-mono">{money(r.period_credit)}</td>
                      <td className="px-3 py-2 text-right font-mono font-semibold">{money(r.closing)}</td>
                      <td className="px-3 py-2 text-right">
                        <button className={linkBtn}
                                onClick={() => setDrill({ accountCode: r.account_code, title: `Vouchers — ${r.account_code} ${r.account_name} · ${period}` })}>
                          Vouchers
                        </button>
                      </td>
                    </tr>
                    {expanded[r.account_code] && (
                      <DimExpansion accountCode={r.account_code} accountName={r.account_name}
                                    period={period} dims={expanded[r.account_code]}
                                    onDrill={setDrill} />
                    )}
                  </Fragment>
                ))}
              </tbody>
              <tfoot>
                <tr className="border-t-2 border-neutral-200 bg-neutral-50 font-semibold">
                  <td className="px-3 py-2" colSpan={4}>Totals</td>
                  <td className="px-3 py-2 text-right font-mono">{money(data.totals.period_debit)}</td>
                  <td className="px-3 py-2 text-right font-mono">{money(data.totals.period_credit)}</td>
                  <td className="px-3 py-2 text-right font-mono">{money(data.totals.closing)}</td>
                  <td />
                </tr>
              </tfoot>
            </table>
          </div>
        )}
      </div>

      {picker && (
        <DimPickerModal accountCode={picker} onClose={() => setPicker(null)}
                        onApply={(dims) => {
                          setExpanded((p) => ({ ...p, [picker]: dims }))
                          setPicker(null)
                        }} />
      )}
      {drill && (
        <AccountVouchersModal accountCode={drill.accountCode} period={period}
                              dimsValues={drill.dimsValues} title={drill.title}
                              onClose={() => setDrill(null)}
                              onOpenJv={(id) => setJvId(id)} />
      )}
      {jvId && (
        <JvDetailModal jvId={jvId} canAct={perms?.can_act ?? false}
                       onClose={() => setJvId(null)} onActed={onJvActed} />
      )}
    </PortalChromeLayout>
  )
}

function DimPickerModal({ accountCode, onClose, onApply }: {
  accountCode: string; onClose: () => void; onApply: (dims: string[]) => void
}) {
  const [chosen, setChosen] = useState<string[]>(['cost_center'])
  const { data, isLoading } = useQuery({
    queryKey: ['ab-dims', accountCode],
    queryFn: () => financeApi.get<{ account_code: string; dims: DimOption[] }>(
      `/gl/account-balance/${accountCode}/dims`),
  })
  const flip = (d: string) => setChosen((p) =>
    p.includes(d) ? p.filter((x) => x !== d) : [...p, d])
  const options = data?.dims ?? []
  const chosenSupported = chosen.filter((c) => options.some((o) => o.dim_code === c && o.supported))

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/30 p-4" onClick={onClose}>
      <div className="w-full max-w-sm rounded-xl bg-white p-5 shadow-xl" onClick={(e) => e.stopPropagation()}>
        <div className="mb-3 flex items-center justify-between">
          <h2 className="text-base font-semibold text-neutral-800">Expand {accountCode} by…</h2>
          <button onClick={onClose} className="rounded p-1 text-neutral-400 hover:text-neutral-700">
            <X className="h-5 w-5" />
          </button>
        </div>
        {isLoading ? (
          <div className="py-6 text-center"><Loader2 className="mx-auto h-5 w-5 animate-spin text-neutral-400" /></div>
        ) : (
          <div className="space-y-2">
            {options.map((o) => (
              <label key={o.dim_code}
                     className={cn('flex items-center gap-2 text-sm',
                       o.supported ? 'text-neutral-700' : 'cursor-not-allowed text-neutral-400')}>
                <input type="checkbox" disabled={!o.supported}
                       checked={chosen.includes(o.dim_code)}
                       onChange={() => flip(o.dim_code)} />
                {o.label}
                {!o.supported && <span className="text-[11px]">(no data yet)</span>}
              </label>
            ))}
          </div>
        )}
        <div className="mt-4 flex justify-end gap-2 border-t border-neutral-100 pt-3">
          <button onClick={onClose} className={secondaryBtn}>Cancel</button>
          <button onClick={() => onApply(chosenSupported)}
                  disabled={chosenSupported.length === 0} className={primaryBtn}>
            Expand
          </button>
        </div>
      </div>
    </div>
  )
}

function DimExpansion({ accountCode, accountName, period, dims, onDrill }: {
  accountCode: string; accountName: string; period: string; dims: string[]
  onDrill: (d: Drill) => void
}) {
  const dimsParam = dims.join(',')
  const { data, isLoading } = useQuery({
    queryKey: ['ab-expand', accountCode, period, dimsParam],
    queryFn: () => financeApi.get<ExpandResp>(
      `/gl/account-balance/${accountCode}/expand?period=${period}&dims=${dimsParam}`),
  })
  if (isLoading) {
    return (
      <tr className="border-t border-neutral-100 bg-neutral-50/60">
        <td colSpan={8} className="px-3 py-3 text-center"><Loader2 className="mx-auto h-4 w-4 animate-spin text-neutral-400" /></td>
      </tr>
    )
  }
  const rows = data?.rows ?? []
  if (rows.length === 0) {
    return (
      <tr className="border-t border-neutral-100 bg-neutral-50/60">
        <td colSpan={8} className="px-3 py-2 pl-12 text-xs text-neutral-400">No movement this period.</td>
      </tr>
    )
  }
  return (
    <>
      {rows.map((row, ri) => {
        const label = row.keys.map((k) =>
          k.code ? `${k.code}${k.name ? ' · ' + k.name : ''}` : '(none)').join('  |  ')
        const dv = row.keys.map((k) => `${k.dim_code}:${k.id ?? 'none'}`).join(',')
        return (
          <tr key={ri} className="border-t border-neutral-100 bg-neutral-50/60 text-xs">
            <td />
            <td colSpan={2} className="px-3 py-1.5 pl-8 text-neutral-600">{label}</td>
            <td colSpan={4} className="px-3 py-1.5 text-right font-mono">{money(row.amount)}</td>
            <td className="px-3 py-1.5 text-right">
              <button className={linkBtn}
                      onClick={() => onDrill({
                        accountCode, dimsValues: dv,
                        title: `Vouchers — ${accountCode} ${accountName} · ${label} · ${period}`,
                      })}>
                Vouchers
              </button>
            </td>
          </tr>
        )
      })}
    </>
  )
}
```

- [ ] **Step 4: Typecheck**

Run: `cd c:/Project/uniops/finance && npx tsc -p tsconfig.app.json --noEmit --ignoreDeprecations 6.0`
Expected: exit 0(显式确认)

- [ ] **Step 5: Commit**

```bash
cd c:/Project/uniops
git add finance/src/pages/finance/AccountBalancePage.tsx finance/src/pages/finance/AccountVouchersModal.tsx finance/src/pages/finance/BudgetActualPage.tsx
git commit -m "feat(finance-ui): multi-dim expansion picker + generic expansion rows + combo drill-down"
```

---

### Task 6: dev 执行 —— 容器内迁移/导入 + 冒烟 + 全量回归(控制器亲自执行)

- [ ] **Step 1: 容器内迁移 + 回填核验**

```bash
docker restart uniops_finance_api && sleep 8
docker exec uniops_finance_api python -m alembic upgrade head
docker exec uniops_postgres psql -U epms -d epms -c "select count(*) filter (where income_expense_item_id is not null) as promoted, (select count(*) from jv_line_dimensions where dim_code='income_expense_item' and value_id is not null) as kv from journal_voucher_lines"
```
Expected: `promoted == kv`(≈66,315)。

- [ ] **Step 2: aux 挂接导入**(宿主跑但目标是本地 DSN,连 NC 只读;若权限拦截交用户命令清单)

```bash
cd c:/Project/uniops/finance-api
./.venv/Scripts/python scripts/nc_migration/aux_items_import.py --dry-run   # 核对映射输出
./.venv/Scripts/python scripts/nc_migration/aux_items_import.py --load
docker exec uniops_postgres psql -U epms -d epms -c "select dim_code, count(*) from coa_aux_items group by dim_code order by 2 desc"
```

- [ ] **Step 3: API 冒烟**(system_admin token;期间 2024-06)

- `GET /gl/account-balance/5101/dims` → 应含 cost_center/department/income_expense_item(supported true)+ 可能的 supplier 等(false)。
- `GET /gl/account-balance/5101/expand?period=2024-06&dims=cost_center,income_expense_item` → 多行,各行相加 ≈ 单维合计。
- `GET .../vouchers?period=2024-06&dims_values=cost_center:<某id>` → 行集非空。

- [ ] **Step 4: 全量回归 + 前端 typecheck 终验**

```bash
cd c:/Project/uniops/finance-api && TEST_PG_PASSWORD=7c0a03bb8c2afef690d1852f8dc3a0195932db5f0f1670e9 ./.venv/Scripts/python -m pytest tests/test_account_balance.py tests/test_journal_voucher.py tests/test_jv_lifecycle.py tests/test_jv_api.py tests/test_jv_backfill.py tests/test_nc_sync.py tests/test_gl.py -q
cd c:/Project/uniops/finance && npx tsc -p tsconfig.app.json --noEmit --ignoreDeprecations 6.0
```
Expected: 全 PASS;tsc exit 0。

---

## Self-Review 记录

- **Spec 覆盖**:§2(T1)、§3 三处写路径(T2)、§4 注册表+§6 三端点(T3)、§5 导入(T4)、§7 UI(T5)、§8 测试矩阵(各任务)、dev 执行(T6)。§9 范围外未越界。
- **Placeholder 扫描**:通过。
- **类型一致性**:`expand_by_dims` 响应 keys 结构(T3 crud→T3 测试→T5 ExpandKey)一致;`dims_values` 字符串格式 `dim:uuid|none`(T3 API 解析→T5 拼装)一致;`AccountVouchersModal.dimsValues`(T5 内三处)一致;nc_sync 14-tuple(T2 transform/worker/脚本/测试断言 l1[13])一致。
- **已知取舍**:expand 每行 `_net` 净额单值(不分借贷两列)——与现 UI 一致;`fetch()` 的 seq 生成 O(n²)(小表,无所谓);aux 导入的 supplier/customer 等只入表标 unsupported。
