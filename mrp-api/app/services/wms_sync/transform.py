"""Pure mapping: Flux WMS raw rows -> wms_inventory_lots payloads.

No DB/network access here on purpose — `run_wms_sync` (service.py) is the
only caller and it owns both the mapping dict (loaded from the
`mrp_status_mapping` table) and `today`, so this stays a pure function the
transform tests can exercise directly without a database.
"""
from datetime import date, datetime


def _d(s):
    if not s:
        return None
    try:
        return datetime.strptime(str(s)[:10], "%Y-%m-%d").date()
    except ValueError:
        return None


def transform_lot_location(raw: dict) -> dict:
    """`INV_LOT_LOC_ID` row -> `wms_lot_locations` payload.

    `trace_id` keeps the source's `'*'` verbatim (122 rows carry it, meaning
    "no handling unit") rather than being normalised to NULL here: it is part
    of the table's unique key, and NULLs do not collide in a Postgres unique
    constraint, so normalising at this layer would quietly stop the key from
    catching a real duplicate. The API maps it for display instead.
    """
    return {
        "warehouse_id": raw["warehouseid"],
        "material_code": raw["sku"],
        "lot_no": raw["lotnum"],
        "location_id": raw["locationid"],
        "trace_id": raw["traceid"],
        "zone_id": raw.get("zoneid"),
        "qty": raw["qty"],
        "qty_allocated": raw.get("qtyallocated") or 0,
        "qty_onhold": raw.get("qtyonhold") or 0,
    }


def transform_lot(raw: dict, mapping: dict[str, str], today: date) -> dict:
    """`mapping` is wms_code -> mapped_status ('01'->'hold', '02'->'available',
    '04'->'hold', ...). Unknown/blank codes default to 'hold' (fail safe: an
    unrecognized quality status should never read as available). An expired
    lot (expiry_date < today) always overrides to 'expired', regardless of
    what the mapping/raw WMS quality status says."""
    expiry = _d(raw.get("lotatt02"))
    status = mapping.get(raw.get("lotatt08") or "", "hold")
    if expiry is not None and expiry < today:
        status = "expired"
    return {
        "warehouse_id": raw["warehouseid"], "material_code": raw["sku"], "lot_no": raw["lotnum"],
        # The warehouse's unit, carried alongside its quantity. None when the
        # warehouse has no packaging row for the SKU -- which must read as
        # "not stated", never fall back to the ERP's unit for a WMS number.
        "uom": raw.get("uom"),
        "qty": raw["qty"], "qty_allocated": raw.get("qtyallocated") or 0,
        "qty_onhold": raw.get("qtyonhold") or 0,
        "wms_status": raw.get("lotatt08"), "mapped_status": status,
        "production_date": _d(raw.get("lotatt01")), "expiry_date": expiry,
        "inbound_date": _d(raw.get("lotatt03")),
        "supplier_batch": raw.get("lotatt05"), "supplier_code": raw.get("lotatt13"),
        "source_doc": raw.get("lotatt14"), "wms_edit_time": raw.get("edittime"),
    }
