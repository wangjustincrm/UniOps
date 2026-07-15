# NC COA + 辅助核算同步 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 给 Finance 加一个 `system_admin` 专用的 COA + 辅助核算同步接口(预览→应用),
用 NC 的事实替换现有导入器的推断,顺带修复 18 个方向记反 + 29 个数量核算错误的存量科目。

**Architecture:** 同步执行(无后台 worker/无进度表/无轮询) —— 360 科目 + 205 辅助核算行,
读取约 1-2 秒。`POST /coa-sync/preview` 只读返回差异,`POST /coa-sync/apply` **重新读 NC、
重新算差异**后单事务写入。核心是三个纯函数(`map_account` / `diff` / `derive_party_dim`),
零 I/O、可穷举测试。阻塞的 oracledb 读一律 `run_in_executor`。

**Tech Stack:** FastAPI + SQLAlchemy(async) + alembic / oracledb 2.5.1(读 NC) +
psycopg2(写本库) / pytest + pytest-asyncio / React + TanStack Query

**Spec:** `docs/superpowers/specs/2026-07-15-finance-nc-coa-sync-design.md` —— 本计划的
每个映射决策都能在 spec 里找到实测依据,有疑问回去查 spec,别自行发挥。

**Worktree:** `c:/Project/uniops/.worktrees/nc-coa-sync`,分支 `feature/finance-nc-coa-sync`。

## Global Constraints

- **NC 是事实来源,不得推断**:遇到未登记的取值一律抛错,**禁止兜底默认值**。
  现有 `coa_import.py` 的 `return "asset"` 兜底正是 18 个方向记反的成因模式(spec §3.1.1)。
- **`~` 是 NC 的空值哨兵**,不是 NULL。`unit`/`currency`/`pid`/`name*` 全部需 `~` 清洗。
- **字段所有权**:同步只写 spec §3.1 的列;`subtype`/`aux_dimensions`/`default_uom` 之外的
  `effective_from`/`effective_to`/`cash_flow_category`/`mnemonic`/`entity_id` **永不触碰**。
- **UI 文案全英文**,注释可中文(既有约定)。
- **测试连本地 docker 库,不得打生产**。命令统一为:
  ```bash
  cd /c/Project/uniops/.worktrees/nc-coa-sync/finance-api
  TEST_PG_PASSWORD=$(grep '^DB_PASSWORD=' /c/Project/uniops/.env | cut -d= -f2- | tr -d ' \r') \
    ./.venv/Scripts/python -m pytest tests/test_nc_coa_sync.py -v
  ```
  (`.venv` 在 `finance-api/` 下;`conftest.py` 会自建 `finance_test` 库并跑 alembic。)
- **每个 Task 结束必须 commit**,commit message 用英文。

## File Structure

| 文件 | 职责 |
|---|---|
| `finance-api/app/models/coa_sync.py` **(新)** | `CoaSyncRun` 审计模型,仅此一个类 |
| `finance-api/app/models/coa.py` **(改)** | `CoaAuxItem` 加 `required` 列 |
| `finance-api/alembic/versions/0023_coa_sync_runs.py` **(新)** | 建 `coa_sync_runs` + `coa_aux_items.required` |
| `finance-api/app/services/nc_coa_sync.py` **(新)** | 映射常量 + 纯函数(map/diff/derive)+ fetch + apply |
| `finance-api/app/api/v1/nc_coa_sync.py` **(新)** | `/coa-sync` 三个端点 + 测试缝 |
| `finance-api/app/api/v1/__init__.py` **(改)** | 注册 router |
| `finance-api/app/crud/account_balance.py` **(改)** | `DIM_LABELS` 补齐 13 个英文标签 |
| `finance-api/tests/test_nc_coa_sync.py` **(新)** | 纯函数单测 + API 测试 |
| `finance/src/pages/finance/CoaSyncModal.tsx` **(新)** | 预览/结果两态弹窗 |
| `finance/src/pages/finance/CoaConfigPage.tsx` **(改)** | headerActions 按钮 + 辅助核算编辑器降只读 |

`nc_coa_sync.py` 会长到 ~350 行,仍属单一职责(NC COA 同步),不再拆分 —— 与
`nc_sync.py`(voucher,~340 行)对称,便于对照阅读。

**不删** `scripts/nc_migration/coa_import.py` 与 `aux_items_import.py`(spec §12)。

---

### Task 1: 迁移 0023 + 审计模型 + `coa_aux_items.required`

**Files:**
- Create: `finance-api/app/models/coa_sync.py`
- Modify: `finance-api/app/models/coa.py`(`CoaAuxItem` 加一列)
- Create: `finance-api/alembic/versions/0023_coa_sync_runs.py`
- Test: `finance-api/tests/test_nc_coa_sync.py`

**Interfaces:**
- Consumes: 无(首个 Task)
- Produces: `app.models.coa_sync.CoaSyncRun`(字段见下);
  `app.models.coa.CoaAuxItem.required: bool`

> ⚠️ `down_revision` 必须是 `"0022_ap_vendor_invno_unique"` —— 2026-07-15 实测链尾。
> **不要按文件名猜**(既有教训:双 head 导致登录 500)。动手前先确认:
> `grep -H "^revision\|^down_revision" alembic/versions/00*.py | tail -4`

- [ ] **Step 1: 写失败的测试**

追加到 `finance-api/tests/test_nc_coa_sync.py`(新建文件):

```python
"""NC COA sync — model/migration + pure mapping + diff + API tests."""
import uuid
from datetime import datetime, timezone

from sqlalchemy import select

from app.models.coa import CoaAuxItem
from app.models.coa_sync import CoaSyncRun


async def test_coa_sync_run_roundtrip(db_session):
    run = CoaSyncRun(id=uuid.uuid4(), started_by=uuid.uuid4(),
                     started_at=datetime.now(timezone.utc))
    db_session.add(run)
    await db_session.flush()
    got = (await db_session.execute(
        select(CoaSyncRun).where(CoaSyncRun.id == run.id))).scalar_one()
    assert got.accounts_inserted == 0        # server default
    assert got.accounts_updated == 0
    assert got.accounts_deactivated == 0
    assert got.aux_items_inserted == 0
    assert got.aux_items_deleted == 0
    assert got.finished_at is None
    assert got.error is None


async def test_coa_aux_item_required_defaults_false(db_session):
    item = CoaAuxItem(id=uuid.uuid4(), account_code="1001", dim_code="employee", seq=1)
    db_session.add(item)
    await db_session.flush()
    got = (await db_session.execute(
        select(CoaAuxItem).where(CoaAuxItem.id == item.id))).scalar_one()
    assert got.required is False             # server default
    assert got.seq == 1
```

- [ ] **Step 2: 跑测试确认失败**

```bash
cd /c/Project/uniops/.worktrees/nc-coa-sync/finance-api
TEST_PG_PASSWORD=$(grep '^DB_PASSWORD=' /c/Project/uniops/.env | cut -d= -f2- | tr -d ' \r') \
  ./.venv/Scripts/python -m pytest tests/test_nc_coa_sync.py -v
```

Expected: FAIL —— `ModuleNotFoundError: No module named 'app.models.coa_sync'`

- [ ] **Step 3: 写模型 `app/models/coa_sync.py`**

```python
"""COA sync run log — lightweight audit for the NC COA/aux sync.

One row per applied sync. Written AFTER the main transaction commits or rolls
back (see services/nc_coa_sync.apply): keeping it in the same transaction would
roll the failure record away exactly when it matters most.

No status/watermark/progress columns — the sync runs synchronously, so the row
is written once the work is over and there is no "in progress" state to report.
"""
import uuid
from datetime import datetime

from sqlalchemy import DateTime, Integer, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin, UUIDPrimaryKey


class CoaSyncRun(UUIDPrimaryKey, TimestampMixin, Base):
    __tablename__ = "coa_sync_runs"

    started_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    accounts_inserted: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    accounts_updated: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    accounts_deactivated: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    aux_items_inserted: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    aux_items_deleted: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
```

- [ ] **Step 4: 给 `CoaAuxItem` 加 `required` 列**

在 `finance-api/app/models/coa.py` 的 `CoaAuxItem` 类中,`seq` 之后追加:

```python
    # NC BD_ACCASS.ISEMPTY 取反:允许为空=N → 必填
    required: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False,
                                           server_default="false")
```

`Boolean` 已在该文件顶部 import,无需改 import。

- [ ] **Step 5: 写迁移 `alembic/versions/0023_coa_sync_runs.py`**

```python
"""coa_sync_runs + coa_aux_items.required — NC COA/aux sync audit & NC parity

Revision ID: 0023_coa_sync_runs
Revises: 0022_ap_vendor_invno_unique
Create Date: 2026-07-15
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

revision = "0023_coa_sync_runs"
down_revision = "0022_ap_vendor_invno_unique"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "coa_sync_runs",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("started_by", UUID(as_uuid=True), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("accounts_inserted", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("accounts_updated", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("accounts_deactivated", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("aux_items_inserted", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("aux_items_deleted", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
    )
    # NC BD_ACCASS.ISEMPTY inverted — never populated before (spec §2.3)
    op.add_column("coa_aux_items",
                  sa.Column("required", sa.Boolean(), nullable=False,
                            server_default="false"))


def downgrade():
    op.drop_column("coa_aux_items", "required")
    op.drop_table("coa_sync_runs")
```

- [ ] **Step 6: 跑测试确认通过**

```bash
cd /c/Project/uniops/.worktrees/nc-coa-sync/finance-api
TEST_PG_PASSWORD=$(grep '^DB_PASSWORD=' /c/Project/uniops/.env | cut -d= -f2- | tr -d ' \r') \
  ./.venv/Scripts/python -m pytest tests/test_nc_coa_sync.py -v
```

Expected: 2 passed。若报 `Multiple head revisions`,说明 `down_revision` 挂错了 —— 回 Step 5。

- [ ] **Step 7: Commit**

```bash
cd /c/Project/uniops/.worktrees/nc-coa-sync
git add finance-api/app/models/coa_sync.py finance-api/app/models/coa.py \
        finance-api/alembic/versions/0023_coa_sync_runs.py \
        finance-api/tests/test_nc_coa_sync.py
git commit -m "feat(finance): coa_sync_runs audit table + coa_aux_items.required

required carries NC BD_ACCASS.ISEMPTY inverted — the column its comment always
described but no importer ever populated."
```

---

### Task 2: 纯映射函数(事实化的核心)

**Files:**
- Create: `finance-api/app/services/nc_coa_sync.py`
- Modify: `finance-api/app/crud/account_balance.py`(`DIM_LABELS`)
- Test: `finance-api/tests/test_nc_coa_sync.py`(追加)

**Interfaces:**
- Consumes: Task 1 的模型(此 Task 不用,仅同文件后续 Task 用)
- Produces:
  - `NC_OWNED_FIELDS: tuple[str, ...]` —— diff 与 apply 都依赖的字段清单
  - `clean(v: str | None) -> str | None` —— `~` 哨兵清洗
  - `map_account_type(acctype_code: str, balanorient: int) -> str`
  - `map_normal_balance(balanorient: int) -> str`
  - `map_aux_item(nc_item_code: str) -> str`
  - `derive_party_dim(account_code: str, account_type: str) -> str`
  - `map_account(row: dict, uom: dict, ccy: dict, acctype: dict, pk2code: dict) -> dict`
  - 异常类 `NcMappingError(ValueError)`

- [ ] **Step 1: 写失败的测试**

追加到 `finance-api/tests/test_nc_coa_sync.py`:

```python
import pytest

from app.services.nc_coa_sync import (
    NcMappingError, clean, derive_party_dim, map_account, map_account_type,
    map_aux_item, map_normal_balance,
)

# ── clean:NC 的 ~ 哨兵 ──────────────────────────────────────────────────────
def test_clean_treats_tilde_as_empty():
    assert clean("~") is None
    assert clean("") is None
    assert clean(None) is None
    assert clean("  KGM  ") == "KGM"

# ── normal_balance:事实,非推断 ────────────────────────────────────────────
def test_normal_balance_from_balanorient():
    assert map_normal_balance(0) == "debit"
    assert map_normal_balance(1) == "credit"

def test_normal_balance_rejects_unknown():
    with pytest.raises(NcMappingError):
        map_normal_balance(2)

# ── account_type:BD_ACCTYPE.code + 损益按方向拆 ────────────────────────────
@pytest.mark.parametrize("acctype,bo,expected", [
    ("1", 0, "asset"), ("1", 1, "asset"),        # 备抵科目仍是资产
    ("2", 1, "liability"),
    ("4", 1, "equity"),
    ("5", 0, "expense"),                          # 成本类
    ("6", 1, "revenue"),                          # 损益贷方 = 收入
    ("6", 0, "expense"),                          # 损益借方 = 费用
])
def test_account_type_mapping(acctype, bo, expected):
    assert map_account_type(acctype, bo) == expected

def test_account_type_rejects_unregistered_code():
    # 现有脚本在这里 return "asset" —— 正是 18 个方向记反的成因模式
    with pytest.raises(NcMappingError):
        map_account_type("3", 0)

# ── aux item:按 NC code 精确映射,不再按名称子串 ────────────────────────────
def test_aux_item_maps_supported_dims():
    assert map_aux_item("ra01") == "cost_center"
    assert map_aux_item("0001") == "department"
    assert map_aux_item("0008") == "income_expense_item"
    assert map_aux_item("0019") == "supplier"
    assert map_aux_item("0017") == "customer"

def test_aux_item_regression_project_substring_false_positives():
    # 旧 NAME_MAP 按「项目」子串匹配,把这两个都错判成 project
    assert map_aux_item("D45") == "project_type"
    assert map_aux_item("CRM02") == "government_grant_project"
    assert map_aux_item("0010") == "project"          # 真·项目

def test_aux_item_rejects_unregistered_code():
    with pytest.raises(NcMappingError):
        map_aux_item("ZZ99")                          # 不 slug、不猜

# ── 客商派生 ────────────────────────────────────────────────────────────────
@pytest.mark.parametrize("code,atype,expected", [
    ("112201", "asset", "customer"),      # 应收非关联单位款
    ("220202", "liability", "supplier"),  # 应付关联单位款
    ("640202", "expense", "supplier"),    # 劳务成本
    ("6002", "revenue", "customer"),      # 销售折扣
    ("4001", "equity", "partner"),        # 实收资本 = 股东
])
def test_derive_party_dim(code, atype, expected):
    assert derive_party_dim(code, atype) == expected

def test_derive_party_dim_exceptions_never_become_customer():
    # 1511/1512 对方是被投资单位,按资产分支会误判为 customer
    assert derive_party_dim("1511", "asset") == "partner"
    assert derive_party_dim("1512", "asset") == "partner"

# ── map_account 整合 ────────────────────────────────────────────────────────
def _lookups():
    return {
        "uom": {"UOMPK": "KGM"},
        "ccy": {"CCYPK": "CAD"},
        "acctype": {"ATPK1": "1", "ATPK6": "6"},
        "pk2code": {"PARENTPK": "1230"},
    }

def _row(**over):
    row = {"pk": "ACCTPK", "code": "123001", "pid": "PARENTPK",
           "acctype_pk": "ATPK1", "balanorient": 0,
           "unit_pk": "UOMPK", "currency_pk": "CCYPK", "outflag": "N",
           "endflag": "Y", "name_soa": "~", "name2_soa": "Goods in Transit",
           "name_acct": "在途物资", "name2_acct": "~"}
    row.update(over)
    return row

def test_map_account_reads_facts_and_joins_masters():
    got = map_account(_row(), **_lookups())
    assert got["code"] == "123001"
    assert got["name"] == "Goods in Transit"      # ACCASOA name2 优先
    assert got["account_type"] == "asset"
    assert got["normal_balance"] == "debit"
    assert got["is_postable"] is True             # endflag=Y
    assert got["parent_code"] == "1230"
    assert got["quantity_accounting"] is True     # unit 有真实值
    assert got["default_uom"] == "KGM"
    assert got["default_currency"] == "CAD"
    assert got["is_off_balance"] is False

def test_map_account_contra_asset_is_credit():
    # 回归:累计折旧类科目 —— 编码以 1 开头但 NC 明确是贷方
    got = map_account(_row(code="1602", balanorient=1), **_lookups())
    assert got["account_type"] == "asset"
    assert got["normal_balance"] == "credit"      # 不是 debit

def test_map_account_tilde_unit_means_no_quantity_accounting():
    got = map_account(_row(unit_pk="~", currency_pk="~"), **_lookups())
    assert got["quantity_accounting"] is False
    assert got["default_uom"] is None
    assert got["default_currency"] is None

def test_map_account_name_fallback_chain():
    got = map_account(_row(name_soa="~", name2_soa="~", name2_acct="~"), **_lookups())
    assert got["name"] == "在途物资"              # 回退到 BD_ACCOUNT.name
    got2 = map_account(_row(name_soa="~", name2_soa="~", name2_acct="~",
                            name_acct="~"), **_lookups())
    assert got2["name"] == "123001"               # 全空 → code

def test_map_account_top_level_pid_tilde():
    got = map_account(_row(pid="~"), **_lookups())
    assert got["parent_code"] is None

def test_map_account_rejects_unknown_uom_pk():
    with pytest.raises(NcMappingError):
        map_account(_row(unit_pk="NOSUCH"), **_lookups())

def test_map_account_rejects_missing_accasoa_row():
    # endflag 为 None = ACCASOA 行缺失;不得降级为按 pid 推断
    with pytest.raises(NcMappingError):
        map_account(_row(endflag=None), **_lookups())
```

- [ ] **Step 2: 跑测试确认失败**

```bash
cd /c/Project/uniops/.worktrees/nc-coa-sync/finance-api
TEST_PG_PASSWORD=$(grep '^DB_PASSWORD=' /c/Project/uniops/.env | cut -d= -f2- | tr -d ' \r') \
  ./.venv/Scripts/python -m pytest tests/test_nc_coa_sync.py -v
```

Expected: FAIL —— `ModuleNotFoundError: No module named 'app.services.nc_coa_sync'`

- [ ] **Step 3: 写 `app/services/nc_coa_sync.py`(映射部分)**

```python
"""NC65 → UniOps COA + aux sync.

Ported from scripts/nc_migration/coa_import.py + aux_items_import.py, with the
inference replaced by facts (see the 2026-07-15 spec §2-§3): NC states the
account direction, the leaf flag and the account type outright, and the old
importers guessed all three from the Chinese code prefix. 18 accounts were
stored with the wrong normal_balance as a result.

Every unmapped value raises. There is deliberately no fallback default — the
old `return "asset"` catch-all is how the wrong data got in.
"""
from dataclasses import dataclass, field

from app.core.config import settings
from app.services.nc_sync import nc_configured  # same NC_* env gate

__all__ = ["nc_configured"]

CHART = "1001A1100000003CG6GD"          # Canada Royal Milk 根科目表
TILDE = "~"                             # NC's empty sentinel — NOT null


class NcMappingError(ValueError):
    """An NC value we refuse to guess about. Aborts the whole sync."""


# ── NC BD_ACCTYPE.code → our account_type ────────────────────────────────────
# 6 (损益) covers both revenue and expense; NC does not split it, balanorient does.
ACCOUNT_TYPE_BY_NC = {"1": "asset", "2": "liability", "4": "equity", "5": "expense"}

# ── NC BD_ACCASSITEM.code → our dim_code (spec §3.4) ─────────────────────────
# Keyed on CODE, not name: the old NAME_MAP matched Chinese substrings and put
# both 项目类型 and 政府拨款项目 onto `project` because both contain 项目.
AUX_ITEM_MAP = {
    "ra01": "cost_center", "0001": "department", "0008": "income_expense_item",
    "0019": "supplier", "0017": "customer",
    "0004": "__party__",                # derived per account — see derive_party_dim
    "0006": "item", "0012": "item_category", "0010": "project",
    "D45": "project_type", "CRM02": "government_grant_project",
    "fa01": "asset_category", "D47": "tax_code", "0022": "bank_category",
    "0023": "bank", "0011": "bank_account", "0044": "country_region",
    "0002": "employee", "D09": "sales_type", "CRM01": "credit_card",
}

PARTY_ITEM = "0004"                     # 客商
# Neither a supplier nor a customer: 4001 实收资本 is a shareholder,
# 1511/1512 长期股权投资 an investee. Resolve to the unexpandable `partner`
# rather than forcing them into customer (user decision 2026-07-15).
PARTY_EXCEPTIONS = {"4001", "1511", "1512"}

# The columns the sync owns. Everything else on chart_of_accounts is UniOps'
# and must never appear in an UPDATE (spec §3.1/§3.2).
NC_OWNED_FIELDS = ("name", "account_type", "normal_balance", "is_postable",
                   "parent_code", "quantity_accounting", "default_uom",
                   "default_currency", "is_off_balance")


def clean(v: str | None) -> str | None:
    """NC stores empty as the string '~'. Everything else treats it as a value."""
    if v is None:
        return None
    s = str(v).strip()
    return None if s == "" or s == TILDE else s


def map_normal_balance(balanorient: int) -> str:
    if balanorient == 0:
        return "debit"
    if balanorient == 1:
        return "credit"
    raise NcMappingError(f"unknown BALANORIENT {balanorient!r} (expected 0 or 1)")


def map_account_type(acctype_code: str, balanorient: int) -> str:
    code = (acctype_code or "").strip()
    if code == "6":                     # 损益: credit = revenue, debit = expense
        return "revenue" if map_normal_balance(balanorient) == "credit" else "expense"
    try:
        return ACCOUNT_TYPE_BY_NC[code]
    except KeyError:
        raise NcMappingError(
            f"unregistered BD_ACCTYPE.code {code!r}; a human must decide its mapping"
        ) from None


def map_aux_item(nc_item_code: str) -> str:
    code = (nc_item_code or "").strip()
    try:
        return AUX_ITEM_MAP[code]
    except KeyError:
        raise NcMappingError(
            f"unregistered BD_ACCASSITEM.code {code!r}; add it to AUX_ITEM_MAP"
        ) from None


def derive_party_dim(account_code: str, account_type: str) -> str:
    """客商 (0004) covers vendors and customers; we keep them apart. Decide from
    the account's nature. `partner` is not in account_balance._dimensions(), so
    it surfaces as supported:false — i.e. not expandable, which beats expanding
    it wrongly."""
    if account_code in PARTY_EXCEPTIONS:
        return "partner"
    if account_type == "asset":
        return "customer"               # receivables — they owe us
    if account_type == "liability":
        return "supplier"               # payables — we owe them
    if account_type == "expense":
        return "supplier"               # cost (5) and P&L debit
    if account_type == "revenue":
        return "customer"               # P&L credit
    return "partner"                    # equity and anything unforeseen


def map_account(row: dict, *, uom: dict, ccy: dict, acctype: dict,
                pk2code: dict) -> dict:
    """One raw NC row (BD_ACCOUNT joined to BD_ACCASOA) -> chart_of_accounts dict."""
    code = row["code"].strip()

    # endflag lives on BD_ACCASOA. Missing row = anomaly (measured 360 == 360),
    # and both is_postable and name depend on it — do not degrade to pid-inference.
    if row.get("endflag") is None:
        raise NcMappingError(f"account {code}: BD_ACCASOA row missing")

    atype_code = acctype.get(row["acctype_pk"])
    if atype_code is None:
        raise NcMappingError(f"account {code}: BD_ACCTYPE pk {row['acctype_pk']!r} not found")
    account_type = map_account_type(atype_code, row["balanorient"])

    unit_pk = clean(row.get("unit_pk"))
    default_uom = None
    if unit_pk is not None:
        default_uom = uom.get(unit_pk)
        if default_uom is None:
            raise NcMappingError(f"account {code}: BD_MEASDOC pk {unit_pk!r} not found")

    ccy_pk = clean(row.get("currency_pk"))
    default_currency = None
    if ccy_pk is not None:
        default_currency = ccy.get(ccy_pk)
        if default_currency is None:
            raise NcMappingError(f"account {code}: BD_CURRTYPE pk {ccy_pk!r} not found")

    pid = clean(row.get("pid"))
    name = (clean(row.get("name2_soa")) or clean(row.get("name_soa"))
            or clean(row.get("name2_acct")) or clean(row.get("name_acct")) or code)

    return {
        "code": code,
        "name": name,
        "account_type": account_type,
        "normal_balance": map_normal_balance(row["balanorient"]),
        "is_postable": row["endflag"] == "Y",
        "parent_code": pk2code.get(pid) if pid else None,
        # 数量核算 is driven by UNIT, not the QUANTITY column — proven by an
        # exhaustive two-table column diff against the NC UI (spec §2.4).
        "quantity_accounting": unit_pk is not None,
        "default_uom": default_uom,
        "default_currency": default_currency,
        "is_off_balance": row.get("outflag") == "Y",
    }
```

- [ ] **Step 4: 补齐 `DIM_LABELS`**

在 `finance-api/app/crud/account_balance.py` 中,把 `DIM_LABELS` 整体替换为:

```python
DIM_LABELS = {
    # expandable (see _dimensions())
    "cost_center": "Cost Center", "department": "Department",
    "income_expense_item": "Income/Expense Item", "supplier": "Supplier",
    "customer": "Customer",
    # carried from NC BD_ACCASS but not expandable — jv_lines has no column for
    # them (spec §3.4). Listed so "NC configured it, we can't expand it" is visible.
    "partner": "Partner (Vendor/Customer)", "employee": "Employee",
    "project": "Project", "project_type": "Project Type",
    "government_grant_project": "Government Grant Project",
    "item": "Item / Material", "item_category": "Item Category",
    "asset_category": "Asset Category", "tax_code": "VAT Tax Code / Rate",
    "bank": "Bank", "bank_account": "Bank Account",
    "bank_category": "Bank Category", "country_region": "Country / Region",
    "sales_type": "Sales Type", "credit_card": "Credit Card",
}
```

- [ ] **Step 5: 跑测试确认通过**

```bash
cd /c/Project/uniops/.worktrees/nc-coa-sync/finance-api
TEST_PG_PASSWORD=$(grep '^DB_PASSWORD=' /c/Project/uniops/.env | cut -d= -f2- | tr -d ' \r') \
  ./.venv/Scripts/python -m pytest tests/test_nc_coa_sync.py -v
```

Expected: 全部 passed(Task 1 的 2 个 + 本 Task 约 22 个)

- [ ] **Step 6: 确认没碰坏 account_balance 既有测试**

```bash
TEST_PG_PASSWORD=$(grep '^DB_PASSWORD=' /c/Project/uniops/.env | cut -d= -f2- | tr -d ' \r') \
  ./.venv/Scripts/python -m pytest tests/test_account_balance.py -v
```

Expected: PASS(数量应与改动前一致;`DIM_LABELS` 只增不改既有键)

- [ ] **Step 7: Commit**

```bash
cd /c/Project/uniops/.worktrees/nc-coa-sync
git add finance-api/app/services/nc_coa_sync.py finance-api/app/crud/account_balance.py \
        finance-api/tests/test_nc_coa_sync.py
git commit -m "feat(finance): NC COA mapping reads facts instead of inferring

normal_balance comes from BALANORIENT, is_postable from ENDFLAG, account_type
from BD_ACCTYPE (with 损益 split by direction) — all three were guessed from the
code prefix before. quantity_accounting keys off UNIT, which an exhaustive
column diff showed is what actually drives the UI's 数量核算.

Aux items map on NC item code, killing the 项目 substring false positives, and
客商 derives per account with 4001/1511/1512 held out as exceptions.

Unmapped values raise; there is no fallback default by design."
```

---

### Task 3: `diff` / `diff_aux`(纯函数,零 I/O)

**Files:**
- Modify: `finance-api/app/services/nc_coa_sync.py`(追加)
- Test: `finance-api/tests/test_nc_coa_sync.py`(追加)

**Interfaces:**
- Consumes: Task 2 的 `NC_OWNED_FIELDS`、`map_account`
- Produces:
  - `@dataclass CoaDiff`: `to_insert: list[dict]`、`to_update: list[dict]`、
    `to_deactivate: list[dict]`、`unchanged: int`
    - `to_update` 每项形如 `{"code": str, "changes": {field: (before, after)},
      "reactivated": bool, "values": dict}`
    - `to_deactivate` 每项形如 `{"code": str, "name": str}`
  - `@dataclass AuxDiff`: `to_insert: list[dict]`、`to_delete: list[dict]`、`unchanged: int`
  - `diff(nc_accounts: list[dict], db_accounts: list[dict]) -> CoaDiff`
  - `diff_aux(nc_aux: list[dict], db_aux: list[dict]) -> AuxDiff`

> **三个桶互斥**:重新激活**不是**独立的桶,它是 `to_update` 的一种
> (`is_active` false→true 也是字段变化),标记为 `reactivated=True`。
> 「既改名又被重新启用」的科目**只出现一次**。`accounts_updated` 计数**包含**重新激活。

- [ ] **Step 1: 写失败的测试**

追加到 `finance-api/tests/test_nc_coa_sync.py`:

```python
from app.services.nc_coa_sync import diff, diff_aux

def _nc(code="1001", **over):
    base = {"code": code, "name": "Cash", "account_type": "asset",
            "normal_balance": "debit", "is_postable": True, "parent_code": None,
            "quantity_accounting": False, "default_uom": None,
            "default_currency": "CAD", "is_off_balance": False}
    base.update(over)
    return base

def _db(code="1001", is_active=True, **over):
    d = _nc(code, **over)
    d["is_active"] = is_active
    return d

def test_diff_inserts_new_accounts():
    d = diff([_nc("9999")], [])
    assert [r["code"] for r in d.to_insert] == ["9999"]
    assert d.to_update == [] and d.to_deactivate == [] and d.unchanged == 0

def test_diff_reports_unchanged_without_operations():
    d = diff([_nc()], [_db()])
    assert d.unchanged == 1
    assert d.to_insert == [] and d.to_update == [] and d.to_deactivate == []

def test_diff_updates_with_before_after():
    d = diff([_nc(normal_balance="credit")], [_db(normal_balance="debit")])
    assert len(d.to_update) == 1
    assert d.to_update[0]["changes"]["normal_balance"] == ("debit", "credit")
    assert d.to_update[0]["reactivated"] is False

def test_diff_deactivates_accounts_missing_from_nc():
    d = diff([], [_db("2000")])
    assert [r["code"] for r in d.to_deactivate] == ["2000"]

def test_diff_ignores_already_inactive_accounts():
    d = diff([], [_db("2000", is_active=False)])
    assert d.to_deactivate == []          # 已停用的不再重复停用

def test_diff_reactivation_lands_in_update():
    d = diff([_nc()], [_db(is_active=False)])
    assert d.to_deactivate == []
    assert len(d.to_update) == 1
    assert d.to_update[0]["reactivated"] is True

def test_diff_reactivation_plus_rename_appears_once():
    # 三桶互斥:既改名又重新启用的科目只出现一次
    d = diff([_nc(name="Petty Cash")], [_db(name="Cash", is_active=False)])
    assert len(d.to_update) == 1
    u = d.to_update[0]
    assert u["reactivated"] is True
    assert u["changes"]["name"] == ("Cash", "Petty Cash")

def test_diff_never_touches_uniops_owned_fields():
    # 字段所有权:同步不得把 aux_dimensions/subtype/effective_from 纳入变更
    db_row = _db()
    db_row.update({"subtype": "cash", "aux_dimensions": [{"code": "employee"}],
                   "effective_from": "2020-01-01"})
    d = diff([_nc(name="Renamed")], [db_row])
    changed = set(d.to_update[0]["changes"])
    assert changed == {"name"}
    assert not changed & {"subtype", "aux_dimensions", "effective_from"}

# ── aux ─────────────────────────────────────────────────────────────────────
def _aux(account_code="1001", dim_code="employee", seq=1, required=True):
    return {"account_code": account_code, "dim_code": dim_code,
            "seq": seq, "required": required}

def test_diff_aux_inserts_and_deletes():
    d = diff_aux([_aux(dim_code="employee")], [_aux(dim_code="project")])
    assert [r["dim_code"] for r in d.to_insert] == ["employee"]
    assert [r["dim_code"] for r in d.to_delete] == ["project"]

def test_diff_aux_unchanged():
    d = diff_aux([_aux()], [_aux()])
    assert d.unchanged == 1 and d.to_insert == [] and d.to_delete == []

def test_diff_aux_seq_change_is_a_replacement():
    d = diff_aux([_aux(seq=2)], [_aux(seq=1)])
    assert len(d.to_insert) == 1 and len(d.to_delete) == 1
```

- [ ] **Step 2: 跑测试确认失败**

```bash
cd /c/Project/uniops/.worktrees/nc-coa-sync/finance-api
TEST_PG_PASSWORD=$(grep '^DB_PASSWORD=' /c/Project/uniops/.env | cut -d= -f2- | tr -d ' \r') \
  ./.venv/Scripts/python -m pytest tests/test_nc_coa_sync.py -k diff -v
```

Expected: FAIL —— `ImportError: cannot import name 'diff'`

- [ ] **Step 3: 追加实现到 `app/services/nc_coa_sync.py`**

```python
@dataclass
class CoaDiff:
    to_insert: list = field(default_factory=list)
    to_update: list = field(default_factory=list)
    to_deactivate: list = field(default_factory=list)
    unchanged: int = 0


@dataclass
class AuxDiff:
    to_insert: list = field(default_factory=list)
    to_delete: list = field(default_factory=list)
    unchanged: int = 0


def diff(nc_accounts: list[dict], db_accounts: list[dict]) -> CoaDiff:
    """Pure. Three mutually exclusive buckets + a count.

    Reactivation is NOT its own bucket: is_active false->true is a field change
    like any other, so it rides in to_update with reactivated=True. An account
    that was both renamed and re-enabled appears exactly once.
    """
    by_code = {a["code"]: a for a in db_accounts}
    out = CoaDiff()
    for nc in nc_accounts:
        cur = by_code.get(nc["code"])
        if cur is None:
            out.to_insert.append(nc)
            continue
        # Only NC_OWNED_FIELDS are ever compared — UniOps' own columns
        # (subtype/aux_dimensions/effective_*/...) must not enter an UPDATE.
        changes = {f: (cur.get(f), nc[f]) for f in NC_OWNED_FIELDS
                   if cur.get(f) != nc[f]}
        reactivated = cur.get("is_active") is False
        if changes or reactivated:
            out.to_update.append({"code": nc["code"], "changes": changes,
                                  "reactivated": reactivated, "values": nc})
        else:
            out.unchanged += 1
    nc_codes = {a["code"] for a in nc_accounts}
    for cur in db_accounts:
        if cur["code"] not in nc_codes and cur.get("is_active") is not False:
            out.to_deactivate.append({"code": cur["code"], "name": cur.get("name")})
    return out


def _aux_key(r: dict) -> tuple:
    return (r["account_code"], r["dim_code"], r["seq"], r["required"])


def diff_aux(nc_aux: list[dict], db_aux: list[dict]) -> AuxDiff:
    """Pure. coa_aux_items is a plain NC projection with no UniOps-side data and
    nothing referencing it, so any difference is a replace (spec §5)."""
    nc_keys = {_aux_key(r): r for r in nc_aux}
    db_keys = {_aux_key(r): r for r in db_aux}
    out = AuxDiff()
    out.to_insert = [r for k, r in nc_keys.items() if k not in db_keys]
    out.to_delete = [r for k, r in db_keys.items() if k not in nc_keys]
    out.unchanged = len(set(nc_keys) & set(db_keys))
    return out
```

- [ ] **Step 4: 跑测试确认通过**

```bash
TEST_PG_PASSWORD=$(grep '^DB_PASSWORD=' /c/Project/uniops/.env | cut -d= -f2- | tr -d ' \r') \
  ./.venv/Scripts/python -m pytest tests/test_nc_coa_sync.py -v
```

Expected: 全部 passed

- [ ] **Step 5: Commit**

```bash
cd /c/Project/uniops/.worktrees/nc-coa-sync
git add finance-api/app/services/nc_coa_sync.py finance-api/tests/test_nc_coa_sync.py
git commit -m "feat(finance): COA diff — three mutually exclusive buckets

Reactivation rides in to_update rather than forming a fourth bucket, so an
account that was both renamed and re-enabled is counted once. Only
NC_OWNED_FIELDS are compared, which is what keeps aux_dimensions and subtype
out of an UPDATE."
```

---

### Task 4: `fetch_coa_from_nc` + `apply`

**Files:**
- Modify: `finance-api/app/services/nc_coa_sync.py`(追加)
- Test: `finance-api/tests/test_nc_coa_sync.py`(追加)

**Interfaces:**
- Consumes: Task 2/3 全部
- Produces:
  - `@dataclass NcCoaExtract`: `accounts: list[dict]`、`aux: list[dict]`、
    `pk2code: dict`、`uom: dict`、`ccy: dict`、`acctype: dict`
  - `fetch_coa_from_nc() -> NcCoaExtract`(阻塞,调用方负责 run_in_executor)
  - `build(extract: NcCoaExtract) -> tuple[list[dict], list[dict]]` ——
    `(mapped_accounts, mapped_aux)`,含零行守卫
  - `apply(coa_diff: CoaDiff, aux_rows: list[dict], aux_diff: AuxDiff, dsn: str,
    started_by) -> dict` —— 返回计数字典
  - `_pg_dsn() -> str`

- [ ] **Step 1: 写失败的测试**

追加到 `finance-api/tests/test_nc_coa_sync.py`:

```python
import os
import psycopg2
from app.services.nc_coa_sync import NcCoaExtract, apply, build

_TEST_DSN = (f"host={os.getenv('TEST_PG_HOST', 'localhost')} "
             f"port={os.getenv('TEST_PG_PORT', '5432')} "
             f"dbname=finance_test user={os.getenv('TEST_PG_USER', 'epms')} "
             f"password={os.getenv('TEST_PG_PASSWORD', 'epms_dev')}")

def _pg(sql, args=None):
    con = psycopg2.connect(_TEST_DSN); con.autocommit = True
    cur = con.cursor(); cur.execute(sql, args or ())
    rows = cur.fetchall() if cur.description else None
    con.close()
    return rows

def _extract(**over):
    e = NcCoaExtract(
        accounts=[{"pk": "P1", "code": "1602", "pid": "~", "acctype_pk": "AT1",
                   "balanorient": 1, "unit_pk": "~", "currency_pk": "C1",
                   "outflag": "N", "endflag": "Y", "name_soa": "~",
                   "name2_soa": "Accumulated depreciation",
                   "name_acct": "累计折旧", "name2_acct": "~"}],
        aux=[{"account_code": "1602", "nc_item_code": "0002", "seq": 1, "isempty": "N"}],
        pk2code={"P1": "1602"}, uom={}, ccy={"C1": "CAD"}, acctype={"AT1": "1"})
    for k, v in over.items():
        setattr(e, k, v)
    return e

def test_build_maps_accounts_and_aux():
    accounts, aux = build(_extract())
    assert accounts[0]["normal_balance"] == "credit"      # 备抵科目,不是 debit
    assert aux == [{"account_code": "1602", "dim_code": "employee",
                    "seq": 1, "required": True}]          # isempty=N -> required

def test_build_derives_party_dim_for_kes():
    e = _extract(aux=[{"account_code": "1602", "nc_item_code": "0004",
                       "seq": 1, "isempty": "Y"}])
    _, aux = build(e)
    assert aux[0]["dim_code"] == "customer"               # 资产类 -> customer
    assert aux[0]["required"] is False                    # isempty=Y -> 可空

def test_build_refuses_zero_accounts():
    # 零行守卫:否则「未返回即停用」会把整表 360 科目全部停用
    with pytest.raises(NcMappingError, match="no accounts"):
        build(_extract(accounts=[]))

def test_build_refuses_zero_aux():
    with pytest.raises(NcMappingError, match="no aux"):
        build(_extract(aux=[]))

async def test_apply_writes_and_audits(db_session):
    _pg("delete from chart_of_accounts")
    _pg("delete from coa_aux_items")
    _pg("delete from coa_sync_runs")
    accounts, aux = build(_extract())
    d = diff(accounts, [])
    ad = diff_aux(aux, [])
    counts = apply(d, aux, ad, _TEST_DSN, uuid.uuid4())
    assert counts["accounts_inserted"] == 1
    assert counts["aux_items_inserted"] == 1
    row = _pg("select normal_balance, default_currency, quantity_accounting "
              "from chart_of_accounts where code='1602'")[0]
    assert row == ("credit", "CAD", False)
    assert _pg("select required, seq from coa_aux_items "
               "where account_code='1602'")[0] == (True, 1)
    assert _pg("select count(*) from coa_sync_runs where error is null")[0][0] == 1

async def test_apply_is_idempotent(db_session):
    _pg("delete from chart_of_accounts"); _pg("delete from coa_aux_items")
    accounts, aux = build(_extract())
    apply(diff(accounts, []), aux, diff_aux(aux, []), _TEST_DSN, uuid.uuid4())
    db_rows = [dict(zip(("code", "name", "account_type", "normal_balance",
                         "is_postable", "parent_code", "quantity_accounting",
                         "default_uom", "default_currency", "is_off_balance",
                         "is_active"), r))
               for r in _pg("select code, name, account_type, normal_balance, "
                            "is_postable, parent_code, quantity_accounting, "
                            "default_uom, default_currency, is_off_balance, "
                            "is_active from chart_of_accounts")]
    d2 = diff(accounts, db_rows)
    assert d2.to_insert == [] and d2.to_update == [] and d2.to_deactivate == []
    assert d2.unchanged == 1

async def test_apply_preserves_uniops_metadata_on_update(db_session):
    _pg("delete from chart_of_accounts"); _pg("delete from coa_aux_items")
    accounts, aux = build(_extract())
    apply(diff(accounts, []), aux, diff_aux(aux, []), _TEST_DSN, uuid.uuid4())
    _pg("update chart_of_accounts set subtype='cash', "
        "aux_dimensions='[{\"code\":\"employee\",\"required\":true}]'::jsonb "
        "where code='1602'")
    db_rows = [{"code": "1602", "name": "STALE", "account_type": "asset",
                "normal_balance": "credit", "is_postable": True,
                "parent_code": None, "quantity_accounting": False,
                "default_uom": None, "default_currency": "CAD",
                "is_off_balance": False, "is_active": True}]
    apply(diff(accounts, db_rows), aux, diff_aux(aux, aux), _TEST_DSN, uuid.uuid4())
    got = _pg("select name, subtype, aux_dimensions from chart_of_accounts "
              "where code='1602'")[0]
    assert got[0] == "Accumulated depreciation"        # NC 字段被覆盖
    assert got[1] == "cash"                            # UniOps 字段原封不动
    assert got[2] == [{"code": "employee", "required": True}]
```

- [ ] **Step 2: 跑测试确认失败**

```bash
TEST_PG_PASSWORD=$(grep '^DB_PASSWORD=' /c/Project/uniops/.env | cut -d= -f2- | tr -d ' \r') \
  ./.venv/Scripts/python -m pytest tests/test_nc_coa_sync.py -k "build or apply" -v
```

Expected: FAIL —— `ImportError: cannot import name 'build'`

- [ ] **Step 3: 追加实现到 `app/services/nc_coa_sync.py`**

```python
import uuid as _uuid
from datetime import datetime, timezone

import psycopg2
from psycopg2.extras import execute_values, register_uuid
from sqlalchemy.engine.url import make_url

register_uuid()


@dataclass
class NcCoaExtract:
    """Raw NC reads, pre-transform. Tests inject a fake one."""
    accounts: list = field(default_factory=list)
    aux: list = field(default_factory=list)
    pk2code: dict = field(default_factory=dict)
    uom: dict = field(default_factory=dict)
    ccy: dict = field(default_factory=dict)
    acctype: dict = field(default_factory=dict)


def fetch_coa_from_nc() -> NcCoaExtract:
    """Live NC read (oracledb, read-only). Blocking — call via run_in_executor."""
    import oracledb
    dsn = oracledb.makedsn(settings.nc_host, settings.nc_port,
                           service_name=settings.nc_service)
    con = oracledb.connect(user=settings.nc_user, password=settings.nc_password, dsn=dsn)
    try:
        cur = con.cursor()
        cur.execute("select pk_measdoc, code from NCSC.BD_MEASDOC")
        uom = {pk: code for pk, code in cur.fetchall()}
        cur.execute("select pk_currtype, code from NCSC.BD_CURRTYPE")
        ccy = {pk: code for pk, code in cur.fetchall()}
        cur.execute("select pk_acctype, code from NCSC.BD_ACCTYPE")
        acctype = {pk: code for pk, code in cur.fetchall()}

        # BD_ACCASOA is the per-chart authoritative record: it covers names
        # 348/360 vs BD_ACCOUNT.name2's 184, and endflag only exists there.
        cur.execute(
            "select a.pk_account, a.code, a.pid, a.pk_acctype, a.balanorient, "
            "       a.unit, a.currency, a.outflag, soa.endflag, "
            "       soa.name, soa.name2, a.name, a.name2 "
            "from NCSC.BD_ACCOUNT a "
            "join NCSC.BD_ACCASOA soa on soa.pk_account = a.pk_account "
            "  and soa.pk_accchart = a.pk_accchart "
            "where a.pk_accchart = :c and a.enablestate = 2", c=CHART)
        accounts = [{"pk": r[0], "code": r[1], "pid": r[2], "acctype_pk": r[3],
                     "balanorient": r[4], "unit_pk": r[5], "currency_pk": r[6],
                     "outflag": r[7], "endflag": r[8], "name_soa": r[9],
                     "name2_soa": r[10], "name_acct": r[11], "name2_acct": r[12]}
                    for r in cur.fetchall()]

        cur.execute(
            "select acc.code, item.code, a.id, a.isempty "
            "from NCSC.BD_ACCASS a "
            "join NCSC.BD_ACCASOA soa on soa.pk_accasoa = a.pk_coveraccasoa "
            "join NCSC.BD_ACCOUNT acc on acc.pk_account = soa.pk_account "
            "join NCSC.BD_ACCASSITEM item on item.pk_accassitem = a.pk_entity "
            "where acc.pk_accchart = :c", c=CHART)
        aux = [{"account_code": r[0], "nc_item_code": r[1], "seq": int(r[2]),
                "isempty": r[3]} for r in cur.fetchall()]
    finally:
        con.close()
    pk2code = {r["pk"]: r["code"].strip() for r in accounts}
    return NcCoaExtract(accounts=accounts, aux=aux, pk2code=pk2code,
                        uom=uom, ccy=ccy, acctype=acctype)


def build(extract: NcCoaExtract) -> tuple[list[dict], list[dict]]:
    """Map + guard. Raises rather than returning anything we'd have to guess at."""
    # Zero-row guards: with "absent from NC = deactivate", an empty read would
    # deactivate the entire 360-account chart. A wrong chart pk looks exactly
    # like this.
    if not extract.accounts:
        raise NcMappingError("NC returned no accounts; refusing to deactivate the chart")
    if not extract.aux:
        raise NcMappingError("NC returned no aux rows; refusing to wipe coa_aux_items")

    accounts = [map_account(r, uom=extract.uom, ccy=extract.ccy,
                            acctype=extract.acctype, pk2code=extract.pk2code)
                for r in extract.accounts]
    type_by_code = {a["code"]: a["account_type"] for a in accounts}

    aux, seen = [], set()
    for r in extract.aux:
        acct = r["account_code"].strip()
        dim = map_aux_item(r["nc_item_code"])
        if dim == "__party__":
            atype = type_by_code.get(acct)
            if atype is None:
                raise NcMappingError(f"aux row for unknown account {acct}")
            dim = derive_party_dim(acct, atype)
        key = (acct, dim)
        if key in seen:            # two NC items can land on one dim; keep the first
            continue
        seen.add(key)
        aux.append({"account_code": acct, "dim_code": dim, "seq": r["seq"],
                    "required": r["isempty"] == "N"})
    return accounts, aux


def _pg_dsn() -> str:
    u = make_url(settings.database_url)
    return (f"host={u.host} port={u.port or 5432} dbname={u.database} "
            f"user={u.username} password={u.password}")


_UPDATE_SQL = (
    "update chart_of_accounts set "
    + ", ".join(f"{f} = %({f})s" for f in NC_OWNED_FIELDS)
    + ", is_active = true, updated_at = now() where code = %(code)s"
)


def apply(coa_diff: CoaDiff, aux_rows: list[dict], aux_diff: AuxDiff,
          dsn: str, started_by) -> dict:
    """Single transaction, all-or-nothing. The audit row is committed SEPARATELY
    afterwards — inside the same transaction a rollback would take the failure
    record with it, exactly when it is most needed."""
    started_at = datetime.now(timezone.utc)
    counts = {"accounts_inserted": len(coa_diff.to_insert),
              "accounts_updated": len(coa_diff.to_update),
              "accounts_deactivated": len(coa_diff.to_deactivate),
              "aux_items_inserted": len(aux_diff.to_insert),
              "aux_items_deleted": len(aux_diff.to_delete)}
    err = None
    con = psycopg2.connect(dsn)
    con.autocommit = False
    try:
        cur = con.cursor()
        for a in coa_diff.to_insert:
            cur.execute(
                "insert into chart_of_accounts (id, code, name, account_type, "
                " normal_balance, is_postable, parent_code, quantity_accounting, "
                " default_uom, default_currency, is_off_balance, is_active, "
                " aux_dimensions, created_at, updated_at) "
                "values (gen_random_uuid(), %(code)s, %(name)s, %(account_type)s, "
                " %(normal_balance)s, %(is_postable)s, %(parent_code)s, "
                " %(quantity_accounting)s, %(default_uom)s, %(default_currency)s, "
                " %(is_off_balance)s, true, '[]'::jsonb, now(), now())", a)
        for u in coa_diff.to_update:
            cur.execute(_UPDATE_SQL, u["values"])
        for d in coa_diff.to_deactivate:
            cur.execute("update chart_of_accounts set is_active = false, "
                        "updated_at = now() where code = %s", (d["code"],))
        # aux is a pure NC projection — replace wholesale (spec §5)
        if aux_diff.to_insert or aux_diff.to_delete:
            cur.execute("delete from coa_aux_items")
            execute_values(cur,
                "insert into coa_aux_items (id, account_code, dim_code, seq, "
                " required, created_at, updated_at) values %s",
                [(_uuid.uuid4(), r["account_code"], r["dim_code"], r["seq"],
                  r["required"]) for r in aux_rows],
                template="(%s,%s,%s,%s,%s, now(), now())", page_size=2000)
        con.commit()
    except Exception as e:
        con.rollback()
        err = str(e)[:2000]
        raise
    finally:
        con.close()
        _write_audit(dsn, started_by, started_at, counts, err)
    return counts


def _write_audit(dsn, started_by, started_at, counts, err) -> None:
    """Separate connection + transaction so it survives a rollback of the main one."""
    con = psycopg2.connect(dsn)
    con.autocommit = True
    try:
        con.cursor().execute(
            "insert into coa_sync_runs (id, started_by, started_at, finished_at, "
            " accounts_inserted, accounts_updated, accounts_deactivated, "
            " aux_items_inserted, aux_items_deleted, error, created_at, updated_at) "
            "values (%s,%s,%s,now(),%s,%s,%s,%s,%s,%s,now(),now())",
            (_uuid.uuid4(), started_by, started_at,
             0 if err else counts["accounts_inserted"],
             0 if err else counts["accounts_updated"],
             0 if err else counts["accounts_deactivated"],
             0 if err else counts["aux_items_inserted"],
             0 if err else counts["aux_items_deleted"], err))
    finally:
        con.close()
```

- [ ] **Step 4: 跑测试确认通过**

```bash
TEST_PG_PASSWORD=$(grep '^DB_PASSWORD=' /c/Project/uniops/.env | cut -d= -f2- | tr -d ' \r') \
  ./.venv/Scripts/python -m pytest tests/test_nc_coa_sync.py -v
```

Expected: 全部 passed

- [ ] **Step 5: Commit**

```bash
cd /c/Project/uniops/.worktrees/nc-coa-sync
git add finance-api/app/services/nc_coa_sync.py finance-api/tests/test_nc_coa_sync.py
git commit -m "feat(finance): NC COA fetch + apply with zero-row guards

The audit row commits on its own connection so a rolled-back sync still leaves
a record. Zero-row reads are refused outright: with deactivate-if-absent, an
empty read (a mistyped chart pk looks identical) would retire all 360 accounts."
```

---

### Task 5: API 端点 + 路由注册

**Files:**
- Create: `finance-api/app/api/v1/nc_coa_sync.py`
- Modify: `finance-api/app/api/v1/__init__.py`
- Test: `finance-api/tests/test_nc_coa_sync.py`(追加)

**Interfaces:**
- Consumes: Task 2/3/4 全部
- Produces: `GET /finance/v1/coa-sync/status`、`POST /finance/v1/coa-sync/preview`、
  `POST /finance/v1/coa-sync/apply`;测试缝 `_fetch`、`_worker_dsn`

- [ ] **Step 1: 写失败的测试**

追加到 `finance-api/tests/test_nc_coa_sync.py`:

```python
from datetime import timedelta

import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from jose import jwt

from app.core.config import settings as app_settings
from app.db.base import get_db
from app.main import app

def _token(role="system_admin", sub=None):
    return jwt.encode({"sub": str(sub or uuid.uuid4()), "role": role,
                       "exp": datetime.now(timezone.utc) + timedelta(hours=1)},
                      app_settings.jwt_secret_key, algorithm=app_settings.jwt_algorithm)

def _h(role="system_admin"):
    return {"Authorization": f"Bearer {_token(role)}"}

@pytest_asyncio.fixture
async def client(db_session):
    async def _override_get_db():
        yield db_session
    app.dependency_overrides[get_db] = _override_get_db
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        yield c
    app.dependency_overrides.clear()

def _configure_nc(monkeypatch):
    for f in ("nc_host", "nc_service", "nc_user", "nc_password"):
        monkeypatch.setattr(app_settings, f, "x")

async def test_status_shape(client, monkeypatch):
    _configure_nc(monkeypatch)
    r = await client.get("/finance/v1/coa-sync/status", headers=_h())
    assert r.status_code == 200
    assert r.json()["configured"] is True and r.json()["can_sync"] is True
    r2 = await client.get("/finance/v1/coa-sync/status", headers=_h(role="finance_manager"))
    assert r2.json()["can_sync"] is False

async def test_preview_requires_system_admin(client, monkeypatch):
    _configure_nc(monkeypatch)
    r = await client.post("/finance/v1/coa-sync/preview", headers=_h(role="finance_manager"))
    assert r.status_code == 403

async def test_preview_503_when_not_configured(client, monkeypatch):
    monkeypatch.setattr(app_settings, "nc_password", None)
    r = await client.post("/finance/v1/coa-sync/preview", headers=_h())
    assert r.status_code == 503

async def test_preview_503_on_zero_rows(client, monkeypatch):
    from app.api.v1 import nc_coa_sync as api_mod
    _configure_nc(monkeypatch)
    monkeypatch.setattr(api_mod, "_fetch", lambda: _extract(accounts=[]))
    r = await client.post("/finance/v1/coa-sync/preview", headers=_h())
    assert r.status_code == 503

async def test_preview_writes_nothing(client, monkeypatch):
    from app.api.v1 import nc_coa_sync as api_mod
    _configure_nc(monkeypatch)
    _pg("delete from chart_of_accounts")
    monkeypatch.setattr(api_mod, "_fetch", lambda: _extract())
    r = await client.post("/finance/v1/coa-sync/preview", headers=_h())
    assert r.status_code == 200
    body = r.json()
    assert body["accounts"]["to_insert"] == 1
    # 正面证据:预览之后库里一行没有
    assert _pg("select count(*) from chart_of_accounts")[0][0] == 0

async def test_apply_writes_and_reports_counts(client, monkeypatch):
    from app.api.v1 import nc_coa_sync as api_mod
    _configure_nc(monkeypatch)
    _pg("delete from chart_of_accounts"); _pg("delete from coa_aux_items")
    monkeypatch.setattr(api_mod, "_fetch", lambda: _extract())
    monkeypatch.setattr(api_mod, "_worker_dsn", lambda: _TEST_DSN)
    r = await client.post("/finance/v1/coa-sync/apply", headers=_h())
    assert r.status_code == 200, r.text
    assert r.json()["accounts_inserted"] == 1
    assert _pg("select normal_balance from chart_of_accounts where code='1602'")[0][0] == "credit"
```

- [ ] **Step 2: 跑测试确认失败**

```bash
TEST_PG_PASSWORD=$(grep '^DB_PASSWORD=' /c/Project/uniops/.env | cut -d= -f2- | tr -d ' \r') \
  ./.venv/Scripts/python -m pytest tests/test_nc_coa_sync.py -k "status or preview or apply_writes" -v
```

Expected: FAIL —— 404 / ModuleNotFoundError

- [ ] **Step 3: 写 `app/api/v1/nc_coa_sync.py`**

```python
"""NC65 COA + aux sync — preview/apply (system_admin only).

Synchronous by design: 360 accounts + 205 aux rows read in ~1-2s. The voucher
sync's worker/run-table/polling machinery exists for volume this does not have,
and preview->confirm already needs two calls.
"""
import asyncio

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import CurrentUser
from app.db.base import get_db
from app.models.coa import ChartOfAccount, CoaAuxItem
from app.models.coa_sync import CoaSyncRun
from app.services import nc_coa_sync as svc

router = APIRouter(prefix="/coa-sync", tags=["coa-sync"])

# test seams — monkeypatched in tests
_fetch = svc.fetch_coa_from_nc
_worker_dsn = svc._pg_dsn


def _require_admin(user: dict) -> None:
    if user.get("role") != "system_admin":
        raise HTTPException(status_code=403, detail="system_admin only")


def _require_configured() -> None:
    if not svc.nc_configured():
        raise HTTPException(status_code=503, detail="NC connection is not configured")


async def _read_db_state(db: AsyncSession) -> tuple[list[dict], list[dict]]:
    accts = (await db.execute(select(ChartOfAccount))).scalars().all()
    fields = svc.NC_OWNED_FIELDS + ("code", "is_active")
    db_accounts = [{f: getattr(a, f) for f in fields} for a in accts]
    items = (await db.execute(select(CoaAuxItem))).scalars().all()
    db_aux = [{"account_code": i.account_code, "dim_code": i.dim_code,
               "seq": i.seq, "required": i.required} for i in items]
    return db_accounts, db_aux


async def _build_or_503():
    loop = asyncio.get_running_loop()
    try:
        extract = await loop.run_in_executor(None, _fetch)
        return svc.build(extract)
    except svc.NcMappingError as e:
        raise HTTPException(status_code=503, detail=str(e)) from None
    except Exception as e:                       # oracledb / network
        raise HTTPException(status_code=503, detail=f"NC read failed: {e}") from None


@router.get("/status")
async def status(user: CurrentUser, db: AsyncSession = Depends(get_db)):
    last = (await db.execute(select(CoaSyncRun)
                             .order_by(CoaSyncRun.started_at.desc()).limit(1))
            ).scalars().first()
    return {
        "can_sync": user.get("role") == "system_admin",
        "configured": svc.nc_configured(),
        "last_run": None if last is None else {
            "id": str(last.id),
            "started_at": last.started_at.isoformat() if last.started_at else None,
            "finished_at": last.finished_at.isoformat() if last.finished_at else None,
            "accounts_inserted": last.accounts_inserted,
            "accounts_updated": last.accounts_updated,
            "accounts_deactivated": last.accounts_deactivated,
            "aux_items_inserted": last.aux_items_inserted,
            "aux_items_deleted": last.aux_items_deleted,
            "error": last.error,
        },
    }


@router.post("/preview")
async def preview(user: CurrentUser, db: AsyncSession = Depends(get_db)):
    _require_admin(user)
    _require_configured()
    nc_accounts, nc_aux = await _build_or_503()
    db_accounts, db_aux = await _read_db_state(db)
    d = svc.diff(nc_accounts, db_accounts)
    ad = svc.diff_aux(nc_aux, db_aux)

    # Warn (never block) when a to-be-deactivated account is still mapped.
    codes = [r["code"] for r in d.to_deactivate]
    mapped = []
    if codes:
        from app.models.coa import AccountMapping
        rows = (await db.execute(select(AccountMapping)
                                 .where(AccountMapping.account_code.in_(codes)))
                ).scalars().all()
        mapped = [{"account_code": m.account_code, "mapping_type": m.mapping_type,
                   "source_code": m.source_code} for m in rows]
    return {
        "accounts": {
            "to_insert": len(d.to_insert), "to_update": len(d.to_update),
            "to_deactivate": len(d.to_deactivate), "unchanged": d.unchanged,
            "updates": [{"code": u["code"], "reactivated": u["reactivated"],
                         "changes": {k: [v[0], v[1]] for k, v in u["changes"].items()}}
                        for u in d.to_update],
            "deactivations": d.to_deactivate,
        },
        "aux_items": {"to_insert": len(ad.to_insert), "to_delete": len(ad.to_delete),
                      "unchanged": ad.unchanged},
        "referenced_by_mappings": mapped,
    }


@router.post("/apply")
async def apply(user: CurrentUser, db: AsyncSession = Depends(get_db)):
    _require_admin(user)
    _require_configured()
    # Re-read NC and recompute: never let the client tell the server what to write.
    nc_accounts, nc_aux = await _build_or_503()
    db_accounts, db_aux = await _read_db_state(db)
    d = svc.diff(nc_accounts, db_accounts)
    ad = svc.diff_aux(nc_aux, db_aux)
    loop = asyncio.get_running_loop()
    try:
        return await loop.run_in_executor(
            None, svc.apply, d, nc_aux, ad, _worker_dsn(), user["sub"])
    except Exception as e:
        raise HTTPException(status_code=503, detail=f"apply failed: {e}") from None
```

- [ ] **Step 4: 注册 router**

在 `finance-api/app/api/v1/__init__.py` 中,`nc_sync_router` 的 import 之后加:

```python
from app.api.v1.nc_coa_sync import router as nc_coa_sync_router
```

在 `api_router.include_router(nc_sync_router)` 之后加:

```python
api_router.include_router(nc_coa_sync_router)
```

- [ ] **Step 5: 跑全部测试确认通过**

```bash
TEST_PG_PASSWORD=$(grep '^DB_PASSWORD=' /c/Project/uniops/.env | cut -d= -f2- | tr -d ' \r') \
  ./.venv/Scripts/python -m pytest tests/test_nc_coa_sync.py -v
```

Expected: 全部 passed

- [ ] **Step 6: 确认没打断既有测试**

```bash
TEST_PG_PASSWORD=$(grep '^DB_PASSWORD=' /c/Project/uniops/.env | cut -d= -f2- | tr -d ' \r') \
  ./.venv/Scripts/python -m pytest tests/ -q
```

Expected: 与改动前同样通过(数量只增不减)

- [ ] **Step 7: Commit**

```bash
cd /c/Project/uniops/.worktrees/nc-coa-sync
git add finance-api/app/api/v1/nc_coa_sync.py finance-api/app/api/v1/__init__.py \
        finance-api/tests/test_nc_coa_sync.py
git commit -m "feat(finance): /coa-sync status + preview + apply

apply re-reads NC and recomputes the diff rather than trusting a snapshot from
the client; preview is proven side-effect-free by asserting the table is still
empty afterwards."
```

---

### Task 6: 前端 —— 同步弹窗 + 按钮 + 辅助核算编辑器降只读

**Files:**
- Create: `finance/src/pages/finance/CoaSyncModal.tsx`
- Modify: `finance/src/pages/finance/CoaConfigPage.tsx`

**Interfaces:**
- Consumes: Task 5 的三个端点
- Produces: `<CoaSyncModal onClose onSynced />`、`CoaSyncStatus` 类型

> 文案**全英文**(既有约定)。参照 `NcSyncModal.tsx` 的结构与 `financeApi` 用法。

- [ ] **Step 1: 写 `finance/src/pages/finance/CoaSyncModal.tsx`**

```tsx
/**
 * COA sync — preview then apply. Two states, no polling: the sync is
 * synchronous (360 accounts read in ~1-2s), so preview returns the diff
 * directly and apply returns the counts.
 */
import { useState } from 'react'
import { useQueryClient } from '@tanstack/react-query'
import { AlertTriangle, Loader2, RefreshCw } from 'lucide-react'
import { financeApi } from '@/lib/api'

export interface CoaSyncStatus {
  can_sync: boolean
  configured: boolean
  last_run: { started_at: string | null; error: string | null } | null
}

interface Change { [field: string]: [unknown, unknown] }
interface Preview {
  accounts: {
    to_insert: number; to_update: number; to_deactivate: number; unchanged: number
    updates: { code: string; reactivated: boolean; changes: Change }[]
    deactivations: { code: string; name: string }[]
  }
  aux_items: { to_insert: number; to_delete: number; unchanged: number }
  referenced_by_mappings: { account_code: string; mapping_type: string; source_code: string }[]
}
interface ApplyResult {
  accounts_inserted: number; accounts_updated: number; accounts_deactivated: number
  aux_items_inserted: number; aux_items_deleted: number
}

const btn = 'rounded-lg px-3 py-2 text-sm font-medium disabled:opacity-50'

export function CoaSyncModal({ onClose, onSynced }:
                             { onClose: () => void; onSynced: () => void }) {
  const qc = useQueryClient()
  const [preview, setPreview] = useState<Preview | null>(null)
  const [result, setResult] = useState<ApplyResult | null>(null)
  const [busy, setBusy] = useState<'preview' | 'apply' | null>(null)
  const [err, setErr] = useState<string | null>(null)

  const runPreview = async () => {
    setBusy('preview'); setErr(null)
    try {
      setPreview(await financeApi.post<Preview>('/coa-sync/preview', {}))
    } catch (e: any) {
      setErr(e?.response?.data?.detail ?? String(e))
    } finally { setBusy(null) }
  }

  const runApply = async () => {
    setBusy('apply'); setErr(null)
    try {
      setResult(await financeApi.post<ApplyResult>('/coa-sync/apply', {}))
      qc.invalidateQueries({ queryKey: ['coa'] })
      onSynced()
    } catch (e: any) {
      setErr(e?.response?.data?.detail ?? String(e))
    } finally { setBusy(null) }
  }

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4">
      <div className="max-h-[85vh] w-full max-w-3xl overflow-y-auto rounded-xl bg-white p-5 shadow-xl">
        <h2 className="mb-1 text-lg font-semibold text-neutral-900">Sync Chart of Accounts from NC</h2>
        <p className="mb-4 text-sm text-neutral-500">
          NC is the source of truth. Accounts NC no longer returns are deactivated, never deleted.
        </p>

        {err && (
          <div className="mb-3 rounded-lg bg-red-50 px-3 py-2 text-sm text-red-700">{err}</div>
        )}

        {!preview && !result && (
          <button className={`${btn} bg-[#085E5E] text-white`} disabled={busy === 'preview'}
                  onClick={runPreview}>
            {busy === 'preview' ? <Loader2 className="h-4 w-4 animate-spin" /> : 'Preview changes'}
          </button>
        )}

        {preview && !result && (
          <>
            <div className="mb-3 grid grid-cols-4 gap-2 text-sm">
              <Stat label="New" value={preview.accounts.to_insert} />
              <Stat label="Updated" value={preview.accounts.to_update} />
              <Stat label="Deactivated" value={preview.accounts.to_deactivate} />
              <Stat label="Unchanged" value={preview.accounts.unchanged} />
            </div>
            <p className="mb-3 text-sm text-neutral-600">
              Aux dimensions: +{preview.aux_items.to_insert} / −{preview.aux_items.to_delete}
              {' '}(unchanged {preview.aux_items.unchanged})
            </p>

            {preview.referenced_by_mappings.length > 0 && (
              <div className="mb-3 rounded-lg bg-amber-50 px-3 py-2 text-sm text-amber-800">
                <AlertTriangle className="mr-1 inline h-4 w-4" />
                {preview.referenced_by_mappings.length} account(s) about to be deactivated are
                still referenced by posting mappings:{' '}
                {preview.referenced_by_mappings
                  .map((m) => `${m.account_code} (${m.mapping_type}/${m.source_code})`).join(', ')}
              </div>
            )}

            {preview.accounts.updates.length > 0 && (
              <div className="mb-3 max-h-64 overflow-y-auto rounded-lg border border-neutral-200">
                <table className="w-full text-left text-xs">
                  <thead className="bg-neutral-50 text-neutral-600">
                    <tr><th className="p-2">Account</th><th className="p-2">Field</th>
                        <th className="p-2">Before</th><th className="p-2">After</th></tr>
                  </thead>
                  <tbody>
                    {preview.accounts.updates.flatMap((u) =>
                      Object.entries(u.changes).map(([f, [before, after]]) => (
                        <tr key={`${u.code}-${f}`} className="border-t border-neutral-100">
                          <td className="p-2 font-mono">{u.code}{u.reactivated ? ' (reactivated)' : ''}</td>
                          <td className="p-2">{f}</td>
                          <td className="p-2 text-neutral-500">{String(before)}</td>
                          <td className="p-2 font-medium">{String(after)}</td>
                        </tr>
                      )))}
                  </tbody>
                </table>
              </div>
            )}

            <div className="flex justify-end gap-2">
              <button className={`${btn} border border-neutral-300`} onClick={onClose}>Cancel</button>
              <button className={`${btn} bg-[#085E5E] text-white`} disabled={busy === 'apply'}
                      onClick={runApply}>
                {busy === 'apply' ? <Loader2 className="h-4 w-4 animate-spin" /> : 'Apply'}
              </button>
            </div>
          </>
        )}

        {result && (
          <>
            <div className="mb-4 rounded-lg bg-green-50 px-3 py-2 text-sm text-green-800">
              Synced. Accounts: +{result.accounts_inserted} / ~{result.accounts_updated} /
              −{result.accounts_deactivated}. Aux: +{result.aux_items_inserted} /
              −{result.aux_items_deleted}.
            </div>
            <div className="flex justify-end">
              <button className={`${btn} bg-[#085E5E] text-white`} onClick={onClose}>Close</button>
            </div>
          </>
        )}
      </div>
    </div>
  )
}

function Stat({ label, value }: { label: string; value: number }) {
  return (
    <div className="rounded-lg border border-neutral-200 p-2 text-center">
      <div className="text-lg font-semibold text-neutral-900">{value}</div>
      <div className="text-xs text-neutral-500">{label}</div>
    </div>
  )
}
```

- [ ] **Step 2: 在 `CoaConfigPage.tsx` 挂按钮**

> **实测(2026-07-15):`useState`(第 15 行)、`useQuery`(第 17 行)、`financeApi`(第 23 行)
> 均已 import,不要重复 import。** 只需两处新增。

**2a.** 第 18-21 行的 lucide 图标 import 块加入 `RefreshCw`(按字母序插在 `Plus` 之后):

```tsx
import {
  AlertCircle, BookOpenCheck, Check, Download, Loader2, Pencil, Plus,
  RefreshCw, Search, Trash2, Upload, X,
} from 'lucide-react'
```

**2b.** 第 23 行 `financeApi` import 之后加:

```tsx
import { CoaSyncModal, type CoaSyncStatus } from './CoaSyncModal'
```

**2c.** 在 `export default function CoaConfigPage() {`(第 337 行)的函数体开头加:

```tsx
  const [showCoaSync, setShowCoaSync] = useState(false)
  const { data: ncStatus } = useQuery({
    queryKey: ['coa-sync-status'],
    queryFn: () => financeApi.get<CoaSyncStatus>('/coa-sync/status'),
  })
```

**2d.** 第 412-415 行的 `PortalChromeLayout` **已有 `title`/`subtitle`,保留它们**,
只追加 `headerActions`(门禁条件与 JV 页一致:缺配置 = 功能隐藏):

```tsx
    <PortalChromeLayout
      activeKey="portal:/finance/coa"
      title="Chart of Accounts"
      subtitle="Accounts, auxiliary dimensions, and account mappings — export/import CSV to manage the chart wholesale"
      headerActions={ncStatus?.configured && ncStatus?.can_sync ? (
        <button onClick={() => setShowCoaSync(true)}
                className="flex items-center gap-1.5 rounded-lg border border-neutral-300 bg-white px-3 py-2 text-sm font-medium text-neutral-700 hover:bg-neutral-50">
          <RefreshCw className="h-4 w-4" /> NC Sync
        </button>
      ) : undefined}
    >
```

**2e.** 在 `</PortalChromeLayout>`(第 583 行附近)之前加:

```tsx
      {showCoaSync && (
        <CoaSyncModal onClose={() => setShowCoaSync(false)}
                      onSynced={() => window.location.reload()} />
      )}
```

- [ ] **Step 3: 辅助核算编辑器降为只读**

`coa_aux_items` 已是唯一真相源(spec §1.2),`aux_dimensions` 不再人工维护。
**不要删除 `aux_dimensions` 列、`coa.py` 的校验或 CSV 导入导出** —— 退役是独立清理任务
(spec §12)。只让编辑器不再改它。

**3a.** 第 97-105 行的 `cycleAux`(注释为 `/** off → optional → required → off */`)
整体替换为:

```tsx
  // Aux dimensions come from NC via coa_aux_items (the single source of truth
  // since 2026-07-15); hand-edits here would be silently overwritten on the
  // next NC Sync, so the picker is display-only. The aux_dimensions column and
  // its API stay put — retiring them is a separate cleanup (spec §12).
  const cycleAux = (_code: string) => { /* read-only */ }
```

`auxOf`(第 96 行)**保留不动** —— 第 215 行仍用它读取展示状态。

**3b.** 第 217 行的按钮加 `disabled` 与 title,并保留既有 `className` 的 `cn(...)` 结构,
仅在首个字符串里追加只读样式:

```tsx
                <button key={d.code} type="button" disabled
                        title="Managed by NC Sync — read-only"
                        onClick={() => cycleAux(d.code)}
                        className={cn(
                          'flex items-center justify-between rounded-lg border px-2.5 py-1.5 text-sm cursor-not-allowed opacity-70',
```

> `cycleAux` 保留为空函数而非删除 `onClick`,是为了让第 217 行的改动最小、
> 且未来恢复可编辑只需还原一个函数体。

- [ ] **Step 4: typecheck**

```bash
cd /c/Project/uniops/.worktrees/nc-coa-sync/finance
npx tsc -p tsconfig.app.json --noEmit --ignoreDeprecations 6.0
```

Expected: 0 errors。(前端是 TS 6.0.3,`tsc -b`/`npm run build` 会因 baseUrl 弃用直接报错 ——
必须用这条命令。)错误数须与改动前基线一致,**不要用「过滤后无输出」当通过**。

- [ ] **Step 5: Commit**

```bash
cd /c/Project/uniops/.worktrees/nc-coa-sync
git add finance/src/pages/finance/CoaSyncModal.tsx finance/src/pages/finance/CoaConfigPage.tsx
git commit -m "feat(finance): COA NC Sync button + preview/apply modal

The preview lists per-field before/after so an admin sees '18 accounts change
from debit to credit' before confirming. The aux editor drops to read-only now
that coa_aux_items is the source of truth."
```

---

## 收尾:端到端验证(非 Task,但必做)

计划全部完成后,**在 dev 上真跑一次**,拿正面证据而非「测试过了」:

```bash
# 1. 迁移
cd /c/Project/uniops
docker compose -f docker-compose.dev.yml exec finance-api alembic upgrade head

# 2. 预览(dev 前端点 NC Sync → Preview),预期看到:
#    Updated ≈ 47+(18 个 normal_balance debit→credit + 29 个 quantity_accounting)
#    并在明细里能看到 1602 累计折旧 normal_balance: debit → credit

# 3. Apply 后核对(正面证据)
docker exec uniops_postgres psql -U epms -d epms -c "
select count(*) filter (where normal_balance='credit'
        and code in ('1231','123101','123102','123103','123104','1471','1512',
                     '1602','1603','1607','1608','1620','1621','1702','1703',
                     '1713','190102')) as contra_assets_now_credit,
       count(*) filter (where quantity_accounting) as qty_accounting_now,
       count(*) filter (where default_currency is not null) as currency_filled,
       count(*) filter (where default_uom is not null) as uom_filled
from chart_of_accounts;"
# 预期: contra_assets_now_credit=17, qty_accounting_now=30,
#       currency_filled=181, uom_filled=30

docker exec uniops_postgres psql -U epms -d epms -c "
select count(*) as aux_rows, count(*) filter (where required) as required_rows,
       min(seq) as seq_min, max(seq) as seq_max from coa_aux_items;"
# 预期: aux_rows≈205, required_rows≈187, seq_min=1, seq_max=7 (不再恒为 0)
```

**上生产前**:把 preview 的改动清单交财务复核(spec §13)—— 这批改动会改变
Account Balance 与 GL 的取数结果。生产部署前提见 spec §13(NC 网络可达 + `NC_*` 已注入)。
