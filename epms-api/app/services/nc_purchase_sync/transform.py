"""Pure mapping: NC raw rows -> EPMS PO/GR upsert payloads."""
from collections import defaultdict
from datetime import date
from decimal import Decimal

# NC supplier-code aliases: NC has dirty duplicate supplier records for the same
# real company. Map the duplicate code to the canonical one so its orders resolve
# to the canonical company's UniOps vendor. NC data itself is never modified (we
# are read-only on NC); this only affects how the sync resolves the vendor.
#   0000012A "1Zhong bai ..."  ->  0000012 "Zhong bai Xingye Food Technology"
_SUPPLIER_CODE_ALIASES = {
    "0000012A": "0000012",
}

# NC PO trade-type code for raw-material/packaging procurement (the normal
# UniOps payment flow). Every other NC PO type is raw-milk procurement.
_RAW_MATERIAL_TRANTYPE = "21-Cxx-CRM01"

# The NC forderstatus values that mean "the ERP has not issued this order yet":
# 0 = free state (saved, never submitted) and 2 = submitted, still working
# through NC's approval chain.
#
# 0 is in here because NC's PO approval chain was switched off in 2026-09: a
# buyer now just saves the order and the officer one-click approves it in NC
# only after UniOps has signed off, so free state IS the normal pre-approval
# state. 2 remains for the orders that were mid-chain when that changed.
#
# Both are mirrored so the buyer can print the UniOps PO PDF and collect the
# sign-off that authorises the ERP approval — see reader._IN_SCOPE for the scope
# rules, including the floor that keeps abandoned 2021-2022 drafts out.
_NC_PENDING_STATUSES = frozenset({0, 2})

# The mirrored status for those orders. Read-only by construction: it appears in
# no payment, receiving or invoice-matching allow-list, so it takes no gate of
# its own to keep an unapproved order out of the money flow. The two places that
# DO name it are the PDF gate and the buyer-details editor.
PENDING_STATUS = "nc_pending"
_PENDING_TAG = "[NC Pending Approval]"


def _num(v):
    return Decimal(str(v)) if v is not None else Decimal("0")


def _quotation_price(ln: dict) -> Decimal:
    """The price per the unit this line's quantity is counted in.

    The mirror stores ``nastnum`` (quantity in NC's QUOTATION unit, ``castunitid``)
    as the line quantity. ``norigtaxprice`` is the price per NC's MAIN unit, so
    pairing the two breaks the line's own arithmetic wherever the units differ:
    PO-078-2411-01 is 3,900 LB at 2.50 = 9,750, and the main-unit price is 5.5556
    because 1 kg is 2.2 lb. NC carries ``nqtorigtaxprice`` for exactly this.

    Verified against NC: ``nastnum * nqtorigtaxprice`` reproduces ``norigtaxmny``
    on all 629 in-scope order lines. The fallback covers a line the ERP left
    without a quotation price — which only happens when the two units coincide,
    and beats writing 0 into a price column.
    """
    return _num(ln.get("nqtorigtaxprice") or ln.get("norigtaxprice"))


def _planned_arrival(raw: str | None) -> date | None:
    """NCSC.PO_ORDER_B.DPLANARRVDATE -> a plain date.

    The column is CHAR holding 'YYYY-MM-DD HH24:MI:SS'. Only the date half is
    the planned arrival; the time is whenever somebody last set the value, and
    carries no business meaning. Verified against the ERP's own PO list screen:
    PO-001-2510-04 shows 2026-02-02 and stores '2026-02-02 10:01:25'.

    ★ Parsed by SLICING the first 10 characters, deliberately. Nothing here
    builds a datetime and nothing converts a timezone: a date-only value put
    through a UTC conversion lands a day early or late -- and on Dec 31, in the
    wrong year. That bug has already shipped across this codebase once.

    Anything unparseable becomes None rather than a guess: NULL means "the ERP
    did not state an arrival date", which readers must be able to tell apart
    from a real one.
    """
    if not raw or not str(raw).strip():
        return None
    try:
        return date.fromisoformat(str(raw).strip()[:10])
    except ValueError:
        return None


def _is_pending(order: dict) -> bool:
    """True when NC has not issued this order yet — free state or in approval.

    ``oracledb.defaults.fetch_decimals`` is on, so NUMBER columns arrive as
    ``Decimal`` — an identity or string comparison would quietly answer False
    for every order and mirror the whole unapproved set as payable.
    """
    try:
        return int(order.get("forderstatus")) in _NC_PENDING_STATUSES
    except (TypeError, ValueError):
        return False


def _derive_status_and_note(order: dict, pay: dict) -> tuple:
    """Derive the mirrored PO (status, notes) from NC closure/payment flags.

    - NC finally closed (bfinalclose='Y') OR every line payment-closed
      (bpayclose='Y') -> UniOps status 'closed' (excluded from the payment
      worklist / not invoice-matchable), imported read-only for archive.
    - otherwise 'issued' (open, actionable by UniOps payment).
    A human-readable NC marker (已付 / 部分已付 / 已开票 / 已关闭 <date>) is
    appended to notes so finance can see the NC state on the record.
    ``pay`` = {'lines','paid','invoiced'} counts for this order's lines.
    """
    finally_closed = (order.get("bfinalclose") == "Y")
    n = pay.get("lines", 0)
    all_paid = n > 0 and pay.get("paid", 0) == n
    any_paid = pay.get("paid", 0) > 0
    all_invoiced = n > 0 and pay.get("invoiced", 0) == n

    status = "closed" if (finally_closed or all_paid) else "issued"

    markers = []
    if all_paid:
        markers.append("NC Paid")
    elif any_paid:
        markers.append("NC Partially Paid")
    if all_invoiced and not all_paid:
        markers.append("NC Invoiced")
    if finally_closed:
        cd = (order.get("dclosedate") or "")[:10]
        markers.append(f"NC Closed {cd}".strip())

    base = order.get("vmemo") or ""
    if markers:
        tag = "[" + "; ".join(markers) + "]"
        notes = f"{base} {tag}".strip() if base else tag
    else:
        notes = base or None
    return status, notes


def transform(raw: dict, vendor_by_erp: dict) -> dict:
    sup, mat, uom, ccy = raw["suppliers"], raw["materials"], raw["uoms"], raw["currencies"]
    inv_arrivals = raw.get("invoiced_arrivals") or set()

    # dedupe arrivals (per-chunk EXISTS may repeat)
    arrivals = {a["pk_arriveorder"]: a for a in raw["arrivals"]}

    # received qty per order line
    recv = defaultdict(lambda: Decimal("0"))
    for al in raw["arrival_lines"]:
        recv[al["pk_order_b"]] += _num(al["nastnum"])

    # per-order NC payment/invoice-close tally + pre-tax/tax money (from order lines)
    pay_by_order = defaultdict(lambda: {"lines": 0, "paid": 0, "invoiced": 0})
    money_by_order = defaultdict(lambda: {"subtotal": Decimal("0"), "tax": Decimal("0")})
    for ln in raw["order_lines"]:
        p = pay_by_order[ln["pk_order"]]
        p["lines"] += 1
        if ln.get("bpayclose") == "Y":
            p["paid"] += 1
        if ln.get("binvoiceclose") == "Y":
            p["invoiced"] += 1
        m = money_by_order[ln["pk_order"]]
        m["subtotal"] += _num(ln.get("norigmny"))   # pre-tax line amount
        m["tax"] += _num(ln.get("ntax"))            # line tax amount

    orders, order_lines, skipped = [], [], []
    kept_order_pks = set()
    for o in raw["orders"]:
        code = sup.get(o["pk_supplier"])
        if code:
            code = _SUPPLIER_CODE_ALIASES.get(code, code)
        vend = vendor_by_erp.get(code) if code else None
        if not vend:
            skipped.append(o["vbillcode"])
            continue
        kept_order_pks.add(o["pk_order"])
        status, notes = _derive_status_and_note(o, pay_by_order.get(o["pk_order"], {}))
        # Only NC PO type 21-Cxx-CRM01 is raw-material/packaging (the normal
        # UniOps payment flow). All other types are raw-milk procurement, whose
        # receipt + stock-in are done separately/manually in NC, so they never
        # close via the flag logic. Mark them 'nc_milk' — a read-only status not
        # in the invoice-matchable set, so they don't sit as perpetually-open POs
        # nor accept UniOps invoices/payment.
        if o.get("vtrantypecode") != _RAW_MATERIAL_TRANTYPE:
            status = "nc_milk"
            tag = f"[Milk / {o.get('vtrantypecode') or '?'}]"
            notes = f"{notes} {tag}".strip() if notes else tag
        # Last, so it overrides everything above: "NC has not approved this yet"
        # is the most restrictive fact about an order and outranks both the
        # closure flags and the milk classification. When NC approves it, the
        # next sync re-derives the status from those same flags and the order
        # lands wherever it belongs — no separate transition to write.
        if _is_pending(o):
            status = PENDING_STATUS
            notes = f"{notes} {_PENDING_TAG}".strip() if notes else _PENDING_TAG
        _m = money_by_order.get(o["pk_order"], {"subtotal": Decimal("0"), "tax": Decimal("0")})
        _sub, _tax = _m["subtotal"], _m["tax"]
        _rate = (_tax / _sub).quantize(Decimal("0.0001")) if _sub else Decimal("0")
        orders.append({
            "nc_source_pk": o["pk_order"], "number": o["vbillcode"], "title": o["vbillcode"],
            "type": 1, "status": status, "source": "nc",
            "currency": ccy.get(o["corigcurrencyid"], "CAD"),
            "total": _num(o["ntotalorigmny"]), "subtotal": _sub,
            "tax_rate": _rate, "tax_amount": _tax,
            "vendor_id": vend[0], "vendor_name": vend[1],
            "pr_id": None, "place_order_method": "nc",
            "place_order_reference": o["vbillcode"], "notes": notes,
            "created_at": o.get("creationtime"),   # NC record creation time
        })

    for ln in raw["order_lines"]:
        if ln["pk_order"] not in kept_order_pks:
            continue
        mcode, mname = mat.get(ln["pk_material"], (None, ln.get("vvendinventoryname") or ""))
        order_lines.append({
            "nc_source_pk": ln["pk_order_b"], "po_nc_pk": ln["pk_order"],
            "material_id": mcode, "description": mname or (ln.get("vvendinventoryname") or mcode or ""),
            "qty": _num(ln["nastnum"]), "unit": uom.get(ln["castunitid"], "EA"),
            "unit_price": _quotation_price(ln), "line_total": _num(ln["norigtaxmny"]),
            "received_qty": recv.get(ln["pk_order_b"], Decimal("0")),
            # The ERP's own planned arrival date for this line. Populated on
            # 4,890 of 4,890 approved NC order lines, and the only source of an
            # arrival date UniOps has: the hand-entered header field is empty
            # on every open raw-material PO.
            "planned_arrival_date": _planned_arrival(ln.get("dplanarrvdate")),
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
            "status": "collected", "source": "nc",
            "received_at": ah.get("dbilldate"),   # NC arrival date
            # NC pays via 到货->入库->发票; a GR with any downstream invoice is
            # treated as paid (user's proxy).
            "notes": "[NC Paid]" if arr_pk in inv_arrivals else None,
        })
        for al in lines:
            mcode, mname = mat.get(al["pk_material"], (None, ""))
            gr_lines.append({
                "nc_source_pk": al["pk_arriveorder_b"], "gr_nc_pk": gr_pk,
                "po_line_nc_pk": al["pk_order_b"], "material_id": mcode,
                "description": mname or mcode or "",
                "qty_ordered": Decimal("0"), "qty_received": _num(al["nastnum"]),
                # The arrival's OWN unit, not the order line's: NC lets a receipt
                # be booked in a different one, and 'EA' — which the writer used
                # to hardcode — is right for almost none of them.
                "unit": uom.get(al.get("castunitid"), "EA"),
                "unit_price": _num(al["norigtaxprice"]), "line_total": _num(al["norigtaxmny"]),
                "sort_order": int(al["crowno"]) if str(al.get("crowno") or "").isdigit() else 0,
            })
    return {"orders": orders, "order_lines": order_lines, "grs": grs,
            "gr_lines": gr_lines, "skipped_no_vendor": skipped}
