"""Click-to-sort on inventory-api's paged lists (sort + dir contract).

/nc/stock, /nc/cost, /nc/ledger and /recon: asc / desc, empties last in BOTH
directions, an unknown key is a 422, no sort = the previous default order,
page 2 continues page 1, and an old one-way key sent without `dir` keeps its
old direction (qty / amount largest first). Sorting by a withheld amount
falls back to the default order rather than leaking the amount's ranking.
"""
from datetime import datetime
from decimal import Decimal

import pytest
from sqlalchemy import insert

from app.core.list_sort import sort_rows
from app.models import (
    NcBatch, NcGlLine, NcIaLedger, NcIcFlow, NcMaterial, NcWarehouse,
)

D = Decimal
URL = "/api/v1/nc"
DAY = {"date": "2026-08-31"}
T = datetime(2026, 8, 1, 9, 0)


@pytest.fixture
async def seeded(db_session):
    await db_session.execute(insert(NcWarehouse), [
        {"pk_stordoc": "W11", "code": "SC11", "name": "Raw", "iscalculatedinvcost": True},
        {"pk_stordoc": "W08", "code": "SC08", "name": "Radient", "iscalculatedinvcost": True},
    ])
    # M4 has rows but is missing from the material mirror → material code null.
    await db_session.execute(insert(NcMaterial), [
        {"pk_material": "M1", "code": "CR0001", "name": "One", "uom_code": "KGM", "class_code": "0102"},
        {"pk_material": "M2", "code": "CR0002", "name": "Two", "uom_code": "KGM", "class_code": "0102"},
        {"pk_material": "M3", "code": "CR0003", "name": "Three", "uom_code": "KGM", "class_code": "0102"},
    ])
    await db_session.execute(insert(NcBatch), [
        {"pk_batchcode": "PB1", "cmaterialoid": "M1", "vbatchcode": "B2", "vvendbatchcode": "sup-b",
         "dvalidate": datetime(2027, 1, 1).date()},
        {"pk_batchcode": "PB2", "cmaterialoid": "M2", "vbatchcode": "b1", "vvendbatchcode": "SUP-A",
         "dvalidate": datetime(2026, 12, 1).date()},
    ])

    def flow(pk, wh, mat, qty, batch, pkb=None):
        return {"pk_flow": pk, "cwarehouseid": wh, "cmaterialoid": mat, "vbatchcode": batch,
                "pk_batchcode": pkb, "ninnum": D(qty), "biz_time": T,
                "dbizdate": T.strftime("%Y-%m-%d %H:%M:%S")}

    await db_session.execute(insert(NcIcFlow), [
        flow("F1", "W11", "M1", 30, "B2", "PB1"),
        flow("F2", "W11", "M2", 10, "b1", "PB2"),
        flow("F3", "W08", "M3", 20, None),           # no batch, external warehouse
        flow("F4", "W11", "M4", 40, "B3"),
    ])

    def ia(pk, mat, qty, amt):
        return {"cdetailledgerid": pk, "cinventoryid": mat, "caccountperiod": "2026-07",
                "fdispatchflag": 0, "nnum": D(qty), "nmny": D(amt) if amt is not None else None,
                "biz_time": datetime(2026, 7, 10), "cstordocid": "W11"}

    await db_session.execute(insert(NcIaLedger), [
        ia("L1", "M1", 10, 50),     # unit price 5
        ia("L2", "M2", 5, 100),     # 20
        ia("L3", "M3", 2, 0),       # 0
        ia("L4", "M4", 0, 7),       # qty 0 → unit price empty
    ])

    def gl(pk, acct, mat, amt, qty):
        return {"pk_detail": pk, "pk_voucher": "V" + pk, "year": "2026", "period": "08",
                "account_code": acct, "material_pk": mat, "debit_amount": D(amt), "debit_qty": D(qty),
                "tallydate": "2026-09-05", "pk_system": "IA", "prepared_time": datetime(2026, 8, 10)}

    await db_session.execute(insert(NcGlLine), [
        gl("G1", "140302", "M1", 40, 10),
        gl("G2", "140302", "M2", 7, 3),
        gl("G3", "140306", "M3", 9, 1),
        gl("G4", "140302", None, 5, 0),             # no material auxiliary
    ])
    await db_session.commit()


def _grant(monkeypatch, *perms):
    async def codes(db, user_id, base_role):
        return {"tester"}

    async def matrix(db):
        return {"tester": set(perms)}

    monkeypatch.setattr("uniops_authz.core.user_role_codes", codes)
    monkeypatch.setattr("uniops_authz.core._effective_matrix", matrix)


async def _items(client, h, path, **params):
    r = await client.get(URL + path, params={**DAY, **params}, headers=h)
    assert r.status_code == 200, r.text
    return r.json()["items"]


def _mats(items):
    return [i["material_code"] for i in items]


# ── pure helper ───────────────────────────────────────────────────────────

def test_sort_rows_empties_last_both_ways_and_stable():
    rows = [{"k": 2, "id": "a"}, {"k": None, "id": "b"}, {"k": 1, "id": "c"},
            {"k": "", "id": "d"}, {"k": 2, "id": "e"}]
    asc = sort_rows(rows, lambda r: r["k"], "asc", lambda r: r["id"])
    desc = sort_rows(rows, lambda r: r["k"], "desc", lambda r: r["id"])
    assert [r["id"] for r in asc] == ["c", "a", "e", "b", "d"]
    assert [r["id"] for r in desc] == ["a", "e", "c", "b", "d"]


# ── /nc/stock ─────────────────────────────────────────────────────────────

async def test_stock_batch_sorts(client, auth_headers, seeded):
    h = auth_headers
    assert _mats(await _items(client, h, "/stock")) == ["CR0001", "CR0002", "CR0003", None]   # default
    assert _mats(await _items(client, h, "/stock", sort="material", dir="desc")) == ["CR0003", "CR0002", "CR0001", None]
    # batch: case-insensitive, "no batch" last both ways
    assert _mats(await _items(client, h, "/stock", sort="batch", dir="asc")) == ["CR0002", "CR0001", None, "CR0003"]
    assert _mats(await _items(client, h, "/stock", sort="batch", dir="desc")) == [None, "CR0001", "CR0002", "CR0003"]
    assert _mats(await _items(client, h, "/stock", sort="supplier_batch", dir="desc")) == ["CR0001", "CR0002", "CR0003", None]
    assert _mats(await _items(client, h, "/stock", sort="expires", dir="desc")) == ["CR0001", "CR0002", "CR0003", None]
    assert _mats(await _items(client, h, "/stock", sort="expires", dir="asc")) == ["CR0002", "CR0001", "CR0003", None]
    # legacy one-way key without dir: largest first, as before
    assert _mats(await _items(client, h, "/stock", sort="qty")) == [None, "CR0001", "CR0003", "CR0002"]
    assert _mats(await _items(client, h, "/stock", sort="qty", dir="asc")) == ["CR0002", "CR0003", "CR0001", None]
    p1 = await _items(client, h, "/stock", sort="qty", dir="asc", page=1, page_size=2)
    p2 = await _items(client, h, "/stock", sort="qty", dir="asc", page=2, page_size=2)
    assert _mats(p1 + p2) == ["CR0002", "CR0003", "CR0001", None]
    r = await client.get(URL + "/stock", params={**DAY, "sort": "drop table"}, headers=h)
    assert r.status_code == 422


async def test_stock_material_group_sorts(client, auth_headers, seeded):
    h = auth_headers
    g = {"group": "material"}
    assert _mats(await _items(client, h, "/stock", **g)) == ["CR0001", "CR0002", "CR0003", None]
    # only CR0003 has external stock; the empties stay last in both directions
    assert _mats(await _items(client, h, "/stock", **g, sort="qty_external", dir="asc"))[0] == "CR0003"
    assert _mats(await _items(client, h, "/stock", **g, sort="qty_external", dir="desc")) == ["CR0003", "CR0001", "CR0002", None]
    assert _mats(await _items(client, h, "/stock", **g, sort="qty_wms", dir="desc")) == [None, "CR0001", "CR0002", "CR0003"]
    # old callers: warehouse/batch by material meant material order
    assert _mats(await _items(client, h, "/stock", **g, sort="warehouse")) == ["CR0001", "CR0002", "CR0003", None]
    r = await client.get(URL + "/stock", params={**DAY, **g, "sort": "supplier_batch"}, headers=h)
    assert r.status_code == 422


# ── /nc/cost ──────────────────────────────────────────────────────────────

async def test_cost_sorts(client, auth_headers, seeded):
    h = auth_headers
    assert _mats(await _items(client, h, "/cost")) == ["CR0001", "CR0002", "CR0003", None]
    assert _mats(await _items(client, h, "/cost", sort="unit_price", dir="asc")) == ["CR0003", "CR0001", "CR0002", None]
    assert _mats(await _items(client, h, "/cost", sort="unit_price", dir="desc")) == ["CR0002", "CR0001", "CR0003", None]
    assert _mats(await _items(client, h, "/cost", sort="amount")) == ["CR0002", "CR0001", None, "CR0003"]   # legacy: desc
    assert _mats(await _items(client, h, "/cost", sort="amount", dir="asc")) == ["CR0003", None, "CR0001", "CR0002"]
    p1 = await _items(client, h, "/cost", sort="qty", dir="desc", page=1, page_size=3)
    p2 = await _items(client, h, "/cost", sort="qty", dir="desc", page=2, page_size=3)
    assert _mats(p1 + p2) == ["CR0001", "CR0002", "CR0003", None]
    r = await client.get(URL + "/cost", params={**DAY, "sort": "nmny"}, headers=h)
    assert r.status_code == 422


async def test_amount_sort_ignored_without_cost_view(client, non_admin_token, monkeypatch, seeded):
    h = {"Authorization": f"Bearer {non_admin_token}"}
    _grant(monkeypatch, "inventory.view")
    for key in ("amount", "unit_price"):
        assert _mats(await _items(client, h, "/cost", sort=key, dir="desc")) == ["CR0001", "CR0002", "CR0003", None]
    led = await _items(client, h, "/ledger", sort="amount", dir="desc")
    assert [(i["account_code"], i["material_code"]) for i in led][0] == ("140302", None)   # default order


# ── /nc/ledger ────────────────────────────────────────────────────────────

async def test_ledger_sorts(client, auth_headers, seeded):
    h = auth_headers

    def keys(items):
        return [(i["account_code"], i["material_code"]) for i in items]

    assert keys(await _items(client, h, "/ledger")) == [
        ("140302", None), ("140302", "CR0001"), ("140302", "CR0002"), ("140306", "CR0003")]
    assert _mats(await _items(client, h, "/ledger", sort="material", dir="asc")) == ["CR0001", "CR0002", "CR0003", None]
    assert _mats(await _items(client, h, "/ledger", sort="material", dir="desc")) == ["CR0003", "CR0002", "CR0001", None]
    assert _mats(await _items(client, h, "/ledger", sort="amount", dir="desc")) == ["CR0001", "CR0003", "CR0002", None]
    assert _mats(await _items(client, h, "/ledger", sort="account", dir="desc"))[0] == "CR0003"
    p1 = await _items(client, h, "/ledger", sort="qty", dir="asc", page=1, page_size=2)
    p2 = await _items(client, h, "/ledger", sort="qty", dir="asc", page=2, page_size=2)
    assert _mats(p1 + p2) == [None, "CR0003", "CR0002", "CR0001"]
    r = await client.get(URL + "/ledger", params={**DAY, "sort": "unposted"}, headers=h)
    assert r.status_code == 422


# ── /recon ────────────────────────────────────────────────────────────────

async def _recon(client, h, **params):
    r = await client.get("/api/v1/recon", params={**DAY, "status": ["ok", "explained", "unexplained"],
                                                  "page_size": 50, **params}, headers=h)
    assert r.status_code == 200, r.text
    return r.json()


async def test_recon_sorts(client, auth_headers, seeded):
    h = auth_headers
    default = [i["material_code"] for i in (await _recon(client, h))["items"]]
    assert default == sorted(default)                                   # material code, unchanged
    rows = (await _recon(client, h, sort="ic_total", dir="desc"))["items"]
    totals = [D(i["ic_total"]) for i in rows]
    assert totals == sorted(totals, reverse=True)
    rows = (await _recon(client, h, sort="ic_total", dir="asc"))["items"]
    assert [D(i["ic_total"]) for i in rows] == sorted(D(i["ic_total"]) for i in rows)
    labels = {"ok": "OK", "explained": "Explained", "unexplained": "Unexplained"}
    st = [labels[i["status"]] for i in (await _recon(client, h, sort="status", dir="asc"))["items"]]
    assert st == sorted(st)
    # difference headers sort by |unexplained remainder| (PRD INV-056);
    # the preset (no dir) is largest first, and dir toggles it
    for key, field in (("ia_ic", "ia_ic"), ("ic_wms", "ic_wms"), ("gl_ia_qty", "gl_ia_qty"),
                       ("gl_ia", "gl_ia_amount")):
        def un(items):
            return [abs(D(i["unexplained"][field] or 0)) for i in items]
        preset = un((await _recon(client, h, sort=key))["items"])
        assert preset == sorted(preset, reverse=True)
        assert un((await _recon(client, h, sort=key, dir="desc"))["items"]) == preset
        asc = un((await _recon(client, h, sort=key, dir="asc"))["items"])
        assert asc == sorted(asc)
    r = await client.get("/api/v1/recon", params={**DAY, "sort": "diff_ic_wms"}, headers=h)
    assert r.status_code == 422
    full = [i["material_code"] for i in (await _recon(client, h, sort="ia_qty", dir="desc"))["items"]]
    p1 = (await _recon(client, h, sort="ia_qty", dir="desc", page=1, page_size=2))["items"]
    p2 = (await _recon(client, h, sort="ia_qty", dir="desc", page=2, page_size=2))["items"]
    assert [i["material_code"] for i in p1 + p2] == full[:4]
    r = await client.get("/api/v1/recon", params={**DAY, "sort": "categories"}, headers=h)
    assert r.status_code == 422


async def test_recon_gl_ia_is_quantity_without_cost_view(client, non_admin_token, monkeypatch, seeded):
    h = {"Authorization": f"Bearer {non_admin_token}"}
    _grant(monkeypatch, "inventory.view")
    items = (await _recon(client, h, sort="gl_ia", dir="desc"))["items"]
    q = [abs(D(i["unexplained"]["gl_ia_qty"])) for i in items]
    assert q == sorted(q, reverse=True)


async def test_recon_amount_sort_ignored_without_cost_view(client, non_admin_token, monkeypatch, seeded):
    h = {"Authorization": f"Bearer {non_admin_token}"}
    _grant(monkeypatch, "inventory.view")
    rows = [i["material_code"] for i in (await _recon(client, h, sort="ia_amount", dir="desc"))["items"]]
    assert rows == sorted(rows)


# ── /admin/nc-sync/tieout-issues ──────────────────────────────────────────

TIE = "/api/v1/admin/nc-sync/tieout-issues"


@pytest.fixture
async def issues(db_session):
    from app.models import InvTieoutIssue

    def row(check, key, label, mirror, nc, measure="qty"):
        return {"check_name": check, "key": key, "label": label, "measure": measure,
                "mirror_value": D(mirror) if mirror is not None else None,
                "nc_value": D(nc) if nc is not None else None}

    await db_session.execute(insert(InvTieoutIssue), [
        row("ic_flow_vs_onhand", "k1", "beta", 10, 4),          # NC stock…  diff 6
        row("ia_ledger_vs_monthnab", "k2", "Alpha", 1, 2),      # NC cost…   diff -1
        row("gl_lines_vs_balance", "k3", None, None, 5),        # NC GL…     diff empty, label → key
        row("ia_ledger_vs_monthnab", "k4", "gamma", 7, 7, measure="amount"),
        row("wms_moves_vs_inv_lot", "w1", "wms", 3, 1),
    ])
    await db_session.commit()


async def _keys(client, h, **params):
    r = await client.get(TIE, params=params, headers=h)
    assert r.status_code == 200, r.text
    return r.json()


async def test_tieout_paging_sort_and_source(client, auth_headers, issues):
    h = auth_headers
    nc = await _keys(client, h, source="nc", page=1, page_size=50)
    assert nc["total"] == 4 and {i["check"] for i in nc["items"]} != {"wms_moves_vs_inv_lot"}
    assert "wms_moves_vs_inv_lot" not in nc["by_check"]
    wms = await _keys(client, h, source="wms", page=1, page_size=50)
    assert [i["key"] for i in wms["items"]] == ["w1"] and wms["total"] == 1
    # default order unchanged: check name, key
    assert [i["key"] for i in nc["items"]] == ["k3", "k2", "k4", "k1"]
    # check sorts by the label the page shows: NC cost < NC GL < NC stock
    by_check = await _keys(client, h, source="nc", page=1, page_size=50, sort="check", dir="asc")
    assert [i["check"] for i in by_check["items"]] == [
        "ia_ledger_vs_monthnab", "ia_ledger_vs_monthnab", "gl_lines_vs_balance", "ic_flow_vs_onhand"]
    lab = await _keys(client, h, source="nc", page=1, page_size=50, sort="label", dir="asc")
    assert [i["key"] for i in lab["items"]] == ["k2", "k1", "k4", "k3"]   # alpha, beta, gamma, k3
    d_asc = await _keys(client, h, source="nc", page=1, page_size=50, sort="diff", dir="asc")
    d_desc = await _keys(client, h, source="nc", page=1, page_size=50, sort="diff", dir="desc")
    assert [i["key"] for i in d_asc["items"]] == ["k2", "k4", "k1", "k3"]   # empty diff last
    assert [i["key"] for i in d_desc["items"]] == ["k1", "k4", "k2", "k3"]  # … both ways
    p1 = await _keys(client, h, source="nc", page=1, page_size=2, sort="diff", dir="desc")
    p2 = await _keys(client, h, source="nc", page=2, page_size=2, sort="diff", dir="desc")
    assert [i["key"] for i in p1["items"] + p2["items"]] == ["k1", "k4", "k2", "k3"]
    assert p1["total"] == p2["total"] == 4
    r = await client.get(TIE, params={"sort": "check_name"}, headers=h)
    assert r.status_code == 422
    # legacy callers (no page_size): first `limit` rows, every source
    legacy = await _keys(client, h, limit=1000)
    assert len(legacy["items"]) == 5
