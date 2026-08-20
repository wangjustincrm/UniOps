"""Columns on EPMS's purchase orders that **mrp-api reads directly**.

MRP's Inventory feature answers "how much of this is on the way" by reading
`purchase_orders` / `po_line_items` straight out of the shared database, through
a read-only mirror (`mrp-api/app/models/epms_mirror.py`). Every UniOps service
shares one Postgres instance, so that is a real read of these very tables — not
a call to an endpoint this service could rename behind.

This file is the other half of that arrangement. It lives here, in the owning
service, because the test that matters is the one that fails in the suite of
whoever does the renaming: MRP's own tests build their fixtures from MRP's
mirror definitions, so they would agree with themselves no matter what EPMS did,
and the first sign of trouble would be an in-transit column reading zero — a
number indistinguishable from "nothing is on order".

If a change here fails this test, the fix is not to edit the expectations.
It is to update `mrp-api/app/models/epms_mirror.py` and
`mrp-api/app/services/in_transit.py` in the same change.
"""
import uuid
from decimal import Decimal

import pytest

# column -> information_schema.data_type. Only what MRP actually reads: a
# narrower contract is a smaller thing to break.
CONSUMED = {
    "purchase_orders": {
        "id": "uuid",
        "number": "character varying",
        # MRP filters on type = 1 (raw material / packaging).
        "type": "integer",
        # MRP counts only 'issued' and 'partially_received', and excludes
        # 'nc_milk' explicitly. Renaming a STATUS VALUE breaks MRP just as
        # thoroughly as renaming the column and this test cannot see it —
        # see test_status_values_mrp_depends_on below.
        "status": "character varying",
        "vendor_name": "character varying",
        "expected_delivery": "date",
    },
    "po_line_items": {
        "id": "uuid",
        "po_id": "uuid",
        "material_id": "character varying",
        "qty": "numeric",
        "received_qty": "numeric",
        "unit": "character varying",
        "planned_arrival_date": "date",
    },
}


@pytest.mark.parametrize("table", sorted(CONSUMED))
def test_columns_mrp_reads_still_exist(pg_cur, table):
    pg_cur.execute(
        "select column_name, data_type from information_schema.columns "
        "where table_name = %s", (table,))
    actual = dict(pg_cur.fetchall())
    assert actual, f"{table} does not exist"
    for column, dtype in CONSUMED[table].items():
        assert column in actual, (
            f"mrp-api reads {table}.{column} directly — renaming or dropping it "
            f"silently zeroes MRP's in-transit figures. Update "
            f"mrp-api/app/models/epms_mirror.py in this same change.")
        assert actual[column] == dtype, (
            f"{table}.{column} is now {actual[column]}, was {dtype}; "
            f"mrp-api/app/models/epms_mirror.py maps the old type.")


def test_nc_milk_status_string_is_unchanged():
    """The status STRINGS are as much a contract as the column names, and
    nothing in the schema pins them: `status` is a free varchar.

    MRP treats 'issued' and 'partially_received' as "on its way", and excludes
    'nc_milk' — raw-milk orders whose receipts are recorded in NC, so their
    received_qty exceeds the order and a naive remainder goes negative (all 238
    such lines net -2,695,743). Renaming any of the three leaves the column
    perfectly valid and MRP's answer silently wrong.

    'nc_milk' is produced right here, so it can be asserted on behaviour rather
    than on a literal. 'issued' and 'partially_received' come from EPMS's own PO
    lifecycle and are recorded above for whoever changes it.
    """
    from app.services.nc_purchase_sync.transform import transform

    raw = {
        "orders": [{"pk_order": "O1", "vbillcode": "PO-MILK-1",
                    "dbilldate": "2026-05-01 00:00:00", "pk_supplier": "S1",
                    "corigcurrencyid": "C1", "ntotalorigmny": Decimal("100"),
                    "forderstatus": 3, "modifiedtime": "2026-05-01 09:00:00",
                    "vmemo": None,
                    # Anything other than the raw-material trade type is milk.
                    "vtrantypecode": "21-Cxx-MILK99"}],
        "order_lines": [], "arrivals": [], "arrival_lines": [],
        "suppliers": {"S1": "0000415"}, "materials": {}, "uoms": {},
        "currencies": {"C1": "CAD"}, "max_modifiedtime": "2026-05-01 09:00:00",
    }
    result = transform(raw, {"0000415": (uuid.uuid4(), "Test Vendor")})
    assert result["orders"][0]["status"] == "nc_milk", (
        "mrp-api excludes purchase orders with status 'nc_milk' by that exact "
        "string (mrp-api/app/services/in_transit.py NC_MILK_STATUS). Renaming "
        "it here puts raw-milk orders back into MRP's in-transit figures, where "
        "they net negative.")
