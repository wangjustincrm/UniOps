import uuid
from decimal import Decimal

from app.services.nc_purchase_sync.reader import (
    nc_configured,
    select_incremental_order_pks,
)
from app.services.nc_purchase_sync.transform import transform


def test_nc_configured_false_when_unset(monkeypatch):
    from app.core.config import settings
    monkeypatch.setattr(settings, "nc_host", None, raising=False)
    assert nc_configured() is False


def test_nc_configured_true_when_all_set(monkeypatch):
    from app.core.config import settings
    for f, v in [("nc_host", "h"), ("nc_service", "ORCL"), ("nc_user", "u"), ("nc_password", "p")]:
        monkeypatch.setattr(settings, f, v, raising=False)
    assert nc_configured() is True


# ── FIX 2: incremental order-set union (arrival-only changes) ────────────────
#
# The reader hits real Oracle, so we unit-test the pure decision helper it
# delegates to: given the candidate order rows and arrival rows an incremental
# run pulled (already SQL-filtered by watermark), which order pks must be fetched?
# The critical case: a NEW arrival posted against an already-synced order whose
# order.modifiedtime did NOT advance (NC doesn't reliably bump it) — VERIFIED in
# prod (139 status-3 orders have order.modifiedtime EARLIER than their latest
# arrival). An order-only watermark filter would miss it and the GR would never
# mirror. NC timestamps are 'YYYY-MM-DD HH24:MI:SS' strings (lexicographic == chronological).
WM = "2026-05-02 00:00:00"


def test_select_incremental_includes_order_modified_after_wm():
    order_rows = [{"pk_order": "O1", "modifiedtime": "2026-05-03 09:00:00"}]
    assert select_incremental_order_pks(order_rows, [], WM) == {"O1"}


def test_select_incremental_arrival_only_change_is_picked_up():
    # O9's own header did NOT move (modifiedtime BEFORE wm) but a new arrival was
    # posted (creationtime AFTER wm). Must be fetched so its GR gets mirrored.
    order_rows = [{"pk_order": "O9", "modifiedtime": "2026-04-01 00:00:00"}]
    arrival_rows = [{"pk_order": "O9", "modifiedtime": "2026-04-01 00:00:00",
                     "creationtime": "2026-05-03 08:00:00"}]
    # order-only filter would drop O9; the union recovers it via the arrival.
    assert select_incremental_order_pks([], arrival_rows, WM) == {"O9"}
    assert select_incremental_order_pks(order_rows, arrival_rows, WM) == {"O9"}


def test_select_incremental_arrival_modifiedtime_also_qualifies():
    arrival_rows = [{"pk_order": "O5", "modifiedtime": "2026-05-04 00:00:00",
                     "creationtime": "2026-04-01 00:00:00"}]
    assert select_incremental_order_pks([], arrival_rows, WM) == {"O5"}


def test_select_incremental_union_of_order_and_arrival_sets():
    order_rows = [{"pk_order": "O1", "modifiedtime": "2026-05-03 09:00:00"}]
    arrival_rows = [{"pk_order": "O2", "modifiedtime": None,
                     "creationtime": "2026-05-03 08:00:00"}]
    assert select_incremental_order_pks(order_rows, arrival_rows, WM) == {"O1", "O2"}


def test_select_incremental_excludes_everything_below_wm():
    order_rows = [{"pk_order": "Oold", "modifiedtime": "2026-01-01 00:00:00"}]
    arrival_rows = [{"pk_order": "Oold", "modifiedtime": "2026-01-01 00:00:00",
                     "creationtime": "2026-01-01 00:00:00"}]
    assert select_incremental_order_pks(order_rows, arrival_rows, WM) == set()


VEND = {"0000415": (uuid.uuid4(), "Lactalis Canada")}


def _raw():
    return {
        "orders": [{"pk_order": "O1", "vbillcode": "PO-010-2105-03",
                    "dbilldate": "2026-05-01 00:00:00", "pk_supplier": "S1",
                    "corigcurrencyid": "C1", "ntotalorigmny": Decimal("100"),
                    "forderstatus": 3, "modifiedtime": "2026-05-01 09:00:00", "vmemo": None}],
        "order_lines": [{"pk_order_b": "OL1", "pk_order": "O1", "crowno": "10",
                         "pk_material": "M1", "vvendinventoryname": "Lactose",
                         "castunitid": "U1", "nastnum": Decimal("38000"),
                         "norigtaxprice": Decimal("1"), "ntaxrate": Decimal("5"),
                         "ctaxcodeid": "T1", "norigtaxmny": Decimal("100"),
                         "norigmny": Decimal("95"), "ntax": Decimal("5")}],
        "arrivals": [{"pk_arriveorder": "A1", "vbillcode": "DH2021", "dbilldate": "2026-05-03 00:00:00",
                      "pk_supplier": "S1", "fbillstatus": 3, "modifiedtime": "2026-05-03 09:00:00"}],
        "arrival_lines": [{"pk_arriveorder_b": "AL1", "pk_arriveorder": "A1", "pk_order": "O1",
                           "pk_order_b": "OL1", "crowno": "10", "pk_material": "M1",
                           "nastnum": Decimal("19000"), "norigtaxprice": Decimal("1"),
                           "norigtaxmny": Decimal("50"), "norigmny": Decimal("47.5")}],
        "suppliers": {"S1": "0000415"}, "materials": {"M1": ("CR0025", "Lactose")},
        "uoms": {"U1": "KG"}, "currencies": {"C1": "CAD"}, "max_modifiedtime": "2026-05-03 09:00:00",
    }


def test_transform_maps_order_header():
    r = transform(_raw(), VEND)
    po = r["orders"][0]
    assert po["nc_source_pk"] == "O1" and po["number"] == "PO-010-2105-03"
    assert po["type"] == 1 and po["status"] == "issued" and po["source"] == "nc"
    assert po["vendor_name"] == "Lactalis Canada" and po["currency"] == "CAD"
    assert po["pr_id"] is None and po["place_order_reference"] == "PO-010-2105-03"


def test_transform_line_uses_nc_material_code():
    r = transform(_raw(), VEND)
    ln = r["order_lines"][0]
    assert ln["material_id"] == "CR0025" and ln["qty"] == Decimal("38000")
    assert ln["unit"] == "KG" and ln["nc_source_pk"] == "OL1"


def test_transform_gr_line_links_po_line_and_uses_arrival_qty():
    r = transform(_raw(), VEND)
    gl = r["gr_lines"][0]
    assert gl["po_line_nc_pk"] == "OL1" and gl["qty_received"] == Decimal("19000")
    assert gl["line_total"] == Decimal("50")


def test_transform_skips_order_without_vendor():
    raw = _raw(); raw["suppliers"] = {"S1": "9999999"}   # not in VEND
    r = transform(raw, VEND)
    assert r["orders"] == [] and "PO-010-2105-03" in r["skipped_no_vendor"]


def test_transform_received_qty_rolled_up_to_po_line():
    raw = _raw()
    # second arrival line against the same PO line -> received_qty must be the SUM,
    # not just the last line's qty.
    raw["arrival_lines"].append({**raw["arrival_lines"][0], "pk_arriveorder_b": "AL1b",
                                 "nastnum": Decimal("5000")})
    r = transform(raw, VEND)
    assert r["order_lines"][0]["received_qty"] == Decimal("19000") + Decimal("5000")


def test_transform_splits_arrival_spanning_two_orders_into_two_grs():
    raw = _raw()
    raw["orders"].append({**raw["orders"][0], "pk_order": "O2", "vbillcode": "PO-011"})
    raw["order_lines"].append({**raw["order_lines"][0], "pk_order_b": "OL2", "pk_order": "O2"})
    raw["arrival_lines"].append({**raw["arrival_lines"][0], "pk_arriveorder_b": "AL2",
                                 "pk_order": "O2", "pk_order_b": "OL2"})
    r = transform(raw, VEND)
    grs_for_a1 = [g for g in r["grs"] if g["nc_source_pk"].startswith("A1")]
    assert len(grs_for_a1) == 2  # one GR per (arrival, order)
    # a genuinely order-spanning arrival gets distinct, deterministic 1-based
    # suffixes ordered by pk_order, not the last-4-chars of pk_order.
    numbers = sorted(g["number"] for g in grs_for_a1)
    assert numbers == ["DH2021-1", "DH2021-2"]


def test_transform_independent_non_splitting_arrivals_not_suffixed():
    # A1->O1 and A2->O2 are two SEPARATE arrivals, each mapping to only one order.
    # len(grp) across the whole batch is 2, but neither arrival individually spans
    # multiple orders, so neither GR number should get a "-N" suffix (regression
    # guard: suffix decision must be scoped per-arrival, not to the global batch).
    raw = _raw()
    raw["orders"].append({**raw["orders"][0], "pk_order": "O2", "vbillcode": "PO-011"})
    raw["order_lines"].append({**raw["order_lines"][0], "pk_order_b": "OL2", "pk_order": "O2"})
    raw["arrivals"].append({**raw["arrivals"][0], "pk_arriveorder": "A2", "vbillcode": "DH2022"})
    raw["arrival_lines"].append({**raw["arrival_lines"][0], "pk_arriveorder_b": "AL2",
                                 "pk_arriveorder": "A2", "pk_order": "O2", "pk_order_b": "OL2"})
    r = transform(raw, VEND)
    numbers = {g["nc_source_pk"]: g["number"] for g in r["grs"]}
    assert numbers["A1:O1"] == "DH2021"
    assert numbers["A2:O2"] == "DH2022"
