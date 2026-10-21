"""The guidance half: what is waiting for this person, and how a process works.

This is the need that came first — a lot of people were never trained on the
system, so the assistant is where they learn it — and it was the last thing
built. Two questions it answers:

  "What should I be doing?"  → their own open tasks
  "How does purchasing work?" → the chain as the engine actually defines it

The second deliberately reads the approval engine's configuration rather than
any document. The PRDs describing these flows are two to four months behind
production; a workflow generated from the live config cannot drift, because it
IS what runs.
"""
import uuid

from app.crud import task as task_crud
from app.crud.current_step import role_label
from app.services import approval_client, doc_type_guide

# doc_type -> how to say it to a person
# Every chain approval-api's /workflows/{doc_type} read-out serves. Adding one
# here is all it takes for the Assistant to explain it (the planner's tool enum
# and the "I can describe…" reply both read this).
#
# Not "pa_dir": OA's Direct Payment Application is retired (expense-api
# api/v1/pa.py DIRECT_PA_RETIRED refuses creation), though approval-api still
# serves its chain for the PAs that already exist. Listing it here had the
# Assistant describe how to get one approved (ISS-0065); pa_types.yaml rule
# direct_pa_retired answers the question instead.
PROCESS_LABELS = {
    "pr": "Purchase Request",
    "po": "Purchase Order",
    "posign": "PO sign-off",
    "agr": "Purchase Agreement",
    "pa": "Payment Application",
    "pa_frt": "Freight Payment Application",
    "exp": "Expense Claim",
    "mil": "Mileage Claim",
    "trv": "Travel Expense",
    "tra": "Travel Application",
    "budget_plan": "Budget Plan",
    "vms_visit": "Visitor Request",
}
SUPPORTED_PROCESSES = tuple(PROCESS_LABELS)


# The Task Inbox's group headers, copied from epms/src/lib/taskTypes.ts
# TASK_TYPE_LABELS (the inbox groups by type and labels with
# `TASK_TYPE_LABELS[k] ?? k`). tests/test_assistant_iss_round2.py fails when the
# two drift, so "Confirm Goods Receipt" here is the header the person sees.
TASK_TYPE_LABELS: dict[str, str] = {
    "create_pr": "Create Purchase Request",
    "create_po": "Create Purchase Order",
    "create_pa": "Create Payment Application",
    "create_prepayment_pa": "Create Prepayment PA",
    "approve_pr": "Approve Purchase Request",
    "approve_po": "Approve Purchase Order",
    "approve_pa": "Approve Payment Application",
    "place_order": "Place Order",
    "confirm_settlement": "Confirm Settlement",
    "review_match": "Confirm Invoice Match",
    "match_invoice": "Match Invoice",
    "resolve_exception": "Resolve Match Exception",
    "attach_receipts": "Attach Invoice Receipts",
    "process_pa": "Process Payment Application",
    "approve_agr": "Approve Agreement",
    "revise_agr": "Revise Agreement",
    "confirm_period": "Confirm Service Period",
    "chase_agreement_invoice": "Chase Missing Invoice",
    "confirm_receipt": "Confirm Goods Receipt",
    "check_out_visitor": "Check Out Visitor",
    "prepare_ppe": "Prepare PPE",
    "revise_pr": "Revise Purchase Request",
    "revise_po": "Revise Purchase Order",
    "revise_pa": "Revise Payment Application",
    "acknowledge_gr": "Acknowledge Goods Receipt",
    "collect_goods": "Collect Goods",
    "confirm_service_gr": "Confirm Service Receipt",
    "gr_damage_report": "Report Goods Damage",
    "approve_budget_plan": "Approve Budget Plan",
    "revise_budget_plan": "Revise Budget Plan",
    "sign_po": "Sign Purchase Order",
    "revise_po_signoff": "Revise PO Sign-off",
    "import_erp_vendor": "Import ERP Vendor",
    "resolve_nc_sync_error": "Resolve NC Sync Error",
    "resolve_jv_validation": "Resolve JV Validation Findings",
    "resolve_ap_duplicate_invoice": "Resolve Duplicate NC Invoices",
}

# How many tasks go to the narrator as examples. The COUNTS cover every task;
# the narrator never counts (ISS-0011: given 365 tasks it produced group counts
# that changed on every answer and added up to 237).
_TASK_EXAMPLES = 12


def task_summary(tasks: list[dict]) -> dict:
    """Counts the narrator reports verbatim, plus the few tasks worth naming.

    Grouped exactly as the Task Inbox groups them, so every number in the
    reply can be checked against a header on that page.
    """
    by_type: dict[str, int] = {}
    for t in tasks:
        by_type[t["type"]] = by_type.get(t["type"], 0) + 1
    groups = sorted(
        ({"type": k, "label": TASK_TYPE_LABELS.get(k, k), "count": n}
         for k, n in by_type.items()),
        key=lambda g: (-g["count"], g["label"]))
    urgent = [t for t in tasks if t.get("priority") == "urgent"]
    # Urgent first, then the oldest — the ones most likely to be overdue.
    ordered = sorted(tasks, key=lambda t: (t.get("priority") != "urgent",
                                           str(t.get("due_date") or "9999"),
                                           str(t.get("created_at") or "")))
    examples = ordered[:_TASK_EXAMPLES]
    # Overdue is counted from the tasks' due dates, never inferred from
    # "urgent": one urgent duplicate-invoice task was reported as "1 overdue,
    # 381 on time" when no task in EPMS carries a due date at all (ISS-0049).
    from app.core.delegation import local_today
    today = local_today()
    with_due = [t for t in tasks if t.get("due_date")]
    overdue = [t for t in with_due if _as_date(t["due_date"]) < today]
    return {
        "open_task_count": len(tasks),
        "urgent_count": len(urgent),
        "normal_count": len(tasks) - len(urgent),
        "groups": groups,
        "groups_add_up_to": sum(g["count"] for g in groups),
        # "Why is this mine?" for the whole list, counted rather than read off
        # the examples — a live check generalised 12 examples into "these are
        # all assigned to you personally" when half came through a role.
        "assigned_to_me_personally_count": sum(
            1 for t in tasks if t.get("assigned_to_me_personally")),
        "reached_me_through_a_role_count": sum(
            1 for t in tasks if not t.get("assigned_to_me_personally")),
        "with_due_date_count": len(with_due),
        "overdue_count": len(overdue),
        "examples": examples,
        "examples_shown": len(examples),
        "examples_left_out": len(tasks) - len(examples),
        # Overdue, urgent, closing a task — the registry's tasks rules.
        "rules": _task_rules(),
    }


def _task_rules() -> list[dict]:
    from app.services import rule_registry
    return rule_registry.static_rules("tasks")


def _as_date(value):
    from datetime import date, datetime
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    return date.fromisoformat(str(value)[:10])


async def my_tasks(role: str, user_id: uuid.UUID) -> list[dict]:
    """This person's open tasks, exactly as their inbox computes them.

    Calls the inbox's own get_for_role rather than re-deriving "assigned to me":
    a task reaches someone through a personal assignment, through any role they
    hold, or through a delegation, and a second implementation of that would
    start disagreeing with the inbox about what they owe.

    It runs on its own session for two reasons. The controlled-query path sets
    the transaction READ ONLY, and get_for_role performs the inbox's self-healing
    backfills — which are writes. Sharing a session would either break those or
    break the query layer's guarantee.
    """
    from app.db.session import AsyncSessionLocal

    async with AsyncSessionLocal() as db:
        tasks = await task_crud.get_for_role(db, role=role, user_id=user_id,
                                             is_completed=None)
        return [{
            "type": t.type,
            "document_type": t.document_type,
            "document_number": t.document_number,
            "title": t.title,
            "description": t.description,
            "priority": t.priority,
            "due_date": t.due_date,
            "amount": str(t.amount) if t.amount is not None else None,
            "vendor": t.vendor,
            # A task can reach someone through a role rather than by name; saying
            # which makes "why is this mine?" answerable.
            "assigned_to_me_personally": t.assigned_user_id == user_id,
            "assigned_role": role_label(t.assigned_role) if t.assigned_role else None,
            "created_at": t.created_at,
        } for t in tasks]


def approval_rules() -> list[dict]:
    """How approvals are given — true of every chain. The rule registry's
    approve_only_in_the_system (knowledge/rules/approval_engine.yaml)."""
    from app.services import rule_registry

    return [r for r in rule_registry.static_rules("approval_engine")
            if r["id"] == "approve_only_in_the_system"]


async def process_steps(doc_type: str, token: str) -> dict:
    """The configured approval chain for a kind of document.

    Read from the engine, never from documentation. Steps marked optional are
    skipped per-document at runtime (a department with no director, an
    under-budget PR), so the list is the shape of the process rather than a
    promise about any one document.
    """
    steps: list[dict] = []
    over_budget = None
    available = True
    try:
        status, body = await approval_client.forward(
            "GET", f"/workflows/{doc_type}", token)
        if status >= 400:
            available = False
        else:
            steps = (body or {}).get("steps", []) if isinstance(body, dict) else []
            over_budget = (body or {}).get("over_budget") if isinstance(body, dict) else None
    except Exception:  # noqa: BLE001
        # forward() lets httpx errors through rather than wrapping them, so a
        # narrower except let a ConnectError escape and turn "the engine is
        # down" into a 500. Any failure here means the same thing: the chain
        # could not be read, and saying so beats describing one from memory.
        available = False

    return {
        "doc_type": doc_type,
        "label": PROCESS_LABELS.get(doc_type, doc_type.upper()),
        "steps": [{
            "index": i + 1,
            "role": s.get("role"),
            "label": s.get("label") or role_label(s.get("role") or ""),
            # The engine annotates these; the stored definition carries no such
            # flag, so reading one off it reported every step as mandatory and
            # the assistant told a requester a PR always needs a Director.
            "conditional": bool(s.get("conditional")),
            "condition": s.get("condition"),
        } for i, s in enumerate(steps)],
        "steps_available": available,
        "how_approvals_are_given": approval_rules(),
        # PR only — the over-budget rule as Budget Config stands, from the
        # engine. Without it "现在超预算的审批模式是什么？" had nothing to read
        # and was answered from a 0-row query plus guesswork (ISS-0005).
        **({"over_budget": {
            "mode": over_budget.get("mode"),
            "mode_label": over_budget.get("mode_label"),
            "added_steps": [s.get("label") for s in over_budget.get("added_steps") or []],
            "blocks_submission": over_budget.get("blocks_submission"),
            "justification_required": over_budget.get("justification_required"),
            "note": over_budget.get("note"),
        }} if over_budget else {}),
        # The PR rules about over-budget, the same ones explain_document_types
        # carries. An over-budget question reaches either route, and on this
        # one it used to get the mode alone, so "do consumables need a budget
        # check?" was answered from the mode name (ISS-0025).
        **({"rules": over_budget_rules()} if doc_type == "pr" else {}),
    }


def over_budget_rules() -> list[dict]:
    return [r for r in doc_type_guide.build("pr")["rules"]
            if r.get("topic") == "over_budget"]
