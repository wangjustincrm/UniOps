"""One standing AP task for vendor + invoice numbers NC has on more than one payable.

Same shape as jv_validation_tasks.py, on purpose: raw psycopg2 over the AP
sync's own cursor, inside a SAVEPOINT in the run's transaction, ONE task that
is refreshed on every run and closes itself the moment nothing unreviewed is
left — so it can never outlive the condition it reports.

Assigned to ap_clerk, not system_admin as the JV task is: this is not a
bridge-plumbing fault, it is AP's work — the people who key payables into NC
are the ones who can stop a draft, hold a copy back from the payment run, or
chase the supplier for a refund. system_admin still sees it (the inbox hands
system_admin every task).

Every kind counts — finance's rule is that vendor + invoice number is unique,
whatever the amounts (2026-09-25). A deliberate split is cleared by reviewing
it as one, once; it is not left off the list. Only money still payable on a
repeated copy, or a draft about to be approved, makes the task urgent.
"""
from __future__ import annotations

import logging
import uuid
from datetime import date
from decimal import Decimal

from psycopg2.extras import register_uuid

from app.services import ap_duplicate_invoices as dup

register_uuid()

logger = logging.getLogger(__name__)

#: The subject is a set of NC payables, not a document finance owns, so the
#: inboxes resolve the link off this (epms/src/lib/taskTypes.ts and Portal's
#: PortalHome.tsx) the way they do for jv_validation.
DOC_TYPE = "ap_duplicate_invoice"
TASK_TYPE = "resolve_ap_duplicate_invoice"
ROLE = "ap_clerk"
TASK_KEY = "AP DUPLICATE INVOICES"

_TITLE_MAX = 255


def _money(by_ccy: dict[str, Decimal]) -> str:
    return ", ".join(f"{v:,.2f} {c}" for c, v in sorted(by_ccy.items()) if v)


def _describe(found: list[dup.Finding]) -> tuple[str, str, str]:
    """(title, description, priority) from the unreviewed actionable findings."""
    exposure: dict[str, Decimal] = {}
    for f in found:
        exposure[f.currency or "?"] = exposure.get(f.currency or "?", Decimal(0)) + f.open_exposure
    n_pending = sum(1 for f in found if f.kind == dup.PENDING)
    urgent = n_pending > 0 or any(v > 0 for v in exposure.values())

    if any(v > 0 for v in exposure.values()):
        title = (f"Duplicate invoices in NC: {_money(exposure)} is still payable "
                 f"on a second copy")
    elif n_pending:
        title = f"{n_pending} unapproved NC payables repeat an invoice number already entered"
    else:
        title = f"{len(found)} vendor invoice numbers are on more than one NC payable"

    body = [
        "A vendor's invoice number must be unique, and NC does not check it. Each "
        "group below is one vendor and one invoice number on more than one payable, "
        "not reviewed yet.",
        "",
    ]
    for kind in dup.ACTIONABLE:
        rows = [f for f in found if f.kind == kind]
        if not rows:
            continue
        extra: dict[str, Decimal] = {}
        for f in rows:
            extra[f.currency or "?"] = extra.get(f.currency or "?", Decimal(0)) + f.extra_amount
        tail = f" — {_money(extra)} billed twice" if any(extra.values()) else ""
        body.append(f"{dup.KIND_LABELS[kind]}: {len(rows)}{tail}")
    body += [
        "",
        "Open Finance → AP Duplicate Invoices. Stop a draft before it is approved; "
        "hold an approved copy back from the payment run; for a copy already paid, "
        "ask the supplier for a refund or credit. A deliberate split, or two genuinely "
        "different invoices, is marked reviewed with the reason.",
    ]
    return title[:_TITLE_MAX], "\n".join(body), ("urgent" if urgent else "normal")


def _open_task(cur):
    cur.execute("select id from tasks where document_type=%s and document_number=%s "
                "and is_completed is false limit 1", (DOC_TYPE, TASK_KEY))
    row = cur.fetchone()
    return row[0] if row else None


def _released(cur, outcome: str) -> str:
    cur.execute("release savepoint _ap_dup_invoice")
    return outcome


def raise_or_clear(cur, run_id, *, today: date | None = None) -> str:
    """Refresh the standing task from what the run just wrote.

    Returns 'raised' | 'refreshed' | 'closed' | 'clean' | 'failed'. Never raises
    and never poisons the caller's transaction — the sync wrote the mirror
    correctly and must still commit even if reporting on it fails."""
    cur.execute("savepoint _ap_dup_invoice")
    try:
        found = [f for f in dup.findings_sync(cur, today=today)
                 if f.kind in dup.ACTIONABLE and f.review is None]
        existing = _open_task(cur)
        if not found:
            if existing is None:
                return _released(cur, "clean")
            cur.execute("update tasks set is_completed=true, completed_at=now(), "
                        "updated_at=now() where id=%s", (existing,))
            return _released(cur, "closed")
        title, description, priority = _describe(found)
        if existing is not None:
            cur.execute("update tasks set title=%s, description=%s, priority=%s, "
                        "document_id=%s, updated_at=now() where id=%s",
                        (title, description, priority, run_id, existing))
            return _released(cur, "refreshed")
        cur.execute(
            "insert into tasks (id, type, priority, document_type, document_id, "
            "document_number, assigned_role, assigned_user_id, title, description, "
            "is_completed, created_at, updated_at) "
            "values (%s,%s,%s,%s,%s,%s,%s,null,%s,%s,false,now(),now())",
            (uuid.uuid4(), TASK_TYPE, priority, DOC_TYPE, run_id, TASK_KEY, ROLE,
             title, description))
        return _released(cur, "raised")
    except Exception:  # noqa: BLE001
        cur.execute("rollback to savepoint _ap_dup_invoice")
        cur.execute("release savepoint _ap_dup_invoice")
        logger.exception("AP duplicate-invoice task refresh failed (run %s)", run_id)
        return "failed"
