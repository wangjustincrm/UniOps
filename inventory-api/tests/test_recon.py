"""Reconciliation: attribution rules (pure), batch grouping (pure), and the
endpoint over a small seeded NC + WMS.

The real-data behaviour these rules were tuned on (2026-08-31): IA−IC left
with 2 unexplained materials of 1,477; GL−IA with exactly the 7 known ones
(CF0086 −183,413.73 …); CR0190 / CP0168 flagged as batch-label differences
and S0104 as a real 5,040 gap. The tests pin the mechanics behind that.
"""
from datetime import date, datetime
from decimal import Decimal

import pytest
from sqlalchemy import insert

from app.models import (
    NcBatch, NcGlLine, NcIaLedger, NcIcFlow, NcMaterial, NcWarehouse, WmsLot, WmsMove,
)
from app.services import recon
from app.services.recon import MaterialRow, _capped, attribute, group_batches, status_of

D = Decimal
T = (D("0.001"), D("0.01"))
DAY = date(2026, 8, 31)


def test_capped_only_explains_what_is_left_in_the_same_direction():
    assert _capped(D(5), D(10)) == D(5)
    assert _capped(D(15), D(10)) == D(10)
    assert _capped(D(-5), D(10)) == 0          # wrong direction explains nothing
    assert _capped(D(5), D(0)) == 0            # nothing to explain


def _row(**kw) -> MaterialRow:
    """A row whose ledgers agree unless a test says otherwise: IA and GL
    default to the IC total, so a test isolates the one side it is about."""
    r = MaterialRow(**kw)
    if "ia_qty" not in kw:
        r.ia_qty = r.ic_total
    if "gl_qty" not in kw:
        r.gl_qty = r.ia_qty
    return r


def _cats(row):
    return {c["code"]: (c["qty"], c["amount"]) for c in row.categories}


def test_no_batch_material_difference_is_explained_from_since_date():
    r = _row(pk="M", code="CF00AF", wms=D(91034), ic_wms=D(76751), no_batch_since=date(2026, 1, 1))
    attribute(r, DAY, *T)
    assert _cats(r)["nc_stopped"][0] == D(76751 - 91034)
    assert r.residual["ic_wms"] == 0 and status_of(r, *T) == "explained"
    # Before the since-date the same gap is a finding.
    r2 = _row(pk="M", code="CF00AF", wms=D(10), ic_wms=D(0), nc_has_batches=True,
                     no_batch_since=date(2026, 1, 1))
    attribute(r2, date(2025, 12, 31), *T)
    assert status_of(r2, *T) == "unexplained"


def test_wms_only_material():
    r = MaterialRow(pk=None, code="STORE", in_nc=False, wms=D(12))
    attribute(r, DAY, *T)
    assert "wms_only_material" in _cats(r) and status_of(r, *T) == "explained"


def test_ia_ic_rules_are_capped_and_do_not_double_count():
    # SC16 360.28 came in on an unsigned document: both rules "see" it, only
    # the first may explain it (the CR0064 case from real data).
    r = MaterialRow(pk="M", code="CR0064", ic_wms=D("1745.46"), ia_qty=D("1385.18"),
                    not_costed_qty=D("360.28"), unsigned_qty=D("360.28"))
    attribute(r, DAY, *T)
    assert _cats(r) == {"not_costed_warehouse": (D("-360.28"), D(0))}
    assert r.residual["ia_ic"] == 0


def test_cumulative_shipped_minus_invoiced_cannot_invent_a_difference():
    # IA = IC, but 4C − I5 since go-live is 1,474.2 (the S0103 case).
    r = _row(pk="M", code="S0103", ic_wms=D(1472),
                    shipped_out=D("13113.9"), invoiced_out=D("11639.7"))
    attribute(r, DAY, *T)
    assert r.categories == [] and status_of(r, *T) == "ok"
    # When the difference is there, it is explained (the S0110 case: +680.4).
    r = _row(pk="M", code="S0110", ic_wms=D("20507.9"), ia_qty=D("21188.3"), gl_qty=D("21188.3"),
                    shipped_out=D("55897.8"), invoiced_out=D("55217.4"))
    attribute(r, DAY, *T)
    assert _cats(r)["shipped_not_invoiced"][0] == D("680.4") and r.residual["ia_ic"] == 0


def test_gl_ia_prior_years_and_non_ia_sources():
    r = MaterialRow(pk="M", code="CF00AF", ia_qty=D(10), gl_qty=D(10),
                    ia_amt=D("511851.10"), gl_amt=D("93677.54"),
                    gl_open_amt=D(-400000), ia_prior_amt=D("18173.56"),
                    gl_non_ia_amt=D(-5))
    attribute(r, DAY, *T)
    c = _cats(r)
    assert c["prior_years"][1] == D("-418173.56")
    assert "gl_non_ia" not in c          # nothing left to explain after prior_years
    assert r.residual["gl_ia_amount"] == 0


def test_gl_ia_equal_totals_are_not_split_into_offsetting_categories():
    """S0115: GL = IA = 0 but large CM/AP flows in both directions. Without the
    cap this produced '+1.96M non-IA' and '−1.96M unexplained'."""
    r = MaterialRow(pk="M", code="S0115", gl_non_ia_amt=D("1965343.19"), gl_non_ia_qty=D("94565.38"))
    attribute(r, DAY, *T)
    assert r.categories == [] and status_of(r, *T) == "ok"


def test_unit_check_is_information_only():
    assert recon.unit_check("KGM", "KG") == "match"
    assert recon.unit_check("EA", "KG") == "mismatch"
    assert recon.unit_check("cPs", "KG") == "unknown"
    r = _row(pk="M", code="CP0016", uom="EA", wms_uom="KG", wms=D(22000), ic_wms=D(22000))
    attribute(r, DAY, *T)
    assert status_of(r, *T) == "ok"     # agreeing quantities are not hidden behind a unit flag


# ── batch groups ────────────────────────────────────────────────────────────

def test_group_batches_either_batch_number_links_and_whitespace_is_ignored():
    wms = [
        {"lotnum": "HGC1", "qc_batch": None, "supplier_batch": "1074664", "qty": D(10)},
        {"lotnum": "HGC2", "qc_batch": "25D262 12511032-01", "supplier_batch": "25D262 12511032-01", "qty": D(5)},
        {"lotnum": "HGC3", "qc_batch": "ZZZ", "supplier_batch": None, "qty": D(1)},
    ]
    nc = [
        {"batch": "20260522107466401", "supplier_batch": "1074664", "ic_wms": D(10), "ic_other": D(0), "ia": D(10)},
        {"batch": "25D26212511032-01", "supplier_batch": None, "ic_wms": D(4), "ic_other": D(0), "ia": D(4)},
        {"batch": "ONLYNC", "supplier_batch": None, "ic_wms": D(2), "ic_other": D(0), "ia": D(2)},
    ]
    g = {x["label"]: x for x in group_batches(wms, nc)}
    assert g["20260522107466401"]["match"] == "both" and g["20260522107466401"]["wms"] == D(10)
    assert g["25D26212511032-01"]["wms"] == D(5) and g["25D26212511032-01"]["ic_wms"] == D(4)
    assert g["ONLYNC"]["match"] == "nc_only"
    assert g["ZZZ"]["match"] == "wms_only"


def test_group_batches_is_transitive_so_nothing_counts_twice():
    # Two WMS lots share NC batch B via different numbers; one group, each once.
    wms = [{"lotnum": "L1", "qc_batch": "B", "supplier_batch": None, "qty": D(3)},
           {"lotnum": "L2", "qc_batch": None, "supplier_batch": "S", "qty": D(4)}]
    nc = [{"batch": "B", "supplier_batch": "S", "ic_wms": D(7), "ic_other": D(0), "ia": D(7)}]
    [g] = group_batches(wms, nc)
    assert (g["wms"], g["ic_wms"], sorted(g["wms_lots"])) == (D(7), D(7), ["L1", "L2"])


# ── endpoint ────────────────────────────────────────────────────────────────

@pytest.fixture
async def seeded(db_session):
    await db_session.execute(insert(NcWarehouse), [
        {"pk_stordoc": "W11", "code": "SC11", "name": "Raw", "iscalculatedinvcost": True},
        {"pk_stordoc": "W16", "code": "SC16", "name": "Old", "iscalculatedinvcost": False},
        {"pk_stordoc": "W08", "code": "SC08", "name": "Radient", "iscalculatedinvcost": True},
    ])
    await db_session.execute(insert(NcMaterial), [
        {"pk_material": "M1", "code": "CR0190", "name": "Raw 190", "uom_code": "KGM", "class_code": "0102"},
        {"pk_material": "M2", "code": "CF00AF", "name": "Animal feed", "uom_code": "KGM", "class_code": "05"},
    ])
    await db_session.execute(insert(NcBatch), [
        {"pk_batchcode": "B1", "cmaterialoid": "M1", "vbatchcode": "25G363", "vvendbatchcode": "SUP-A"},
    ])
    t = datetime(2026, 8, 10, 10)

    def flow(pk, wh, mat, qty, batch=None, pkb=None):
        return {"pk_flow": pk, "cwarehouseid": wh, "cmaterialoid": mat, "vbatchcode": batch,
                "pk_batchcode": pkb, "ninnum": D(qty), "biz_time": t, "fbillflag": 3}

    await db_session.execute(insert(NcIcFlow), [
        flow("F1", "W11", "M1", 100, "25G363", "B1"),
        flow("F2", "W16", "M1", 7, "OLD"),          # not costed
        flow("F3", "W08", "M1", 50, "25G363", "B1"),  # external — not compared with WMS
        flow("F4", "W11", "M2", 30),
    ])
    await db_session.execute(insert(NcIaLedger), [
        {"cdetailledgerid": "L1", "cinventoryid": "M1", "caccountperiod": "2026-08", "fdispatchflag": 0,
         "nnum": D(150), "nmny": D(300), "biz_time": t, "vbatchcode": "25G363"},
        {"cdetailledgerid": "L2", "cinventoryid": "M2", "caccountperiod": "2026-08", "fdispatchflag": 0,
         "nnum": D(30), "nmny": D(60), "biz_time": t},
    ])
    await db_session.execute(insert(NcGlLine), [
        {"pk_detail": "G1", "pk_voucher": "V1", "year": "2026", "period": "08", "account_code": "140302",
         "material_pk": "M1", "debit_amount": D(300), "debit_qty": D(150), "tallydate": "x",
         "pk_system": "IA", "prepared_time": t},
        {"pk_detail": "G2", "pk_voucher": "V2", "year": "2026", "period": "08", "account_code": "1404",
         "material_pk": "M2", "debit_amount": D(60), "debit_qty": D(30), "tallydate": "x",
         "pk_system": "IA", "prepared_time": t},
    ])
    await db_session.execute(insert(WmsLot), [
        {"sku": "CR0190", "lotnum": "HGC1", "supplier_batch": "SUP-A"},
        {"sku": "CR0190", "lotnum": "HGC9", "supplier_batch": "WMS-ONLY"},
    ])
    await db_session.execute(insert(WmsMove), [
        {"move_id": "1:T", "transactionid": "1", "txn_type": "IN", "sku": "CR0190", "lotnum": "HGC1",
         "qty": D(90), "txn_time": t},
        {"move_id": "2:T", "transactionid": "2", "txn_type": "IN", "sku": "CR0190", "lotnum": "HGC9",
         "qty": D(17), "txn_time": t},
        {"move_id": "3:T", "transactionid": "3", "txn_type": "IN", "sku": "CF00AF", "lotnum": "HGC5",
         "qty": D(41), "txn_time": t},
    ])
    await db_session.commit()


async def test_recon_endpoint(client, auth_headers, seeded):
    r = await client.get("/api/v1/recon", params={"date": "2026-08-31", "expand_batches": "true"},
                         headers=auth_headers)
    assert r.status_code == 200, r.text
    body = r.json()
    items = {i["material_code"]: i for i in body["items"]}

    cr = items["CR0190"]
    # WMS compares with SC11 + SC16 (both WMS warehouses); SC08 is external.
    assert (cr["wms"], cr["ic_wms"], cr["ic_external"], cr["ic_total"]) == ("107", "107", "50", "157")
    assert cr["diff"]["ic_wms"] == "0"
    # IA lacks the 7 in not-costed SC16 → explained.
    assert {c["code"] for c in cr["categories"]} >= {"not_costed_warehouse", "batch_label"}
    groups = {g["label"]: g for g in cr["batches"]}
    assert groups["25G363"]["wms"] == "90" and groups["25G363"]["ic_wms"] == "100"
    assert groups["WMS-ONLY"]["match"] == "wms_only"

    af = items["CF00AF"]
    assert af["batch_expandable"] is False and "batches" not in af
    assert [c["code"] for c in af["categories"]] == ["nc_stopped"]
    assert af["status"] == "explained"


async def test_recon_withholds_amounts(client, non_admin_token, monkeypatch, seeded):
    async def codes(db, user_id, base_role):
        return {"tester"}

    async def matrix(db):
        return {"tester": {"inventory.view"}}

    monkeypatch.setattr("uniops_authz.core.user_role_codes", codes)
    monkeypatch.setattr("uniops_authz.core._effective_matrix", matrix)
    body = (await client.get("/api/v1/recon", params={"date": "2026-08-31"},
                             headers={"Authorization": f"Bearer {non_admin_token}"})).json()
    assert body["amounts_visible"] is False
    assert body["totals"]["ia_amount"] is None and body["totals"]["gl_amount"] is None
    for i in body["items"]:
        assert i["ia_amount"] is None and i["gl_amount"] is None
        assert i["diff"]["gl_ia_amount"] is None and i["unexplained"]["gl_ia_amount"] is None
        assert i["gl_by_source"] is None
        assert all(c["amount"] is None for c in i["categories"])
    assert body["items"]   # quantities still there


async def test_recon_before_wms_go_live(client, auth_headers, seeded):
    body = (await client.get("/api/v1/recon", params={"date": "2025-01-31"}, headers=auth_headers)).json()
    assert body["wms_live"] is False and body["totals"]["wms"] is None
