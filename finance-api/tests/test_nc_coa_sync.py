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

def test_aux_item_maps_party_to_partner_without_deriving():
    # 0004 客商 covers vendors and customers; it maps to one `partner` dim
    # rather than being derived per account (the old rule contradicted the data)
    assert map_aux_item("0004") == "partner"

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

def test_map_account_rejects_unknown_acctype_pk():
    with pytest.raises(NcMappingError):
        map_account(_row(acctype_pk="NOSUCH"), **_lookups())

def test_map_account_rejects_unknown_currency_pk():
    with pytest.raises(NcMappingError):
        map_account(_row(currency_pk="NOSUCH"), **_lookups())

def test_map_account_rejects_missing_accasoa_row():
    # endflag 为 None = ACCASOA 行缺失;不得降级为按 pid 推断
    with pytest.raises(NcMappingError):
        map_account(_row(endflag=None), **_lookups())


# ── diff ──────────────────────────────────────────────────────────────────
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


# ── fetch_coa_from_nc + build + apply ────────────────────────────────────────
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
