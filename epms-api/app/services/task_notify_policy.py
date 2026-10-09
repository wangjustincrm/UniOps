"""Per-task-type notification policy (Portal → Admin → Notifications).

One row per task type: whether a new task of that type emails its holder, and
when — the moment it is created ("immediate") or bundled into the holder's
once-a-day digest ("digest"). Stored in company_config.notification_settings
under ``task_notifications`` as {task_type: {"email": bool, "mode": str}};
a type with no stored row falls back to DEFAULT_IMMEDIATE below.

The list of task types is DERIVED, never typed out here, so a new type shows
up on the admin page without anyone remembering to add it:
  * crud.task's three registries — every type epms-api can emit (an AST test
    keeps them complete), plus the approval-api / vms-api types registered there
    by hand;
  * OA expense-claim types — approval-api names them approve_<claim type> /
    revise_<claim type>, open-ended because every custom form adds one;
  * whatever types already exist in the tasks table — covers the services the
    registries cannot see (finance-api's resolve_* tasks).
"""
from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

SETTINGS_KEY = "task_notifications"
MODES = ("immediate", "digest")

# The types that were already being emailed the moment they were created when
# this policy was introduced (prod, 2026-10-08: each emailed for most of its
# tasks over the previous 30 days, from an explicit notify call on its creation
# path). Defaulting these ON keeps those emails going through the switch-over;
# everything else starts OFF and is the admin's to turn on.
DEFAULT_IMMEDIATE: frozenset[str] = frozenset({
    "approve_pr", "revise_pr", "create_po",
    "approve_po", "revise_po", "place_order", "create_prepayment_pa",
    "sign_po", "revise_po_signoff",
    "acknowledge_gr", "collect_goods", "confirm_service_gr", "gr_damage_report",
    "match_invoice", "review_match", "resolve_exception", "confirm_receipt",
})

# Types whose creator already sends a dedicated email of its own and stamps the
# task as notified, so this policy never applies to them. Shown on the admin
# page with the reason instead of a checkbox that would do nothing.
SELF_NOTIFIED: dict[str, str] = {
    "chase_agreement_invoice":
        "Emailed by the agreement overdue check when a billing period goes overdue.",
}

_BUILT_IN_CLAIM_TYPES = ("exp", "mil", "trv")

_ABBREV = {
    "pr", "po", "pa", "gr", "agr", "exp", "mil", "trv", "cfm", "vms", "ppe",
    "erp", "nc", "jv", "ap",
}

_MODULE_BY_TOKEN = (
    ({"vms", "visitor", "ppe", "training"}, "VMS"),
    ({"exp", "mil", "trv", "cfm", "expense"}, "OA"),
    ({"budget"}, "Budget"),
    ({"jv", "nc", "duplicate", "erp"}, "Finance"),
)


@dataclass(frozen=True)
class TaskTypePolicy:
    task_type: str
    label: str
    module: str
    email: bool
    mode: str
    default_email: bool
    note: str | None = None

    def as_dict(self) -> dict:
        return {
            "task_type": self.task_type, "label": self.label, "module": self.module,
            "email": self.email, "mode": self.mode,
            "default_email": self.default_email, "note": self.note,
        }


def label_for(task_type: str) -> str:
    """approve_pr → "Approve PR", revise_cfm_travel → "Revise CFM Travel"."""
    return " ".join(
        w.upper() if w in _ABBREV else w.capitalize()
        for w in task_type.split("_") if w
    )


def module_for(task_type: str) -> str:
    tokens = set(task_type.split("_"))
    for keys, module in _MODULE_BY_TOKEN:
        if tokens & keys:
            return module
    return "Procurement"


def stored_policies(notif_settings: dict | None) -> dict[str, dict]:
    raw = (notif_settings or {}).get(SETTINGS_KEY)
    return raw if isinstance(raw, dict) else {}


def effective_policy(notif_settings: dict | None, task_type: str) -> tuple[bool, str]:
    """(email on?, mode) for one task type — stored row, else the default."""
    if task_type in SELF_NOTIFIED:
        return False, "immediate"
    row = stored_policies(notif_settings).get(task_type)
    if isinstance(row, dict):
        mode = row.get("mode") if row.get("mode") in MODES else "immediate"
        return bool(row.get("email")), mode
    return task_type in DEFAULT_IMMEDIATE, "immediate"


async def known_task_types(db: AsyncSession) -> list[str]:
    from app.crud.task import (
        TASK_LIVENESS, _TASK_DEDICATED_HANDLING, _TASK_NO_STATUS_INVARIANT,
    )

    types: set[str] = set(TASK_LIVENESS) | set(_TASK_DEDICATED_HANDLING) | set(_TASK_NO_STATUS_INVARIANT)

    claim_codes = list(_BUILT_IN_CLAIM_TYPES)
    # expense-api's table, same physical DB in every deployment — but not in
    # epms-api's own test schema, hence the existence check.
    has_forms = (await db.execute(text(
        "SELECT to_regclass('custom_form_definitions') IS NOT NULL"
    ))).scalar()
    form_codes = (await db.execute(text(
        "SELECT code FROM custom_form_definitions"
    ))).scalars().all() if has_forms else []
    claim_codes += [f"cfm_{c.lower()}" for c in form_codes if c]
    for code in claim_codes:
        types.add(f"approve_{code}")
        types.add(f"revise_{code}")

    seen = (await db.execute(text("SELECT DISTINCT type FROM tasks"))).scalars().all()
    types.update(t for t in seen if t)
    return sorted(types)


async def list_policies(db: AsyncSession, notif_settings: dict | None) -> list[TaskTypePolicy]:
    out: list[TaskTypePolicy] = []
    for t in await known_task_types(db):
        email, mode = effective_policy(notif_settings, t)
        out.append(TaskTypePolicy(
            task_type=t, label=label_for(t), module=module_for(t),
            email=email, mode=mode, default_email=t in DEFAULT_IMMEDIATE,
            note=SELF_NOTIFIED.get(t),
        ))
    out.sort(key=lambda p: (p.module, p.label))
    return out


def validate_update(rows: list[dict]) -> dict[str, dict]:
    """Normalise an admin save into the stored shape; raises ValueError."""
    stored: dict[str, dict] = {}
    for row in rows:
        t = str(row.get("task_type") or "").strip()
        if not t:
            raise ValueError("task_type is required")
        mode = row.get("mode", "immediate")
        if mode not in MODES:
            raise ValueError(f"{t}: mode must be one of {', '.join(MODES)}")
        if t in SELF_NOTIFIED:
            continue
        stored[t] = {"email": bool(row.get("email")), "mode": mode}
    return stored
