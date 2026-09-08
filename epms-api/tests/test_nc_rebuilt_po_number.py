"""An NC order NC DELETED and REBUILT must come back under its OWN number.

PO-058-2607-02 is the case. The buyer deleted the order in NC and re-created it
under the same ``vbillcode`` — twice. In UniOps that produced three purchase
orders: the original, plus ``-2`` and ``-3``, with the live ERP document wearing
the ``-3``.

The cause is that ``reconcile_pending`` CANCELS the mirror of an order NC no
longer lists; it does not remove the row, and ``purchase_orders.number`` is
UNIQUE. The cancelled row is a tombstone squatting on the number, so the
rebuilt order — the one the ERP considers real — cannot have it.

The other half of that incident (deleting the three in Data Maintenance made the
live order vanish for good) is in test_nc_delete_refetch.py.
"""
import uuid
from datetime import date
from decimal import Decimal

from app.services.nc_purchase_sync import writer


_PK_LIVE = "REB-LIVE"
_PK_DEAD = "REB-DEAD"
_NUMBER = "PO-REB-01"


# ── payload helpers (same shape as test_nc_purchase_number_collision) ─────────

def _order(nc_pk, number, vendor, status="issued"):
    vid, vname = vendor
    return {
        "nc_source_pk": nc_pk, "number": number, "title": number,
        "type": 1, "status": status, "source": "nc", "currency": "CAD",
        "total": Decimal("100.00"), "subtotal": Decimal("100.00"),
        "tax_rate": Decimal("0"), "tax_amount": Decimal("0"),
        "vendor_id": vid, "vendor_name": vname, "pr_id": None,
        "place_order_method": "nc", "place_order_reference": number,
        "notes": None, "created_at": None,
    }


def _payload(vendor, orders):
    """``orders`` = [(nc_pk, number)] — one line each, no arrivals."""
    return {
        "orders": [_order(pk, num, vendor) for pk, num in orders],
        "order_lines": [{
            "nc_source_pk": f"{pk}-L1", "po_nc_pk": pk, "material_id": "MAT-1",
            "description": "Widget", "qty": Decimal("10"), "unit": "KGM",
            "unit_price": Decimal("10.00"), "line_total": Decimal("100.00"),
            "received_qty": Decimal("0"), "planned_arrival_date": date(2026, 5, 5),
            "sort_order": 1,
        } for pk, _ in orders],
        "grs": [], "gr_lines": [], "skipped_no_vendor": [],
    }


def _row(cur, nc_pk):
    cur.execute("select number, status from purchase_orders "
                "where source='nc' and nc_source_pk=%s", (nc_pk,))
    return cur.fetchone()


def _withdraw(cur, nc_pk):
    """What reconcile_pending does to a mirror NC stopped listing."""
    cur.execute("update purchase_orders set status='cancelled' "
                "where source='nc' and nc_source_pk=%s", (nc_pk,))


# ── 1. the tombstone must give the number back ───────────────────────────────

def test_a_rebuilt_order_reclaims_the_erp_number_from_its_withdrawn_twin(
    pg_cur, seeded_vendor, system_user_id,
):
    """The PO-058-2607-02 shape: mirror an order, NC deletes it, NC re-creates it
    under the same number. The replacement is the live ERP document and must
    carry the ERP's number — not a ``-2`` that matches nothing anybody can read
    off the ERP."""
    writer.upsert(pg_cur, _payload(seeded_vendor, [(_PK_DEAD, _NUMBER)]), system_user_id)
    _withdraw(pg_cur, _PK_DEAD)

    writer.upsert(pg_cur, _payload(seeded_vendor, [(_PK_LIVE, _NUMBER)]), system_user_id)

    assert _row(pg_cur, _PK_LIVE)[0] == _NUMBER, "the live order did not get the ERP number"
    parked = _row(pg_cur, _PK_DEAD)
    assert parked[0] == f"{_NUMBER}-VOID1", "the tombstone kept the number"
    assert parked[1] == "cancelled", "parking must not resurrect the withdrawn order"


def test_the_parked_tombstone_keeps_its_erp_reference(
    pg_cur, seeded_vendor, system_user_id,
):
    """Parking renames the UniOps document number only. ``place_order_reference``
    is where the ERP's own ``vbillcode`` lives, and it has to survive — it is the
    only thing tying the cancelled row back to the ERP document it mirrored."""
    writer.upsert(pg_cur, _payload(seeded_vendor, [(_PK_DEAD, _NUMBER)]), system_user_id)
    _withdraw(pg_cur, _PK_DEAD)
    writer.upsert(pg_cur, _payload(seeded_vendor, [(_PK_LIVE, _NUMBER)]), system_user_id)

    pg_cur.execute("select place_order_reference from purchase_orders "
                   "where source='nc' and nc_source_pk=%s", (_PK_DEAD,))
    assert pg_cur.fetchone()[0] == _NUMBER


def test_a_second_rebuild_parks_into_the_next_void_slot(
    pg_cur, seeded_vendor, system_user_id,
):
    """PO-058-2607-02 was rebuilt TWICE. Two tombstones on one number means the
    parking slot has to count, or the second park collides with the first."""
    writer.upsert(pg_cur, _payload(seeded_vendor, [("REB-D1", _NUMBER)]), system_user_id)
    _withdraw(pg_cur, "REB-D1")
    writer.upsert(pg_cur, _payload(seeded_vendor, [("REB-D2", _NUMBER)]), system_user_id)
    _withdraw(pg_cur, "REB-D2")

    writer.upsert(pg_cur, _payload(seeded_vendor, [(_PK_LIVE, _NUMBER)]), system_user_id)

    assert _row(pg_cur, _PK_LIVE)[0] == _NUMBER
    assert {_row(pg_cur, "REB-D1")[0], _row(pg_cur, "REB-D2")[0]} == {
        f"{_NUMBER}-VOID1", f"{_NUMBER}-VOID2"}


def test_an_already_suffixed_mirror_reclaims_the_number_once_the_twin_is_parked(
    pg_cur, seeded_vendor, system_user_id,
):
    """The rebuild may already be mirrored under a suffix by the time the
    predecessor is withdrawn — which is exactly the state production was left in.
    The UPDATE branch has to heal it on the next run, otherwise the only way back
    to the ERP number is deleting the row, which is the OTHER bug in this file."""
    writer.upsert(pg_cur, _payload(seeded_vendor, [(_PK_DEAD, _NUMBER)]), system_user_id)
    writer.upsert(pg_cur, _payload(seeded_vendor, [(_PK_LIVE, _NUMBER)]), system_user_id)
    assert _row(pg_cur, _PK_LIVE)[0] == f"{_NUMBER}-2", "precondition: mirrored under a suffix"

    _withdraw(pg_cur, _PK_DEAD)
    writer.upsert(pg_cur, _payload(seeded_vendor, [(_PK_LIVE, _NUMBER)]), system_user_id)

    assert _row(pg_cur, _PK_LIVE)[0] == _NUMBER


def test_a_live_twin_still_holds_its_number(pg_cur, seeded_vendor, system_user_id):
    """The suffix behaviour for genuinely duplicated ``vbillcode``s is unchanged:
    only a CANCELLED mirror gives its number up. Two live orders on one number
    (PO-005-2402-01) still get one number each."""
    writer.upsert(pg_cur, _payload(seeded_vendor, [(_PK_DEAD, _NUMBER)]), system_user_id)

    writer.upsert(pg_cur, _payload(seeded_vendor, [(_PK_LIVE, _NUMBER)]), system_user_id)

    assert _row(pg_cur, _PK_DEAD)[0] == _NUMBER
    assert _row(pg_cur, _PK_LIVE)[0] == f"{_NUMBER}-2"


def test_a_withdrawn_order_a_goods_receipt_points_at_is_never_renamed(
    pg_cur, seeded_vendor, system_user_id,
):
    """Should never happen — a withdrawn ``nc_pending`` order has no GR by
    construction — and is guarded anyway: renaming a document somebody's receipt
    or invoice was built on is far worse than a suffix on a new one."""
    writer.upsert(pg_cur, _payload(seeded_vendor, [(_PK_DEAD, _NUMBER)]), system_user_id)
    _withdraw(pg_cur, _PK_DEAD)
    pg_cur.execute("select id, vendor_id, vendor_name from purchase_orders "
                   "where nc_source_pk=%s", (_PK_DEAD,))
    po_id, vid, vname = pg_cur.fetchone()
    pg_cur.execute(
        "insert into goods_receipts (id,number,title,po_id,po_number,vendor_id,vendor_name,"
        "gr_type,procurement_type,currency,status,created_by,created_at,updated_at) "
        "values (%s,%s,%s,%s,%s,%s,%s,'physical',1,'CAD','collected',%s,now(),now())",
        (uuid.uuid4(), "GR-REB-1", "GR", po_id, _NUMBER, vid, vname, system_user_id))

    writer.upsert(pg_cur, _payload(seeded_vendor, [(_PK_LIVE, _NUMBER)]), system_user_id)

    assert _row(pg_cur, _PK_DEAD)[0] == _NUMBER, "a receipted order was renamed"
    assert _row(pg_cur, _PK_LIVE)[0] == f"{_NUMBER}-2"
