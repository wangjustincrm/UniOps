"""Read-only NC65 Oracle reader for purchase orders + arrivals."""
from app.core.config import settings


def nc_configured() -> bool:
    return all([settings.nc_host, settings.nc_service, settings.nc_user, settings.nc_password])


def _connect():
    import oracledb
    oracledb.defaults.fetch_decimals = True
    dsn = oracledb.makedsn(settings.nc_host, settings.nc_port, service_name=settings.nc_service)
    return oracledb.connect(user=settings.nc_user, password=settings.nc_password, dsn=dsn)


def select_incremental_order_pks(order_rows, arrival_rows, watermark) -> set:
    """Pure decision helper: given the candidate order rows and arrival rows for
    an incremental run, return the set of order pks that must be (re)fetched.

    An order qualifies if EITHER its own change time advanced to/past the
    watermark (``changed_at`` — see ``_CHANGED_AT``; NC leaves ``modifiedtime``
    NULL on most rows), OR any of its arrivals did (``modifiedtime`` OR
    ``creationtime`` >= watermark). The arrival branch exists because NC does NOT reliably bump
    ``po_order.modifiedtime`` when an arrival is posted, so an order-only
    watermark filter would miss a new arrival against an already-synced order
    (its GR would never be mirrored → the 3-way gate never opens → payment
    stalls). NC timestamps are 'YYYY-MM-DD HH24:MI:SS' strings, which compare
    lexicographically in chronological order.

    order_rows: iterable of dicts with 'pk_order' and 'modifiedtime'.
    arrival_rows: iterable of dicts with 'pk_order', 'modifiedtime', 'creationtime'.
    """
    pks: set = set()
    for o in order_rows:
        # 'changed_at' is what the incremental query aliases (_CHANGED_AT);
        # 'modifiedtime' is accepted so a caller handing raw header rows —
        # tests, and any future path that reuses this helper — still works.
        mt = o.get("changed_at") or o.get("modifiedtime")
        if mt is not None and (watermark is None or mt >= watermark):
            pks.add(o["pk_order"])
    for a in arrival_rows:
        mt, ct = a.get("modifiedtime"), a.get("creationtime")
        if watermark is None or (mt is not None and mt >= watermark) \
                or (ct is not None and ct >= watermark):
            pks.add(a["pk_order"])
    return pks


#: NC keeps every version of a CHANGED purchase order as its own PO_ORDER row —
#: same ``vbillcode``, same ``forderstatus=3``, same ``dr=0``. Only
#: ``bislatest='Y'`` marks the version in force, and the arrivals hang off that
#: one: not a single PO_ARRIVEORDER_B row in production points at a superseded
#: order. Fetching both versions puts two rows with the same document number in
#: front of a UNIQUE(number) mirror, where whichever Oracle returned first won —
#: so the mirror could keep the CANCELLED version while the live one, carrying
#: every arrival, was dropped. PO-019-2505-01 is exactly that: v1 (LB, no
#: arrivals) mirrored, v2 (KGM, three arrivals) skipped, PO reads as never
#: received in EPMS and inflates MRP's in-transit figure forever.
_LATEST_VERSION = "bislatest='Y'"

#: NC ``PO_ORDER.forderstatus``, measured in production: 3 = approved and in
#: force (1,681 orders), 2 = submitted and still working through the approval
#: chain (28), 0 = free state, never submitted (17, all of them 2021-2022
#: leftovers under a document-number series nobody uses any more).
#:
#: Orders still IN APPROVAL are mirrored so the buyer can print the UniOps PO
#: PDF and get it signed off-line — the signature is what feeds NC's approval,
#: so the PDF has to exist first. They land in the mirror under the read-only
#: ``nc_pending`` status (see transform), which no payment, receiving or
#: invoice-matching allow-list contains. Free-state orders stay out.
_APPROVED = "3"
_PENDING = "2"

#: A pending order is in scope only while its document number has no approved
#: sibling. NC's ``vbillcode`` is not unique, and two of the 28 pending orders
#: share a number with an approved one (PO-022-2512-01, PO-073-2307-01). Both
#: would be mirrored — the second under a ``-2`` suffix (see writer._free_number)
#: — leaving two documents on screen with the same ERP number and no way for a
#: human to tell which one the ERP considers real. The approved one wins.
#:
#: ``dr`` (NC's soft-delete flag) is checked on the pending side only: every
#: approved row in production carries dr=0, while 143 of the 160 free-state rows
#: are deleted, so pending is where the flag actually decides anything.
_NO_APPROVED_SIBLING = (
    "not exists (select 1 from NCSC.PO_ORDER ap "
    f"where ap.vbillcode = o.vbillcode and ap.forderstatus={_APPROVED} "
    "and ap.bislatest='Y' and nvl(ap.dr,0)=0)")

#: The orders the mirror carries: approved, plus pending-with-no-approved-twin.
_IN_SCOPE = (f"(o.forderstatus={_APPROVED} or (o.forderstatus={_PENDING} "
             f"and nvl(o.dr,0)=0 and {_NO_APPROVED_SIBLING}))")

#: When this order last changed, as NC actually records it.
#:
#: ``modifiedtime`` is NULL on 1,534 of the 1,681 approved orders and on 27 of
#: the 28 pending ones — NC simply does not maintain it. An incremental run
#: keyed on it alone therefore never sees a newly submitted order, and (the
#: expensive one) never sees the 2→3 approval that has to flip a mirrored PO out
#: of ``nc_pending`` into the payable flow. ``taudittime`` is set on every
#: approved order and is the moment approval completed; ``creationtime`` covers
#: an order that has neither. All three are CHAR 'YYYY-MM-DD HH24:MI:SS', which
#: compares lexicographically in chronological order.
_CHANGED_AT = "nvl(o.modifiedtime, nvl(o.taudittime, o.creationtime))"


def changed_at(order: dict) -> str | None:
    """Python-side twin of ``_CHANGED_AT`` — the two must agree, or the watermark
    advances past rows the query would still have returned."""
    return (order.get("modifiedtime") or order.get("taudittime")
            or order.get("creationtime") or None)


def fetch_nc(cutover: str, watermark: str | None) -> dict:
    """cutover: 'YYYY-MM-DD HH24:MI:SS' (only orders with dbilldate >= cutover).
    watermark: last synced NC modifiedtime, or None for full.

    Superseded order versions are excluded at the source — see
    ``_LATEST_VERSION``. EVERY read of PO_ORDER carries the predicate;
    tests/test_nc_purchase_reader_sql.py asserts that in aggregate so a query
    added later cannot quietly reintroduce them."""
    con = _connect()
    try:
        cur = con.cursor()
        def lookup(sql):
            cur.execute(sql)
            return {r[0]: r[1] for r in cur.fetchall()}
        suppliers = lookup("select pk_supplier, code from NCSC.BD_SUPPLIER")
        uoms = lookup("select pk_measdoc, code from NCSC.BD_MEASDOC")
        currencies = lookup("select pk_currtype, code from NCSC.BD_CURRTYPE")
        # Prefer the English material name (ename); fall back to name when a
        # material has no English name.
        cur.execute("select pk_material, code, name, ename from NCSC.BD_MATERIAL")
        materials = {
            r[0]: (r[1], (r[3].strip() if r[3] and r[3].strip() else r[2]))
            for r in cur.fetchall()
        }

        _order_cols = ("pk_order, vbillcode, dbilldate, pk_supplier, corigcurrencyid, "
                       "ntotalorigmny, forderstatus, modifiedtime, taudittime, vmemo, "
                       "bfinalclose, dclosedate, creationtime, vtrantypecode")

        # Every order NC currently lists for the mirror, watermark or not. The
        # reconcile pass needs the whole set: an order REJECTED in NC drops to
        # forderstatus=0 (or is soft-deleted) and thereby leaves every
        # watermark-filtered result set silently, so absence from an incremental
        # payload cannot be told apart from "unchanged". Only membership of this
        # set can. ~1,700 single-column rows.
        cur.execute(f"select o.pk_order from NCSC.PO_ORDER o "
                    f"where {_IN_SCOPE} and o.{_LATEST_VERSION} "
                    "and o.dbilldate >= :cut", {"cut": cutover})
        in_scope_pks = {r[0] for r in cur.fetchall()}

        if watermark:
            # INCREMENTAL: union of (a) orders whose OWN change time advanced and
            # (b) orders that have a new arrival (NC may not bump order.modifiedtime
            # on arrival posting). Then fetch FULL headers for the unioned set so an
            # order pulled in only by a new arrival still gets its header/lines
            # mirrored (needed to resolve po_line_id and ensure the PO exists).
            cur.execute(
                f"select o.pk_order, {_CHANGED_AT} changed_at from NCSC.PO_ORDER o "
                f"where {_IN_SCOPE} and o.{_LATEST_VERSION} "
                f"and o.dbilldate >= :cut and {_CHANGED_AT} >= :wm",
                {"cut": cutover, "wm": watermark})
            order_rows = [dict(zip([c[0].lower() for c in cur.description], r))
                          for r in cur.fetchall()]
            cur.execute(
                "select ab.pk_order, ah.modifiedtime, ah.creationtime "
                "from NCSC.PO_ARRIVEORDER ah "
                "join NCSC.PO_ARRIVEORDER_B ab on ab.pk_arriveorder = ah.pk_arriveorder "
                "join NCSC.PO_ORDER o on o.pk_order = ab.pk_order "
                # Approved orders ONLY, deliberately: an order still in approval
                # has no arrival in NC (all 28 carry zero), and mirroring a goods
                # receipt against a PO the ERP has not yet issued would put stock
                # and a 3-way match behind a document that may still be withdrawn.
                f"where ah.fbillstatus=3 and o.forderstatus={_APPROVED} "
                f"and o.{_LATEST_VERSION} "
                "and o.dbilldate >= :cut "
                "and (ah.modifiedtime >= :wm or ah.creationtime >= :wm)",
                {"cut": cutover, "wm": watermark})
            arrival_rows = [dict(zip([c[0].lower() for c in cur.description], r))
                            for r in cur.fetchall()]
            order_pks = list(select_incremental_order_pks(order_rows, arrival_rows, watermark))
            orders = []
            for chunk in _chunks(order_pks, 900):
                ph = ",".join(f":p{i}" for i in range(len(chunk)))
                b = {f"p{i}": v for i, v in enumerate(chunk)}
                # Redundant with the two queries that produced these pks, and
                # kept anyway: this is the statement that actually MATERIALISES
                # an order into the payload, so it is the one that must be
                # unable to emit a superseded version.
                cur.execute(f"select {_order_cols} from NCSC.PO_ORDER o "
                            f"where o.{_LATEST_VERSION} and o.pk_order in ({ph})", b)
                ocols = [c[0].lower() for c in cur.description]
                orders += [dict(zip(ocols, r)) for r in cur.fetchall()]
        else:
            # FULL: everything in scope past cutover.
            cur.execute(f"select {_order_cols} from NCSC.PO_ORDER o "
                        f"where {_IN_SCOPE} and o.{_LATEST_VERSION} "
                        "and o.dbilldate >= :cut", {"cut": cutover})
            ocols = [c[0].lower() for c in cur.description]
            orders = [dict(zip(ocols, r)) for r in cur.fetchall()]
            order_pks = [o["pk_order"] for o in orders]

        order_lines, arrivals, arrival_lines = [], [], []
        invoiced_arrivals: set = set()
        for chunk in _chunks(order_pks, 900):
            ph = ",".join(f":p{i}" for i in range(len(chunk)))
            b = {f"p{i}": v for i, v in enumerate(chunk)}
            cur.execute(
                "select pk_order_b, pk_order, crowno, pk_material, vvendinventoryname, "
                "castunitid, nastnum, norigtaxprice, "
                # nqtorigtaxprice = 报价单位含税单价, the price per CASTUNITID —
                # the same unit nastnum counts, which is what the mirror stores.
                # norigtaxprice is per the MAIN unit and disagrees whenever the
                # two differ (LB vs KGM: 2.2x).
                "nqtorigtaxprice, "
                "ntaxrate, ctaxcodeid, norigtaxmny, norigmny, ntax, "
                # dplanarrvdate = 计划到货日期, the date NC's PO list shows as
                # "Delivery Date". CHAR 'YYYY-MM-DD HH24:MI:SS'; the transform
                # keeps only the date half. Line level, not header -- NC lets
                # each line differ and real orders do.
                "dplanarrvdate, "
                "bpayclose, binvoiceclose "
                f"from NCSC.PO_ORDER_B where pk_order in ({ph})", b)
            lcols = [c[0].lower() for c in cur.description]
            order_lines += [dict(zip(lcols, r)) for r in cur.fetchall()]
            cur.execute(
                "select pk_arriveorder, vbillcode, dbilldate, pk_supplier, fbillstatus, modifiedtime "
                "from NCSC.PO_ARRIVEORDER ah where fbillstatus=3 and exists("
                "select 1 from NCSC.PO_ARRIVEORDER_B ab where ab.pk_arriveorder=ah.pk_arriveorder "
                f"and ab.pk_order in ({ph}))", b)
            acols = [c[0].lower() for c in cur.description]
            arrivals += [dict(zip(acols, r)) for r in cur.fetchall()]
            cur.execute(
                "select pk_arriveorder_b, pk_arriveorder, pk_order, pk_order_b, crowno, "
                "pk_material, castunitid, nastnum, norigtaxprice, norigtaxmny, norigmny "
                f"from NCSC.PO_ARRIVEORDER_B where pk_order in ({ph})", b)
            albcols = [c[0].lower() for c in cur.description]
            arrival_lines += [dict(zip(albcols, r)) for r in cur.fetchall()]
            # Which arrivals (GRs) are already invoiced downstream in NC. NC pays
            # via 到货->采购入库单->采购发票, so an arrival is "invoiced/paid" (per
            # the user's proxy: has any downstream invoice) when a stock-in line
            # (ic_purchasein_b.csourcebillbid = this arrival line) has an invoice
            # line (po_invoice_b.csourcebid = that stock-in line's cgeneralbid).
            # NC uses '~' as a null placeholder and CHAR-pads pks, hence trim().
            cur.execute(
                "select distinct ab.pk_arriveorder from NCSC.PO_ARRIVEORDER_B ab "
                f"where ab.pk_order in ({ph}) and exists("
                "select 1 from NCSC.IC_PURCHASEIN_B ic "
                "join NCSC.PO_INVOICE_B vb on trim(vb.csourcebid)=trim(ic.cgeneralbid) "
                "where trim(ic.csourcebillbid)=trim(ab.pk_arriveorder_b))", b)
            invoiced_arrivals.update(r[0] for r in cur.fetchall())
        # Advance the watermark to the TRUE max across orders AND arrivals — an
        # arrival can be newer than every order.modifiedtime (the whole reason the
        # order-only watermark missed arrival-only changes), so an orders-only max
        # would re-fetch the same arrival forever.
        #
        # Orders contribute their COALESCED change time, matching the predicate
        # the incremental query filtered on. Taking the max of a column the
        # filter no longer uses would leave the watermark pinned to whatever
        # ancient row happened to carry a modifiedtime, and every "incremental"
        # run would re-read the entire order book. Every value in the max was
        # itself fetched by a `>= watermark` query, so advancing to it cannot
        # step over anything that had already changed.
        _mts = [t for t in (changed_at(o) for o in orders) if t is not None]
        _mts += [a["modifiedtime"] for a in arrivals if a.get("modifiedtime") is not None]
        maxmt = max(_mts, default=None)
        return {
            "orders": orders, "order_lines": order_lines,
            "arrivals": arrivals, "arrival_lines": arrival_lines,
            "invoiced_arrivals": invoiced_arrivals,
            "suppliers": suppliers, "materials": materials, "uoms": uoms,
            "currencies": currencies, "max_modifiedtime": maxmt,
            "in_scope_pks": in_scope_pks,
        }
    finally:
        con.close()


def _chunks(seq, n):
    for i in range(0, len(seq), n):
        yield seq[i:i + n]
