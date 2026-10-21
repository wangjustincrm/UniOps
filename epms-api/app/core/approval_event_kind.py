"""What an approval event really was, derived from what the engine wrote.

approval-api records a step it skips as ``action="approve"`` with the comment
``"Auto-skipped (<reason>)"``, and as its actor whoever happened to trigger the
skip — the submitter (leading steps skipped at submit), the person who approved
the step before (a Director step skipped because the department has none), or
the requester (``"Re-synced (config change): Auto-skipped (...)"`` after a
Budget Config change; ``engine._routing_user_id``). The PMS import backfilled
missing approvals the same way, under the import account. Read literally, the
History tab then says "Approve by finance manager — <the requester>" (ISS-0077).

The rows are not rewritten as ``action="skip"``: more than a dozen readers key
on ``action == "approve"`` (PDF signatories, GR/invoice/receipt reports,
expense-api's claim PDF, budget-api, the detail-page timelines, which treat a
step as passed when it has an approve event). The meaning is derived here
instead, once, when events are read, and every reader asks this module rather
than matching comment text itself.
"""
from typing import Literal

SKIP_MARKER = "Auto-skipped"
# PMS migration: "Auto-approved on behalf of <role> — PMS migration (...)".
IMPORT_MARKER = "Auto-approved on behalf of"
# One person holds two consecutive steps; the engine approves the second for
# them. A real person's decision (PRD: recorded as that person) — not machine.
SAME_APPROVER_MARKER = "Auto-approved (same approver"

# Approvals no person decided at that step: their actor is not the approver,
# so a PDF must not print the name and History must not show it.
MACHINE_APPROVAL_MARKERS = (SKIP_MARKER, IMPORT_MARKER)

EventKind = Literal["manual", "skipped", "imported", "same_approver"]


def is_machine_approval(comment: str | None) -> bool:
    text = comment or ""
    return any(marker in text for marker in MACHINE_APPROVAL_MARKERS)


def event_kind(action: str | None, comment: str | None) -> EventKind:
    """``skipped`` — the engine skipped the step (no one approved it);
    ``imported`` — an approval backfilled by the PMS import;
    ``same_approver`` — auto-approved for the person who holds both steps;
    ``manual`` — everything else: a person's own submit/approve/return/...
    Only approve events are ever machine-written."""
    if action != "approve":
        return "manual"
    text = comment or ""
    if SKIP_MARKER in text:
        return "skipped"
    if IMPORT_MARKER in text:
        return "imported"
    if SAME_APPROVER_MARKER in text:
        return "same_approver"
    return "manual"


def skip_reason(comment: str | None) -> str | None:
    """Why a step was skipped, without the marker.

    "Auto-skipped (department has no Director)" -> "department has no Director";
    "Re-synced (config change): Auto-skipped (over-budget ... Allow)" ->
    "over-budget ... Allow — Re-synced (config change)" (the prefix says when,
    so it is kept). None when the comment is not a skip.
    """
    text = comment or ""
    at = text.find(SKIP_MARKER)
    if at < 0:
        return None
    rest = text[at + len(SKIP_MARKER):].strip()
    if rest.startswith("(") and rest.endswith(")"):
        rest = rest[1:-1].strip()
    prefix = text[:at].strip().rstrip(":").strip()
    if prefix and rest:
        return f"{rest} — {prefix}"
    return rest or prefix or None
