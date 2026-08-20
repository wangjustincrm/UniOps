"""Two NC purchase orders may legitimately carry the SAME document number.

``vbillcode`` is not unique in NC: production holds 1,682 approved orders under
1,645 distinct numbers. The duplicates are not data noise and not versions of
one order — they are genuinely different orders that people numbered the same:
different suppliers (PO-005-2402-01), different materials (PO-032-2403-01),
different trade types, and in one case five separate orders for five delivery
days (PO-022-2507-01). Every one of them is ``bislatest='Y'``, ``forderstatus=3``,
``dr=0`` — all in force.

``purchase_orders.number`` is UNIQUE, so the mirror cannot store them verbatim.
It used to resolve that by DROPPING the loser and all of its children, silently:
12 duplicate groups inside the cutover meant 15 NC orders and 16 arrivals never
reached UniOps, and the PO whose number won showed zero received forever.

Dropping a live order is the one outcome that is never acceptable — an order
nobody can see is an order nobody pays. So the number is disambiguated with a
suffix instead, and the order is mirrored.
"""
import uuid
from datetime import date
from decimal import Decimal

import psycopg2
import pytest

from app.services.nc_purchase_sync import writer

#: Its own ERP code, kept distinct from test_nc_purchase_writer's committed
#: environment (0000415) so the two committed fixtures cannot wipe each other's
#: rows in a shared run.
_ERP_ID = "0000806"


@pytest.fixture
def committed_dup_env(test_engine, test_pg_dsn):
    """Committed vendor + system user: the service worker opens its OWN psycopg2
    connection, so anything left uncommitted is invisible to it. Mirrors
    test_nc_purchase_writer.committed_nc_env, scoped to this module's vendor."""
    con = psycopg2.connect(test_pg_dsn)
    con.autocommit = True
    cur = con.cursor()

    #: The order pks this module's fake NC fetch produces. The wipe is scoped to
    #: them rather than to every source='nc' row: a fixture that clears another
    #: module's committed data makes whichever test runs next depend on the order
    #: pytest happened to choose.
    own_pks = ["D1", "D2"]

    def _wipe():
        cur.execute("delete from invoices where po_id in (select id from "
                    "purchase_orders where nc_source_pk = any(%s))", (own_pks,))
        cur.execute("delete from goods_receipts g using purchase_orders p "
                    "where p.id=g.po_id and p.nc_source_pk = any(%s)", (own_pks,))
        cur.execute("delete from purchase_orders where nc_source_pk = any(%s)", (own_pks,))
        cur.execute("delete from business_partners where erp_id=%s", (_ERP_ID,))
        # Truncated, not scoped: start_run picks its watermark off the most
        # recent run row, so a leftover run from another module would silently
        # turn this module's incremental into a no-op.
        cur.execute("truncate nc_purchase_sync_runs")

    _wipe()
    vid = uuid.uuid4()
    cur.execute(
        "insert into business_partners "
        "(id, code, erp_id, name, category, contact_name, contact_email, "
        " payment_terms, currency, is_active, is_supplier, is_customer) "
        "values (%s,%s,%s,%s,'supplier',%s,%s,'net30','CAD',true,true,false)",
        (vid, f"NCV-{_ERP_ID}", _ERP_ID, "NC Dup Vendor", "NC Contact",
         "nc-dup@example.com"))
    uid = writer.ensure_system_user_sync(cur)
    yield test_pg_dsn, uid, con
    _wipe()
    con.close()


def _order(nc_pk, number, vendor):
    vid, vname = vendor
    return {
        "nc_source_pk": nc_pk, "number": number, "title": number,
        "type": 1, "status": "issued", "source": "nc", "currency": "CAD",
        "total": Decimal("100.00"), "subtotal": Decimal("100.00"),
        "tax_rate": Decimal("0"), "tax_amount": Decimal("0"),
        "vendor_id": vid, "vendor_name": vname, "pr_id": None,
        "place_order_method": "nc", "place_order_reference": number,
        "notes": None, "created_at": None,
    }


def _line(nc_pk, po_nc_pk):
    return {
        "nc_source_pk": nc_pk, "po_nc_pk": po_nc_pk, "material_id": "MAT-1",
        "description": "Widget", "qty": Decimal("10"), "unit": "KGM",
        "unit_price": Decimal("10.00"), "line_total": Decimal("100.00"),
        "received_qty": Decimal("0"), "planned_arrival_date": date(2026, 5, 5),
        "sort_order": 1,
    }


def _gr(nc_pk, po_nc_pk, number):
    return {
        "nc_source_pk": nc_pk, "po_nc_pk": po_nc_pk, "number": number,
        "title": number, "gr_type": "physical", "procurement_type": 1,
        "status": "collected", "source": "nc", "received_at": None, "notes": None,
    }


def _payload(vendor, orders):
    """``orders`` = [(nc_pk, number)] — each gets one line and one GR."""
    return {
        "orders": [_order(pk, num, vendor) for pk, num in orders],
        "order_lines": [_line(f"{pk}-L1", pk) for pk, _ in orders],
        "grs": [_gr(f"A-{pk}:{pk}", pk, f"DH-{pk}") for pk, _ in orders],
        "gr_lines": [{
            "nc_source_pk": f"AL-{pk}", "gr_nc_pk": f"A-{pk}:{pk}",
            "po_line_nc_pk": f"{pk}-L1", "material_id": "MAT-1",
            "description": "Widget", "qty_ordered": Decimal("0"),
            "qty_received": Decimal("4"), "unit_price": Decimal("10.00"),
            "line_total": Decimal("40.00"), "sort_order": 1,
        } for pk, _ in orders],
        "skipped_no_vendor": [],
    }


def _numbers_by_pk(cur, pks) -> dict:
    """``nc_source_pk -> number`` for THIS test's orders only.

    Scoped rather than "every source='nc' row": the shared test database can
    carry NC rows another module committed — `test_nc_mirror_pipeline`'s fixture
    raises before its yield, so its post-yield wipe never runs and its PO
    survives the session. A test that asserts over global state fails on
    somebody else's leftovers instead of on its own subject.
    """
    cur.execute("select nc_source_pk, number from purchase_orders "
                "where source='nc' and nc_source_pk = any(%s)", (list(pks),))
    return dict(cur.fetchall())


# ── the bug ──────────────────────────────────────────────────────────────────

def test_both_orders_sharing_a_number_are_mirrored(pg_cur, seeded_vendor, system_user_id):
    """Neither order may be dropped. This is the PO-005-2402-01 shape: two live
    NC orders, two different suppliers, one number."""
    payload = _payload(seeded_vendor, [("DUP-A", "PO-DUP-01"), ("DUP-B", "PO-DUP-01")])

    counts = writer.upsert(pg_cur, payload, system_user_id)

    assert counts["pos_upserted"] == 2, "an order sharing a number was dropped"
    numbers = _numbers_by_pk(pg_cur, ["DUP-A", "DUP-B"])
    assert set(numbers) == {"DUP-A", "DUP-B"}
    assert len(set(numbers.values())) == 2, f"numbers must be distinct: {numbers}"
    assert "PO-DUP-01" in numbers.values(), "one order should keep the ERP number"


def test_the_children_of_the_renamed_order_are_mirrored_too(
    pg_cur, seeded_vendor, system_user_id,
):
    """The receipts are the reason this matters. Dropping the order dropped its
    arrivals, and the PO then read as never received — in EPMS and in MRP's
    in-transit figure, which reads ``received_qty`` straight off this table."""
    payload = _payload(seeded_vendor, [("DUP-A", "PO-DUP-01"), ("DUP-B", "PO-DUP-01")])

    writer.upsert(pg_cur, payload, system_user_id)

    pks = ["DUP-A", "DUP-B"]
    pg_cur.execute(
        "select count(*) from goods_receipts g join purchase_orders p on p.id=g.po_id "
        "where p.source='nc' and p.nc_source_pk = any(%s)", (pks,))
    assert pg_cur.fetchone()[0] == 2, "the renamed order lost its goods receipt"
    pg_cur.execute(
        "select count(*) from po_line_items l join purchase_orders p on p.id=l.po_id "
        "where p.source='nc' and p.nc_source_pk = any(%s)", (pks,))
    assert pg_cur.fetchone()[0] == 2, "the renamed order lost its line"


def test_five_orders_on_one_number_all_survive(pg_cur, seeded_vendor, system_user_id):
    """PO-022-2507-01: five orders, five delivery days, one number."""
    payload = _payload(seeded_vendor, [(f"FIVE-{i}", "PO-FIVE-01") for i in range(5)])

    writer.upsert(pg_cur, payload, system_user_id)

    numbers = _numbers_by_pk(pg_cur, [f"FIVE-{i}" for i in range(5)])
    assert len(numbers) == 5
    assert len(set(numbers.values())) == 5, f"numbers must be distinct: {numbers}"


# ── the suffix must not create a NEW collision ───────────────────────────────

def test_suffix_steps_over_a_number_the_erp_already_uses(
    pg_cur, seeded_vendor, system_user_id,
):
    """NC really does have numbers ending in ``-2`` (PO-022-2302-01-2), so the
    suffix cannot assume its first guess is free — it has to look."""
    payload = _payload(seeded_vendor, [
        ("TAKEN-X", "PO-TAKEN-01"),
        ("TAKEN-Y", "PO-TAKEN-01"),
        ("TAKEN-Z", "PO-TAKEN-01-2"),   # a real ERP number that looks like a suffix
    ])

    writer.upsert(pg_cur, payload, system_user_id)

    numbers = _numbers_by_pk(pg_cur, ["TAKEN-X", "TAKEN-Y", "TAKEN-Z"])
    assert len(numbers) == 3
    assert len(set(numbers.values())) == 3, f"numbers must be distinct: {numbers}"
    assert numbers["TAKEN-Z"] == "PO-TAKEN-01-2", (
        "an order whose ERP number happens to look like a suffix must keep it")


def test_collision_with_a_non_nc_po_renames_rather_than_drops(
    pg_cur, seeded_vendor, system_user_id,
):
    """A PMS-imported PO holding the number must not be touched — but the NC
    order must still arrive, under a name of its own."""
    vid, vname = seeded_vendor
    existing = uuid.uuid4()
    pg_cur.execute(
        "insert into purchase_orders (id,number,title,type,status,currency,subtotal,"
        " tax_rate,tax_amount,total,vendor_id,vendor_name,created_by,is_prepaid,"
        " approval_step_idx,place_order_method,place_order_reference,created_at,updated_at) "
        "values (%s,'PO-PMS-01','PMS order',1,'issued','CAD',0,0,0,0,%s,%s,%s,false,0,"
        " 'manual','PO-PMS-01',now(),now())",
        (existing, vid, vname, system_user_id))

    writer.upsert(pg_cur, _payload(seeded_vendor, [("NCX", "PO-PMS-01")]), system_user_id)

    pg_cur.execute("select source, number from purchase_orders where id=%s", (existing,))
    assert pg_cur.fetchone() == (None, "PO-PMS-01"), "the pre-existing PO was rewritten"
    numbers = _numbers_by_pk(pg_cur, ["NCX"])
    assert "NCX" in numbers, "the NC order was dropped instead of renamed"
    assert numbers["NCX"] != "PO-PMS-01"


# ── stability ────────────────────────────────────────────────────────────────

def test_assignment_does_not_depend_on_the_order_oracle_returned_rows_in(
    pg_cur, seeded_vendor, system_user_id,
):
    """Oracle makes no ordering promise. If the assignment followed fetch order,
    a re-run could swap two POs' numbers under the people reading them."""
    pairs = [("STABLE-A", "PO-STABLE-01"), ("STABLE-B", "PO-STABLE-01"),
             ("STABLE-C", "PO-STABLE-01")]

    pks = [pk for pk, _ in pairs]
    writer.upsert(pg_cur, _payload(seeded_vendor, pairs), system_user_id)
    first = _numbers_by_pk(pg_cur, pks)

    # Only this test's rows: wiping every source='nc' row would delete whatever
    # another module committed and make this test's outcome depend on it.
    pg_cur.execute("delete from gr_line_items where gr_id in (select g.id "
                   "from goods_receipts g join purchase_orders p on p.id=g.po_id "
                   "where p.nc_source_pk = any(%s))", (pks,))
    pg_cur.execute("delete from goods_receipts g using purchase_orders p "
                   "where p.id=g.po_id and p.nc_source_pk = any(%s)", (pks,))
    pg_cur.execute("delete from po_line_items l using purchase_orders p "
                   "where p.id=l.po_id and p.nc_source_pk = any(%s)", (pks,))
    pg_cur.execute("delete from purchase_orders where nc_source_pk = any(%s)", (pks,))

    writer.upsert(pg_cur, _payload(seeded_vendor, list(reversed(pairs))), system_user_id)
    second = _numbers_by_pk(pg_cur, pks)

    assert first == second, f"fetch order changed the numbering: {first} vs {second}"


def test_a_renamed_order_keeps_its_number_across_reruns(
    pg_cur, seeded_vendor, system_user_id,
):
    """The update path must not try to put the ERP number back on a row that was
    renamed — that both renames a document under its readers and would violate
    UNIQUE(number) against whichever order holds it."""
    payload = _payload(seeded_vendor, [("RERUN-A", "PO-RERUN-01"),
                                       ("RERUN-B", "PO-RERUN-01")])

    writer.upsert(pg_cur, payload, system_user_id)
    first = _numbers_by_pk(pg_cur, ["RERUN-A", "RERUN-B"])
    writer.upsert(pg_cur, payload, system_user_id)
    second = _numbers_by_pk(pg_cur, ["RERUN-A", "RERUN-B"])

    assert first == second, f"a re-run moved the numbers: {first} vs {second}"


# ── visibility ───────────────────────────────────────────────────────────────

def test_renames_are_counted_so_the_run_can_report_them(
    pg_cur, seeded_vendor, system_user_id,
):
    """The old skip only reached a container log, so nobody ever learned that 15
    orders had gone missing. A number that had to be changed is a fact finance
    needs on the run itself."""
    payload = _payload(seeded_vendor, [("CNT-A", "PO-CNT-01"), ("CNT-B", "PO-CNT-01")])

    counts = writer.upsert(pg_cur, payload, system_user_id)

    assert counts["renamed_number_collision"] == 1


def _dup_number_fetch():
    """A reader-shaped fake: two live NC orders, one vbillcode, one arrival each."""
    def fetch(cutover, watermark):
        return {
            "orders": [
                {"pk_order": "D1", "vbillcode": "PO-NC-DUP", "pk_supplier": "SUP1",
                 "corigcurrencyid": "C1", "ntotalorigmny": Decimal("100"),
                 "vmemo": None, "vtrantypecode": "21-Cxx-CRM01"},
                {"pk_order": "D2", "vbillcode": "PO-NC-DUP", "pk_supplier": "SUP1",
                 "corigcurrencyid": "C1", "ntotalorigmny": Decimal("200"),
                 "vmemo": None, "vtrantypecode": "21-Cxx-CRM01"},
            ],
            "order_lines": [
                {"pk_order_b": f"{pk}L", "pk_order": pk, "crowno": "1",
                 "pk_material": "M1", "vvendinventoryname": "Widget",
                 "castunitid": "U1", "nastnum": Decimal("10"),
                 "norigtaxprice": Decimal("10"), "norigtaxmny": Decimal("100")}
                for pk in ("D1", "D2")
            ],
            "arrivals": [{"pk_arriveorder": f"A{pk}", "vbillcode": f"DH-{pk}"}
                         for pk in ("D1", "D2")],
            "arrival_lines": [
                {"pk_arriveorder_b": f"AL{pk}", "pk_arriveorder": f"A{pk}",
                 "pk_order": pk, "pk_order_b": f"{pk}L", "crowno": "1",
                 "pk_material": "M1", "nastnum": Decimal("4"),
                 "norigtaxprice": Decimal("10"), "norigtaxmny": Decimal("40")}
                for pk in ("D1", "D2")
            ],
            "suppliers": {"SUP1": _ERP_ID},
            "materials": {"M1": ("MAT-1", "Widget")},
            "uoms": {"U1": "KGM"}, "currencies": {"C1": "CAD"},
            "max_modifiedtime": "2026-08-19 10:00:00",
        }
    return fetch


def test_a_real_run_records_the_renumbering_on_its_run_row(committed_dup_env):
    """End to end: the count has to survive the service layer and reach the
    column the admin screen reads. It reached a container log before, which is
    why 15 missing orders went unnoticed for months."""
    from app.services.nc_purchase_sync import service

    dsn, uid, con = committed_dup_env
    rid = service.start_run("incremental", uid, fetch=_dup_number_fetch(),
                            pg_dsn=dsn, run_worker=True)

    cur = con.cursor()
    cur.execute("select status, pos_upserted, grs_upserted, renamed_number_collision, "
                "skipped_number_collision from nc_purchase_sync_runs where id=%s", (rid,))
    status, pos, grs, renamed, skipped = cur.fetchone()
    assert status == "success"
    assert (pos, grs) == (2, 2), "an order sharing a number lost its receipt"
    assert renamed == 1
    assert skipped == 0
