"""Tests for the NC purchase idempotent writer + sync orchestration (Task 4).

Schema-smoke test (`test_provenance_columns_exist`) is metadata-only. The rest
run against the real epms_test schema over psycopg2 (fixtures in conftest.py):
writer.upsert idempotency + consumed-doc guard, and service single-flight /
end-to-end / full-reload orchestration.
"""
import uuid
from datetime import date
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
            "received_qty": Decimal("10"),
            # Always present on a transformed line -- the writer indexes it
            # strictly on purpose, so a transform that ever stopped setting it
            # fails loudly instead of writing NULL on every row.
            "planned_arrival_date": date(2026, 5, 5),
            "sort_order": 1,
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


def _attach_invoice_with_status(cur, nc_pk, status):
    """Attach an invoice in a downstream payment-flow status (e.g. 'paid',
    'partially_paid') with a gr_id set — as it would look once payment executed.
    The old matched-only guard (status='matched' AND gr_id) would treat these as
    NOT consumed and let an incremental upsert overwrite a PO a payment was built
    on; the broadened ANY-invoice guard must freeze them."""
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
        " %s, '[]'::jsonb, %s, %s, %s)",
        (uuid.uuid4(), f"INV-{status}-{nc_pk}", f"VINV-{status}", vid, vname,
         Decimal("100"), Decimal("100"), status, uid, po_id, gr_id))


def _attach_unmatched_invoice(cur, nc_pk):
    """An invoice that references the NC PO but is NOT consumed (unmatched, no
    gr_id) — invoices.po_id is ON DELETE RESTRICT, so a full reload must still
    preserve this PO or the DELETE would FK-fail."""
    cur.execute("select id, vendor_id, vendor_name from purchase_orders "
                "where nc_source_pk=%s and source='nc'", (nc_pk,))
    po_id, vid, vname = cur.fetchone()
    cur.execute("select id from users where email=%s", ("nc-sync@epms.local",))
    uid = cur.fetchone()[0]
    cur.execute(
        "insert into invoices (id, internal_ref, vendor_invoice_number, vendor_id, "
        " vendor_name, amount, tax_amount, total_amount, currency, invoice_date, "
        " due_date, status, line_items, uploaded_by, po_id) "
        "values (%s,%s,%s,%s,%s,%s,0,%s,'CAD', current_date, current_date, "
        " 'unmatched', '[]'::jsonb, %s, %s)",
        (uuid.uuid4(), f"INVU-{nc_pk}", "VINV-2", vid, vname, Decimal("50"),
         Decimal("50"), uid, po_id))


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


def test_planned_arrival_date_lands_on_insert_and_is_refreshed_on_update(
    pg_cur, seeded_vendor, system_user_id,
):
    """The whole point of the column is the lines that ALREADY exist: every open
    raw-material PO line was synced before this column did, so an insert-only
    write would leave all 87 of them null forever. The second upsert here takes
    the UPDATE branch, with a different date, and must move the stored value.
    """
    from app.services.nc_purchase_sync import writer

    payload = _mini_payload(seeded_vendor)
    writer.upsert(pg_cur, payload, system_user_id)
    pg_cur.execute("select planned_arrival_date from po_line_items where nc_source_pk='OL1'")
    assert pg_cur.fetchone()[0] == date(2026, 5, 5), "insert path did not carry the date"

    moved = _mini_payload(seeded_vendor)
    moved["order_lines"][0]["planned_arrival_date"] = date(2026, 9, 30)
    writer.upsert(pg_cur, moved, system_user_id)
    pg_cur.execute("select planned_arrival_date from po_line_items where nc_source_pk='OL1'")
    assert pg_cur.fetchone()[0] == date(2026, 9, 30), "update path did not refresh the date"


def test_planned_arrival_date_of_none_is_stored_as_null(pg_cur, seeded_vendor, system_user_id):
    """A UniOps-native line, or an NC line the ERP left blank, must store NULL
    rather than anything that reads as a real arrival date."""
    from app.services.nc_purchase_sync import writer

    payload = _mini_payload(seeded_vendor)
    payload["order_lines"][0]["planned_arrival_date"] = None
    writer.upsert(pg_cur, payload, system_user_id)
    pg_cur.execute("select planned_arrival_date from po_line_items where nc_source_pk='OL1'")
    assert pg_cur.fetchone()[0] is None


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
    # Second run tries to rewrite BOTH the header total AND the line qty/price —
    # a consumed PO must leave header and children untouched.
    payload = _mini_payload(seeded_vendor)
    payload["orders"][0]["total"] = Decimal("999")
    payload["order_lines"][0]["qty"] = Decimal("555")
    payload["order_lines"][0]["unit_price"] = Decimal("777")
    counts = writer.upsert(pg_cur, payload, system_user_id)
    pg_cur.execute("select total from purchase_orders where nc_source_pk='O1'")
    assert pg_cur.fetchone()[0] != Decimal("999")     # header not overwritten
    pg_cur.execute("select qty, unit_price from po_line_items where nc_source_pk='OL1'")
    qty, unit_price = pg_cur.fetchone()
    assert qty == Decimal("10.0000") and unit_price == Decimal("10.00")   # children untouched
    assert counts["skipped_consumed"] >= 1


@pytest.mark.parametrize("status", ["paid", "partially_paid"])
def test_upsert_skips_po_in_payment_flow(pg_cur, seeded_vendor, system_user_id, status):
    """Once payment executes, the invoice moves matched -> partially_paid -> paid.
    A PAID/PARTIALLY-PAID NC PO must stay frozen against re-sync overwrite — the
    old matched-only guard let an incremental upsert rewrite its header/lines,
    corrupting data a payment was built on."""
    from app.services.nc_purchase_sync import writer
    writer.upsert(pg_cur, _mini_payload(seeded_vendor), system_user_id)
    _attach_invoice_with_status(pg_cur, "O1", status)
    payload = _mini_payload(seeded_vendor)
    payload["orders"][0]["total"] = Decimal("999")
    payload["order_lines"][0]["qty"] = Decimal("555")
    payload["order_lines"][0]["unit_price"] = Decimal("777")
    counts = writer.upsert(pg_cur, payload, system_user_id)
    pg_cur.execute("select total from purchase_orders where nc_source_pk='O1'")
    assert pg_cur.fetchone()[0] != Decimal("999")     # header not overwritten
    pg_cur.execute("select qty, unit_price from po_line_items where nc_source_pk='OL1'")
    qty, unit_price = pg_cur.fetchone()
    assert qty == Decimal("10.0000") and unit_price == Decimal("10.00")   # children untouched
    assert counts["skipped_consumed"] >= 1


def test_upsert_heartbeat_called_during_write(pg_cur, seeded_vendor, system_user_id):
    """FIX 3: upsert invokes the heartbeat callable so a long full load can refresh
    its run row and dodge the stale sweep. Threshold is every ~500 rows, so force a
    payload past it and assert the callback fired."""
    from app.services.nc_purchase_sync import writer
    payload = _mini_payload(seeded_vendor)
    base_line = payload["order_lines"][0]
    payload["order_lines"] = [
        {**base_line, "nc_source_pk": f"OL{i}", "sort_order": i} for i in range(600)
    ]
    beats = []
    writer.upsert(pg_cur, payload, system_user_id, heartbeat=lambda: beats.append(1))
    assert len(beats) >= 1


def test_upsert_persists_realistic_composite_gr_key(pg_cur, seeded_vendor, system_user_id):
    """GR nc_source_pk is transform's "{arr_pk}:{ord_pk}" — ~41 chars on real
    20-char NC pks. Must persist without truncation (fails against String(20))."""
    from app.services.nc_purchase_sync import writer
    long_pk = "1001A1100000003CIP7S:1001A1100000003CGZLN"   # 41 chars, two 20-char pks
    assert len(long_pk) == 41
    payload = _mini_payload(seeded_vendor)
    payload["grs"][0]["nc_source_pk"] = long_pk
    payload["gr_lines"][0]["gr_nc_pk"] = long_pk
    writer.upsert(pg_cur, payload, system_user_id)
    pg_cur.execute("select nc_source_pk from goods_receipts "
                   "where source='nc' and nc_source_pk=%s", (long_pk,))
    row = pg_cur.fetchone()
    assert row is not None and row[0] == long_pk     # no truncation


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
                        "vmemo": "memo", "vtrantypecode": "21-Cxx-CRM01"}],
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


def _raw_fetch_unknown_supplier():
    """Same shape as _raw_fetch, but the order's ERP supplier has no vendor —
    the exact condition that silently dropped PO-029-2609-01 in production."""
    def fetch(cutover, watermark):
        raw = _raw_fetch()(cutover, watermark)
        raw["suppliers"] = {"SUP1": "9999999"}
        raw["supplier_names"] = {"9999999": "Independent Chemical"}
        # A change time on the order, so the run has something to hold the
        # watermark at (reader.changed_at reads these columns).
        raw["orders"][0]["taudittime"] = "2026-08-01 08:00:00"
        return raw
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
        cur.execute("delete from tasks where document_type='nc_sync'")
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


def test_run_worker_puts_an_unimportable_order_in_the_admin_inbox(committed_nc_env):
    """End-to-end wiring: a run that skips an order for a missing vendor leaves
    an Admin task naming it. Nothing else in the product says the order was
    dropped — the run row only carries a count."""
    from app.services.nc_purchase_sync import error_tasks, service
    dsn, vid, vname, uid, con = committed_nc_env
    service.start_run("incremental", uid, fetch=_raw_fetch_unknown_supplier(),
                      pg_dsn=dsn, run_worker=True)
    cur = con.cursor()
    cur.execute("select count(*) from purchase_orders where nc_source_pk='O1'")
    assert cur.fetchone()[0] == 0, "precondition: the order was NOT imported"
    cur.execute("select type, assigned_role, title from tasks "
                "where document_type=%s and document_number='PO-NC-O1' "
                "and is_completed is false", (error_tasks.DOC_TYPE,))
    rows = cur.fetchall()
    assert len(rows) == 1
    assert rows[0][0] == error_tasks.VENDOR_TASK and rows[0][1] == "system_admin"
    assert "9999999" in rows[0][2]
    # And the run does NOT step over the order it dropped: the watermark stays
    # at that order's change time instead of advancing to the payload's max, so
    # the next run reads it again once the vendor exists.
    cur.execute("select watermark_to from nc_purchase_sync_runs "
                "where status='success' order by started_at desc limit 1")
    assert cur.fetchone()[0] == "2026-08-01 08:00:00"


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


def test_full_reload_preserves_invoice_linked_po(committed_nc_env):
    """A NC PO referenced by a NON-consumed invoice (unmatched, no gr) must also
    survive a full reload — invoices.po_id is ON DELETE RESTRICT, so deleting it
    would abort the run. The run succeeds and the PO is kept."""
    from app.services.nc_purchase_sync import service
    dsn, vid, vname, uid, con = committed_nc_env
    service.start_run("incremental", uid, fetch=_raw_fetch(), pg_dsn=dsn, run_worker=True)
    _attach_unmatched_invoice(con.cursor(), "O1")     # invoice-linked but NOT consumed

    rid = service.start_run("full", uid, fetch=_raw_fetch("2026-08-03 09:00:00"),
                            pg_dsn=dsn, run_worker=True)
    cur = con.cursor()
    cur.execute("select count(*) from purchase_orders where nc_source_pk='O1'")
    assert cur.fetchone()[0] == 1     # kept despite non-matched invoice; run didn't FK-fail
    cur.execute("select status, skipped_consumed from nc_purchase_sync_runs where id=%s", (rid,))
    status, skipped = cur.fetchone()
    assert status == "success"
    assert skipped >= 1


# ── buyer-edited data survives re-sync ───────────────────────────────────────

def test_upsert_preserves_buyer_details_and_manual_tax(pg_cur, seeded_vendor, system_user_id):
    """A PO whose buyer_edited_at is set keeps its hand-entered columns and its
    hand-set tax rate. The money is re-derived from NC's new subtotal so the
    header still satisfies subtotal + tax_amount == total."""
    from app.services.nc_purchase_sync import writer
    payload = _mini_payload(seeded_vendor)
    writer.upsert(pg_cur, payload, system_user_id)

    pg_cur.execute(
        "update purchase_orders set buyer_notes=%s, incoterms=%s, tax_rate=%s, "
        "buyer_edited_at=now() where nc_source_pk='O1'",
        ("Ship in one lot", "FOB Shanghai", Decimal("0.13")))
    pg_cur.execute(
        "update po_line_items set supplier_item_id=%s, sample=%s where nc_source_pk='OL1'",
        ("SKU-9", "500 g"))

    # NC re-sends the order with a bigger subtotal and its own zero tax.
    payload["orders"][0]["subtotal"] = Decimal("200.00")
    payload["orders"][0]["tax_rate"] = Decimal("0")
    payload["orders"][0]["tax_amount"] = Decimal("0")
    payload["orders"][0]["total"] = Decimal("200.00")
    writer.upsert(pg_cur, payload, system_user_id)

    pg_cur.execute(
        "select buyer_notes, incoterms, subtotal, tax_rate, tax_amount, total "
        "from purchase_orders where nc_source_pk='O1'")
    notes, inco, subtotal, rate, tax_amount, total = pg_cur.fetchone()
    assert notes == "Ship in one lot"
    assert inco == "FOB Shanghai"
    assert subtotal == Decimal("200.00")      # NC still owns the subtotal
    assert rate == Decimal("0.13")            # buyer's rate survives
    assert tax_amount == Decimal("26.00")     # re-derived off the new subtotal
    assert total == Decimal("226.00")
    assert subtotal + tax_amount == total

    pg_cur.execute(
        "select supplier_item_id, sample from po_line_items where nc_source_pk='OL1'")
    assert pg_cur.fetchone() == ("SKU-9", "500 g")


def test_upsert_takes_nc_tax_when_edit_did_not_touch_tax_rate(pg_cur, seeded_vendor, system_user_id):
    """A buyer edit that only fills in Incoterms/Supplier Item ID — never the tax
    rate — must NOT arm the tax-rate guard. crud.po.update_imported_details only
    stamps buyer_edited_at when "tax_rate" is among the changed fields, so this
    simulates that: buyer_notes/incoterms/supplier_item_id/sample change but
    buyer_edited_at is left NULL. The next NC upsert must still adopt NC's
    tax_rate/tax_amount/total verbatim, not silently freeze them."""
    from app.services.nc_purchase_sync import writer
    payload = _mini_payload(seeded_vendor)
    writer.upsert(pg_cur, payload, system_user_id)

    # A buyer edit that never touched tax_rate — buyer_edited_at stays NULL.
    pg_cur.execute(
        "update purchase_orders set buyer_notes=%s, incoterms=%s "
        "where nc_source_pk='O1'",
        ("Ship in one lot", "FOB Shanghai"))
    pg_cur.execute(
        "update po_line_items set supplier_item_id=%s, sample=%s where nc_source_pk='OL1'",
        ("SKU-9", "500 g"))
    pg_cur.execute(
        "select buyer_edited_at from purchase_orders where nc_source_pk='O1'")
    assert pg_cur.fetchone()[0] is None

    # NC re-sends the order with a new subtotal and its own non-zero tax.
    payload["orders"][0]["subtotal"] = Decimal("200.00")
    payload["orders"][0]["tax_rate"] = Decimal("0.05")
    payload["orders"][0]["tax_amount"] = Decimal("10.00")
    payload["orders"][0]["total"] = Decimal("210.00")
    writer.upsert(pg_cur, payload, system_user_id)

    pg_cur.execute(
        "select tax_rate, tax_amount, total from purchase_orders where nc_source_pk='O1'")
    assert pg_cur.fetchone() == (Decimal("0.05"), Decimal("10.00"), Decimal("210.00"))

    # And the buyer's earlier edits are still intact — untouched by this sync.
    pg_cur.execute(
        "select buyer_notes, incoterms from purchase_orders where nc_source_pk='O1'")
    assert pg_cur.fetchone() == ("Ship in one lot", "FOB Shanghai")
    pg_cur.execute(
        "select supplier_item_id, sample from po_line_items where nc_source_pk='OL1'")
    assert pg_cur.fetchone() == ("SKU-9", "500 g")


def test_upsert_takes_nc_tax_when_po_was_never_buyer_edited(pg_cur, seeded_vendor, system_user_id):
    """Guard against over-reach: with buyer_edited_at NULL the mirror must still
    take NC's tax verbatim, exactly as before this change."""
    from app.services.nc_purchase_sync import writer
    payload = _mini_payload(seeded_vendor)
    writer.upsert(pg_cur, payload, system_user_id)

    payload["orders"][0]["subtotal"] = Decimal("200.00")
    payload["orders"][0]["tax_rate"] = Decimal("0.05")
    payload["orders"][0]["tax_amount"] = Decimal("10.00")
    payload["orders"][0]["total"] = Decimal("210.00")
    writer.upsert(pg_cur, payload, system_user_id)

    pg_cur.execute(
        "select tax_rate, tax_amount, total from purchase_orders where nc_source_pk='O1'")
    assert pg_cur.fetchone() == (Decimal("0.05"), Decimal("10.00"), Decimal("210.00"))
