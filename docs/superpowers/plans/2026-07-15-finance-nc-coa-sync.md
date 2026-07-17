# NC COA + 辅助核算同步 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 给 Finance 加一个由 `finance.coa.manage` 权限门禁的 COA + 辅助核算同步接口
(预览→应用),用 NC 的事实替换现有导入器的推断,顺带修复 18 个方向记反 + 29 个数量核算
错误的存量科目。

**Architecture:** 同步执行(无后台 worker/无进度表/无轮询) —— 350 科目 + 234 辅助核算行,
读取约 1-2 秒。`POST /coa-sync/preview` 只读返回差异,`POST /coa-sync/apply` **重新读 NC、
重新算差异**后单事务写入。核心是两个纯函数(`map_account` / `diff`),
零 I/O、可穷举测试。阻塞的 oracledb 读一律 `run_in_executor`。

**Tech Stack:** FastAPI + SQLAlchemy(async) + alembic / oracledb 2.5.1(读 NC) +
psycopg2(写本库) / pytest + pytest-asyncio / React + TanStack Query

**Spec:** `docs/superpowers/specs/2026-07-15-finance-nc-coa-sync-design.md` —— 本计划的
每个映射决策都能在 spec 里找到实测依据,有疑问回去查 spec,别自行发挥。

**Worktree:** `c:/Project/uniops/.worktrees/nc-coa-sync`,分支 `feature/finance-nc-coa-sync`。

## Global Constraints

- **科目表 = CRM0001 `1001A1100000003CGN3F`(加拿大皇家妙克)**,**不是** root
  `1001A1100000003CG6GD`(飞鹤加拿大_根科目表)。2026-07-17 实测:凭证同步已对账的
  `PK_BOOK` 下 315,539 条 GL_DETAIL 行**全部**属于 CRM0001。科目定义在 root 并被继承,
  但**每科目表的属性(启用/末级/名称/辅助核算)一律取 CRM0001 的 `BD_ACCASOA`**;
  判断启用用 **`soa.enablestate = 2`**,不是 `a.enablestate`(两者差 10 个科目)。
  详见 spec §2.0。
- **辅助核算 join = `soa.pk_accasoa = a.pk_accasoa`**,**不是** `pk_coveraccasoa`。
  后者拿 NC UI 真值实测返回空(spec §2.0.1)。老脚本 `aux_items_import.py` join 错了列。
- **权限门禁 = `finance.coa.manage`**(2026-07-16 修订,spec §1.2):经
  `from app.core.authz import require_permission` —— **不要**硬编码
  `user.get("role") == "system_admin"`。authz ②期已确立「COA 谁能改由矩阵决定,
  不改代码」,COA 页 CSV 导入用的就是这把锁;同步与之破坏力相同,必须同锁。
  (voucher 的 `nc_sync.py` 仍是硬编码 system_admin —— **那是未迁的存量,别照抄**。)
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
  - `map_account(row: dict, uom: dict, ccy: dict, acctype: dict, pk2code: dict) -> dict`
  - 异常类 `NcMappingError(ValueError)`

- [ ] **Step 1: 写失败的测试**

追加到 `finance-api/tests/test_nc_coa_sync.py`:

```python
import pytest

from app.services.nc_coa_sync import (
    NcMappingError, clean, map_account, map_account_type,
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

CHART = "1001A1100000003CGN3F"          # CRM0001 加拿大皇家妙克 — 凭证所在的科目表(spec §2.0)
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
    "0004": "partner",                  # 客商 = vendors ∪ customers (spec §3.4.1)
    "0006": "item", "0012": "item_category", "0010": "project",
    "D45": "project_type", "CRM02": "government_grant_project",
    "fa01": "asset_category", "D47": "tax_code", "0022": "bank_category",
    "0023": "bank", "0011": "bank_account", "0044": "country_region",
    "0002": "employee", "D09": "sales_type", "CRM01": "credit_card",
}

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

> ⚠️ **改 `CHART` 常量**:当前是 root `1001A1100000003CG6GD`,必须改成
> **CRM0001 `1001A1100000003CGN3F`**(加拿大皇家妙克)。见 Global Constraints 与 spec §2.0。
> 注释同步改为 `# CRM0001 加拿大皇家妙克 — 凭证所在的科目表(spec §2.0)`。

**Interfaces:**
- Consumes: Task 2/3 全部(注意:`derive_party_dim` 已删除,`0004` 直接映射为 `partner`)
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

def test_build_maps_party_item_straight_to_partner():
    # 0004 客商 -> partner, no per-account derivation (spec §3.4.1)
    e = _extract(aux=[{"account_code": "1602", "nc_item_code": "0004",
                       "seq": 1, "isempty": "Y"}])
    _, aux = build(e)
    assert aux[0]["dim_code"] == "partner"
    assert aux[0]["required"] is False                    # isempty=Y -> 可空

def test_build_rejects_aux_for_unknown_account():
    # coa_aux_items has no FK — an orphan would land silently
    e = _extract(aux=[{"account_code": "9999", "nc_item_code": "0002",
                       "seq": 1, "isempty": "N"}])
    with pytest.raises(NcMappingError, match="unknown account"):
        build(e)

def test_build_rejects_unexpected_isempty():
    # required must not silently default — an unexpected value would turn a
    # mandatory dimension optional (global constraint: no fallback defaults)
    e = _extract(aux=[{"account_code": "1602", "nc_item_code": "0002",
                       "seq": 1, "isempty": "~"}])
    with pytest.raises(NcMappingError, match="ISEMPTY"):
        build(e)

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

        # Accounts are DEFINED in the root chart and inherited; they are
        # ENABLED and CONFIGURED per chart via BD_ACCASOA (one account can carry
        # up to 19 of them). So join BD_ACCASOA on OUR chart and read enablement
        # from it — a.enablestate is the root's answer and would admit 10
        # accounts CRM0001 has actually retired. endflag/name live only on ACCASOA.
        cur.execute(
            "select a.pk_account, a.code, a.pid, a.pk_acctype, a.balanorient, "
            "       a.unit, a.currency, a.outflag, soa.endflag, "
            "       soa.name, soa.name2, a.name, a.name2 "
            "from NCSC.BD_ACCOUNT a "
            "join NCSC.BD_ACCASOA soa on soa.pk_account = a.pk_account "
            "where soa.pk_accchart = :c and soa.enablestate = 2", c=CHART)
        accounts = [{"pk": r[0], "code": r[1], "pid": r[2], "acctype_pk": r[3],
                     "balanorient": r[4], "unit_pk": r[5], "currency_pk": r[6],
                     "outflag": r[7], "endflag": r[8], "name_soa": r[9],
                     "name2_soa": r[10], "name_acct": r[11], "name2_acct": r[12]}
                    for r in cur.fetchall()]

        # Join pk_accasoa, NOT pk_coveraccasoa: the latter returns nothing for
        # 101201/1402 against the NC UI (spec §2.0.1). Scope to OUR chart's
        # ACCASOA rows and its enablement, mirroring the accounts query.
        cur.execute(
            "select acc.code, item.code, a.id, a.isempty "
            "from NCSC.BD_ACCASS a "
            "join NCSC.BD_ACCASOA soa on soa.pk_accasoa = a.pk_accasoa "
            "join NCSC.BD_ACCOUNT acc on acc.pk_account = soa.pk_account "
            "join NCSC.BD_ACCASSITEM item on item.pk_accassitem = a.pk_entity "
            "where soa.pk_accchart = :c and soa.enablestate = 2", c=CHART)
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
    known = {a["code"] for a in accounts}

    aux = []
    for r in extract.aux:
        acct = r["account_code"].strip()
        # An aux row outside our account set means the two queries disagree about
        # the chart — a bug, not data to paper over. coa_aux_items has no FK, so
        # it would otherwise land silently as an orphan.
        if acct not in known:
            raise NcMappingError(f"aux row references unknown account {acct}")
        if r["isempty"] not in ("Y", "N"):
            raise NcMappingError(
                f"account {acct}: unexpected BD_ACCASS.ISEMPTY {r['isempty']!r}")
        aux.append({"account_code": acct,
                    "dim_code": map_aux_item(r["nc_item_code"]),
                    "seq": r["seq"],
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

> **权限(2026-07-16 修订)**:三个端点均用 `finance.coa.manage`,写法照抄
> [coa.py:27-53](../../../finance-api/app/api/v1/coa.py#L27-L53) 的 `_manage_gate`/
> `_can_manage` 模式。**测试库的权限矩阵 conftest 已 seed 好**
> (`tests/conftest.py:77-139`,按 `seed_phase2_keys.py` 的默认值),
> 所以 `_h("finance_manager")` 直接能过、`_h("requester")` 直接 403,**无需额外造数据**。

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

def _token(role="finance_manager", sub=None):
    return jwt.encode({"sub": str(sub or uuid.uuid4()), "role": role,
                       "exp": datetime.now(timezone.utc) + timedelta(hours=1)},
                      app_settings.jwt_secret_key, algorithm=app_settings.jwt_algorithm)

def _h(role="finance_manager"):
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

# 权限矩阵由 conftest 按 seed_phase2_keys.py 的默认值 seed:
# finance.coa.manage = (system_admin, finance_manager)
async def test_status_shape(client, monkeypatch):
    _configure_nc(monkeypatch)
    r = await client.get("/finance/v1/coa-sync/status", headers=_h("system_admin"))
    assert r.status_code == 200
    assert r.json()["configured"] is True and r.json()["can_sync"] is True
    # finance_manager 持有 finance.coa.manage —— 与 COA 页 CSV 导入同一把锁
    r2 = await client.get("/finance/v1/coa-sync/status", headers=_h("finance_manager"))
    assert r2.json()["can_sync"] is True
    r3 = await client.get("/finance/v1/coa-sync/status", headers=_h("requester"))
    assert r3.json()["can_sync"] is False

async def test_preview_requires_coa_manage_permission(client, monkeypatch):
    _configure_nc(monkeypatch)
    r = await client.post("/finance/v1/coa-sync/preview", headers=_h("requester"))
    assert r.status_code == 403

async def test_preview_allows_finance_manager(client, monkeypatch):
    # 回归:CSV 导入这条路 finance_manager 本就能整表覆盖 COA,
    # 同步不得比它严 —— 否则同样的破坏力两套门禁,且严的那套绕得过
    from app.api.v1 import nc_coa_sync as api_mod
    _configure_nc(monkeypatch)
    monkeypatch.setattr(api_mod, "_fetch", lambda: _extract())
    r = await client.post("/finance/v1/coa-sync/preview", headers=_h("finance_manager"))
    assert r.status_code == 200

async def test_preview_503_when_not_configured(client, monkeypatch):
    monkeypatch.setattr(app_settings, "nc_password", None)
    r = await client.post("/finance/v1/coa-sync/preview", headers=_h("system_admin"))
    assert r.status_code == 503

async def test_preview_503_on_zero_rows(client, monkeypatch):
    from app.api.v1 import nc_coa_sync as api_mod
    _configure_nc(monkeypatch)
    monkeypatch.setattr(api_mod, "_fetch", lambda: _extract(accounts=[]))
    r = await client.post("/finance/v1/coa-sync/preview", headers=_h("system_admin"))
    assert r.status_code == 503

async def test_preview_writes_nothing(client, monkeypatch):
    from app.api.v1 import nc_coa_sync as api_mod
    _configure_nc(monkeypatch)
    _pg("delete from chart_of_accounts")
    monkeypatch.setattr(api_mod, "_fetch", lambda: _extract())
    r = await client.post("/finance/v1/coa-sync/preview", headers=_h("system_admin"))
    assert r.status_code == 200
    body = r.json()
    assert body["accounts"]["to_insert"] == 1
    # 正面证据:预览之后库里一行没有
    assert _pg("select count(*) from chart_of_accounts")[0][0] == 0

async def test_apply_requires_coa_manage_permission(client, monkeypatch):
    _configure_nc(monkeypatch)
    r = await client.post("/finance/v1/coa-sync/apply", headers=_h("requester"))
    assert r.status_code == 403

async def test_apply_writes_and_reports_counts(client, monkeypatch):
    from app.api.v1 import nc_coa_sync as api_mod
    _configure_nc(monkeypatch)
    _pg("delete from chart_of_accounts"); _pg("delete from coa_aux_items")
    monkeypatch.setattr(api_mod, "_fetch", lambda: _extract())
    monkeypatch.setattr(api_mod, "_worker_dsn", lambda: _TEST_DSN)
    r = await client.post("/finance/v1/coa-sync/apply", headers=_h("system_admin"))
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
"""NC65 COA + aux sync — preview/apply, gated on finance.coa.manage.

Synchronous by design: 350 accounts + 234 aux rows read in ~1-2s (measured
against the live NC box). The voucher sync's worker/run-table/polling machinery
exists for volume this does not have, and preview->confirm already needs two
calls anyway.

Same lock as the COA page's writes (including CSV import, which can overwrite
the whole chart): the blast radius is identical and this sync's source is NC
rather than a hand-edited spreadsheet. A stricter gate here would only mean
finance_manager reaches the same end via CSV while the sync button 403s.
"""
import asyncio

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.authz import require_permission
from app.core.deps import CurrentUser
from app.db.base import get_db
from app.models.coa import ChartOfAccount, CoaAuxItem
from app.models.coa_sync import CoaSyncRun
from app.services import nc_coa_sync as svc

router = APIRouter(prefix="/coa-sync", tags=["coa-sync"])

# test seams — monkeypatched in tests
_fetch = svc.fetch_coa_from_nc
_worker_dsn = svc._pg_dsn

_MANAGE_KEY = "finance.coa.manage"          # same key coa.py gates its writes on
_manage_gate = require_permission(_MANAGE_KEY)


async def _can_manage(db: AsyncSession, user: dict) -> bool:
    try:
        await _manage_gate(user, db)
        return True
    except HTTPException:
        return False


async def _require_manage(db: AsyncSession, user: dict) -> None:
    if not await _can_manage(db, user):
        raise HTTPException(status_code=403,
                            detail="Insufficient permission to sync the chart of accounts")


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
        "can_sync": await _can_manage(db, user),
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
    await _require_manage(db, user)
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
    await _require_manage(db, user)
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

**3a.** **删除**第 97-105 行的整个 `cycleAux` 函数(注释为
`/** off → optional → required → off */` 的那个),并在其位置留下说明:

```tsx
  // Aux dimensions come from NC via coa_aux_items (the single source of truth
  // since 2026-07-15); hand-edits here would be silently overwritten on the
  // next NC Sync, so the picker below is display-only. The aux_dimensions
  // column and its API stay put — retiring them is a separate cleanup (§12).
```

`auxOf`(第 96 行)**保留不动** —— 第 215 行仍用它读取展示状态。

**3b.** 第 217 行的按钮:**删除 `onClick`**(disabled 的按钮不会触发它,留着就是死代码),
加 `disabled` 与 title,并保留既有 `className` 的 `cn(...)` 结构,
仅在首个字符串里追加只读样式:

```tsx
                <button key={d.code} type="button" disabled
                        title="Managed by NC Sync — read-only"
                        className={cn(
                          'flex items-center justify-between rounded-lg border px-2.5 py-1.5 text-sm cursor-not-allowed opacity-70',
```

> 删 `cycleAux` 而非留空函数(2026-07-16 决策):空函数 + 未使用参数 +
> disabled 按钮上永不触发的 onClick = 三重死代码。将来要恢复可编辑,
> 重新加回 handler 的改动量与填回函数体一样小。
>
> ⚠️ 删掉 `cycleAux` 后如果 `cn` 或某个 import 变成未使用,**一并清理** ——
> Step 4 的 tsc 会报出来。

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

### Task 7: `account_balance` 新增 `partner` 维度(supplier ∪ customer)

**Files:**
- Modify: `finance-api/app/crud/account_balance.py`
- Test: `finance-api/tests/test_account_balance.py`(追加)

**Interfaces:**
- Consumes: 无(独立于 nc_coa_sync 服务;`DIM_LABELS["partner"]` 由 Task 2 已加好)
- Produces: `_dimensions()` 多一个 `partner` 键;`expand_by_dims` 支持展开它

**为什么**(spec §3.4.1/§3.4.2):NC 的 `0004 客商` 同时涵盖供应商与客户,同步把它**直接**
映射为 `partner`(不再按科目性质派生 —— 那套规则已被实测推翻两次)。但 `partner` 目前
**不在 `_dimensions()` 里**,所以它是 `supported:false`、展不开。本任务让它可展开。

**可行性已实测**:`journal_voucher_lines.partner_id` **本就是并集** —— voucher 同步的
`_resolve_dims` 做 `uni_sup.get(sup) or uni_cust.get(cust)` 写进同一列,并把 `partner_name`
反规范化写在行上。dev 实测:`erp_suppliers` 1148 / `nc_customers` 81、**两表 id 重叠 0**、
56,291 行有 partner_id(990 个 id 解析为 supplier、31 个为 customer、
**627 个两边都查不到**:`Alloc Vendor` 730 行、`Assign Vendor` 578 行 …)。
那 627 个是主数据行已不存在的往来方 —— **现有 supplier/customer 维度对它们同样显示空**,
但 `partner_name` 就在行上,partner 维度可回退取用。

> ⚠️ 现有 `_dimensions()` 的元组是 `(column, model, code_attr, name_attr)` ——
> **一个 dim 一个 model**。partner 要合并两个 model,且二者属性名不同
> (`erp_supplier_code`/`supplier_name` vs `code`/`name`),故需把查找从
> 「id → model 行」改成「id → (code, name)」。这是本任务唯一的结构改动。

- [ ] **Step 1: 先读懂既有测试的造数方式**

```bash
grep -n "async def test_\|expand_by_dims\|partner_id" finance-api/tests/test_account_balance.py | head -20
```

找一个已有的 `expand_by_dims` 用例,**照抄它的 JV/line setup**。不要新造 helper。

- [ ] **Step 2: 写失败的测试**

追加到 `finance-api/tests/test_account_balance.py`,用 Step 1 看到的 setup 填实:

```python
async def test_partner_dim_is_supported(db_session):
    from app.crud.account_balance import _dimensions
    assert "partner" in _dimensions()


async def test_partner_dim_resolves_supplier_and_customer_together(db_session):
    # partner = supplier ∪ customer: both masters resolve through the one
    # partner_id column, each row carrying its own master's code/name.
    # setup: one line whose partner_id is a real erp_suppliers.id, one whose
    # partner_id is a real nc_customers.id, same account + period.
    out = await expand_by_dims(db_session, account_code=ACCT, period=PERIOD,
                               dims=["partner"])
    names = {k["name"] for r in out["rows"] for k in r["keys"]}
    assert names == {SUPPLIER_NAME, CUSTOMER_NAME}


async def test_partner_dim_falls_back_to_line_name_when_master_is_gone(db_session):
    # 627 partner_ids in dev resolve against neither master — their master row
    # is gone. The name is denormalized on the line; use it rather than blank.
    # setup: one line with partner_id = a uuid in neither master,
    #        partner_name = "Ghost Vendor"
    out = await expand_by_dims(db_session, account_code=ACCT, period=PERIOD,
                               dims=["partner"])
    key = out["rows"][0]["keys"][0]
    assert key["name"] == "Ghost Vendor"      # not None
    assert key["code"] is None                # no master row -> no code
```

- [ ] **Step 3: 跑测试确认失败**

```bash
cd c:/Project/uniops/.worktrees/nc-coa-sync/finance-api
TEST_PG_PASSWORD=$(grep '^DB_PASSWORD=' /c/Project/uniops/.env | cut -d= -f2- | tr -d ' \r') \
  DATABASE_URL=postgresql+asyncpg://x:x@localhost/x JWT_SECRET_KEY=x \
  /c/Project/uniops/finance-api/.venv/Scripts/python -m pytest tests/test_account_balance.py -v
```

Expected: FAIL —— `"partner" not in _dimensions()` / `BadDims`

- [ ] **Step 4: 实现 —— `_dimensions()` 加 partner**

```python
def _dimensions():
    """dim_code -> (jv_lines column, master model, code attr, name attr).

    supplier/customer share the partner_id column — coa_aux_items keeps them
    apart per account (AP accounts carry suppliers, AR customers). `partner` is
    the union of both: NC's 客商 (BD_ACCASS 0004) does not say which one it is,
    and the voucher sync already folds supplier-or-customer into this very
    column (_resolve_dims), so the union is what the column actually holds.
    Its master is resolved in _resolve_dim, not here — hence the Nones.
    """
    from app.models.mirrors import BudgetAccount, CostCenter, Department, ErpSupplier
    from app.models.nc_customer import NcCustomer
    return {
        "cost_center": (JournalVoucherLine.cost_center_id, CostCenter, "code", "name"),
        "department": (JournalVoucherLine.department_id, Department, "code", "name"),
        "income_expense_item": (JournalVoucherLine.income_expense_item_id, BudgetAccount, "code", "name"),
        "supplier": (JournalVoucherLine.partner_id, ErpSupplier, "erp_supplier_code", "supplier_name"),
        "customer": (JournalVoucherLine.partner_id, NcCustomer, "code", "name"),
        "partner": (JournalVoucherLine.partner_id, None, None, None),   # union — see _resolve_dim
    }
```

- [ ] **Step 5: 新增 `_resolve_dim`(放在 `_dimensions` 之后)**

```python
async def _resolve_dim(db: AsyncSession, dim: str, ids: set, reg: dict) -> dict:
    """id -> (code, name) for one dimension.

    `partner` unions suppliers and customers — their ids never collide (separate
    tables, separate UUID pks; measured 0 overlap). Ids in neither master fall
    back to the line's own denormalized partner_name: those parties' master rows
    are gone, and a name beats a blank.
    """
    if not ids:
        return {}
    if dim != "partner":
        _, model, code_attr, name_attr = reg[dim]
        rows = (await db.execute(select(model).where(model.id.in_(ids)))).scalars()
        return {r.id: (getattr(r, code_attr), getattr(r, name_attr)) for r in rows}

    from app.models.mirrors import ErpSupplier
    from app.models.nc_customer import NcCustomer
    out: dict = {}
    for model, code_attr, name_attr in (
            (ErpSupplier, "erp_supplier_code", "supplier_name"),
            (NcCustomer, "code", "name")):
        for r in (await db.execute(select(model).where(model.id.in_(ids)))).scalars():
            out[r.id] = (getattr(r, code_attr), getattr(r, name_attr))
    missing = ids - set(out)
    if missing:
        rows = (await db.execute(
            select(JournalVoucherLine.partner_id, JournalVoucherLine.partner_name)
            .where(JournalVoucherLine.partner_id.in_(missing)).distinct())).all()
        for pid, pname in rows:
            out.setdefault(pid, (None, pname))
    return out
```

- [ ] **Step 6: `expand_by_dims` 改用它(只改这两处,其余不动)**

把「批量加载」与「取值」两段替换为:

```python
    # batch-load per dimension: id -> (code, name)
    lookups: dict[str, dict] = {}
    for i, d in enumerate(dims):
        ids = {row[i] for row in raw if row[i] is not None}
        lookups[d] = await _resolve_dim(db, d, ids, reg)

    rows = []
    for row in raw:
        keys = []
        for i, d in enumerate(dims):
            vid = row[i]
            code, name = lookups[d].get(vid, (None, None))
            keys.append({"dim_code": d, "id": str(vid) if vid else None,
                         "code": code, "name": name})
        rows.append({"keys": keys, "amount": _s(_net(row[len(dims)], row[len(dims) + 1]))})
```

- [ ] **Step 7: 跑测试确认通过**

```bash
cd c:/Project/uniops/.worktrees/nc-coa-sync/finance-api
TEST_PG_PASSWORD=$(grep '^DB_PASSWORD=' /c/Project/uniops/.env | cut -d= -f2- | tr -d ' \r') \
  DATABASE_URL=postgresql+asyncpg://x:x@localhost/x JWT_SECRET_KEY=x \
  /c/Project/uniops/finance-api/.venv/Scripts/python -m pytest tests/test_account_balance.py -v
```

Expected: 全部 passed(既有用例 + 你的 3 个新用例)。
**既有用例数不得减少** —— 它们覆盖 cost_center/department/supplier/customer 的解析,
正是本次结构改动的回归网。

- [ ] **Step 8: 跑全量 finance 套件**

```bash
cd c:/Project/uniops/.worktrees/nc-coa-sync/finance-api
TEST_PG_PASSWORD=$(grep '^DB_PASSWORD=' /c/Project/uniops/.env | cut -d= -f2- | tr -d ' \r') \
  DATABASE_URL=postgresql+asyncpg://x:x@localhost/x JWT_SECRET_KEY=x \
  /c/Project/uniops/finance-api/.venv/Scripts/python -m pytest tests/ -q
```

Expected: 与改动前一致。若有 `test_account_balance.py` 之外的失败,先在 base commit 上
复现再判断是否与本改动有关,并说明是哪种。

- [ ] **Step 9: Commit**

```bash
cd /c/Project/uniops/.worktrees/nc-coa-sync
git add finance-api/app/crud/account_balance.py finance-api/tests/test_account_balance.py
git commit -m "feat(finance): partner dimension = suppliers union customers

NC's 客商 does not say whether a party is a vendor or a customer, and the
voucher sync already folds both into one partner_id column, so the union is what
that column holds. Ids whose master row is gone fall back to the line's
denormalized partner_name rather than rendering blank — more than the
supplier/customer dims manage today."
```

---

### Task 8: 凭证同步(`nc_sync.py`)四修 —— spec §14

**Files:**
- Modify: `finance-api/app/services/nc_sync.py`
- Modify: `finance-api/app/crud/journal_voucher.py`(§14.4.2 守卫)
- Test: `finance-api/tests/test_nc_sync.py`(追加)

**Interfaces:**
- Consumes: 无(独立于 nc_coa_sync)
- Produces: `NcExtract` 多两个字段;`transform` 的 voucher dict 多 `status`

> ⚠️ **`nc_sync.py` 是已对账 0 差异的生产代码**(2026-07-11..13 与 NC 逐科目净额对平)。
> **只做 §14 这四处,别顺手重构**。`resolve_aux_type_pks` 的「常量对不上就抛错」已经是
> 对的,别动。`_net_side`/`_resolve_dims` 的算法字节不动。

**四处修复(全部经 2026-07-17 实连 NC 实测,数字写在 spec §14)**

- [ ] **Step 1: 写失败的测试**

追加到 `finance-api/tests/test_nc_sync.py`。**先读该文件的 `_mini_extract()`** —— 你要改它的
voucher 元组(加 tallydate),既有测试都依赖它:

```python
def test_transform_marks_untallied_vouchers_draft():
    # NC's TALLYDATE empty = not yet posted to NC's ledger. status was hardcoded
    # "posted", which put $1.73M of un-tallied entries (incl. future periods) into
    # reports that filter on status == POSTED (spec §14.4).
    from app.services.nc_sync import transform
    e = _mini_extract()                      # its voucher has a tallydate
    vs, _, _, _ = transform(e, {}, {}, {}, {}, {}, set())
    assert vs[0]["status"] == "posted"
    e2 = _mini_extract(tallydate=None)       # not tallied in NC
    vs2, _, _, _ = transform(e2, {}, {}, {}, {}, {}, set())
    assert vs2[0]["status"] == "draft"


def test_transform_rejects_unknown_currency():
    # ccy.get(curr, "CAD") silently defaulted — same class as coa_import's
    # `return "asset"`. USD/CNY/EUR/GBP are real on this book (spec §14.3).
    from app.services.nc_sync import NcSyncError, transform
    e = _mini_extract()
    e.ccy = {}                               # currency pk resolves to nothing
    with pytest.raises(NcSyncError):
        transform(e, {}, {}, {}, {}, {}, set())


def test_cc_by_dept_covers_the_four_measured_codes():
    # 956 lines lost their cost centre to exactly these 4 dept codes
    # (404+274+163+115). 0106/0104 were already reachable via CC_BY_CODE —
    # the dept fallback simply missed them (spec §14.2).
    from app.services.nc_sync import CC_BY_DEPT
    assert CC_BY_DEPT["0106"] == "MOH-0106-E01"
    assert CC_BY_DEPT["0104"] == "MOH-0104-P01"
    assert CC_BY_DEPT["0102"] == "GA-0107"
    assert CC_BY_DEPT["0108"] == "RD-0109"
```

- [ ] **Step 2: 跑测试确认失败**

```bash
cd c:/Project/uniops/.worktrees/nc-coa-sync/finance-api
TEST_PG_PASSWORD=$(grep '^DB_PASSWORD=' /c/Project/uniops/.env | cut -d= -f2- | tr -d ' \r') \
  DATABASE_URL=postgresql+asyncpg://x:x@localhost/x JWT_SECRET_KEY=x \
  /c/Project/uniops/finance-api/.venv/Scripts/python -m pytest tests/test_nc_sync.py -v
```

Expected: FAIL(KeyError / 无 status 键 / 无 NcSyncError)

- [ ] **Step 3: §14.2 —— `CC_BY_DEPT` 补 4 个部门码**

```python
CC_BY_DEPT = {
    "0100": "GA-0100", "0101": "GA-0101", "0103": "GA-0103",
    "0105": "GA-0105", "0107": "GA-0107", "0109": "RD-0109",
    "0110": "SELL-0110", "0111": "SELL-0111", "0112": "SELL-0112", "0113": "SELL-0113",
    # 2026-07-17: these four were missing, costing exactly the 956 lines that
    # unmapped_cc_count had been reporting all along (404+274+163+115).
    # 0106/0104 were an outright oversight — CC_BY_CODE already routes
    # E01-E07/ENG -> MOH-0106-E01 and P01-P03/PD -> MOH-0104-*, so only the
    # dept-only path lost them.
    "0106": "MOH-0106-E01", "0104": "MOH-0104-P01",
    # 0102 (named "Purchasing(NOT USE)") and 0108 have no obvious EPMS
    # counterpart. These two targets are the USER'S call (2026-07-17), not
    # inferred — do not "improve" them from the code's side.
    "0102": "GA-0107", "0108": "RD-0109",
}
```

- [ ] **Step 4: §14.3 —— 币种查不到即抛错**

模块顶部加异常(放在 `nc_configured` 之前):

```python
class NcSyncError(ValueError):
    """An NC value we refuse to guess about. Aborts the run."""
```

`transform` 的行循环里,把 `extract.ccy.get(curr, "CAD")` 换成:

```python
        ccy_code = extract.ccy.get(curr)
        if ccy_code is None:
            raise NcSyncError(f"voucher line {pk}/{idx}: currency pk {curr!r} not in "
                              f"BD_CURRTYPE — refusing to default it to CAD")
```
并在 `lines.append(...)` 里用 `ccy_code` 取代原来的 `extract.ccy.get(curr, "CAD")`。

- [ ] **Step 5: §14.4 —— 未记账 → draft**

`NcExtract` 加两个字段:

```python
@dataclass
class NcExtract:
    """Raw NC reads, pre-transform. Tests inject a fake one."""
    ccy: dict           # pk_currtype -> currency code
    aux: dict           # freevalueid -> (dept_code, cc_code, io_code, sup_code, cust_code)
    vouchers: list      # (pk, year, period, num, explanation, prepareddate, creationtime,
                        #  tallydate)
    details: list       # (pk_voucher, detailindex, accountcode, dr, cr, ldr, lcr,
                        #  pk_currtype, excrate1, explanation, assid)
    max_creationtime: str | None
    tallied: set        # EVERY tallied pk in the book (NOT watermark-limited) —
                        # drives the status backfill, see _sync_statuses
```

`transform` 的 voucher 循环解包多一个 `tallydate`,并写入 status:

```python
    for pk, year, period, num, expl, pdate, _ctime, tallydate in extract.vouchers:
        ...
        vouchers.append({
            ...
            "summary": (expl or "")[:255], "nc_pk": pk,
            # NC's TALLYDATE empty = not yet posted to NC's ledger. Mirror that:
            # the GL and Account Balance both read status == POSTED only, so an
            # un-tallied voucher must not colour reports (spec §14.4).
            "status": "posted" if (tallydate and tallydate != "~") else "draft",
        })
```

`fetch_from_nc` 的 voucher 查询:加 discardflag 过滤(§14.1)、取 tallydate、
并额外拉一次**不受水位限制**的全量 tally 事实:

```python
        # Discarded (作废) vouchers are not ledger entries. Measured 2026-07-17:
        # exactly one on this book ($4,298.52) — it had been importing as posted.
        vq = ("select pk_voucher, year, period, num, explanation, prepareddate, "
              "creationtime, tallydate from NCSC.GL_VOUCHER "
              "where pk_accountingbook = :b "
              "  and (discardflag is null or discardflag <> 'Y')")
        if watermark:
            cur.execute(vq + " and creationtime >= :wm", b=PK_BOOK, wm=watermark)
        else:
            cur.execute(vq, b=PK_BOOK)
        vouchers = list(cur.fetchall())
        max_ct = max((v[6] for v in vouchers if v[6]), default=None)

        # Status backfill feed: the whole book's tally facts, deliberately NOT
        # watermark-limited. A voucher created in June and tallied in July keeps
        # its June creationtime, so the watermark would never bring it back and
        # it would sit at draft forever (spec §14.4.1). Two columns x ~40k rows.
        cur.execute("select pk_voucher, tallydate from NCSC.GL_VOUCHER "
                    "where pk_accountingbook = :b "
                    "  and (discardflag is null or discardflag <> 'Y')", b=PK_BOOK)
        tallied = {pk for pk, td in cur if td and td != "~"}
```
`return NcExtract(...)` 加 `tallied=tallied`。

- [ ] **Step 6: §14.4.1 —— 状态回填(diff-then-apply,与 COA 同构)**

`_run_worker` 里 `v_rows` 改用每张凭证自己的 status:

```python
        v_rows = [(v["id"], v["jv_number"], "JV", v["vdate"], v["period"], v["summary"],
                   v["status"], "nc", "nc_voucher", v["jv_number"], v["nc_pk"],
                   *(tot.get(v["id"], [Decimal("0")] * 4))) for v in vouchers]
```

在 voucher/line/dim 全部插入之后、`con.commit()` 之前,加回填(新函数,放模块内):

```python
def _sync_statuses(cur, tallied: set) -> tuple[int, int]:
    """Align every NC-sourced voucher's status with NC's tally fact.

    Incremental skips pks it has already imported and its watermark is on
    creationtime, so a voucher tallied AFTER import never returns through that
    path — without this it would sit at draft forever. Diff first and update only
    what actually changed (usually nothing), same shape as the COA sync.
    """
    cur.execute("select nc_source_pk, status from journal_vouchers "
                "where nc_source_pk is not null")
    current = dict(cur.fetchall())
    to_posted = [pk for pk, st in current.items() if pk in tallied and st != "posted"]
    to_draft = [pk for pk, st in current.items() if pk not in tallied and st != "draft"]
    if to_posted:
        cur.execute("update journal_vouchers set status = 'posted', updated_at = now() "
                    "where nc_source_pk = any(%s)", (to_posted,))
    if to_draft:
        cur.execute("update journal_vouchers set status = 'draft', updated_at = now() "
                    "where nc_source_pk = any(%s)", (to_draft,))
    return len(to_posted), len(to_draft)
```
在 `_run_worker` 内调用(紧接 dims 插入之后):

```python
        _sync_statuses(cur, extract.tallied)
```
> ⚠️ `extract` 在 `_run_worker` 里的变量名照实际改;若该函数没有 extract 引用,
> 从 `fetch(...)` 的返回值取。**先读 `_run_worker` 全文再动手**。

- [ ] **Step 7: §14.4.2 —— NC 来源的 JV 禁止人工改状态**

`finance-api/app/crud/journal_voucher.py`,`review()` 与 `post()` 各加一句守卫,
紧跟各自的 `_require(...)` 之后:

```python
    if jv.nc_source_pk is not None:
        raise JvPermissionError(
            "This voucher mirrors NC's tally status and cannot be posted or "
            "reviewed here — it follows NC (spec §14.4.2)")
```
> 用该文件既有的异常类(`JvPermissionError`);**先确认它的名字与构造**。
> 加一个测试:对 `nc_source_pk` 非空的 draft 凭证调 `review()` 必须抛错。

- [ ] **Step 8: 跑测试**

```bash
cd c:/Project/uniops/.worktrees/nc-coa-sync/finance-api
TEST_PG_PASSWORD=$(grep '^DB_PASSWORD=' /c/Project/uniops/.env | cut -d= -f2- | tr -d ' \r') \
  DATABASE_URL=postgresql+asyncpg://x:x@localhost/x JWT_SECRET_KEY=x \
  /c/Project/uniops/finance-api/.venv/Scripts/python -m pytest tests/test_nc_sync.py tests/test_journal_voucher.py -v
```
既有用例数**不得减少** —— `test_nc_sync.py` 是那次 0 差异对账的回归网。
`_mini_extract()` 改了元组,所有依赖它的既有用例必须仍绿。

- [ ] **Step 9: 全量 finance 套件**

```bash
cd c:/Project/uniops/.worktrees/nc-coa-sync/finance-api
TEST_PG_PASSWORD=$(grep '^DB_PASSWORD=' /c/Project/uniops/.env | cut -d= -f2- | tr -d ' \r') \
  DATABASE_URL=postgresql+asyncpg://x:x@localhost/x JWT_SECRET_KEY=x \
  /c/Project/uniops/finance-api/.venv/Scripts/python -m pytest tests/ -q
```
基线 264 passed(约 15 分钟,**前台跑,不要后台** —— 后台跑会与其它测试抢
`finance_test` 库并互相 DROP SCHEMA)。有 `test_nc_sync.py` 之外的失败,
先在 base commit 复现再判断归属。

- [ ] **Step 10: Commit**

```bash
cd /c/Project/uniops/.worktrees/nc-coa-sync
git add finance-api/app/services/nc_sync.py finance-api/app/crud/journal_voucher.py \
        finance-api/tests/test_nc_sync.py finance-api/tests/test_journal_voucher.py
git commit -m "fix(finance): voucher sync — discarded, un-tallied, currency, cost centres

Auditing the voucher sync the same way as the COA one found the book itself was
right all along (all 315,539 lines sit in CRM0001) but four things were not.

Discarded vouchers were never filtered, so one 作废 entry (\$4,298.52) sat in the
ledger as posted. Currency silently defaulted to CAD when a pk did not resolve —
harmless today since all five resolve, but USD alone carries 27,624 lines, so a
new NC currency would quietly become CAD. CC_BY_DEPT was missing four dept codes,
which is exactly the 956 lines unmapped_cc_count had been reporting all along
(404+274+163+115); 0106/0104 were plainly an oversight since CC_BY_CODE already
routes their cost-centre codes, while 0102/0108 are the user's call, not inferred.

The largest was status: it was hardcoded posted, so 272 un-tallied vouchers worth
\$1.73M — ten of them in periods that have not happened yet — were colouring the
GL and Account Balance, both of which read status == POSTED. They now mirror NC's
TALLYDATE. That needs the backfill too: incremental skips pks it has imported and
its watermark is on creationtime, so a voucher tallied after import would never
return and would sit at draft forever."
```

---

## 收尾:端到端验证(非 Task,但必做)

计划全部完成后,**在 dev 上真跑一次**,拿正面证据而非「测试过了」:

```bash
# 1. 迁移
cd /c/Project/uniops
docker compose -f docker-compose.dev.yml exec finance-api alembic upgrade head

# 2. 预览(dev 前端点 NC Sync → Preview),预期看到:
#    Deactivated = 10  (dev 现有 360 个是按 ROOT 口径导的; CRM0001 只启用 350 ——
#                       PST paid / PST Collected / 折扣 / 存货冲销 / NR 税 ... )
#    Updated 里应含: 18 个 normal_balance debit→credit + 29 个 quantity_accounting
#                    + 172 个 default_currency + 30 个 default_uom 从空变有值
#    明细里能看到 1602 累计折旧 normal_balance: debit → credit
#    并在「将被停用」清单里逐个看到那 10 个科目(交财务复核用, spec §13)

# 3. Apply 后核对(正面证据)
docker exec uniops_postgres psql -U epms -d epms -c "
select count(*) filter (where normal_balance='credit'
        and code in ('1231','123101','123102','123103','123104','1471','1512',
                     '1602','1603','1607','1608','1620','1621','1702','1703',
                     '1713','190102')) as contra_assets_now_credit,
       count(*) filter (where quantity_accounting) as qty_accounting_now,
       count(*) filter (where default_currency is not null) as currency_filled,
       count(*) filter (where default_uom is not null) as uom_filled,
       count(*) filter (where is_active) as active_accounts,
       count(*) filter (where not is_active) as inactive_accounts
from chart_of_accounts;"
# 预期(CRM0001 口径, 均已实连 NC 实测):
#   contra_assets_now_credit=17  (修复前为 0 —— 17 个全存成了 debit)
#   qty_accounting_now=30        (修复前为 1 —— 旧规则只蒙对 6002)
#   currency_filled=172, uom_filled=30   (修复前均为 0)
#   active_accounts=350, inactive=10     (ROOT 多出来的 10 个被停用, 非删除)

docker exec uniops_postgres psql -U epms -d epms -c "
select count(*) as aux_rows, count(*) filter (where required) as required_rows,
       min(seq) as seq_min, max(seq) as seq_max,
       count(*) filter (where dim_code='partner') as partner_rows
from coa_aux_items;"
# 预期(CRM0001 口径, 已实连 NC 实测): aux_rows=234, required_rows=200,
#   seq_min=1, seq_max=7, partner_rows=41
# 对照修复前: 100 行 / seq 1-4(老脚本按 SELECT 顺序编的计数器, 非 NC 序号)
#             / required 列此前根本不存在

# 4. partner 维度可展开了吗(Task 7 的意义所在)
docker exec uniops_postgres psql -U epms -d epms -c "
select code, name from chart_of_accounts
where code in (select account_code from coa_aux_items where dim_code='partner')
order by code limit 5;"
# 然后在 Account Balance 页对其中一个科目按 Partner 展开 —— 应能出数,
# 且主数据已不存在的往来方(如 Alloc Vendor)显示行上的 partner_name 而非空白
```

**上生产前**:把 preview 的改动清单交财务复核(spec §13)—— 这批改动会改变
Account Balance 与 GL 的取数结果。生产部署前提见 spec §13(NC 网络可达 + `NC_*` 已注入)。
