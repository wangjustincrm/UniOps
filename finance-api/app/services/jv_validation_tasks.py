"""One standing Admin Task for what the NC voucher sync could not place.

Shape borrowed from epms-api/app/services/nc_purchase_sync/error_tasks.py: raw
psycopg2 over the sync's own cursor, inside the same transaction as the mirror
write, so a rolled-back run raises nothing.

Deliberately ONE task, not one per finding. The findings are a standing
condition of the NC data — 927 lines in 2026 alone — and a task per line (or per
run) would bury the inbox within a day. The single task carries the counts in
its description and is refreshed on every run; it closes itself the moment the
period range comes back clean, so it can never outlive the condition it reports
(the Task Inbox's recurring failure mode).

Scope is the CURRENT and PREVIOUS fiscal year: older periods are closed books
whose vouchers nobody is going to re-enter, and flagging them forever would make
the task permanently unclosable.
"""
from __future__ import annotations

import logging
import uuid
from datetime import date

from psycopg2.extras import register_uuid

from app.services import jv_validation as val

# uuid.UUID -> Postgres uuid. nc_sync registers this too, but this module is
# also reached on its own (tests, and any future caller with its own cursor);
# an unregistered adapter fails at INSERT time with "can't adapt type 'UUID'".
register_uuid()

logger = logging.getLogger(__name__)

#: Not a document type finance owns — the subject is a set of voucher LINES —
#: so the inbox resolves the link off this (epms/src/lib/taskTypes.ts), the same
#: way nc_sync tasks do.
DOC_TYPE = "jv_validation"
TASK_TYPE = "resolve_jv_validation"

#: Broadcast to system_admin, matching the NC purchase sync's error tasks: the
#: NC↔UniOps bridge has no single owner, and crud.task.get_for_role hands
#: system_admin holders every task anyway.
ADMIN_ROLE = "system_admin"

#: There is at most one of these open, keyed by this number.
TASK_KEY = "JV VALIDATION"

_TITLE_MAX = 255


def scope_periods(today: date) -> tuple[str, str]:
    """(period_from, period_to) — this year and last."""
    return f"{today.year - 1}-01", f"{today.year}-12"


def _describe(found: dict) -> tuple[str, str]:
    """(title, description) from a {rule: {...}} summary. Amounts are DEBIT:
    the Budget Dashboard's actual is gross debit, so debit is what went missing
    from it."""
    dropped = {r: v for r, v in found.items() if r in val.DROPPED_RULES}
    drop_lines = sum(v["lines"] for v in dropped.values())
    drop_debit = sum((v["debit"] for v in dropped.values()), start=0)
    other = sum(v["lines"] for r, v in found.items() if r not in val.DROPPED_RULES)

    if drop_lines:
        title = (f"{drop_lines} posted voucher lines ({drop_debit:,.2f} CAD) are "
                 f"missing from the Budget Dashboard")
    else:
        title = f"{other} posted voucher lines have questionable budget dimensions"

    body = [
        "The NC voucher sync placed every line it could. These could not be "
        "placed, so the Budget Dashboard's NC actual does not include them — "
        "which is why a cost center can read 0 and why the cost centers no "
        "longer add up to the company total.",
        "",
    ]
    for rule in val.RULES:
        v = found.get(rule)
        if not v or not v["lines"]:
            continue
        mark = "DROPPED" if rule in val.DROPPED_RULES else "CHECK"
        body.append(f"[{mark}] {val.RULE_LABELS[rule]}: "
                    f"{v['lines']} lines, {v['debit']:,.2f} CAD debit "
                    f"({v['vouchers']} vouchers)")
    body += [
        "",
        "Open Finance → JV Validation for the line-by-line list. The fix is "
        "normally one of: add the (account, department, cost center) row to the "
        "budget-actual cost center map, add the missing income-expense code to "
        "the budget account catalog, or correct the voucher in NC.",
    ]
    return title[:_TITLE_MAX], "\n".join(body)


def _open_task(cur):
    cur.execute("select id from tasks where document_type=%s and document_number=%s "
                "and is_completed is false limit 1", (DOC_TYPE, TASK_KEY))
    row = cur.fetchone()
    return row[0] if row else None


def _released(cur, outcome: str) -> str:
    cur.execute("release savepoint _jv_validation")
    return outcome


def raise_or_clear(cur, run_id, *, today: date | None = None) -> str:
    """Refresh the standing validation task from the data the run just wrote.

    Returns what it did: 'raised' | 'refreshed' | 'closed' | 'clean' | 'failed'.

    Never raises, and never poisons the caller's transaction: everything here
    runs inside a SAVEPOINT, because a failure after an aborted statement would
    take the whole sync down with it ("current transaction is aborted" on the
    next statement) — the run wrote the vouchers correctly and must still
    commit. Reporting is allowed to fail; the sync is not."""
    cur.execute("savepoint _jv_validation")
    try:
        period_from, period_to = scope_periods(today or date.today())
        found = {r: v for r, v in val.summary_sync(cur, period_from, period_to).items()
                 if v["lines"]}
        existing = _open_task(cur)
        if not found:
            if existing is None:
                return _released(cur, "clean")
            cur.execute("update tasks set is_completed=true, completed_at=now(), "
                        "updated_at=now() where id=%s", (existing,))
            return _released(cur, "closed")
        title, description = _describe(found)
        if existing is not None:
            cur.execute("update tasks set title=%s, description=%s, document_id=%s, "
                        "updated_at=now() where id=%s",
                        (title, description, run_id, existing))
            return _released(cur, "refreshed")
        cur.execute(
            "insert into tasks (id, type, priority, document_type, document_id, "
            "document_number, assigned_role, assigned_user_id, title, description, "
            "is_completed, created_at, updated_at) "
            "values (%s,%s,'normal',%s,%s,%s,%s,null,%s,%s,false,now(),now())",
            (uuid.uuid4(), TASK_TYPE, DOC_TYPE, run_id, TASK_KEY, ADMIN_ROLE,
             title, description))
        return _released(cur, "raised")
    except Exception:  # noqa: BLE001
        cur.execute("rollback to savepoint _jv_validation")
        cur.execute("release savepoint _jv_validation")
        logger.exception("JV validation task refresh failed (run %s)", run_id)
        return "failed"
