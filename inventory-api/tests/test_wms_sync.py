"""WMS transaction history: leg rules, location moves, tie-out vs INV_LOT,
and the NC/WMS syncs not erasing each other's findings."""
from datetime import datetime
from decimal import Decimal

from sqlalchemy import func, select

from app.models import InvSyncState, InvTieoutIssue, WmsLot, WmsMove
from app.services.nc_sync import scheduler
from app.services.wms_sync.service import run_wms_sync, txn_to_moves

D = Decimal


def _txn(tid, ttype, fs=None, fl=None, fq=None, ts=None, tl=None, tq=None, **kw):
    return {"transactionid": tid, "transactiontype": ttype, "fmsku": fs, "fmlotnum": fl,
            "fmqty_each": fq, "tosku": ts, "tolotnum": tl, "toqty_each": tq,
            "transactiontime": datetime(2026, 9, 1, 10, 0), **kw}


def _legs(row):
    return [(m["sku"], m["lotnum"], m["qty"]) for m in txn_to_moves(row)]


def test_leg_rules():
    assert _legs(_txn("1", "IN", ts="CR1", tl="L1", tq=D(50), fq=D(0))) == [("CR1", "L1", D(50))]
    assert _legs(_txn("2", "SO", fs="CR1", fl="L1", fq=D(20), tq=D(20))) == [("CR1", "L1", D(-20))]
    # AD: FM is the quantity before, TO after → net +3.
    assert _legs(_txn("3", "AD", fs="CR1", fl="L1", fq=D(30), ts="CR1", tl="L1", tq=D(33))) == [
        ("CR1", "L1", D(-30)), ("CR1", "L1", D(33))]
    # TR across lots moves the TO quantity on BOTH legs (FMQTY is the whole source pallet).
    assert _legs(_txn("4", "TR", fs="CR1", fl="L1", fq=D(100), ts="CR1", tl="L2", tq=D(25))) == [
        ("CR1", "L1", D(-25)), ("CR1", "L2", D(25))]
    # Same SKU and lot: a location move, no legs.
    assert _legs(_txn("5", "TR", fs="CR1", fl="L1", fq=D(100), ts="CR1", tl="L1", tq=D(25))) == []


def _wms():
    return {
        "tables": {
            "txn": [
                _txn("T1", "IN", ts="CR1", tl="L1", tq=D(50), fq=D(0), doctype="ASN", docno="CASN1"),
                _txn("T2", "SO", fs="CR1", fl="L1", fq=D(20), doctype="SO", docno="CASO1"),
                _txn("T3", "TR", fs="CR1", fl="L1", fq=D(30), ts="CR1", tl="L1", tq=D(30)),
                _txn("T4", "TR", fs="CR1", fl="L1", fq=D(30), ts="CR1", tl="L2", tq=D(5)),
            ],
            "lot": [
                {"sku": "CR1", "lotnum": "L1", "lotatt04": "QC-1", "lotatt05": "SUP 1",
                 "lotatt01": "2026-05-01", "lotatt02": "2027-05-01"},
                {"sku": "CR1", "lotnum": "L2", "lotatt04": None, "lotatt05": "SUP-2"},
            ],
            "doc": [{"doc_kind": "ASN", "docno": "CASN1", "doc_type": "PR", "ref1": "PO-001"}],
            "code": [{"codetype": "TRN_TYP", "codeid": "IN", "languageid": "en", "descr": "Receiving"}],
        },
        "filtered": {"txn_cancelled": D("7")},
        "reference": {"inv_lot": [{"sku": "CR1", "lotnum": "L1", "qty": D(25)},
                                  {"sku": "CR1", "lotnum": "L2", "qty": D(5)}]},
    }


async def test_wms_sync_ties_out(db_session):
    r = await run_wms_sync(db_session, extract=_wms)
    assert r["tieout_ok"] is True
    assert r["rows"]["move"] == 4          # IN, SO, TR×2 legs; the location move dropped
    assert r["filtered"]["tr_location_only"] == 1 and r["filtered"]["txn_cancelled"] == 7
    lot = await db_session.get(WmsLot, ("CR1", "L1"))
    assert lot.qc_batch == "QC-1" and lot.supplier_batch == "SUP 1" and str(lot.expires) == "2027-05-01"
    total = (await db_session.execute(select(func.sum(WmsMove.qty)))).scalar_one()
    assert total == D(30)


async def test_wms_tieout_names_the_lot(db_session):
    w = _wms()
    w["reference"]["inv_lot"][0]["qty"] = D(24)
    r = await run_wms_sync(db_session, extract=lambda: w)
    assert r["tieout_ok"] is False
    [i] = (await db_session.execute(select(InvTieoutIssue))).scalars().all()
    assert (i.check_name, i.label, i.mirror_value, i.nc_value) == (
        "wms_moves_vs_inv_lot", "CR1 · lot L1", D(25), D(24))


async def test_nc_sync_keeps_wms_findings(db_session):
    from tests.test_nc_sync_service import _nc
    from app.services.nc_sync.service import run_nc_sync
    w = _wms()
    w["reference"]["inv_lot"][0]["qty"] = D(24)
    await run_wms_sync(db_session, extract=lambda: w)
    await run_nc_sync(db_session, extract=_nc)
    checks = (await db_session.execute(select(InvTieoutIssue.check_name))).scalars().all()
    assert checks == ["wms_moves_vs_inv_lot"]


async def test_scheduler_runs_each_source_independently(db_session, monkeypatch):
    calls = []

    async def ok(db):
        calls.append("nc")

    async def boom(db):
        calls.append("wms")
        raise RuntimeError("WMS down")

    monkeypatch.setattr(scheduler, "SOURCES", (("nc", lambda: True, ok), ("wms", lambda: True, boom)))
    out = await scheduler.run_tick_with_session(db_session)
    assert out == {"nc": "synced", "wms": "failed"} and calls == ["nc", "wms"]


async def test_scheduler_reports_unconfigured(db_session, monkeypatch):
    monkeypatch.setattr(scheduler, "SOURCES", (("nc", lambda: False, None), ("wms", lambda: False, None)))
    assert await scheduler.run_tick_with_session(db_session) == {"nc": "not_configured", "wms": "not_configured"}


async def test_wms_status_endpoint(client, auth_headers, db_session):
    await run_wms_sync(db_session, extract=_wms)
    r = await client.get("/api/v1/admin/wms-sync/status", headers=auth_headers)
    assert r.status_code == 200 and r.json()["status"] == "success"
    assert (await db_session.get(InvSyncState, "wms")).row_counts["move"] == 4
