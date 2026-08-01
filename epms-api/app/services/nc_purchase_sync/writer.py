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


def upsert(cur, payload: dict, system_user_id, heartbeat=None) -> dict:
    """Idempotent mirror write. ``heartbeat`` (optional) is a zero-arg callable
    invoked every ~500 processed rows so a long-running full load can refresh its
    run row's updated_at and not be swept as stale mid-flight."""
    counts = dict(pos_upserted=0, po_lines_upserted=0, grs_upserted=0,
                  gr_lines_upserted=0, skipped_consumed=0)
    po_id_by_ncpk, po_line_id_by_ncpk, gr_id_by_ncpk = {}, {}, {}
    consumed_pks: set = set()

    _processed = 0

    def _beat():
        nonlocal _processed
        _processed += 1
        if heartbeat is not None and _processed % 500 == 0:
            heartbeat()

    for po in payload["orders"]:
        _beat()
        cur.execute("select id from purchase_orders where nc_source_pk=%s and source='nc'",
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
            pid = row[0]
            cur.execute("update purchase_orders set number=%s,title=%s,status=%s,currency=%s,"
                        "total=%s,vendor_id=%s,vendor_name=%s,notes=%s,updated_at=now() where id=%s",
                        (po["number"], po["title"], po["status"], po["currency"], po["total"],
                         po["vendor_id"], po["vendor_name"], po["notes"], pid))
        else:
            pid = uuid.uuid4()
            cur.execute(
                "insert into purchase_orders (id,number,title,type,status,currency,subtotal,"
                "tax_rate,tax_amount,total,vendor_id,vendor_name,is_prepaid,approval_step_idx,"
                "pr_id,created_by,place_order_method,place_order_reference,source,nc_source_pk,"
                "notes,created_at,updated_at) values (%s,%s,%s,1,%s,%s,0,0,0,%s,%s,%s,false,0,"
                "NULL,%s,'nc',%s,'nc',%s,%s,now(),now())",
                (pid, po["number"], po["title"], po["status"], po["currency"], po["total"],
                 po["vendor_id"], po["vendor_name"], system_user_id,
                 po["place_order_reference"], po["nc_source_pk"], po["notes"]))
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
            cur.execute("update po_line_items set description=%s,material_id=%s,qty=%s,unit=%s,"
                        "unit_price=%s,line_total=%s,received_qty=%s,sort_order=%s where id=%s",
                        (ln["description"], ln["material_id"], ln["qty"], ln["unit"],
                         ln["unit_price"], ln["line_total"], ln["received_qty"], ln["sort_order"], lid))
        else:
            lid = uuid.uuid4()
            cur.execute(
                "insert into po_line_items (id,po_id,description,material_id,qty,unit,unit_price,"
                "line_total,received_qty,sort_order,nc_source_pk) "
                "values (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                (lid, pid, ln["description"], ln["material_id"], ln["qty"], ln["unit"],
                 ln["unit_price"], ln["line_total"], ln["received_qty"], ln["sort_order"],
                 ln["nc_source_pk"]))
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
            cur.execute(
                "insert into goods_receipts (id,number,title,po_id,po_number,pr_id,vendor_id,"
                "vendor_name,gr_type,procurement_type,currency,status,created_by,source,nc_source_pk,"
                "created_at,updated_at) values (%s,%s,%s,%s,%s,NULL,%s,%s,'physical',1,%s,%s,%s,'nc',%s,now(),now())",
                (gid, gr["number"], gr["title"], pid, _num, vid, vname, ccy, gr["status"],
                 system_user_id, gr["nc_source_pk"]))
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
             gl["qty_received"], "EA", gl["unit_price"], gl["line_total"], gl["sort_order"],
             gl["nc_source_pk"]))
        counts["gr_lines_upserted"] += 1
    return counts
