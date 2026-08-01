"""Pure mapping: NC raw rows -> EPMS PO/GR upsert payloads."""
from collections import defaultdict
from decimal import Decimal


def _num(v):
    return Decimal(str(v)) if v is not None else Decimal("0")


def transform(raw: dict, vendor_by_erp: dict) -> dict:
    sup, mat, uom, ccy = raw["suppliers"], raw["materials"], raw["uoms"], raw["currencies"]

    # dedupe arrivals (per-chunk EXISTS may repeat)
    arrivals = {a["pk_arriveorder"]: a for a in raw["arrivals"]}

    # received qty per order line
    recv = defaultdict(lambda: Decimal("0"))
    for al in raw["arrival_lines"]:
        recv[al["pk_order_b"]] += _num(al["nastnum"])

    orders, order_lines, skipped = [], [], []
    kept_order_pks = set()
    for o in raw["orders"]:
        code = sup.get(o["pk_supplier"])
        vend = vendor_by_erp.get(code) if code else None
        if not vend:
            skipped.append(o["vbillcode"])
            continue
        kept_order_pks.add(o["pk_order"])
        orders.append({
            "nc_source_pk": o["pk_order"], "number": o["vbillcode"], "title": o["vbillcode"],
            "type": 1, "status": "issued", "source": "nc",
            "currency": ccy.get(o["corigcurrencyid"], "CAD"),
            "total": _num(o["ntotalorigmny"]), "subtotal": Decimal("0"),
            "tax_rate": Decimal("0"), "tax_amount": Decimal("0"),
            "vendor_id": vend[0], "vendor_name": vend[1],
            "pr_id": None, "place_order_method": "nc",
            "place_order_reference": o["vbillcode"], "notes": o.get("vmemo"),
        })

    for ln in raw["order_lines"]:
        if ln["pk_order"] not in kept_order_pks:
            continue
        mcode, mname = mat.get(ln["pk_material"], (None, ln.get("vvendinventoryname") or ""))
        order_lines.append({
            "nc_source_pk": ln["pk_order_b"], "po_nc_pk": ln["pk_order"],
            "material_id": mcode, "description": mname or (ln.get("vvendinventoryname") or mcode or ""),
            "qty": _num(ln["nastnum"]), "unit": uom.get(ln["castunitid"], "EA"),
            "unit_price": _num(ln["norigtaxprice"]), "line_total": _num(ln["norigtaxmny"]),
            "received_qty": recv.get(ln["pk_order_b"], Decimal("0")),
            "sort_order": int(ln["crowno"]) if str(ln.get("crowno") or "").isdigit() else 0,
        })

    # GR: split each arrival by order → one GR per (arrival, order)
    grp = defaultdict(list)
    for al in raw["arrival_lines"]:
        if al["pk_order"] not in kept_order_pks:
            continue
        grp[(al["pk_arriveorder"], al["pk_order"])].append(al)

    # per-arrival order sets, used to decide whether THIS arrival spans multiple
    # orders (global grp size is irrelevant — other unrelated arrivals in the same
    # batch must not affect this arrival's GR number).
    orders_per_arrival = defaultdict(set)
    for arr_pk, ord_pk in grp:
        orders_per_arrival[arr_pk].add(ord_pk)

    grs, gr_lines = [], []
    for (arr_pk, ord_pk), lines in grp.items():
        ah = arrivals.get(arr_pk)
        if not ah:
            continue
        gr_pk = f"{arr_pk}:{ord_pk}"          # composite, unique per split GR
        arrival_orders = orders_per_arrival[arr_pk]
        if len(arrival_orders) > 1:
            idx = sorted(arrival_orders).index(ord_pk) + 1
            number = f"{ah['vbillcode']}-{idx}"
        else:
            number = ah["vbillcode"]
        grs.append({
            "nc_source_pk": gr_pk, "po_nc_pk": ord_pk,
            "number": number,
            "title": ah["vbillcode"], "gr_type": "physical", "procurement_type": 1,
            "status": "confirmed", "source": "nc",
        })
        for al in lines:
            mcode, mname = mat.get(al["pk_material"], (None, ""))
            gr_lines.append({
                "nc_source_pk": al["pk_arriveorder_b"], "gr_nc_pk": gr_pk,
                "po_line_nc_pk": al["pk_order_b"], "material_id": mcode,
                "description": mname or mcode or "",
                "qty_ordered": Decimal("0"), "qty_received": _num(al["nastnum"]),
                "unit_price": _num(al["norigtaxprice"]), "line_total": _num(al["norigtaxmny"]),
                "sort_order": int(al["crowno"]) if str(al.get("crowno") or "").isdigit() else 0,
            })
    return {"orders": orders, "order_lines": order_lines, "grs": grs,
            "gr_lines": gr_lines, "skipped_no_vendor": skipped}
