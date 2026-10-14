"""NC Inventory as of any date: time zones, date-only rows, IA by accounting
period, GL posting toggle, and amounts withheld without inventory.cost.view.

Fixture: one material (CR0025) in SC11 (WMS) and SC16 (not costed), with
flows and ledger rows placed right at the edges of 2026-08-31 in Toronto.
NC time zone is the default, Asia/Shanghai: Toronto (EDT, UTC−4) end of
2026-08-31 = 2026-09-01 11:59:59 in NC's clock.
"""
from datetime import date, datetime
from decimal import Decimal

import pytest
from sqlalchemy import insert

from app.models import (
    NcGlLine, NcIaLedger, NcIaPeriod, NcIcFlow, NcIcOnhand, NcMaterial, NcWarehouse,
)
from app.services import asof

D = Decimal
URL = "/api/v1/nc"


def test_cutoff_converts_toronto_end_of_day_to_nc_clock():
    a = asof.make_asof(date(2026, 8, 31), "Asia/Shanghai")
    assert a.nc_cutoff == datetime(2026, 9, 1, 11, 59, 59)       # EDT: +12h
    assert a.month_end and a.period == "2026-08"
    w = asof.make_asof(date(2026, 1, 15), "Asia/Shanghai")
    assert w.nc_cutoff == datetime(2026, 1, 16, 12, 59, 59)      # EST: +13h
    assert not w.month_end
    t = asof.make_asof(date(2026, 8, 31), "America/Toronto")
    assert t.nc_cutoff == datetime(2026, 8, 31, 23, 59, 59)


@pytest.fixture
async def seeded(db_session):
    await db_session.execute(insert(NcWarehouse), [
        {"pk_stordoc": "W11", "code": "SC11", "name": "Raw", "iscalculatedinvcost": True},
        {"pk_stordoc": "W16", "code": "SC16", "name": "Old", "iscalculatedinvcost": False},
    ])
    await db_session.execute(insert(NcMaterial), [
        {"pk_material": "M1", "code": "CR0025", "name": "Lactose", "uom_code": "KGM", "class_code": "0102"},
    ])

    def flow(pk, wh, qty, t, batch="B1"):
        return {"pk_flow": pk, "cwarehouseid": wh, "cmaterialoid": "M1", "vbatchcode": batch,
                "ninnum": D(qty) if qty > 0 else None, "noutnum": D(-qty) if qty < 0 else None,
                "biz_time": t, "dbizdate": t.strftime("%Y-%m-%d %H:%M:%S") if t else None}

    await db_session.execute(insert(NcIcFlow), [
        flow("F1", "W11", 100, datetime(2026, 8, 1, 9, 0)),
        # 2026-09-01 10:00 China = 2026-08-31 22:00 Toronto → IN on 08-31.
        flow("F2", "W11", -10, datetime(2026, 9, 1, 10, 0)),
        # 2026-09-01 13:00 China = 2026-09-01 01:00 Toronto → NOT on 08-31.
        flow("F3", "W11", -5, datetime(2026, 9, 1, 13, 0)),
        # Date-only 2026-09-01: a September document, NOT "China midnight =
        # Aug 31 noon in Toronto".
        flow("F4", "W11", -7, datetime(2026, 9, 1, 0, 0)),
        # Date-only 2026-08-31 → in.
        flow("F5", "W16", 20, datetime(2026, 8, 31, 0, 0), batch="B9"),
    ])
    await db_session.execute(insert(NcIcOnhand), [
        {"pk_onhanddim": "H1", "cwarehouseid": "W11", "cmaterialoid": "M1", "vbatchcode": "B1",
         "nonhandnum": D("78"), "nlocknum": D("8")},
        {"pk_onhanddim": "H2", "cwarehouseid": "W16", "cmaterialoid": "M1", "vbatchcode": "B9",
         "nonhandnum": D("20")},
        # Residue: zero on hand, lingering lock — counted, not listed.
        {"pk_onhanddim": "H3", "cwarehouseid": "W11", "cmaterialoid": "M1", "vbatchcode": "OLD",
         "nonhandnum": D("0"), "nlocknum": D("5")},
    ])
    await db_session.execute(insert(NcIaPeriod), [{"caccountid": "P", "caccountperiod": "2026-07"}])

    def ia(pk, period, flag, qty, amt, t):
        return {"cdetailledgerid": pk, "cinventoryid": "M1", "caccountperiod": period,
                "fdispatchflag": flag, "nnum": D(qty), "nmny": D(amt) if amt is not None else None,
                "biz_time": t, "cstordocid": "W11", "vbatchcode": "B1"}

    await db_session.execute(insert(NcIaLedger), [
        ia("L1", "2026-07", 0, 100, 200, datetime(2026, 7, 10)),
        ia("L2", "2026-08", 1, 10, 20, datetime(2026, 8, 15)),
        # Back-dated into August but business date in September: by
        # ACCOUNTING PERIOD it belongs to August's month end.
        ia("L3", "2026-08", 1, 4, 8, datetime(2026, 9, 3)),
        ia("L4", "2026-09", 1, 6, None, datetime(2026, 9, 5)),   # uncosted out
    ])

    def gl(pk, period, acct, amt, qty, posted, prepared, mat="M1"):
        return {"pk_detail": pk, "pk_voucher": "V" + pk, "year": "2026", "period": period,
                "account_code": acct, "material_pk": mat, "debit_amount": D(amt), "debit_qty": D(qty),
                "tallydate": "2026-09-05" if posted else None, "pk_system": "IA",
                "prepared_time": prepared}

    await db_session.execute(insert(NcGlLine), [
        gl("G0", "00", "140302", 100, 50, True, datetime(2026, 1, 1)),
        gl("G1", "08", "140302", 40, 10, True, datetime(2026, 8, 10, 15)),
        gl("G2", "08", "140302", 7, 0, False, datetime(2026, 8, 30, 15)),       # unposted
        gl("G3", "08", "1471", -30, 0, True, datetime(2026, 8, 31, 15), mat=None),
        gl("G4", "08", "140306", 9, 0, True, datetime(2026, 8, 31, 15), mat=None),
    ])
    await db_session.commit()


async def test_ic_stock_historical_respects_cutoff_and_date_only(client, auth_headers, seeded):
    r = await client.get(f"{URL}/stock", params={"date": "2026-08-31"}, headers=auth_headers)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["is_today"] is False and body["state_available"] is False
    rows = {(i["warehouse_code"], i["batch"]): i for i in body["items"]}
    assert rows[("SC11", "B1")]["qty"] == "90"      # 100 − 10 (F3, F4 are September)
    assert rows[("SC16", "B9")]["qty"] == "20"
    assert rows[("SC11", "B1")]["locked"] is None   # no state/lock for past dates
    assert rows[("SC16", "B9")]["costed"] is False


async def test_ic_stock_material_group_splits_by_warehouse_class(client, auth_headers, seeded):
    r = await client.get(f"{URL}/stock", params={"date": "2026-08-31", "group": "material"},
                         headers=auth_headers)
    [m] = r.json()["items"]
    # SC11 and SC16 are both seeded as WMS warehouses.
    assert m["qty"] == "110" and m["qty_wms"] == "110" and m["qty_external"] is None


async def test_ic_stock_toronto_zone_switch_moves_the_cutoff(client, auth_headers, seeded):
    await client.put("/api/v1/settings/params/nc_time_zone", headers=auth_headers,
                     json={"value": "America/Toronto"})
    r = await client.get(f"{URL}/stock", params={"date": "2026-08-31"}, headers=auth_headers)
    rows = {(i["warehouse_code"], i["batch"]): i for i in r.json()["items"]}
    # Read as Toronto time, F2 (09-01 10:00) is September too.
    assert rows[("SC11", "B1")]["qty"] == "100"


async def test_ic_stock_today_uses_onhand_with_lock_and_available(client, auth_headers, seeded):
    r = await client.get(f"{URL}/stock", headers=auth_headers)
    body = r.json()
    assert body["is_today"] is True and body["state_available"] is True
    rows = {(i["warehouse_code"], i["batch"]): i for i in body["items"]}
    assert rows[("SC11", "B1")]["qty"] == "78"
    assert rows[("SC11", "B1")]["available"] == "70"
    assert ("SC11", "OLD") not in rows and body["lock_residue_rows"] == 1


async def test_future_date_rejected(client, auth_headers, seeded):
    r = await client.get(f"{URL}/stock", params={"date": "2999-01-01"}, headers=auth_headers)
    assert r.status_code == 422


async def test_ia_cost_by_accounting_period(client, auth_headers, seeded):
    # Month end: period ≤ 2026-08, including L3 dated in September.
    r = (await client.get(f"{URL}/cost", params={"date": "2026-08-31"}, headers=auth_headers)).json()
    [m] = r["items"]
    assert (m["qty"], m["amount"], m["unit_price"]) == ("86", "172", "2")
    assert r["ia"]["status"] == "open_costed"      # last closed is 2026-07
    # Mid-month: July whole + August rows dated ≤ 08-20 (L2 only).
    r = (await client.get(f"{URL}/cost", params={"date": "2026-08-20"}, headers=auth_headers)).json()
    assert r["items"][0]["qty"] == "90"
    # September: an outbound without cost is flagged.
    r = (await client.get(f"{URL}/cost", params={"date": "2026-09-30"}, headers=auth_headers)).json()
    assert r["ia"] == {"last_closed_period": "2026-07", "status": "open_uncosted", "uncosted_rows": 1}
    assert r["items"][0]["uncosted_rows"] == 1
    # July is closed.
    r = (await client.get(f"{URL}/cost", params={"date": "2026-07-31"}, headers=auth_headers)).json()
    assert r["ia"]["status"] == "closed"


async def test_gl_ledger_posting_toggle_and_impairment_line(client, auth_headers, seeded):
    r = (await client.get(f"{URL}/ledger", params={"date": "2026-08-31"}, headers=auth_headers)).json()
    items = {(i["account_code"], i["material_code"]): i for i in r["items"]}
    assert items[("140302", "CR0025")]["amount"] == "147"           # 100 + 40 + 7 unposted
    assert items[("140302", "CR0025")]["unposted_lines"] == 1
    assert items[("140306", None)]["material_name"] == "(no material)"
    assert [i["account_code"] for i in r["impairment"]] == ["1471"]
    assert all(i["account_code"] != "1471" for i in r["items"])

    r = (await client.get(f"{URL}/ledger", params={"date": "2026-08-31", "include_unposted": "false"},
                          headers=auth_headers)).json()
    items = {(i["account_code"], i["material_code"]): i for i in r["items"]}
    assert items[("140302", "CR0025")]["amount"] == "140"

    lines = (await client.get(f"{URL}/ledger/lines", params={
        "date": "2026-08-31", "account_code": "140306"}, headers=auth_headers)).json()
    assert lines["total"] == 1 and lines["items"][0]["amount"] == "9"
    # A material missing from the material mirror is still drillable by pk.
    by_pk = (await client.get(f"{URL}/ledger/lines", params={
        "date": "2026-08-31", "account_code": "140302", "material_pk": "M1"}, headers=auth_headers)).json()
    assert by_pk["total"] == 3


def _grant(monkeypatch, *keys):
    async def codes(db, user_id, base_role):
        return {"tester"}

    async def matrix(db):
        return {"tester": set(keys)}

    monkeypatch.setattr("uniops_authz.core.user_role_codes", codes)
    monkeypatch.setattr("uniops_authz.core._effective_matrix", matrix)


@pytest.mark.parametrize("path,params", [
    ("/cost", {"date": "2026-08-31"}),
    ("/ledger", {"date": "2026-08-31"}),
    ("/cost/CR0025/lines", {"date": "2026-08-31"}),
    ("/ledger/lines", {"date": "2026-08-31", "account_code": "140302", "material_code": "CR0025"}),
])
async def test_amounts_withheld_without_cost_view(client, non_admin_token, monkeypatch, seeded,
                                                   path, params):
    h = {"Authorization": f"Bearer {non_admin_token}"}
    _grant(monkeypatch, "inventory.view")
    body = (await client.get(URL + path, params=params, headers=h)).json()
    assert body["amounts_visible"] is False
    assert body["items"] and all(i["amount"] is None for i in body["items"])
    assert all(i.get("unit_price") is None for i in body["items"])
    assert "Lactose" in str(body) or "CR0025" in str(body)  # quantities still served

    _grant(monkeypatch, "inventory.view", "inventory.cost.view")
    body = (await client.get(URL + path, params=params, headers=h)).json()
    assert body["amounts_visible"] is True
    assert any(i["amount"] is not None for i in body["items"])


async def test_meta(client, auth_headers, seeded):
    m = (await client.get(f"{URL}/meta", params={"date": "2026-08-31"}, headers=auth_headers)).json()
    assert m["nc_time_zone"] == "Asia/Shanghai"
    assert m["nc_cutoff"] == "2026-09-01 11:59:59"
    assert m["month_end"] is True and m["amounts_visible"] is True
    assert m["undated_ic_rows_with_qty"] == 0
