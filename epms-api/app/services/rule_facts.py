"""The facts a `derived` rule card states, read from the running system.

Each function takes the request's session and returns the values for the
card's {placeholders}. They read the same constants, schemas and settings the
behaviour itself reads — a setting changed in Portal, a field added to a
schema, a window shortened in code changes the Assistant's answer with it.
"Production has the service-completion reminder on and live" is a fact only
the configuration row knows (ISS-0063), and a rule that typed it in would be
out of date the next time an admin touched the switch.

Reads only: a plain SELECT of the config row, never get_or_create, which
writes when the row is missing.
"""
from __future__ import annotations

from sqlalchemy import select

from app.models.config import CompanyConfig


async def _config(db) -> CompanyConfig | None:
    return (await db.execute(select(CompanyConfig).limit(1))).scalar_one_or_none()


def _join(words: list[str]) -> str:
    words = list(words)
    if len(words) <= 1:
        return "".join(words)
    return ", ".join(words[:-1]) + " and " + words[-1]


# ── NC sync ──────────────────────────────────────────────────────────────────

async def nc_sync_schedule(db) -> dict:
    from app.tasks.nc_purchase_sync_scheduler import (
        MAX_INTERVAL_MINUTES, resolve_interval_minutes)

    cfg = await _config(db)
    minutes = resolve_interval_minutes(cfg.nc_purchase_sync_interval_minutes if cfg else None)
    schedule = (f"automatically every {minutes} minutes" if minutes > 0 else
                "only when someone presses Sync NC — the automatic schedule is "
                "switched off (interval 0)")
    return {"schedule": schedule, "max_minutes": MAX_INTERVAL_MINUTES}


def nc_statuses(db) -> dict:
    """What nc_pending / nc_milk mean — the ontology's own page name and note,
    the same text a query result is read back with."""
    from app.core.ontology import REGISTRY

    status = REGISTRY["purchase_order"].fields["status"]
    labels, notes = dict(status.value_labels), dict(status.value_notes)
    return {k: f"{labels[k]}: {notes[k]}" for k in ("nc_milk", "nc_pending")}


# ── Purchase orders ──────────────────────────────────────────────────────────

# Words for the Edit Details fields. Every field of the schema must have one —
# tests/test_rule_registry.py fails otherwise, so a field added to the form
# reaches the Assistant's answer or the build breaks.
IMPORTED_HEADER_WORDS = {
    "expected_delivery": "Expected Delivery",
    "delivery_address": "Delivery Address",
    "incoterms_code": "Incoterms (required on raw-material orders)",
    "incoterms": "Incoterms named place",
    "tax_code": "Tax Code",
    "tax_rate": "Tax Rate (CAD orders only)",
    "is_prepaid": "the Prepaid flag",
    "buyer_notes": "Buyer Notes / T&C",
}
IMPORTED_LINE_WORDS = {"supplier_item_id": "Supplier Item ID", "sample": "Sample"}
# Schema keys that carry the line lists themselves rather than a field.
_IMPORTED_LIST_KEYS = {"lines", "manual_lines"}


def po_imported_fields(db) -> dict:
    from app.schemas.po import PoImportedDetailsUpdate, PoImportedLineUpdate

    header = [IMPORTED_HEADER_WORDS[f] for f in PoImportedDetailsUpdate.model_fields
              if f not in _IMPORTED_LIST_KEYS]
    line = [IMPORTED_LINE_WORDS[f] for f in PoImportedLineUpdate.model_fields if f != "id"]
    return {"header_fields": _join(header), "line_fields": _join(line)}


# ── Goods receipts ───────────────────────────────────────────────────────────

def gr_po_statuses(db) -> dict:
    from app.api.v1.gr import PHYSICAL_GR_PO_STATUSES, SERVICE_GR_PO_STATUSES

    return {"physical": _join(list(PHYSICAL_GR_PO_STATUSES)),
            "service": _join(list(SERVICE_GR_PO_STATUSES))}


async def service_gr_reminders(db) -> dict:
    from app.tasks import service_gr_due as due

    cfg = await _config(db)
    sla = (cfg.service_gr_sla if cfg else None) or {}
    ns = (cfg.notification_settings if cfg else None) or {}
    # The sweep's own fallbacks (service_gr_due.py reads them the same way).
    reminder = int(sla.get("reminder_days", due._DEFAULT_REMINDER_DAYS))
    escalation = int(sla.get("manager_escalation_days", due._DEFAULT_MANAGER_ESCALATION_DAYS))
    enabled = bool(ns.get("service_gr_due_enabled", False))
    dry_run = bool(ns.get("service_gr_due_dry_run", True))
    if not enabled:
        state = ("switched off (Portal → Admin → Notification Settings → Service "
                 "Completion Reminder), so no such reminders are being sent")
    elif dry_run:
        state = ("switched on in dry run: the list of overdue orders is emailed to "
                 "the System Admins only, and no owner or manager is reminded yet")
    else:
        state = ("switched on and live: owners and their managers are being "
                 "reminded")
    return {"reminder_days": reminder, "escalation_days": escalation, "state": state,
            "time": (ns.get("followup_time") or "08:00")}


async def notification_switches(db) -> dict:
    """Portal → Notification Settings switches the Assistant states (ISS-0137).
    Fallbacks are the consumers' own: agreement_overdue.run_agreement_overdue
    reads True, notification.send_pa_approved reads True (PRD PA-007)."""
    cfg = await _config(db)
    ns = (cfg.notification_settings if cfg else None) or {}
    pa_on = bool(ns.get("pa_approved_notify_requester", True))
    overdue_on = bool(ns.get("agreement_overdue_enabled", True))
    return {
        "pa_approved": ("switched on, so the requester is emailed when the PA is "
                        "fully approved" if pa_on else
                        "switched off, so the requester is not emailed"),
        "agreement_overdue": ("switched on" if overdue_on else
                              "switched off, so no overdue email or chase task is "
                              "raised (the period is still marked overdue)"),
    }


async def gr_ack_reminders(db) -> dict:
    """GR acknowledgement reminder (ISS-0146 ①), read the way gr_ack_due reads it."""
    from app.tasks import gr_ack_due as due

    cfg = await _config(db)
    sla = getattr(cfg, "gr_notification_sla", None) or {}
    ns = getattr(cfg, "notification_settings", None) or {}
    enabled = bool(ns.get("gr_ack_due_enabled", False))
    dry_run = bool(ns.get("gr_ack_due_dry_run", True))
    if not enabled:
        state = "switched off, so no such reminders are being sent"
    elif dry_run:
        state = ("switched on in dry run: the list is emailed to the System Admins "
                 "only, and nobody is reminded yet")
    else:
        state = "switched on and live"
    return {"reminder_days": int(sla.get("reminder_days", due._DEFAULT_REMINDER_DAYS)),
            "escalation_days": int(sla.get("manager_escalation_days",
                                           due._DEFAULT_MANAGER_ESCALATION_DAYS)),
            "state": state, "time": ns.get("followup_time") or "08:00"}


async def budget_alerts(db) -> dict:
    """Budget threshold alert emails (ISS-0146 ③), read the way
    tasks/budget_alerts reads them."""
    cfg = await _config(db)
    ns = getattr(cfg, "notification_settings", None) or {}
    bac = getattr(cfg, "budget_admin_config", None) or {}
    if not ns.get("budget_alert_enabled", False):
        state = "switched off, so no budget alerts are sent"
    elif ns.get("budget_alert_dry_run", True):
        state = ("switched on in dry run: the list goes to the System Admins only, "
                 "nobody else is emailed yet")
    else:
        state = "switched on and live"
    return {"state": state, "warning": bac.get("yellow_threshold_pct", 80),
            "over": bac.get("red_threshold_pct", 100),
            "time": ns.get("followup_time") or "08:00"}


async def collection_gate(db) -> dict:
    """Admin → Collection Config "Collection Confirmation Required" (ISS-0146),
    read the way crud.po.collection_gate_on reads it."""
    cfg = await _config(db)
    cc = getattr(cfg, "collection_config", None) or {}
    on = bool(cc.get("collection_gate_enabled", False))
    return {"state": ("switched on: a physical PO's PA waits until its goods are collected"
                      if on else
                      "switched off: a PA is allowed as soon as a goods receipt is recorded"),
            "reminder_days": cc.get("reminder_days", 2),
            "manager_days": cc.get("manager_escalation_days", 4),
            "fm_days": cc.get("fm_alert_days", 7)}


# ── Payment applications and invoices ────────────────────────────────────────

def pa_invoice_window(db) -> dict:
    from app.api.v1.pa import PA_INVOICE_DUE_WINDOW_DAYS

    return {"window": PA_INVOICE_DUE_WINDOW_DAYS}


async def invoice_match_tolerance(db) -> dict:
    from app.crud.invoice import _match_tolerance_pct

    pct = await _match_tolerance_pct(db)
    return {"pct": f"{pct.normalize():f}" if pct is not None else "0"}


# ── Notifications ────────────────────────────────────────────────────────────

def reminder_rules(db) -> dict:
    from app.services.manual_reminder import APPROVAL_TASK_TYPES, COOLDOWN_HOURS

    words = {"pr": "purchase requests", "po": "purchase orders"}
    return {"hours": COOLDOWN_HOURS,
            "documents": _join([words.get(k, k) for k in APPROVAL_TASK_TYPES])}


def _policy_words(ns: dict, task_type: str) -> str:
    from app.services.task_notify_policy import effective_policy

    email, mode = effective_policy(ns, task_type)
    if not email:
        return "no email"
    return "an email at once" if mode == "immediate" else "a line in the daily digest email"


async def approval_email_policy(db) -> dict:
    from app.services.task_notify_policy import DEFAULT_IMMEDIATE

    cfg = await _config(db)
    ns = (cfg.notification_settings if cfg else None) or {}
    return {
        "approve_pr": _policy_words(ns, "approve_pr"),
        "approve_po": _policy_words(ns, "approve_po"),
        "approve_pa": _policy_words(ns, "approve_pa"),
        "approve_pa_default": "on" if "approve_pa" in DEFAULT_IMMEDIATE else "off",
        "channel": ns.get("default_channel") or "email_only",
    }


async def digest_time(db) -> dict:
    cfg = await _config(db)
    ns = (cfg.notification_settings if cfg else None) or {}
    return {"time": ns.get("followup_time") or "08:00"}


# ── Reports ──────────────────────────────────────────────────────────────────

REPORT_WORDS = {
    "/reports/pr": "Purchase Requisitions",
    "/reports/po": "Purchase Orders",
    "/reports/gr": "Goods Receipts",
    "/reports/invoices": "Invoices",
    "/reports/pa": "Payment Applications",
    "/reports/budget": "Budget Accounts",
    "/reports/vendors": "Vendors",
}


def report_types(db) -> dict:
    """The Report Centre's exports, read off the router that serves them."""
    from app.api.v1.reports import router

    paths = [r.path for r in router.routes]
    names = [REPORT_WORDS[p] for p in paths]
    return {"count": len(names), "reports": _join(names)}
