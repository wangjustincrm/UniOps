"""Trace: categories, document keys, the two pairing tiers, the divergence,
and the endpoint over a small NC + WMS history.

Real-data checks this mirrors (2026-10-14): S0104 → WMS CASO2610020002
−5,040 (12 lines) with no NC movement; CP0168 batch 20250815011E → WMS
CA00002237 −2,520 batch change; the finished-goods receipt pairs per
document (one NC line = 13 WMS pallets).
"""
from datetime import datetime
from decimal import Decimal

import pytest
from sqlalchemy import insert

from app.models import (
    NcBatch, NcIcFlow, NcMaterial, NcWarehouse, WmsDoc, WmsLot, WmsMove,
)
from app.services import trace
from app.services.trace import Line, first_divergence, keys_of, pair_lines

D = Decimal
TOL = D("0.001")


def L(side, id_, t, cat, qty, doc=None, keys=()):
    return Line(side=side, id=id_, time=t, date_only=False, category=cat, qty=D(qty), doc=doc,
                keys=set(keys))


def test_categories():
    assert trace.nc_category("4I-Cxx-005", False) == "production_issue"
    assert trace.nc_category("4I-Cxx-099", True) == "internal"        # SC11 ↔ SC16
    assert trace.nc_category("4I-Cxx-099", False) == "other"
    assert trace.nc_category("4460", False) == "state_change"
    assert trace.wms_category("IN", "QC", False) == "manual_opening"  # D15
    assert trace.wms_category("SO", "QTCK", False) == "ship_out"
    assert trace.wms_category("TR", None, True) == "internal"
    assert trace.wms_category("TR", None, False) == "batch_change"


def test_keys_ignore_short_and_whitespace():
    assert keys_of("55A2 2026 0001", "SC11", None, "1") == {"55A220260001"}


def test_nc_time_to_toronto_and_date_only_kept():
    t, date_only = trace.nc_local_time(datetime(2026, 9, 1, 2, 43), "Asia/Shanghai")
    assert t == datetime(2026, 8, 31, 14, 43) and not date_only
    t, date_only = trace.nc_local_time(datetime(2026, 9, 1), "Asia/Shanghai")
    assert t == datetime(2026, 9, 1) and date_only


def test_tier1_groups_one_document_to_many_lines():
    nc = [L("nc", "n1", datetime(2026, 1, 5), "production_issue", -100, "TC1", ["55A2X0001"])]
    wms = [L("wms", "w1", datetime(2026, 1, 4), "production_issue", -60, "CASO1", ["55A2X0001"]),
           L("wms", "w2", datetime(2026, 1, 6), "production_issue", -40, "CASO2", ["55A2X0001"])]
    [g] = pair_lines(nc, wms, TOL)
    assert g["tier"] == 1 and len(g["wms"]) == 2 and all(x.pair == 0 for x in nc + wms)


def test_tier2_pairs_per_document_within_window():
    """One NC receipt of 5,216.4 = 13 WMS pallets on one ASN (S0104)."""
    nc = [L("nc", "n1", datetime(2026, 9, 7, 3), "fg_receipt", "5216.4", "TR1")]
    wms = [L("wms", f"w{i}", datetime(2026, 9, 7, 15), "fg_receipt", 420, "CASN1") for i in range(12)]
    wms.append(L("wms", "w12", datetime(2026, 9, 7, 15), "fg_receipt", "176.4", "CASN1"))
    [g] = pair_lines(nc, wms, TOL)
    assert g["tier"] == 2 and len(g["wms"]) == 13


def test_tier2_respects_window_and_category():
    nc = [L("nc", "n1", datetime(2026, 1, 1), "production_issue", -50, "TC1")]
    wms = [L("wms", "w1", datetime(2026, 2, 1), "production_issue", -50, "CASO1"),   # 31 days > 7
           L("wms", "w2", datetime(2026, 1, 2), "other_issue", -50, "CASO2")]       # other category
    assert pair_lines(nc, wms, TOL) == []


def test_divergence_first_and_main_document():
    nc = [L("nc", "n1", datetime(2026, 9, 7), "fg_receipt", "5216.4", "TR1")]
    wms = [L("wms", "w0", datetime(2026, 9, 7, 1), "fg_receipt", "5216.4", "CASN1"),
           L("wms", "w1", datetime(2026, 10, 5), "ship_out", "-4.2", "CASO0"),
           L("wms", "w2", datetime(2026, 10, 6), "ship_out", -2520, "CASO1"),
           L("wms", "w3", datetime(2026, 10, 6), "ship_out", -2520, "CASO1")]
    pair_lines(nc, wms, TOL)
    div = first_divergence(nc, wms, D(0), TOL)
    assert div["line"].id == "w1"                              # the first non-agreeing event
    assert (div["main"]["doc"], div["main"]["qty"], div["main"]["lines"]) == ("CASO1", D(-5040), 2)


def test_no_divergence_when_balances_end_equal():
    nc = [L("nc", "n1", datetime(2026, 1, 1), "purchase_in", 10, "CR1")]
    wms = [L("wms", "w1", datetime(2026, 1, 2), "purchase_in", 10, "CASN1")]
    assert first_divergence(nc, wms, D(0), TOL) is None


@pytest.fixture
async def seeded(db_session):
    await db_session.execute(insert(NcWarehouse), [
        {"pk_stordoc": "W13", "code": "SC13", "name": "FG", "iscalculatedinvcost": True},
        {"pk_stordoc": "W02", "code": "Pro02", "name": "Line", "iscalculatedinvcost": True},
    ])
    await db_session.execute(insert(NcMaterial), [{"pk_material": "M", "code": "S0104", "name": "Niuriss"}])
    await db_session.execute(insert(NcBatch), [
        {"pk_batchcode": "B", "cmaterialoid": "M", "vbatchcode": "26E248121010211"}])
    await db_session.execute(insert(NcIcFlow), [
        {"pk_flow": "F1", "cwarehouseid": "W13", "cotherwhid": "W02", "cmaterialoid": "M",
         "vbatchcode": "26E248121010211", "pk_batchcode": "B", "ninnum": D("5216.4"),
         "biz_time": datetime(2026, 9, 7, 15, 6), "vtrantypecode": "4A-Cxx-093", "vbillcode": "TR1",
         "fbillflag": 3},
        # Line-side warehouse: out of the WMS scope (D12), never shown.
        {"pk_flow": "F2", "cwarehouseid": "W02", "cmaterialoid": "M", "vbatchcode": "26E248121010211",
         "ninnum": D(999), "biz_time": datetime(2026, 9, 1), "vtrantypecode": "4A-Cxx-001"},
    ])
    await db_session.execute(insert(WmsLot), [
        {"sku": "S0104", "lotnum": f"L{i}", "qc_batch": None, "supplier_batch": "26E248121010211"}
        for i in range(3)])
    await db_session.execute(insert(WmsDoc), [
        {"doc_kind": "ASN", "docno": "CASN1", "doc_type": "FP"},
        {"doc_kind": "SO", "docno": "CASO1", "doc_type": "QTCK", "ref1": "RADIANT_1st"},
    ])
    await db_session.execute(insert(WmsMove), [
        {"move_id": "1:T", "transactionid": "1", "txn_type": "IN", "doctype": "ASN", "docno": "CASN1",
         "sku": "S0104", "lotnum": "L0", "qty": D(5000), "txn_time": datetime(2026, 9, 7, 15, 39)},
        {"move_id": "2:T", "transactionid": "2", "txn_type": "IN", "doctype": "ASN", "docno": "CASN1",
         "sku": "S0104", "lotnum": "L1", "qty": D("216.4"), "txn_time": datetime(2026, 9, 7, 15, 40)},
        {"move_id": "3:F", "transactionid": "3", "txn_type": "SO", "doctype": "SO", "docno": "CASO1",
         "sku": "S0104", "lotnum": "L0", "qty": D(-5040), "txn_time": datetime(2026, 10, 6, 11, 7)},
    ])
    await db_session.commit()


async def test_trace_endpoint_batch(client, auth_headers, seeded):
    r = await client.get("/api/v1/trace", params={"material": "s0104", "batch": "26E 248121010211",
                                                  "date": "2026-10-14"}, headers=auth_headers)
    assert r.status_code == 200, r.text
    b = r.json()
    assert b["batch_scope"]["nc_batches"] == ["26E248121010211"]
    assert b["closing"] == {"nc": "5216.4", "wms": "176.4", "diff": "5040"}
    assert b["counts"]["paired_by_quantity"] == 1 and b["counts"]["wms_only"] == 1
    assert b["divergence"]["main"]["doc"] == "CASO1" and b["divergence"]["main"]["qty"] == "-5040"
    # The line-side warehouse flow is out of scope.
    assert all(ln["id"] != "F2" for row in b["rows"] for ln in row["nc"])
    # NC time is shown in Toronto: 15:06 China = 03:06 Toronto the same day.
    nc_line = next(ln for row in b["rows"] for ln in row["nc"])
    assert nc_line["time"] == "2026-09-07 03:06:00"
    cats = {s["category"]: s for s in b["summary"]}
    assert cats["ship_out"]["diff"] == "5040"


async def test_trace_unknown_material_404(client, auth_headers, seeded):
    r = await client.get("/api/v1/trace", params={"material": "NOPE"}, headers=auth_headers)
    assert r.status_code == 404


async def test_trace_refuses_batch_for_no_batch_materials(client, auth_headers, db_session):
    from app.models import NcMaterial as M
    await db_session.execute(insert(M), [{"pk_material": "AF", "code": "CF00AF"}])
    await db_session.commit()
    r = await client.get("/api/v1/trace", params={"material": "CF00AF", "batch": "X"}, headers=auth_headers)
    assert r.status_code == 422


async def test_material_and_batch_options(client, auth_headers, seeded):
    m = (await client.get("/api/v1/trace/materials", params={"q": "s01"}, headers=auth_headers)).json()
    assert [i["code"] for i in m["items"]] == ["S0104"]
    assert m["items"][0]["in_nc"] is True and m["items"][0]["wms_qty"] == "176.4"
    b = (await client.get("/api/v1/trace/batches", params={"material": "s0104"}, headers=auth_headers)).json()
    [g] = b["items"]
    assert g["batch"] == "26E248121010211" and g["match"] == "both"
    assert (g["nc_qty"], g["wms_qty"], g["wms_lots"]) == ("5216.4", "176.4", 3)
    # Typing part of either number narrows; whitespace is ignored.
    assert (await client.get("/api/v1/trace/batches", params={"material": "S0104", "q": "26E 2481"},
                             headers=auth_headers)).json()["total"] == 1
    assert (await client.get("/api/v1/trace/batches", params={"material": "S0104", "q": "ZZZ"},
                             headers=auth_headers)).json()["total"] == 0


async def test_same_time_movements_follow_document_numbers(client, auth_headers, db_session):
    """CF0086 on 2025-12-23 23:01: three NC documents with one timestamp. The
    balance must run 625 → 0 → 25 → 50 (document order), not by internal id."""
    from app.models import NcMaterial as M, NcWarehouse as W
    await db_session.execute(insert(W), [{"pk_stordoc": "W13", "code": "SC13", "iscalculatedinvcost": True}])
    await db_session.execute(insert(M), [{"pk_material": "M86", "code": "CF0086"}])
    t = datetime(2025, 12, 24, 12, 1)   # NC clock → 2025-12-23 23:01 Toronto
    rows = [
        # ids deliberately in the WRONG order relative to the document numbers
        ("A3", "TC2025122400202346", -625, "4I-Cxx-103"),
        ("A1", "TC2025122400202351", 25, "4I-Cxx-CRM004"),
        ("A2", "TC2025122400202352", 25, "4I-Cxx-CRM004"),
    ]
    await db_session.execute(insert(NcIcFlow), [
        {"pk_flow": pk, "cwarehouseid": "W13", "cmaterialoid": "M86", "vbillcode": bill,
         "ninnum": D(q) if q > 0 else None, "noutnum": D(-q) if q < 0 else None,
         "biz_time": t, "vtrantypecode": tt, "fbillflag": 3}
        for pk, bill, q, tt in rows])
    # Opening 625 before the window.
    await db_session.execute(insert(NcIcFlow), [{
        "pk_flow": "A0", "cwarehouseid": "W13", "cmaterialoid": "M86", "vbillcode": "OPEN",
        "ninnum": D(625), "biz_time": datetime(2025, 1, 10), "vtrantypecode": "45-Cxx-CRM02", "fbillflag": 3}])
    await db_session.commit()
    b = (await client.get("/api/v1/trace", params={"material": "CF0086", "date": "2026-01-31"},
                          headers=auth_headers)).json()
    seq = [(ln["doc"], ln["balance"]) for r in b["rows"] for ln in r["nc"]]
    assert seq == [("TC2025122400202346", "0"), ("TC2025122400202351", "25"), ("TC2025122400202352", "50")]
