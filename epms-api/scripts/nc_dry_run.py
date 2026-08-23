"""Read-only dry run of the NC purchase mirror: NC -> reader -> transform.

Writes NOTHING. Prints what the mirror WOULD store for the purchase orders named
on the command line, so a change to the reader or the transform can be checked
against the real ERP rather than against a fixture.

    python scripts/nc_dry_run.py PO-019-2505-01 PO-078-2411-01

Needs the NC_* settings the sync itself uses (host/port/service/user/password).
"""
import sys
from decimal import Decimal

from app.services.nc_purchase_sync import reader
from app.services.nc_purchase_sync.transform import transform


def main(argv):
    wanted = {a.upper() for a in argv[1:]}
    if not wanted:
        print(__doc__)
        return 2
    if not reader.nc_configured():
        print("NC_* settings are not configured in this environment.")
        return 2

    raw = reader.fetch_nc("2024-01-01 00:00:00", None)

    # Every supplier resolves, so nothing is dropped for a reason unrelated to
    # what is being checked. This is a dry run — no vendor id is ever written.
    vendor_by_erp = {code: (f"vendor-{code}", f"Vendor {code}")
                     for code in set(raw["suppliers"].values())}
    out = transform(raw, vendor_by_erp)

    pos = [p for p in out["orders"] if p["number"].upper() in wanted]
    if not pos:
        print(f"No mirrored order matches {sorted(wanted)}. "
              f"(Fetched {len(out['orders'])} orders.)")
        return 1

    pk_by_number = {p["nc_source_pk"]: p["number"] for p in pos}
    for p in pos:
        print(f"\n=== {p['number']}  [{p['status']}]  nc_pk={p['nc_source_pk']}")
        print(f"    total={p['total']}  vendor={p['vendor_name']}  notes={p['notes']!r}")
        for ln in out["order_lines"]:
            if ln["po_nc_pk"] != p["nc_source_pk"]:
                continue
            implied = ln["qty"] * ln["unit_price"]
            agrees = abs(implied - ln["line_total"]) <= Decimal("0.01") * abs(
                ln["line_total"] or Decimal("1"))
            print(f"    line {ln['material_id']:<10} qty={ln['qty']} {ln['unit']:<6}"
                  f" received={ln['received_qty']}"
                  f" price={ln['unit_price']} total={ln['line_total']}"
                  f"  qty*price={implied} {'OK' if agrees else '*** MISMATCH ***'}")

    grs = [g for g in out["grs"] if g["po_nc_pk"] in pk_by_number]
    print(f"\n=== goods receipts mirrored for these orders: {len(grs)}")
    for g in sorted(grs, key=lambda g: g["number"]):
        lines = [l for l in out["gr_lines"] if l["gr_nc_pk"] == g["nc_source_pk"]]
        qty = sum((l["qty_received"] for l in lines), Decimal("0"))
        units = {l["unit"] for l in lines}
        print(f"    {g['number']:<24} on {pk_by_number[g['po_nc_pk']]:<18}"
              f" received={qty} {'/'.join(sorted(units))}  at={g['received_at']}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
