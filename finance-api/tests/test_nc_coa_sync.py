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
