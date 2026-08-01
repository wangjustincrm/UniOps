"""Tests for the NC purchase idempotent writer + sync orchestration (Task 4).

Schema-smoke test (`test_provenance_columns_exist`) is metadata-only. The rest
run against the real epms_test schema over psycopg2 (fixtures in conftest.py):
writer.upsert idempotency + consumed-doc guard, and service single-flight /
end-to-end / full-reload orchestration.
"""
import uuid
from decimal import Decimal

import psycopg2
import pytest
from sqlalchemy import inspect  # noqa: F401 (kept: original smoke test import)

from app.db.base import Base


def test_provenance_columns_exist():
    po = Base.metadata.tables["purchase_orders"]
    assert "source" in po.c and "nc_source_pk" in po.c
    gr = Base.metadata.tables["goods_receipts"]
    assert "source" in gr.c and "nc_source_pk" in gr.c
    assert "nc_source_pk" in Base.metadata.tables["po_line_items"].c
    assert "nc_source_pk" in Base.metadata.tables["gr_line_items"].c
    assert "nc_purchase_sync_runs" in Base.metadata.tables


# ── transform-shaped helpers ─────────────────────────────────────────────────

def _mini_payload(seeded_vendor):
    """1 order (O1) + 1 line (OL1) + 1 GR (A1:O1) + 1 gr line (AL1). Mirrors the
    keys transform.transform() emits."""
    vid, vname = seeded_vendor
    return {
        "orders": [{
            "nc_source_pk": "O1", "number": "PO-NC-O1", "title": "PO-NC-O1",
            "type": 1, "status": "issued", "source": "nc", "currency": "CAD",
            "total": Decimal("100.00"), "subtotal": Decimal("0"),
            "tax_rate": Decimal("0"), "tax_amount": Decimal("0"),
            "vendor_id": vid, "vendor_name": vname, "pr_id": None,
            "place_order_method": "nc", "place_order_reference": "PO-NC-O1",
            "notes": "nc order",
        }],
        "order_lines": [{
            "nc_source_pk": "OL1", "po_nc_pk": "O1", "material_id": "MAT-1",
            "description": "Widget", "qty": Decimal("10"), "unit": "EA",
            "unit_price": Decimal("10.00"), "line_total": Decimal("100.00"),
            "received_qty": Decimal("10"), "sort_order": 1,
        }],
        "grs": [{
            "nc_source_pk": "A1:O1", "po_nc_pk": "O1", "number": "GR-NC-A1",
            "title": "GR-NC-A1", "gr_type": "physical", "procurement_type": 1,
            "status": "confirmed", "source": "nc",
        }],
        "gr_lines": [{
            "nc_source_pk": "AL1", "gr_nc_pk": "A1:O1", "po_line_nc_pk": "OL1",
            "material_id": "MAT-1", "description": "Widget",
            "qty_ordered": Decimal("0"), "qty_received": Decimal("10"),
            "unit_price": Decimal("10.00"), "line_total": Decimal("100.00"),
            "sort_order": 1,
        }],
        "skipped_no_vendor": [],
    }


def _attach_matched_invoice(cur, nc_pk):
    """Insert a matched invoice (status='matched', gr_id set) against the
    mirrored NC PO, making writer._po_consumed() return True."""
    cur.execute("select id, vendor_id, vendor_name from purchase_orders "
                "where nc_source_pk=%s and source='nc'", (nc_pk,))
    po_id, vid, vname = cur.fetchone()
    cur.execute("select id from goods_receipts where source='nc' limit 1")
    gr_id = cur.fetchone()[0]
    cur.execute("select id from users where email=%s", ("nc-sync@epms.local",))
    uid = cur.fetchone()[0]
    cur.execute(
        "insert into invoices (id, internal_ref, vendor_invoice_number, vendor_id, "
        " vendor_name, amount, tax_amount, total_amount, currency, invoice_date, "
        " due_date, status, line_items, uploaded_by, po_id, gr_id) "
        "values (%s,%s,%s,%s,%s,%s,0,%s,'CAD', current_date, current_date, "
        " 'matched', '[]'::jsonb, %s, %s, %s)",
        (uuid.uuid4(), f"INV-{nc_pk}", "VINV-1", vid, vname, Decimal("100"),
         Decimal("100"), uid, po_id, gr_id))


# ── writer.upsert ────────────────────────────────────────────────────────────

def test_upsert_inserts_po_and_gr(pg_cur, seeded_vendor, system_user_id):
    from app.services.nc_purchase_sync import writer
    counts = writer.upsert(pg_cur, _mini_payload(seeded_vendor), system_user_id)
    assert counts["pos_upserted"] == 1 and counts["grs_upserted"] == 1
    pg_cur.execute("select status, type, source from purchase_orders "
                   "where nc_source_pk='O1'")
    assert pg_cur.fetchone() == ("issued", 1, "nc")


def test_upsert_is_idempotent(pg_cur, seeded_vendor, system_user_id):
    from app.services.nc_purchase_sync import writer
    writer.upsert(pg_cur, _mini_payload(seeded_vendor), system_user_id)
    writer.upsert(pg_cur, _mini_payload(seeded_vendor), system_user_id)
    pg_cur.execute("select count(*) from purchase_orders where nc_source_pk='O1'")
    assert pg_cur.fetchone()[0] == 1
    pg_cur.execute("select count(*) from gr_line_items l join goods_receipts g "
                   "on g.id=l.gr_id where g.nc_source_pk='A1:O1'")
    assert pg_cur.fetchone()[0] == 1


def test_gr_line_resolves_po_line_id(pg_cur, seeded_vendor, system_user_id):
    from app.services.nc_purchase_sync import writer
    writer.upsert(pg_cur, _mini_payload(seeded_vendor), system_user_id)
    pg_cur.execute("select l.po_line_id from gr_line_items l "
                   "join goods_receipts g on g.id=l.gr_id where g.nc_source_pk='A1:O1'")
    assert pg_cur.fetchone()[0] is not None


def test_upsert_skips_consumed_po(pg_cur, seeded_vendor, system_user_id):
    from app.services.nc_purchase_sync import writer
    writer.upsert(pg_cur, _mini_payload(seeded_vendor), system_user_id)
    _attach_matched_invoice(pg_cur, "O1")
    payload = _mini_payload(seeded_vendor)
    payload["orders"][0]["total"] = Decimal("999")
    counts = writer.upsert(pg_cur, payload, system_user_id)
    pg_cur.execute("select total from purchase_orders where nc_source_pk='O1'")
    assert pg_cur.fetchone()[0] != Decimal("999")     # not overwritten
    assert counts["skipped_consumed"] >= 1


# ── service orchestration ────────────────────────────────────────────────────

def _empty_raw():
    return {"orders": [], "order_lines": [], "arrivals": [], "arrival_lines": [],
            "suppliers": {}, "materials": {}, "uoms": {}, "currencies": {},
            "max_modifiedtime": None}


def _raw_fetch(max_mt="2026-08-01 10:00:00"):
    """A reader.fetch_nc(cutover, watermark)-shaped fake yielding O1/OL1/A1/AL1
    for a supplier whose ERP code (0000415) matches seeded_vendor."""
    def fetch(cutover, watermark):
        return {
            "orders": [{"pk_order": "O1", "vbillcode": "PO-NC-O1", "pk_supplier": "SUP1",
                        "corigcurrencyid": "C1", "ntotalorigmny": Decimal("100"),
                        "vmemo": "memo"}],
            "order_lines": [{"pk_order_b": "OL1", "pk_order": "O1", "crowno": "1",
                             "pk_material": "M1", "vvendinventoryname": "Widget",
                             "castunitid": "U1", "nastnum": Decimal("10"),
                             "norigtaxprice": Decimal("10"), "norigtaxmny": Decimal("100")}],
            "arrivals": [{"pk_arriveorder": "A1", "vbillcode": "GR-NC-A1"}],
            "arrival_lines": [{"pk_arriveorder_b": "AL1", "pk_arriveorder": "A1",
                               "pk_order": "O1", "pk_order_b": "OL1", "crowno": "1",
                               "pk_material": "M1", "nastnum": Decimal("10"),
                               "norigtaxprice": Decimal("10"), "norigtaxmny": Decimal("100")}],
            "suppliers": {"SUP1": "0000415"},
            "materials": {"M1": ("MAT-1", "Widget")},
            "uoms": {"U1": "EA"}, "currencies": {"C1": "CAD"},
            "max_modifiedtime": max_mt,
        }
    return fetch


def test_start_run_rejects_concurrent(system_user_id, test_pg_dsn, clean_nc_sync_runs):
    from app.services.nc_purchase_sync import service
    rid = service.start_run("incremental", system_user_id,
                            fetch=lambda *a, **k: _empty_raw(),
                            pg_dsn=test_pg_dsn, run_worker=False)
    assert rid is not None
    with pytest.raises(service.SyncAlreadyRunning):
        service.start_run("incremental", system_user_id,
                          fetch=lambda *a, **k: _empty_raw(),
                          pg_dsn=test_pg_dsn, run_worker=False)


@pytest.fixture
def committed_nc_env(test_engine, test_pg_dsn):
    """Committed (not transactional) vendor + system user, so service worker
    connections — which open their own psycopg2 connection — can see them.
    Wipes all NC purchase data + this vendor + runs before and after."""
    dsn = test_pg_dsn
    con = psycopg2.connect(dsn); con.autocommit = True
    cur = con.cursor()

    def _wipe():
        cur.execute("delete from invoices where po_id in "
                    "(select id from purchase_orders where source='nc')")
        cur.execute("delete from goods_receipts where source='nc'")
        cur.execute("delete from purchase_orders where source='nc'")
        cur.execute("delete from business_partners where erp_id='0000415'")
        cur.execute("truncate nc_purchase_sync_runs")

    _wipe()
    vid = uuid.uuid4()
    cur.execute(
        "insert into business_partners "
        "(id, code, erp_id, name, category, contact_name, contact_email, "
        " payment_terms, currency, is_active, is_supplier, is_customer) "
        "values (%s,%s,%s,%s,%s,%s,%s,'net30','CAD',true,true,false)",
        (vid, "NCV-0000415", "0000415", "NC Vendor 415", "supplier",
         "NC Contact", "nc-vendor@example.com"))
    from app.services.nc_purchase_sync import writer
    uid = writer.ensure_system_user_sync(cur)
    yield dsn, vid, "NC Vendor 415", uid, con
    _wipe()
    con.close()


def test_run_worker_incremental_end_to_end(committed_nc_env):
    from app.services.nc_purchase_sync import service
    dsn, vid, vname, uid, con = committed_nc_env
    rid = service.start_run("incremental", uid, fetch=_raw_fetch(), pg_dsn=dsn,
                            run_worker=True)
    cur = con.cursor()
    cur.execute("select status, source from purchase_orders where nc_source_pk='O1'")
    assert cur.fetchone() == ("issued", "nc")
    cur.execute("select count(*) from goods_receipts where source='nc'")
    assert cur.fetchone()[0] == 1
    cur.execute("select status, watermark_to, pos_upserted, grs_upserted "
                "from nc_purchase_sync_runs where id=%s", (rid,))
    status, wm, pos, grs = cur.fetchone()
    assert status == "success"
    assert wm == "2026-08-01 10:00:00"
    assert pos == 1 and grs == 1


def test_full_reload_preserves_consumed_po(committed_nc_env):
    from app.services.nc_purchase_sync import service
    dsn, vid, vname, uid, con = committed_nc_env
    service.start_run("incremental", uid, fetch=_raw_fetch(), pg_dsn=dsn, run_worker=True)
    _attach_matched_invoice(con.cursor(), "O1")     # committed (autocommit conn)

    rid = service.start_run("full", uid, fetch=_raw_fetch("2026-08-02 09:00:00"),
                            pg_dsn=dsn, run_worker=True)
    cur = con.cursor()
    cur.execute("select count(*) from purchase_orders where nc_source_pk='O1'")
    assert cur.fetchone()[0] == 1     # consumed PO survived the full reload
    cur.execute("select status, skipped_consumed from nc_purchase_sync_runs where id=%s", (rid,))
    status, skipped = cur.fetchone()
    assert status == "success"
    assert skipped >= 1
