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

#: NC ``PO_ORDER.forderstatus``: 3 = approved and in force, 2 = submitted and
#: working through NC's own approval chain, 0 = free state (saved, not
#: submitted).
#:
#: **2026-09: NC's PO approval chain was switched off** to match the UniOps
#: process. A buyer now only SAVES the order, so it sits at forderstatus=0, and
#: the officer presses NC's one-click approve *after* UniOps has signed off —
#: taking it straight 0 → 3. Nothing new ever reaches 2 again.
#:
#: That makes FREE STATE the normal "waiting on our approval" state, so it has
#: to be mirrored: the whole point of mirroring an unapproved order is that the
#: buyer needs the UniOps PO PDF *before* the ERP approval, because the sign-off
#: on that PDF is what authorises the approval. Leaving free state out is why
#: PO-081-2609-01 / PO-055-2609-01 / PO-029-2609-01 never appeared.
#:
#: 2 stays in scope for the handful of orders that were mid-chain when the
#: change was made (10 live ones at the time of writing).
#:
#: Both land in the mirror under the read-only ``nc_pending`` status (see
#: transform), which no payment, receiving or invoice-matching allow-list
#: contains.
_APPROVED = "3"
_PENDING = "2"
_FREE = "0"

#: Free state counts only from here. NC holds 22 live (dr=0, latest) free-state
#: orders, and 17 of them are abandoned drafts from 2021-2022 — the era when
#: free state meant "nobody ever submitted this" — the newest created
#: 2022-06-08. Every order made under the new process is 2026-08 or later.
#: Without a floor those 17 would land in EPMS as permanent ``nc_pending`` work
#: that nobody will ever act on and the reconcile pass will never retire (NC
#: still lists them).
#:
#: The main cutover cannot do this job: it filters EVERY status on dbilldate,
#: and the mirror deliberately carries approved orders back to 2020 — 958 of
#: them are dated before 2023 — so raising the cutover far enough to clear 17
#: drafts would delete those 958 instead.
_FREE_STATE_FROM = "2023-01-01 00:00:00"

#: An unapproved order is in scope only while its document number has no
#: further-along sibling. NC's ``vbillcode`` is not unique, and pending orders
#: share a number with an approved one (PO-022-2512-01, PO-073-2307-01). Both
#: would be mirrored — the second under a ``-2`` suffix (see writer._free_number)
#: — leaving two documents on screen with the same ERP number and no way for a
#: human to tell which one the ERP considers real. The further-along one wins.
#:
#: Comparing forderstatus rather than naming ``=3`` covers the free-state rows
#: this scope now admits with the same rule: a draft loses to a submitted OR an
#: approved twin, a submitted one loses only to an approved twin, and a row can
#: never knock itself out because the comparison is strict.
#:
#: ``dr`` (NC's soft-delete flag) matters on the unapproved side: every approved
#: row in production carries dr=0, while 146 of the 168 free-state rows are
#: deleted — buyers throw drafts away, and a discarded draft must not be
#: mirrored.
_NO_SENIOR_SIBLING = (
    "not exists (select 1 from NCSC.PO_ORDER sib "
    "where sib.vbillcode = o.vbillcode and sib.pk_order <> o.pk_order "
    "and sib.forderstatus > o.forderstatus "
    "and sib.bislatest='Y' and nvl(sib.dr,0)=0)")

#: The orders the mirror carries: approved, plus unapproved (free or in-chain)
#: with no further-along twin — free state additionally floored at
#: ``_FREE_STATE_FROM`` so the abandoned 2021-2022 drafts stay out.
_IN_SCOPE = (
    f"(o.forderstatus={_APPROVED} or ("
    f"o.forderstatus in ({_PENDING},{_FREE}) and nvl(o.dr,0)=0 "
    f"and (o.forderstatus={_PENDING} or o.creationtime >= '{_FREE_STATE_FROM}') "
    f"and {_NO_SENIOR_SIBLING}))")

#: When this order last changed, as NC actually records it.
#:
#: ``modifiedtime`` is NULL on the large majority of orders — NC simply does not
#: maintain it — so it cannot carry the filter alone. ``taudittime`` is the
#: moment approval completed, ``creationtime`` covers an order that has neither,
#: and ``ts`` is NC's row stamp, bumped by EVERY write to the row. All four are
#: CHAR 'YYYY-MM-DD HH24:MI:SS', which compares lexicographically in
#: chronological order.
#:
#: **GREATEST, not NVL.** ``nvl()`` stops at the first non-null value, which is
#: wrong whenever an order carries an OLD ``modifiedtime`` and a NEWER approval:
#: PO-034-2608-02 has modifiedtime 2026-08-22 06:06:23 and was approved at
#: taudittime 2026-08-25 03:58:08 *without* modifiedtime being touched. nvl()
#: answers 08-22 — behind a watermark any run in between had already advanced
#: past — so the approval is invisible to every future incremental and the PO is
#: stuck at ``nc_pending`` for good, out of the payable flow forever. 134 orders
#: in production carry taudittime > modifiedtime.
#:
#: That was survivable while free state was out of scope, because an order was
#: only ever mirrored *after* approval. Under the new process every order is
#: mirrored while free and approved later, so the 0→3 flip is the one event the
#: watermark must never miss — it is what turns a printable draft into a payable
#: PO.
#:
#: ``ts`` is included because it is the only column NC bumps unconditionally:
#: measured across all 1,703 in-scope orders it is >= taudittime with zero
#: exceptions, and 934 of them carry a ts newer than what the old expression
#: returned.
_TS_FLOOR = "'0000-00-00 00:00:00'"
_CHANGED_AT = (f"greatest(nvl(o.ts,{_TS_FLOOR}), nvl(o.modifiedtime,{_TS_FLOOR}), "
               f"nvl(o.taudittime,{_TS_FLOOR}), nvl(o.creationtime,{_TS_FLOOR}))")

#: The columns ``changed_at()`` reads, newest-wins. Must stay in step with
#: ``_CHANGED_AT`` and with the header column list, or the watermark advances
#: past rows the query would still have returned.
_CHANGE_COLUMNS = ("ts", "modifiedtime", "taudittime", "creationtime")


def changed_at(order: dict) -> str | None:
    """Python-side twin of ``_CHANGED_AT`` — the two must agree, or the watermark
    advances past rows the query would still have returned."""
    seen = [v for v in (order.get(c) for c in _CHANGE_COLUMNS) if v]
    return max(seen) if seen else None


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
        # Supplier NAMES are keyed by code, not pk: the only consumer is the
        # "this ERP supplier has no UniOps vendor" error task, which knows the
        # code and needs something a human recognises next to it.
        cur.execute("select pk_supplier, code, name from NCSC.BD_SUPPLIER")
        _sup_rows = cur.fetchall()
        suppliers = {r[0]: r[1] for r in _sup_rows}
        supplier_names = {r[1]: r[2] for r in _sup_rows}
        uoms = lookup("select pk_measdoc, code from NCSC.BD_MEASDOC")
        currencies = lookup("select pk_currtype, code from NCSC.BD_CURRTYPE")
        # Prefer the English material name (ename); fall back to name when a
        # material has no English name.
        cur.execute("select pk_material, code, name, ename from NCSC.BD_MATERIAL")
        materials = {
            r[0]: (r[1], (r[3].strip() if r[3] and r[3].strip() else r[2]))
            for r in cur.fetchall()
        }

        # ``ts`` is here for changed_at()/the watermark, not for the payload —
        # drop it and the Python twin silently loses the one column that sees an
        # approval, and the watermark stops advancing past it.
        _order_cols = ("pk_order, vbillcode, dbilldate, pk_supplier, corigcurrencyid, "
                       "ntotalorigmny, forderstatus, modifiedtime, taudittime, ts, vmemo, "
                       "bfinalclose, dclosedate, creationtime, vtrantypecode")

        # Every order NC currently lists for the mirror, watermark or not. The
        # reconcile pass needs the whole set: an order REJECTED in NC drops to
        # forderstatus=0 (or is soft-deleted) and thereby leaves every
        # watermark-filtered result set silently, so absence from an incremental
        # payload cannot be told apart from "unchanged". Only membership of this
        # set can. ~1,700 single-column rows.
        # vbillcode rides along for the error tasks: an order that dropped out of
        # NC's scope (rejected, deleted, superseded) must not leave a "this order
        # never arrived" task open forever, and the tasks are keyed by number.
        cur.execute(f"select o.pk_order, o.vbillcode from NCSC.PO_ORDER o "
                    f"where {_IN_SCOPE} and o.{_LATEST_VERSION} "
                    "and o.dbilldate >= :cut", {"cut": cutover})
        _in_scope_rows = cur.fetchall()
        in_scope_pks = {r[0] for r in _in_scope_rows}
        in_scope_numbers = {r[1] for r in _in_scope_rows}

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
                # Approved orders ONLY, deliberately: no unapproved order in NC
                # carries an arrival — every free-state order that has arrival
                # lines is a soft-deleted (dr=1) row, none of the live ones do —
                # and mirroring a goods receipt against a PO the ERP has not yet
                # issued would put stock and a 3-way match behind a document that
                # may still be discarded. The arrivals arrive with the order on
                # the first run after NC approves it.
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
            "in_scope_pks": in_scope_pks, "in_scope_numbers": in_scope_numbers,
            "supplier_names": supplier_names,
        }
    finally:
        con.close()


def _chunks(seq, n):
    for i in range(0, len(seq), n):
        yield seq[i:i + n]
