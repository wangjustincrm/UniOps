"""Admin Task Inbox entries for what the NC purchase sync could NOT import.

The sync drops rows for reasons that are nobody's fault at read time and used to
leave nothing behind but an integer on the run row: an order whose ERP supplier
has no UniOps vendor is skipped (`skipped_no_vendor`), an order whose document
number is unavailable is skipped (`skipped_number_collision`), and a run that
raises loses everything it read. None of those are visible unless somebody opens
the Portal admin panel and reads the counters — so PO-029-2609-01 sat unimported
for two days while its buyer waited for a PDF, and four more orders had been
missing since August for the same reason.

These functions raise a Task in the Admin inbox naming the document and the
reason, and close it again on evidence, never on absence:

* the order is now mirrored (`purchase_orders`), or
* NC no longer lists the order in scope (rejected / deleted / superseded),
* and for the run-level failure, a later run succeeded.

Absence from a payload proves nothing here: an incremental run reads a WINDOW,
so an order skipped last week is simply not in today's payload — closing on that
would retire the task while the order is still missing.

Written over the run's own psycopg2 cursor (the sync writes no ORM), inside the
same transaction as the mirror write, so a rolled-back run raises no tasks.
"""
import uuid

from psycopg2.extras import register_uuid

# uuid.UUID -> Postgres uuid. service.py registers this too, but this module is
# also reached on its own (the failed-run path opens its own connection), and an
# unregistered adapter fails at INSERT time with "can't adapt type 'UUID'".
register_uuid()

#: `tasks.document_type` for every row here. Not a document type EPMS owns — the
#: subject is an NC order that, by definition, is NOT in EPMS — so the inbox
#: link resolves off the task TYPE instead (epms/src/lib/taskTypes.ts).
DOC_TYPE = "nc_sync"

#: Broadcast to system_admin: no single person owns the ERP bridge, and
#: `crud.task.get_for_role` gives system_admin holders every task anyway.
ADMIN_ROLE = "system_admin"

#: An order was skipped because its ERP supplier has no UniOps vendor. The fix
#: is Vendors -> Import from ERP, which is where this task's card links.
VENDOR_TASK = "import_erp_vendor"

#: Anything else the sync could not do: a number collision, or a failed run. The
#: card links to the Portal admin NC sync panel.
FAILURE_TASK = "resolve_nc_sync_error"

#: `document_number` of the single run-level failure task. There is at most one
#: open at a time — a broken NC connection breaks every run, and one task per
#: hourly attempt would bury the inbox.
RUN_FAILURE_KEY = "NC SYNC"

_TITLE_MAX = 255          # tasks.title is varchar(255)
_NUMBER_MAX = 40          # tasks.document_number is varchar(40)


def _open_tasks(cur, types) -> dict:
    """{document_number: id} for the open nc_sync tasks of these types."""
    cur.execute("select document_number, id from tasks "
                "where document_type=%s and type = any(%s) and is_completed is false",
                (DOC_TYPE, list(types)))
    return {r[0]: r[1] for r in cur.fetchall()}


def _upsert_task(cur, existing, *, task_type, number, title, description,
                 run_id, created_by, priority="normal", vendor=None) -> bool:
    """Open the task, or refresh the text of the one already open. Returns True
    when a new row was inserted."""
    title = title[:_TITLE_MAX]
    number = number[:_NUMBER_MAX]
    task_id = existing.get(number)
    if task_id is not None:
        cur.execute("update tasks set title=%s, description=%s, document_id=%s, "
                    "priority=%s, vendor=%s, updated_at=now() where id=%s",
                    (title, description, run_id, priority, vendor, task_id))
        return False
    cur.execute(
        "insert into tasks (id, type, priority, document_type, document_id, "
        "document_number, assigned_role, assigned_user_id, title, description, "
        "vendor, is_completed, created_by, created_at, updated_at) "
        "values (%s,%s,%s,%s,%s,%s,%s,null,%s,%s,%s,false,%s,now(),now())",
        (uuid.uuid4(), task_type, priority, DOC_TYPE, run_id, number, ADMIN_ROLE,
         title, description, vendor, created_by))
    return True


def _complete(cur, task_ids) -> int:
    if not task_ids:
        return 0
    cur.execute("update tasks set is_completed=true, completed_at=now(), "
                "updated_at=now() where id = any(%s) and is_completed is false",
                (list(task_ids),))
    return cur.rowcount


def _is_mirrored(cur, number) -> bool:
    """Did this NC document number reach the mirror? A suffixed number counts:
    when the ERP number was already held, writer._free_number mirrors the order
    as `<number>-2`, and the order is imported either way."""
    cur.execute("select 1 from purchase_orders where source='nc' "
                "and (number=%s or number like %s) limit 1",
                (number, f"{number}-%"))
    return cur.fetchone() is not None


def _vendor_gap_text(gap) -> tuple[str, str]:
    number = gap["number"]
    code = gap.get("supplier_code") or "(none)"
    name = gap.get("supplier_name")
    who = f"{code} ({name})" if name else code
    title = (f"NC order {number} was not imported — ERP supplier {who} "
             f"has no vendor in EPMS")
    lines = [
        f"The NC purchase sync skipped {number}: its ERP supplier {who} has no "
        f"matching vendor in EPMS, so the order cannot be mirrored.",
        "",
        f"Fix: Vendors -> Import from ERP -> import supplier {code}. The next "
        "scheduled sync then imports the order by itself — there is no second "
        "step.",
    ]
    changed = gap.get("changed_at")
    if changed:
        # Why the sync looks stuck while this is open, in the panel and in the
        # run rows: it deliberately stops advancing here so the order is re-read
        # every run instead of being left behind.
        lines += [
            "",
            f"Until then the sync holds its position at this order (NC change "
            f"time {changed}) and re-reads it on every run, so nothing is lost.",
        ]
    lines += ["", "This task closes on its own once the order reaches EPMS, or "
                  "once NC stops listing it."]
    return title, "\n".join(lines)


def _collision_text(number) -> tuple[str, str]:
    title = f"NC order {number} was not imported — no free document number"
    body = (
        f"The NC purchase sync skipped {number}: every candidate document number "
        f"({number}, {number}-2, {number}-3, ...) is already held by another "
        "document, so the order cannot be inserted.\n\n"
        "Fix: find what holds the number (Data Maintenance -> Purchase Orders) "
        "and renumber or remove the stale document. The sync holds its position "
        "at this order and imports it on the next run once a number is free.\n\n"
        "This task closes on its own once the order reaches EPMS, or once NC "
        "stops listing it."
    )
    return title, body


def record_import_errors(cur, run_id, *, vendor_gaps, collision_numbers,
                         in_scope_numbers, created_by) -> dict:
    """Raise/refresh a task per order this run could not import, and close the
    ones whose order has since arrived (or left NC). Returns counters."""
    opened = 0
    seen: set = set()
    existing = _open_tasks(cur, (VENDOR_TASK, FAILURE_TASK))

    for gap in vendor_gaps or []:
        number = gap.get("number")
        if not number or number in seen:
            continue
        seen.add(number)
        title, body = _vendor_gap_text(gap)
        opened += _upsert_task(
            cur, existing, task_type=VENDOR_TASK, number=number, title=title,
            description=body, run_id=run_id, created_by=created_by,
            vendor=(gap.get("supplier_name") or gap.get("supplier_code")))

    for number in collision_numbers or []:
        if not number or number in seen:
            continue
        seen.add(number)
        title, body = _collision_text(number)
        opened += _upsert_task(
            cur, existing, task_type=FAILURE_TASK, number=number, title=title,
            description=body, run_id=run_id, created_by=created_by)

    # Close on evidence. `in_scope_numbers` is None when the reader did not
    # report a scope set (a stubbed fetch in tests) — an empty set there would
    # mean "NC lists nothing" and retire every open task.
    resolved = []
    for number, task_id in _open_tasks(cur, (VENDOR_TASK, FAILURE_TASK)).items():
        if number == RUN_FAILURE_KEY:
            continue
        gone = in_scope_numbers is not None and number not in in_scope_numbers
        if gone or _is_mirrored(cur, number):
            resolved.append(task_id)
    return {"opened": opened, "closed": _complete(cur, resolved)}


def record_run_failure(cur, run_id, message, created_by) -> None:
    """Raise/refresh the single run-level failure task."""
    existing = _open_tasks(cur, (FAILURE_TASK,))
    body = (
        "The NC purchase sync run failed, so NOTHING was imported in this pass — "
        "new orders, approvals and receipts from NC are all on hold until it "
        "succeeds again.\n\n"
        f"Error: {message}\n\n"
        "Check the Portal admin NC Purchase Sync panel for the run history. "
        "This task closes on its own after the next successful run."
    )
    _upsert_task(cur, existing, task_type=FAILURE_TASK, number=RUN_FAILURE_KEY,
                 title="NC purchase sync run failed", description=body,
                 run_id=run_id, created_by=created_by, priority="urgent")


def clear_run_failure(cur) -> int:
    """Close the run-level failure task after a successful run."""
    task_id = _open_tasks(cur, (FAILURE_TASK,)).get(RUN_FAILURE_KEY)
    return _complete(cur, [task_id] if task_id else [])
