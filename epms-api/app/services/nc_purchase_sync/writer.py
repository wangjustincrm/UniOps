"""psycopg2 idempotent writer for mirrored NC orders/arrivals.

Resolves po_id / po_line_id / gr_id via each row's nc_source_pk (natural key),
so a re-run updates in place instead of duplicating. Refuses destructive updates
to a PO once it has been CONSUMED downstream (referenced by ANY invoice — the
same broad exclusion service._full_reload_delete uses) so the sync can never
rewrite a document a human/payment has already acted on, even after the invoice
has advanced matched -> partially_paid -> paid.

System-user ensure mirrors scripts/import_pms/load.py (find-by-email, else
insert a locked system_admin using app.core.security.hash_password).
"""
from decimal import Decimal
import secrets
import uuid


def load_vendor_map(cur) -> dict:
    """erp_id -> (id, name) for every supplier business_partner."""
    cur.execute("select erp_id, id, name from business_partners "
                "where erp_id is not null and is_supplier is true")
    return {r[0]: (r[1], r[2]) for r in cur.fetchall()}


def ensure_system_user_sync(cur) -> uuid.UUID:
    cur.execute("select id from users where email=%s", ("nc-sync@epms.local",))
    row = cur.fetchone()
    if row:
        return row[0]
    from app.core.security import hash_password   # same helper the importer uses
    uid = uuid.uuid4()
    # mfa_enabled / notification_channel / erp_imported are NOT NULL with only a
    # Python-side default on the ORM (no server_default), so a raw INSERT must
    # supply them explicitly.
    cur.execute(
        "insert into users (id, email, hashed_password, full_name, role, is_active, "
        "must_change_password, mfa_enabled, notification_channel, erp_imported, "
        "created_at, updated_at) "
        "values (%s,%s,%s,%s,'system_admin',true,true,false,'email_only',false,now(),now())",
        (uid, "nc-sync@epms.local", hash_password(secrets.token_urlsafe(24)), "NC Sync"))
    return uid


def _po_consumed(cur, po_id) -> bool:
    """A PO is consumed once ANY invoice references it — the same broad exclusion
    the full-reload delete-guard uses (service._full_reload_delete). Once a NC PO
    has entered the invoice/payment flow it must be frozen against re-sync
    overwrite: the invoice moves matched -> partially_paid -> paid as payment
    executes, so a matched-only guard would let an incremental upsert rewrite the
    header/lines of a PAID PO if NC bumped its modifiedtime — corrupting data a
    payment was built on. This is the safe superset (matched, partially_paid,
    paid, and even draft/unmatched — all frozen)."""
    cur.execute("select 1 from invoices where po_id=%s limit 1", (po_id,))
    return cur.fetchone() is not None


def _number_conflict(cur, number, nc_source_pk) -> bool:
    """True if purchase_orders already holds this document number on a row that is
    NOT this NC order — e.g. a PMS-imported PO, or another NC order that genuinely
    carries the same ``vbillcode``. purchase_orders.number is UNIQUE, so inserting
    a colliding order would abort the whole run."""
    cur.execute(
        "select 1 from purchase_orders where number=%s "
        "and (source is distinct from 'nc' or nc_source_pk is distinct from %s) limit 1",
        (number, nc_source_pk),
    )
    return cur.fetchone() is not None


#: How far the suffix search will walk before giving up. The worst real group is
#: five orders on one number (PO-022-2507-01); 50 is far past anything the ERP
#: has produced while still bounding the loop, so a pathological input degrades
#: to the old skip-and-log instead of hanging the run.
_MAX_NUMBER_SUFFIX = 50


def _erp_number_held_by_another(cur, number, nc_source_pk) -> bool:
    """True if ``number`` is some OTHER NC order's genuine ERP number.

    A mirrored order keeps its ``vbillcode`` in ``place_order_reference`` even
    when ``number`` had to be suffixed, so this is where the ERP's own numbering
    survives. A suffix must not land on one: NC really issues numbers ending in
    ``-2`` (PO-022-2302-01-2), and handing that string to a DIFFERENT order would
    make the mirror disagree with the ERP about which document is which.
    """
    cur.execute(
        "select 1 from purchase_orders where source='nc' and place_order_reference=%s "
        "and nc_source_pk is distinct from %s limit 1",
        (number, nc_source_pk),
    )
    return cur.fetchone() is not None


def _free_number(cur, number, nc_source_pk, reserved=frozenset()) -> str | None:
    """A document number this NC order can own: ``number``, else ``number-2``,
    ``number-3``… — the first one nobody holds.

    NC's ``vbillcode`` is NOT unique (1,682 approved orders, 1,645 distinct
    numbers) and the duplicates are separate live orders, not versions: different
    suppliers, different materials, in one case five delivery days. UniOps needs
    a unique number, so the later arrivals get a suffix and are MIRRORED. The
    previous behaviour — drop the order and everything under it — cost 15 orders
    and 16 goods receipts inside the cutover, and left the surviving PO reading
    as never received.

    ``-N`` matches the suffix the split-GR numbering already uses, so there is
    one convention rather than two. NC itself has numbers that end that way
    (PO-022-2302-01-2), which is exactly why each candidate is CHECKED rather
    than assumed free.

    ``reserved`` holds every ERP number in this batch. A suffix is never allowed
    to land on one, because that string may be another order's real ``vbillcode``
    rather than a free slot — the base number is exempt, since that one IS this
    order's own ERP number.

    Returns None if nothing is free within ``_MAX_NUMBER_SUFFIX`` — the caller
    then skips, as before, rather than looping.

    Stability: an order that already exists keeps the number it was given (the
    UPDATE branch never renames), and the insert order is sorted, so repeated
    runs reproduce the same assignment. The one case that can move a suffix is a
    FULL reload after NC has added a new order whose pk sorts ahead of an
    existing duplicate — the reload discards the old assignment along with the
    rows. Rare, visible in the run's rename count, and the alternative (carrying
    assignments across a reload that exists precisely to rebuild from scratch)
    buys less than it costs.
    """
    if not _number_conflict(cur, number, nc_source_pk):
        return number
    for n in range(2, _MAX_NUMBER_SUFFIX + 1):
        candidate = f"{number}-{n}"
        if candidate in reserved:
            continue
        if _number_conflict(cur, candidate, nc_source_pk):
            continue
        if _erp_number_held_by_another(cur, candidate, nc_source_pk):
            continue
        return candidate
    return None


def upsert(cur, payload: dict, system_user_id, heartbeat=None) -> dict:
    """Idempotent mirror write. ``heartbeat`` (optional) is a zero-arg callable
    invoked every ~500 processed rows so a long-running full load can refresh its
    run row's updated_at and not be swept as stale mid-flight."""
    counts = dict(pos_upserted=0, po_lines_upserted=0, grs_upserted=0,
                  gr_lines_upserted=0, skipped_consumed=0, skipped_number_collision=0,
                  renamed_number_collision=0)
    collisions: list = []
    renames: list = []
    po_id_by_ncpk, po_line_id_by_ncpk, gr_id_by_ncpk = {}, {}, {}
    consumed_pks: set = set()

    _processed = 0

    def _beat():
        nonlocal _processed
        _processed += 1
        if heartbeat is not None and _processed % 500 == 0:
            heartbeat()

    # Every ERP number in this batch, so a suffix can never be handed to one
    # order while it is another order's genuine vbillcode.
    erp_numbers = frozenset(o["number"] for o in payload["orders"])

    # Sorted, not fetch-ordered. When several orders share a number the suffix
    # goes to whoever is processed second, and Oracle promises no ordering — so
    # an unsorted loop could hand PO-005-2402-01 to a different one of the two
    # orders on every run, renaming documents under the people reading them.
    # (number, nc_source_pk) is stable for as long as NC keeps the rows.
    for po in sorted(payload["orders"], key=lambda o: (o["number"], o["nc_source_pk"])):
        _beat()
        cur.execute("select id, number from purchase_orders "
                    "where nc_source_pk=%s and source='nc'",
                    (po["nc_source_pk"],))
        row = cur.fetchone()
        if row and _po_consumed(cur, row[0]):
            # A consumed PO and ALL its children are left untouched: record its
            # nc_source_pk but DON'T map it — the line/GR loops below skip it, so
            # the data the 3-way match relied on is never rewritten.
            consumed_pks.add(po["nc_source_pk"])
            counts["skipped_consumed"] += 1
            continue
        if row:
            pid, current_number = row
            # Keep whatever number this row already owns when the ERP number is
            # held by somebody else: the row may BE a renamed duplicate, and
            # putting the ERP number back would rename a document under its
            # readers and violate UNIQUE(number) against the order that holds it.
            number = (po["number"] if not _number_conflict(cur, po["number"], po["nc_source_pk"])
                      else current_number)
            # buyer_notes / incoterms / buyer_edited_at here, and sample /
            # supplier_item_id on the line UPDATE below, are human-owned: NC has
            # no source for them, so they are deliberately absent from these
            # column lists. Never add them — a sync would silently erase work a
            # buyer did by hand.
            cur.execute("select tax_rate from purchase_orders "
                        "where id=%s and buyer_edited_at is not null", (pid,))
            edited = cur.fetchone()
            if edited is None:
                tax_rate = po["tax_rate"]
                tax_amount = po["tax_amount"]
                total = po["total"]
            else:
                # A buyer set this rate by hand (PATCH /po/{id}/imported-details).
                # Keep it, but re-derive the money from NC's fresh subtotal —
                # simply skipping the columns would leave subtotal + tax != total
                # whenever NC changed the line amounts.
                tax_rate = edited[0]
                tax_amount = (Decimal(po["subtotal"]) * Decimal(tax_rate)).quantize(
                    Decimal("0.01"))
                total = Decimal(po["subtotal"]) + tax_amount
            cur.execute("update purchase_orders set number=%s,title=%s,status=%s,currency=%s,"
                        "subtotal=%s,tax_rate=%s,tax_amount=%s,total=%s,"
                        "vendor_id=%s,vendor_name=%s,notes=%s,updated_at=now() where id=%s",
                        (number, po["title"], po["status"], po["currency"],
                         po["subtotal"], tax_rate, tax_amount, total,
                         po["vendor_id"], po["vendor_name"], po["notes"], pid))
        else:
            number = _free_number(cur, po["number"], po["nc_source_pk"], erp_numbers)
            if number is None:
                # Nothing free within the bound — fall back to the old behaviour
                # rather than loop. Skipping the order and ALL its children keeps
                # the run from aborting on UNIQUE(number); it is reported, not
                # swallowed.
                consumed_pks.add(po["nc_source_pk"])
                counts["skipped_number_collision"] += 1
                collisions.append(po["number"])
                continue
            if number != po["number"]:
                # Somebody else holds the ERP number: another NC order that
                # genuinely carries the same vbillcode, or a PMS-imported PO.
                # Either way this order is mirrored under a name of its own — the
                # holder is never rewritten.
                counts["renamed_number_collision"] += 1
                renames.append(f"{po['number']} -> {number}")
            pid = uuid.uuid4()
            cur.execute(
                "insert into purchase_orders (id,number,title,type,status,currency,subtotal,"
                "tax_rate,tax_amount,total,vendor_id,vendor_name,is_prepaid,approval_step_idx,"
                "pr_id,created_by,place_order_method,place_order_reference,source,nc_source_pk,"
                "notes,created_at,updated_at) values (%s,%s,%s,1,%s,%s,%s,%s,%s,%s,%s,%s,false,0,"
                "NULL,%s,'nc',%s,'nc',%s,%s,coalesce(%s::timestamptz, now()),now())",
                (pid, number, po["title"], po["status"], po["currency"],
                 po["subtotal"], po["tax_rate"], po["tax_amount"], po["total"],
                 po["vendor_id"], po["vendor_name"], system_user_id,
                 po["place_order_reference"], po["nc_source_pk"], po["notes"],
                 po.get("created_at")))
        po_id_by_ncpk[po["nc_source_pk"]] = pid
        counts["pos_upserted"] += 1

    for ln in payload["order_lines"]:
        _beat()
        if ln["po_nc_pk"] in consumed_pks:   # consumed parent — never touch its lines
            continue
        pid = po_id_by_ncpk.get(ln["po_nc_pk"])
        if pid is None:      # parent skipped (no-vendor / not in this batch)
            continue
        cur.execute("select id from po_line_items where nc_source_pk=%s", (ln["nc_source_pk"],))
        row = cur.fetchone()
        if row:
            lid = row[0]
            # supplier_item_id and sample are omitted on purpose — see the note
            # on the PO UPDATE above.
            # planned_arrival_date is on the UPDATE too, not just the INSERT:
            # every NC line already exists, so an insert-only column would stay
            # null forever on exactly the rows the feature is for.
            cur.execute("update po_line_items set description=%s,material_id=%s,qty=%s,unit=%s,"
                        "unit_price=%s,line_total=%s,received_qty=%s,"
                        "planned_arrival_date=%s,sort_order=%s where id=%s",
                        (ln["description"], ln["material_id"], ln["qty"], ln["unit"],
                         ln["unit_price"], ln["line_total"], ln["received_qty"],
                         ln["planned_arrival_date"], ln["sort_order"], lid))
        else:
            lid = uuid.uuid4()
            cur.execute(
                "insert into po_line_items (id,po_id,description,material_id,qty,unit,unit_price,"
                "line_total,received_qty,planned_arrival_date,sort_order,nc_source_pk) "
                "values (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                (lid, pid, ln["description"], ln["material_id"], ln["qty"], ln["unit"],
                 ln["unit_price"], ln["line_total"], ln["received_qty"],
                 ln["planned_arrival_date"], ln["sort_order"], ln["nc_source_pk"]))
        po_line_id_by_ncpk[ln["nc_source_pk"]] = lid
        counts["po_lines_upserted"] += 1

    for gr in payload["grs"]:
        _beat()
        if gr["po_nc_pk"] in consumed_pks:   # consumed parent — don't insert GRs
            continue
        pid = po_id_by_ncpk.get(gr["po_nc_pk"])
        if pid is None:
            continue
        cur.execute("select id from goods_receipts where nc_source_pk=%s and source='nc'",
                    (gr["nc_source_pk"],))
        row = cur.fetchone()
        if row:
            gid = row[0]
        else:
            gid = uuid.uuid4()
            cur.execute("select number, vendor_id, vendor_name, currency from purchase_orders where id=%s", (pid,))
            _num, vid, vname, ccy = cur.fetchone()
            # NC arrivals are already-received goods: mirror as a fully collected
            # GR with the whole receipt lifecycle timestamped from the NC arrival
            # date (avoids null acknowledged/collected fields in the UI).
            rat = gr.get("received_at")
            cur.execute(
                "insert into goods_receipts (id,number,title,po_id,po_number,pr_id,vendor_id,"
                "vendor_name,gr_type,procurement_type,currency,status,notes,"
                "received_at,received_by,acknowledged_at,acknowledged_by,collected_at,collected_by,"
                "created_by,source,nc_source_pk,created_at,updated_at) "
                "values (%s,%s,%s,%s,%s,NULL,%s,%s,'physical',1,%s,%s,%s,"
                "%s,'NC ERP',%s,'NC ERP',%s,'NC ERP',%s,'nc',%s,coalesce(%s::timestamptz, now()),now())",
                (gid, gr["number"], gr["title"], pid, _num, vid, vname, ccy, gr["status"],
                 gr.get("notes"), rat, rat, rat, system_user_id, gr["nc_source_pk"], rat))
            counts["grs_upserted"] += 1
        gr_id_by_ncpk[gr["nc_source_pk"]] = gid

    for gl in payload["gr_lines"]:
        _beat()
        gid = gr_id_by_ncpk.get(gl["gr_nc_pk"])
        if gid is None:
            continue
        cur.execute("select id from gr_line_items where nc_source_pk=%s", (gl["nc_source_pk"],))
        if cur.fetchone():
            continue
        plid = po_line_id_by_ncpk.get(gl["po_line_nc_pk"])
        cur.execute(
            "insert into gr_line_items (id,gr_id,po_line_id,description,material_id,qty_ordered,"
            "qty_received,unit,unit_price,line_total,condition,sort_order,nc_source_pk) "
            "values (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,'good',%s,%s)",
            (uuid.uuid4(), gid, plid, gl["description"], gl["material_id"], gl["qty_ordered"],
             gl["qty_received"], gl.get("unit") or "EA", gl["unit_price"],
             gl["line_total"], gl["sort_order"],
             gl["nc_source_pk"]))
        counts["gr_lines_upserted"] += 1
    if renames:
        print(f"[nc_purchase_sync] mirrored {len(renames)} PO(s) under a suffixed "
              f"number (the ERP number was already held): {renames[:20]}", flush=True)
    if collisions:
        print(f"[nc_purchase_sync] skipped {len(collisions)} PO(s): no free document "
              f"number within {_MAX_NUMBER_SUFFIX} suffixes: {collisions[:20]}", flush=True)
    return counts
