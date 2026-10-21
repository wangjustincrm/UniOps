"""Approval Engine — unified workflow execution for all UniOps modules.

Supports action keys: pr, po, agr, pa, pa_dir, exp, mil, trv, tra, cfm, cfm_<code>, vms_visit
Each action key binds to a configurable workflow stored in CompanyConfig.workflow_defs.
"""
import uuid
from dataclasses import dataclass
from datetime import date, datetime, timezone
from typing import Any

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.crud.delegation import active_delegator_ids
from app.crud.workflow import (get_approval_setting, get_dept_director_mapping,
                                get_dept_gm_opm_mapping, get_dept_supervisor_enabled,
                                get_role_management, pa_department_ids, post_holder_ids)
from app.models.routing import CROSS_DEPT_GM_OR_OPM
from app.models.agreement import PurchaseAgreement
from app.models.budget_plan import BudgetPlan
from app.models.config import CompanyConfig
from app.models.event import ApprovalEvent
from app.models.expense import ExpenseClaim
from app.models.invoice import Invoice
from app.models.pa import PaymentApplication
from app.models.po import PurchaseOrder
from app.models.pr import PurchaseRequest
from app.models.task import Task
from app.models.user import User
from app.models.visit import Visit as VmsVisit
from app.schemas.action import ActionResult


# ── status_attr indirection ───────────────────────────────────────────────────
# Per S2_ARCHITECTURE_REVIEW.md F1: each doc_type's `_DOC_META` entry can
# specify a `status_attr` field name; the engine reads/writes through that
# attribute instead of hardcoded `doc.status`. Default is `"status"` so the
# existing PR/PO/PA/EXP/MIL/TRV/CFM/BudgetPlan doc_types behave unchanged
# (`getattr(doc, "status")` is identical to `doc.status` by Python semantics).
def _status_of(meta: dict, doc: Any) -> Any:
    return getattr(doc, meta.get("status_attr", "status"))


def _set_status(meta: dict, doc: Any, value: str) -> None:
    setattr(doc, meta.get("status_attr", "status"), value)


# The same indirection for the workflow cursor. A doc_type whose _DOC_META
# carries `step_attr` walks its own column instead of `approval_step_idx`,
# which is what lets a second, independent workflow run over a document that
# already has one (PO sign-off over an NC-imported PO: its `approval_step_idx`
# belongs to the PO's own approval chain and must not be touched). Default is
# "approval_step_idx", so every existing doc_type is unchanged.
def _step_of(meta: dict, doc: Any) -> int:
    # NULL = never submitted = step 0. vms_visits.approval_step_idx is nullable
    # with no default, and since execute_action started reading the step before
    # branching on the action (e9f39159), a NULL here raised TypeError on every
    # VMS submit — which vms-api then turned into "confirmed without approval".
    return getattr(doc, meta.get("step_attr", "approval_step_idx")) or 0


def _set_step(meta: dict, doc: Any, value: int) -> None:
    setattr(doc, meta.get("step_attr", "approval_step_idx"), value)


# ── Per-action-key metadata ───────────────────────────────────────────────────

def _expense_meta(claim_type: str) -> dict:
    """Build _DOC_META entry for OA expense claim types (EXP / MIL / TRV / CFM*)."""
    code = claim_type.lower()
    return {
        "model":        ExpenseClaim,
        "number_attr":  "claim_number",
        "amount_attr":  "total_amount",
        "vendor_attr":  "employee_name",
        "task_approve": f"approve_{code}",
        "task_revise":  f"revise_{code}",
        "valid_submit":  ("draft", "returned"),
        "valid_approve": ("submitted", "in_review"),
        "valid_return":  ("submitted", "in_review"),
        "valid_cancel":  ("draft", "returned"),
    }


_DOC_META: dict[str, dict] = {
    # ── EPMS procurement ──────────────────────────────────────────────────────
    "pr": {
        "model":        PurchaseRequest,
        "number_attr":  "number",
        "amount_attr":  "amount",
        "vendor_attr":  "vendor_name",
        "task_approve": "approve_pr",
        "task_revise":  "revise_pr",
        "valid_submit":  ("draft", "returned"),
        "valid_approve": ("submitted", "in_review"),
        "valid_return":  ("submitted", "in_review"),
        "valid_cancel":  ("draft", "returned", "submitted"),
    },
    "po": {
        "model":        PurchaseOrder,
        "number_attr":  "number",
        "amount_attr":  "total",
        "vendor_attr":  "vendor_name",
        "task_approve": "approve_po",
        "task_revise":  "revise_po",
        "valid_submit":  ("draft", "returned"),
        "valid_approve": ("submitted", "in_review"),
        "valid_return":  ("submitted", "in_review"),
        "valid_cancel":  ("draft", "returned", "submitted"),
    },
    # PO sign-off — a SECOND workflow over the same purchase_orders row.
    #
    # NC-imported POs used to be printed and signed by hand (Purchasing Manager
    # initials, OPM full signature) before anyone released them in NC. That
    # process now runs here. It cannot reuse doc_type "po": these POs never walk
    # the PO's own approval chain, and their `status` is rewritten from NC's
    # forderstatus on every sync. status_attr / step_attr point the engine at
    # the dedicated signoff_* columns instead, so the two workflows never
    # collide — including their tasks, which _complete_tasks scopes by
    # document_type.
    #
    # Steps are admin-configurable (Portal → Approval Workflows) like every
    # other action key. Only company-level singleton posts make sense here:
    # purchase_orders has no department column and an NC PO has no PR to borrow
    # one from, so dept_manager / director / supervisor / gm_or_opm resolve to
    # nobody at all. Portal filters the role dropdown accordingly.
    "posign": {
        "model":        PurchaseOrder,
        "number_attr":  "number",
        "amount_attr":  "total",
        "vendor_attr":  "vendor_name",
        "status_attr":  "signoff_status",
        "step_attr":    "signoff_step_idx",
        "revise_assignee_attr": "signoff_submitted_by",
        "labels": {"doc": "PO", "revise": "PO sign-off",
                   "approve_verb": "Sign", "approve_noun": "signature"},
        # NOT "approve_*": delegation, in both the engine and EPMS's task inbox
        # and document scope, keys off `type LIKE 'approve%'`. Keeping sign_po
        # outside that prefix keeps a stand-in from signing — a signature is
        # personal, and a delegate signing would stamp their own name and image
        # into the slot the workflow assigned to someone else. A signature can
        # be waited for; it cannot be covered.
        "task_approve": "sign_po",
        "task_revise":  "revise_po_signoff",
        "valid_submit":  ("draft", "returned"),
        "valid_approve": ("submitted", "in_review"),
        "valid_return":  ("submitted", "in_review"),
        "valid_cancel":  ("draft", "returned", "submitted"),
    },
    # ── EPMS purchase agreements ──────────────────────────────────────────────
    # Terminal approve flips status to "active" (not "approved") — an agreement
    # is an authorisation window, and "active" is what the invoice match
    # candidate pool filters on. Handled by _post_approve_agr below.
    "agr": {
        "model":        PurchaseAgreement,
        "number_attr":  "number",
        "amount_attr":  "not_to_exceed",
        "vendor_attr":  "vendor_name",
        "task_approve": "approve_agr",
        "task_revise":  "revise_agr",
        "valid_submit":  ("draft", "returned"),
        "valid_approve": ("submitted", "in_review"),
        "valid_return":  ("submitted", "in_review"),
        "valid_cancel":  ("draft", "returned", "submitted"),
    },
    # ── OA payment applications ───────────────────────────────────────────────
    "pa": {
        "model":        PaymentApplication,
        "number_attr":  "pa_number",
        "amount_attr":  "payment_amount",
        "vendor_attr":  "vendor_name",
        "task_approve": "approve_pa",
        "task_revise":  "revise_pa",
        "valid_submit":  ("draft", "returned"),
        "valid_approve": ("submitted", "in_review"),
        "valid_return":  ("submitted", "in_review"),
        "valid_cancel":  ("draft", "returned"),
    },
    "pa_dir": {
        "model":        PaymentApplication,
        "number_attr":  "pa_number",
        "amount_attr":  "payment_amount",
        "vendor_attr":  "vendor_name",
        "task_approve": "approve_pa",
        "task_revise":  "revise_pa",
        "valid_submit":  ("draft", "returned"),
        "valid_approve": ("submitted", "in_review"),
        "valid_return":  ("submitted", "in_review"),
        "valid_cancel":  ("draft", "returned"),
    },
    # ── OA expense claims ─────────────────────────────────────────────────────
    "exp": _expense_meta("EXP"),
    "mil": _expense_meta("MIL"),
    "trv": _expense_meta("TRV"),
    "tra": _expense_meta("TRA"),
    "cfm": _expense_meta("CFM"),
    # ── VMS visitor appointments (S2-B) ───────────────────────────────────────
    # Engine reads `approval_status`, not `status` — VMS owns a separate
    # `status: VisitStatus` enum for user-facing display. The post-approve
    # callback flips that column via raw SQL when all steps pass.
    "vms_visit": {
        "model":        VmsVisit,
        "number_attr":  "visit_title",   # populated by vms-api at submit
        "amount_attr":  None,            # visits have no money attached
        "vendor_attr":  None,
        "task_approve": "approve_vms_visit",
        "task_revise":  "revise_vms_visit",
        "status_attr":  "approval_status",
        # `returned` belongs in both, as for every other doc_type: a visit sent
        # back for edit is resubmitted (vms-api POST /visits/{id}/submit) or
        # cancelled by its creator. Without it a returned visit could do
        # neither — it sat in Pending Approval and its revise task never closed.
        "valid_submit":  ("draft", "returned"),
        "valid_approve": ("submitted", "in_review"),
        "valid_return":  ("submitted", "in_review"),
        "valid_cancel":  ("draft", "returned", "submitted", "in_review"),
        # Task titles read "Approve visitor visit: <visit_title> — <area, date>".
        # Without labels the doc label fell back to "VMS_VISIT".
        "labels": {"doc": "visitor visit", "revise": "visitor visit"},
    },
    # ── budget-api plans ──────────────────────────────────────────────────────
    "budget_plan": {
        "model":        BudgetPlan,
        "number_attr":  "plan_number",   # synthetic property (BP-FYxxxx)
        "amount_attr":  "plan_amount",   # synthetic property (Decimal(0))
        "vendor_attr":  "cc_label",      # synthetic property (CC reference)
        "task_approve": "approve_budget_plan",
        "task_revise":  "revise_budget_plan",
        "valid_submit":  ("draft", "returned"),
        "valid_approve": ("submitted", "in_review"),
        "valid_return":  ("submitted", "in_review"),
        "valid_cancel":  ("draft", "returned"),
    },
}


def _resolve_meta(doc_type: str) -> dict:
    """Return meta for known action keys; for cfm_<code> variants fall back to cfm."""
    if doc_type in _DOC_META:
        return _DOC_META[doc_type]
    if doc_type.startswith("cfm_"):
        return _expense_meta(doc_type.upper())
    raise KeyError(f"Unknown action key: {doc_type}")


# ── Config helpers ────────────────────────────────────────────────────────────

async def _get_config(db: AsyncSession) -> CompanyConfig | None:
    result = await db.execute(select(CompanyConfig).limit(1))
    return result.scalar_one_or_none()


async def _get_workflow(db: AsyncSession, doc_type: str) -> list[dict]:
    cfg = await _get_config(db)
    if cfg and cfg.workflow_defs:
        nodes = cfg.workflow_defs.get(doc_type, [])
        if nodes:
            return nodes
        # cfm_<code> falls back to the base cfm workflow if no per-form override
        if doc_type.startswith("cfm_"):
            nodes = cfg.workflow_defs.get("cfm", [])
            if nodes:
                return nodes
    return _WORKFLOW_DEFAULTS.get(doc_type) or _WORKFLOW_DEFAULTS.get(
        "cfm" if doc_type.startswith("cfm_") else doc_type, []
    )


# ── Over-budget pre-approval steps (PR only) ─────────────────────────────────
#
# The mode a PR was submitted under is FROZEN on the PR (ob_mode_applied), not
# re-read from Budget Config on every call. Re-reading it is what broke
# PR-20261015-0003 (ISS-0006): Budget Config moved fm_only → allow, the
# Over-Budget step vanished from every already-submitted PR, the remaining
# approvals slid one step_idx onto the wrong role in the timeline, and the five
# PRs still waiting on the Finance Manager became unapprovable — step 0 now
# meant Department Manager, so the FM's task 403'd.
#
# A LOOSER mode does reach in-flight PRs, but as a waiver, never by rebuilding
# the list: the step stays where it was and is auto-skipped with a reason
# (_ob_step_waived), so step indices — and the history recorded against them —
# never move. A STRICTER mode never adds steps to a PR already submitted.

OB_MODE_NONE = "none"   # not over budget at submission → no pre-approval steps
_OB_STEP_IDS = ("ob_finance_manager", "ob_gm_or_opm")
_OB_MODE_LABELS = {
    "fm_gm_opm": "Finance Manager + GM/OPM",
    "fm_only": "Finance Manager only",
    "allow": "Allow without extra approval",
    "hard_block": "Hard block",
}


def config_ob_mode(cfg: CompanyConfig | None) -> str:
    """Budget Config's over-budget mode as saved right now."""
    bac = (cfg.budget_admin_config if cfg else None) or {}
    return bac.get("over_budget_mode", "fm_gm_opm")


def _ob_prefix(mode: str) -> list[dict]:
    # `allow`: submit proceeds on the normal workflow with no pre-approval
    # steps — the justification is still demanded by the PR form and shown
    # to every approver. Unknown values keep failing safe to an FM step.
    if mode in (OB_MODE_NONE, "hard_block", "allow"):
        return []
    prepend = [{"id": "ob_finance_manager", "role": "finance_manager",
                "label": "Over-Budget — Finance Manager"}]
    if mode == "fm_gm_opm":
        prepend.append({"id": "ob_gm_or_opm", "role": "gm_or_opm",
                        "label": "Over-Budget — GM / OPM"})
    return prepend


def submission_ob_mode(doc: Any, status: str, cfg: CompanyConfig | None) -> str:
    """The mode a submission of `doc` in `status` freezes onto it.

    A first submission takes Budget Config as it stands. A resubmission after
    `return` is the same approval run continuing — approvals already given
    stand (resume_step) — so it keeps the mode it was frozen with, unless the
    PR itself crossed the budget line while it was back with the requester, in
    which case the step list genuinely changes and the run restarts.
    """
    live = config_ob_mode(cfg) if getattr(doc, "over_budget", False) else OB_MODE_NONE
    applied = getattr(doc, "ob_mode_applied", None)
    if status == "returned" and applied is not None \
            and (applied == OB_MODE_NONE) == (live == OB_MODE_NONE):
        return applied
    return live


def _ob_step_waived(node: dict, cfg_mode: str) -> str | None:
    """Skip reason when Budget Config no longer requires this over-budget step,
    else None. Only loosening waives; `hard_block` never waives (it forbids
    over-budget spend — letting in-flight PRs through would invert it)."""
    if node.get("id") not in _OB_STEP_IDS or cfg_mode == "hard_block":
        return None
    if any(n["id"] == node["id"] for n in _ob_prefix(cfg_mode)):
        return None
    # "Auto-skipped" is what the timelines key on to render a struck-through step.
    return ("Auto-skipped (over-budget approval no longer required — Budget Config "
            f"mode changed to {_OB_MODE_LABELS.get(cfg_mode, cfg_mode)})")


def workflow_key(doc_type: str, doc: Any) -> str:
    """Which workflow_defs chain a document walks.

    The chain key is the document type, except for a Freight PA: it stays a
    `pa` document — its tasks, events, signatories, inbox links and task
    liveness are all `pa`'s — but walks the `pa_frt` chain (OPM → GM →
    Finance Manager → Finance BP by default; admins edit it in Portal).
    """
    if doc_type == "pa" and getattr(doc, "source", None) == "freight":
        return "pa_frt"
    return doc_type


async def build_effective_workflow(
    db: AsyncSession, doc_type: str, doc: Any, cfg: CompanyConfig | None,
    *, ob_mode: str | None = None,
) -> list[dict]:
    """The ordered step list actually walked for THIS document: base workflow_defs
    plus the over-budget steps of the mode frozen on the PR at submission
    (`ob_mode` overrides it — the submit path passes the mode being frozen).
    Optional supervisor/director nodes stay in the list (skip is decided per-step
    at execution/render time)."""
    workflow = await _get_workflow(db, workflow_key(doc_type, doc))
    if doc_type != "pr":
        return workflow
    mode = ob_mode
    if mode is None and getattr(doc, "status", None) != "draft":
        mode = getattr(doc, "ob_mode_applied", None)
    if mode is None:
        # A draft (a recalled one starts over too), or a PR submitted before
        # the mode was frozen and never backfilled: what a submission walks now.
        mode = submission_ob_mode(doc, "draft", cfg)
    return _ob_prefix(mode) + workflow


# Default workflows shipped as PRD §3.3 seed values.
# These are only used as final fallback when CompanyConfig.workflow_defs is missing/empty.
# AE-2 seeds these into the DB on startup so this fallback is rarely reached.
_WORKFLOW_DEFAULTS: dict[str, list[dict]] = {
    "pr": [
        {"id": "supervisor",   "role": "supervisor",   "label": "Supervisor"},
        {"id": "dept_manager", "role": "dept_manager", "label": "Department Manager"},
        {"id": "director",     "role": "director",     "label": "Director"},
        {"id": "gm_or_opm",    "role": "gm_or_opm",    "label": "GM / OPM"},
    ],
    "po": [
        {"id": "proc_mgr",  "role": "procurement_manager", "label": "Procurement Manager"},
        {"id": "gm_or_opm", "role": "gm_or_opm",           "label": "GM / OPM"},
    ],
    # Mirrors the paper form: Purchasing Manager initials, OPM signs.
    "posign": [
        {"id": "proc_mgr", "role": "procurement_manager",
         "label": "Purchasing Manager", "sig_slot": "initials"},
        {"id": "opm", "role": "opm", "label": "Operations Manager",
         "sig_slot": "signature"},
    ],
    "agr": [
        {"id": "dept_manager",        "role": "dept_manager",        "label": "Department Manager"},
        {"id": "procurement_manager", "role": "procurement_manager", "label": "Procurement Manager"},
        {"id": "finance_manager",     "role": "finance_manager",     "label": "Finance Manager"},
    ],
    "pa": [
        {"id": "dept_manager", "role": "dept_manager",   "label": "Department Manager"},
        {"id": "director",     "role": "director",       "label": "Director"},
        {"id": "gm_or_opm",    "role": "gm_or_opm",      "label": "GM / OPM"},
        {"id": "finance_bp",   "role": "finance_bp",     "label": "Finance BP"},
        {"id": "finance_mgr",  "role": "finance_manager", "label": "Finance Manager"},
    ],
    # Courier freight PA (epms-api Freight): a document of type `pa`, walked
    # through this chain instead of `pa`'s (workflow_key). The invoice spans
    # several departments, so no step may resolve by department: OPM and GM are
    # separate plain roles here, never gm_or_opm (which would always land on
    # the GM for a document without a single department).
    "pa_frt": [
        {"id": "opm",         "role": "opm",             "label": "Operations Manager"},
        {"id": "gm",          "role": "gm",              "label": "General Manager"},
        {"id": "finance_mgr", "role": "finance_manager", "label": "Finance Manager"},
        {"id": "finance_bp",  "role": "finance_bp",      "label": "Finance BP"},
    ],
    "pa_dir": [
        {"id": "finance_bp",  "role": "finance_bp",      "label": "Finance BP"},
        {"id": "finance_mgr", "role": "finance_manager", "label": "Finance Manager"},
    ],
    "exp": [
        {"id": "dept_manager", "role": "dept_manager", "label": "Department Manager"},
        {"id": "finance_bp",   "role": "finance_bp",   "label": "Finance BP"},
    ],
    "mil": [
        {"id": "dept_manager", "role": "dept_manager", "label": "Department Manager"},
        {"id": "finance_bp",   "role": "finance_bp",   "label": "Finance BP"},
    ],
    "trv": [
        {"id": "dept_manager", "role": "dept_manager", "label": "Department Manager"},
        {"id": "finance_bp",   "role": "finance_bp",   "label": "Finance BP"},
    ],
    "tra": [
        {"id": "dept_manager", "role": "dept_manager",    "label": "Department Manager"},
        {"id": "finance_mgr",  "role": "finance_manager", "label": "Finance Manager"},
        {"id": "gm",           "role": "gm",              "label": "General Manager"},
    ],
    "cfm": [
        {"id": "dept_manager", "role": "dept_manager", "label": "Department Manager"},
    ],
    "budget_plan": [
        {"id": "dept_manager",   "role": "dept_manager",   "label": "Department Manager"},
        {"id": "finance_mgr",    "role": "finance_manager", "label": "Finance Manager"},
    ],
    "vms_visit": [
        # Default single-step. Admin adds a `quality_manager` step (W11 UI)
        # for GMP / Laboratory zones; vms-api resolves the actual user from
        # `vms_config.quality_manager_user_ids` and stores the chosen UUID
        # on `vms_visits.quality_approver_id` at submit time.
        {"id": "dept_manager", "role": "dept_manager", "label": "Department Manager"},
    ],
}


# ── Dept manager lookup ───────────────────────────────────────────────────────

async def _get_dept_manager_id(db: AsyncSession, dept_id: uuid.UUID | None) -> uuid.UUID | None:
    if not dept_id:
        return None
    mgr_result = await db.execute(
        select(User.id).where(
            User.role == "dept_manager",
            User.department_id == dept_id,
            User.is_active.is_(True),
        ).limit(1)
    )
    return mgr_result.scalar_one_or_none()


# ── Role map builder ──────────────────────────────────────────────────────────

def _build_role_map(rm: dict, dept_gm_opm: dict, department_id: uuid.UUID | None) -> dict[str, uuid.UUID | None]:
    def _uid(key: str) -> uuid.UUID | None:
        v = rm.get(key)
        return uuid.UUID(v) if v else None

    role_map: dict[str, uuid.UUID | None] = {
        "gm": _uid("gm_user_id"),
        "opm": _uid("opm_user_id"),
        "finance_manager": _uid("finance_manager_user_id"),
        "procurement_manager": _uid("procurement_manager_user_id"),
        "vendor_manager": _uid("vendor_manager_user_id"),
        "finance_director": _uid("finance_director_user_id"),
    }

    # gm_or_opm resolves via dept mapping
    if department_id:
        resolved = dept_gm_opm.get(str(department_id), "gm")
        role_map["gm_or_opm"] = role_map.get(resolved)
    else:
        role_map["gm_or_opm"] = role_map["gm"]

    return role_map


# ── Authorization check ───────────────────────────────────────────────────────

async def _actor_is_step_holder(
    db: AsyncSession,
    step_role: str,
    actor_id: uuid.UUID,
    routing_dept_id: uuid.UUID | None,
    rm: dict,
    dept_gm_opm: dict,
    finance_bp_ids: set[uuid.UUID],
    doc: Any | None = None,
    director_uid: uuid.UUID | None = None,
    supervisor_uid: uuid.UUID | None = None,
    cross_dept_pa: bool = False,
) -> bool:
    """Is this actor, in their OWN right, a holder of `step_role` for this document?

    Identity only — deliberately WITHOUT the `system_admin` bypass that
    `_actor_can_approve` layers on top. Two callers depend on that omission:

      * `_actor_can_approve` adds the bypass itself, so approving as an admin
        still works;
      * the same-approver auto-skip walk in `execute_action` must NOT treat an
        admin as the holder of every remaining step — that would auto-approve
        a whole workflow off a single admin click.

    This is the single authority on "does X hold step Y". It replaced a second,
    weaker copy (the `_holds` closure in `execute_action`) that compared against
    the COLLAPSED first post holder `rm['<role>_user_id']` and therefore missed
    multi-holder posts: a Department Manager who also held GM as an additional
    role was not recognised at gm_or_opm whenever the real GM sorted first, so
    the skip never fired and they had to approve the same document twice.
    """
    return actor_id in await _step_holder_ids(
        db, step_role, routing_dept_id, rm, dept_gm_opm, finance_bp_ids,
        doc, director_uid, supervisor_uid, cross_dept_pa)


async def _step_holder_ids(
    db: AsyncSession,
    step_role: str,
    routing_dept_id: uuid.UUID | None,
    rm: dict,
    dept_gm_opm: dict,
    finance_bp_ids: set[uuid.UUID],
    doc: Any | None = None,
    director_uid: uuid.UUID | None = None,
    supervisor_uid: uuid.UUID | None = None,
    cross_dept_pa: bool = False,
) -> set[uuid.UUID]:
    """Everyone who holds `step_role` for this document in their own right.

    Membership in this set is what _actor_is_step_holder answers; the set itself
    is what the claimant skip needs (is the claimant the ONLY one who could act).
    """
    if step_role == "quality_manager":
        expected = getattr(doc, "quality_approver_id", None) if doc is not None else None
        return {expected} if expected is not None else set()
    if step_role == "finance_bp":
        return set(finance_bp_ids)
    if step_role == "dept_manager":
        dept_mgr_id = await _get_dept_manager_id(db, routing_dept_id)
        return {dept_mgr_id} if dept_mgr_id is not None else set()
    if step_role == "gm_or_opm":
        # Resolve which post (gm/opm) this department routes to — same logic as
        # task assignment — then match ANY active holder of that post, not just
        # the single collapsed rm['<role>_user_id'] (= _post_holders()[0]).
        # A post can legitimately have >1 holder (e.g. a Department Manager who
        # ALSO holds GM via an additional user_roles role); the broadcast approve
        # task is visible to all of them, so all of them must be able to act.
        resolved_role, resolved_user_id = await _resolve_gm_or_opm(
            db, routing_dept_id, rm, dept_gm_opm, cross_dept_pa)
        holders = await post_holder_ids(db, resolved_role)
        if resolved_user_id is not None:
            holders.add(resolved_user_id)
        return holders
    if step_role == "director":
        # Director is resolved per-department (routing_dept_id), not via a
        # collapsed global holder, so a director covering multiple departments
        # already matches on any of their departments' documents.
        return {director_uid} if director_uid is not None else set()
    if step_role == "supervisor":
        return {supervisor_uid} if supervisor_uid is not None else set()
    # Named post role (gm/opm/finance_manager/procurement_manager/vendor_manager/
    # finance_director as a direct step): membership in the holder set, not
    # equality to the single collapsed rm['<role>_user_id'].
    return await post_holder_ids(db, step_role)


async def _actor_can_approve(
    db: AsyncSession,
    step_role: str,
    actor_id: uuid.UUID,
    actor_role: str,
    routing_dept_id: uuid.UUID | None,
    rm: dict,
    dept_gm_opm: dict,
    finance_bp_ids: set[uuid.UUID],
    doc: Any | None = None,
    director_uid: uuid.UUID | None = None,
    supervisor_uid: uuid.UUID | None = None,
    today: date | None = None,
    cross_dept_pa: bool = False,
) -> bool:
    """Return True if the actor is authorized to approve the current workflow step.

    `doc` is optional for back-compat (callers pre-S2 don't pass it). The
    VMS Quality Manager step needs it to read `doc.quality_approver_id`
    (per-visit assignment is VMS-local — no `role_management` mapping for
    quality_manager). See S2_ARCHITECTURE_REVIEW.md F2.

    `routing_dept_id` is the department that drives dept_manager/gm_or_opm
    routing for this document — the PR's selected department_id, falling back
    to the routing user's own department (see `_routing_department_id`).

    `today` drives dated delegation (代班) below — a bind parameter, never SQL
    CURRENT_DATE, and defaults to None which `active_delegator_ids` resolves
    to the plant-local date via `local_today()`.
    """
    if actor_role == "system_admin":
        return True
    if await _actor_is_step_holder(
        db, step_role, actor_id, routing_dept_id, rm, dept_gm_opm,
        finance_bp_ids, doc, director_uid, supervisor_uid, cross_dept_pa,
    ):
        return True
    # Delegation: act for anyone who has named this actor their stand-in today.
    # Deliberately placed AFTER the own-identity check and BELOW the
    # system_admin bypass — _actor_is_step_holder has no bypass, so an admin's
    # delegate inherits nothing.
    for delegator_id in await active_delegator_ids(db, actor_id, today=today):
        if await _actor_is_step_holder(
            db, step_role, delegator_id, routing_dept_id, rm, dept_gm_opm,
            finance_bp_ids, doc, director_uid, supervisor_uid, cross_dept_pa,
        ):
            return True
    # Fallback: actor's own JWT role must match the step role. Only reachable
    # for a step whose post has NO holders at all — with holders configured,
    # membership above is the authority and a non-holder must stay denied.
    if step_role in ("quality_manager", "finance_bp", "dept_manager",
                     "gm_or_opm", "director", "supervisor"):
        return False
    return actor_role == step_role and not await post_holder_ids(db, step_role)


async def _acting_on_behalf_of(
    db: AsyncSession,
    step_role: str,
    actor_id: uuid.UUID,
    routing_dept_id: uuid.UUID | None,
    rm: dict,
    dept_gm_opm: dict,
    finance_bp_ids: set[uuid.UUID],
    doc: Any | None = None,
    director_uid: uuid.UUID | None = None,
    supervisor_uid: uuid.UUID | None = None,
    today: date | None = None,
    cross_dept_pa: bool = False,
) -> uuid.UUID | None:
    """The delegator whose identity the actor borrowed, or None when the actor
    holds this step in their own right.

    A delegate may cover several people at once, so more than one delegator can
    satisfy the step. Sorting makes the annotation deterministic rather than
    dependent on row order.
    """
    if await _actor_is_step_holder(
        db, step_role, actor_id, routing_dept_id, rm, dept_gm_opm,
        finance_bp_ids, doc, director_uid, supervisor_uid, cross_dept_pa,
    ):
        return None
    for delegator_id in sorted(await active_delegator_ids(db, actor_id, today=today)):
        if await _actor_is_step_holder(
            db, step_role, delegator_id, routing_dept_id, rm, dept_gm_opm,
            finance_bp_ids, doc, director_uid, supervisor_uid, cross_dept_pa,
        ):
            return delegator_id
    return None


# ── Task helpers ──────────────────────────────────────────────────────────────

async def _complete_tasks(db: AsyncSession, doc_type: str, doc_id: uuid.UUID) -> None:
    result = await db.execute(
        select(Task).where(
            Task.document_type == doc_type,
            Task.document_id == doc_id,
            Task.is_completed.is_(False),
        )
    )
    now = datetime.now(timezone.utc)
    for task in result.scalars().all():
        task.is_completed = True
        task.completed_at = now


async def _is_cross_department_pa(db: AsyncSession, doc_type: str, doc: Any) -> bool:
    """Does this payment application spend against more than one department?

    Only PAs can: every other document type descends from a single PR. A PA may
    settle several purchase orders, and a vendor's one invoice covers whatever
    it covers — it has no idea where the department boundaries are, which is
    precisely why the payment cannot be split along them.

    When true, the department-scoped steps step aside and the GM/OPM step is
    resolved from an engine-wide setting instead (see _should_skip_step and
    _resolve_gm_or_opm).
    """
    if doc_type != "pa":
        return False
    return len(await pa_department_ids(db, doc.id)) > 1


async def _resolve_gm_or_opm(
    db: AsyncSession,
    dept_id: uuid.UUID | None,
    rm: dict,
    dept_gm_opm: dict,
    cross_dept_pa: bool = False,
) -> tuple[str, uuid.UUID | None]:
    """Return (resolved_role, user_id) for a gm_or_opm step.

    Normally the department decides (`approval_dept_routing.gm_or_opm`). A
    payment spanning several departments has no such answer — one of its
    departments may route to GM and another to OPM, and there is no principled
    way to choose between them — so an engine-wide setting names the post that
    approves those. Configured in Approval Routing; defaults to GM.
    """
    if cross_dept_pa:
        resolved_role = await get_approval_setting(db, CROSS_DEPT_GM_OR_OPM, "gm")
        if resolved_role not in ("gm", "opm"):
            resolved_role = "gm"
    else:
        resolved_role = dept_gm_opm.get(str(dept_id), "gm") if dept_id else "gm"
    uid_str = rm.get(f"{resolved_role}_user_id")
    return resolved_role, (uuid.UUID(uid_str) if uid_str else None)


async def _active_user_id(db: AsyncSession, user_id: uuid.UUID | None) -> uuid.UUID | None:
    """Return user_id iff it references an existing, active user; else None."""
    if user_id is None:
        return None
    row = (await db.execute(
        select(User.id).where(User.id == user_id, User.is_active.is_(True))
    )).scalar_one_or_none()
    return row


async def _resolve_director(
    db: AsyncSession, dept_id: uuid.UUID | None, dept_director_mapping: dict,
) -> uuid.UUID | None:
    """Director for the routing department, or None (unmapped / inactive)."""
    if not dept_id:
        return None
    uid_str = dept_director_mapping.get(str(dept_id))
    if not uid_str:
        return None
    return await _active_user_id(db, uuid.UUID(uid_str))


async def _resolve_supervisor(
    db: AsyncSession, routing_uid: uuid.UUID, dept_supervisor_enabled: dict,
) -> uuid.UUID | None:
    """The requester's assigned supervisor, iff their department has the
    supervisor level enabled and the assignee is active; else None."""
    row = (await db.execute(
        select(User.department_id, User.supervisor_id).where(User.id == routing_uid)
    )).first()
    if row is None:
        return None
    dept_id, supervisor_id = row
    if not dept_id or not dept_supervisor_enabled.get(str(dept_id)):
        return None
    return await _active_user_id(db, supervisor_id)


async def _routing_user_id(db: AsyncSession, doc_type: str, doc: Any) -> uuid.UUID:
    """Return the user whose department drives department-based approval routing
    (the `dept_manager` and `gm_or_opm` steps).

    Routing must follow the *requester's* department — the person who raised the
    originating PR — NOT whoever created the PO/PA. POs are created by procurement
    officers and PAs by AP staff, so `doc.created_by` is the wrong department for
    these doc types. The chain is:
        po       → pr_id → PurchaseRequest.created_by
        pa/pa_dir → po_id → PurchaseOrder.pr_id → PurchaseRequest.created_by
    Falls back to `doc.created_by` when no PR is linked (direct PO / direct PA)
    and for every other doc_type (pr, exp, mil, trv, cfm*, budget_plan, vms_visit),
    where the creator IS the requester — so their behaviour is unchanged.
    """
    if doc_type == "po":
        pr_id = getattr(doc, "pr_id", None)
        if pr_id:
            uid = (await db.execute(
                select(PurchaseRequest.created_by).where(PurchaseRequest.id == pr_id)
            )).scalar_one_or_none()
            if uid:
                return uid
    elif doc_type in ("pa", "pa_dir"):
        po_id = getattr(doc, "po_id", None)
        if po_id:
            pr_id = (await db.execute(
                select(PurchaseOrder.pr_id).where(PurchaseOrder.id == po_id)
            )).scalar_one_or_none()
            if pr_id:
                uid = (await db.execute(
                    select(PurchaseRequest.created_by).where(PurchaseRequest.id == pr_id)
                )).scalar_one_or_none()
                if uid:
                    return uid
    return doc.created_by


async def _routing_department_id(
    db: AsyncSession, doc_type: str, doc: Any, routing_uid: uuid.UUID,
) -> uuid.UUID | None:
    """Department that drives dept-based approval routing (dept_manager /
    gm_or_opm / director). Prefer the department the DOCUMENT names — the one
    filled in on the agreement (for an agreement and for a PA raised against
    one), else the one selected on the originating PR; fall back to the routing
    user's own department (legacy)."""
    # 协议自带 department_id(建档时选定),没有 PR 可追溯 —— 直接用它。
    # 必须与可见性口径一致:epms-api 的 PA 列表按 PurchaseAgreement.department_id
    # 收窄(crud/pa.py)。若这里改用提交人部门,受限审批人会收到任务却在列表里
    # 找不到单据;而且提交人无部门时(常见:采购/系统账号)整条链直接 409 卡死,
    # 尽管协议自己的部门明明有在职经理。
    if doc_type == "agr":
        dept = getattr(doc, "department_id", None)
        if dept:
            return dept

    # 协议关联的 PA 同理:它没有 PO、也没有 PR(epms-api crud/pa.py 的协议路径
    # po_links 为空 → po_id 为 NULL),原本只能退回"建单人的部门",于是部门经理
    # 那步落到了协议 Owner / AP 代建人所在部门的经理身上,而不是协议上填的部门。
    # 口径与协议本身(上面的 "agr" 分支)以及可见性(epms-api crud/pa.py::get_all
    # 按 PurchaseAgreement.department_id 收窄 PA 列表)保持一致。
    if doc_type in ("pa", "pa_dir"):
        agr_id = getattr(doc, "agreement_id", None)
        if agr_id:
            dept = (await db.execute(
                select(PurchaseAgreement.department_id)
                .where(PurchaseAgreement.id == agr_id)
            )).scalar_one_or_none()
            if dept:
                return dept

    pr_id = None
    if doc_type == "pr":
        pr_id = doc.id
    elif doc_type == "po":
        pr_id = getattr(doc, "pr_id", None)
    elif doc_type in ("pa", "pa_dir"):
        po_id = getattr(doc, "po_id", None)
        if po_id:
            pr_id = (await db.execute(
                select(PurchaseOrder.pr_id).where(PurchaseOrder.id == po_id)
            )).scalar_one_or_none()
    if pr_id:
        dept = (await db.execute(
            select(PurchaseRequest.department_id).where(PurchaseRequest.id == pr_id)
        )).scalar_one_or_none()
        if dept:
            return dept
    return (await db.execute(
        select(User.department_id).where(User.id == routing_uid)
    )).scalar_one_or_none()


async def _cc_label_for_plan(db: AsyncSession, doc: Any, fallback: str | None) -> str | None:
    """Resolve a human cost-center label ("CODE — Name") for a budget plan task.

    The BudgetPlan mirror only carries cost_center_id, so its `cc_label` property
    can only render "CC <uuid>". The cost_centers table (shared DB, owned by
    epms-api) holds code/name; look it up so the Task Inbox shows the name instead
    of a raw UUID. Falls back to whatever the mirror produced if the lookup misses.
    """
    cc_id = getattr(doc, "cost_center_id", None)
    if cc_id is None:
        return fallback
    row = (await db.execute(
        text("SELECT code, name FROM cost_centers WHERE id = CAST(:id AS uuid)"),
        {"id": str(cc_id)},
    )).first()
    if row is None:
        return fallback
    code, name = row
    return f"{code} — {name}" if name else (code or fallback)


MSG_BUDGET_HARD_BLOCK = (
    "This PR exceeds the available budget for its account. "
    "Submission is blocked by Budget Config (hard_block mode). "
    "Reduce the budget code's commitment or split the PR into smaller items."
)


def msg_cannot_submit(doc_type: str, status: str) -> str:
    return f"Cannot submit {doc_type.upper()} in status '{status}'"


def first_actionable_step(
    doc_type: str,
    doc: Any,
    workflow: list[dict],
    director_uid: uuid.UUID | None,
    supervisor_uid: uuid.UUID | None,
    dept_has_director: bool,
    dept_has_supervisor: bool,
    cross_dept_pa: bool,
    start: int = 0,
    cfg_ob_mode: str | None = None,
) -> tuple[int, list[tuple[int, str, str]]]:
    """Walk the auto-skipped prefix and return where the document actually lands.

    `start` is where the walk begins: 0 for a first submission, the step it was
    sent back from for a resubmission (see resume_step).

    Returns (start, skipped) where skipped is [(step_idx, role, reason)] — the
    caller decides what to do with those (execution records an ApprovalEvent per
    skip; a dry run just reports them). Pure: no writes, so both can share it.
    """
    skipped: list[tuple[int, str, str]] = []
    while start < len(workflow):
        role = workflow[start]["role"]
        skip, reason = _step_skip(workflow[start], doc_type, doc, director_uid,
                                  supervisor_uid, dept_has_director,
                                  dept_has_supervisor, cross_dept_pa, cfg_ob_mode)
        if not skip:
            break
        skipped.append((start, role, reason))
        start += 1
    return start, skipped


async def resume_step(db: AsyncSession, doc_type: str, doc: Any, status: str,
                      workflow: list[dict]) -> int:
    """Where a submission picks the workflow up: the step a returned document
    was sent back from, otherwise the top.

    Resubmitting used to restart every chain at step 0, so the approvers who had
    already signed — four of five on EXP-20260929-0001 — had to sign again for
    a document they had cleared. Their approvals stand now and it goes straight
    back to whoever returned it (company rule, every document type).

    `return` resets approval_step_idx to 0, so the step comes from the return
    event, which records the step it was taken at. No such event (imported
    data), or a step the workflow no longer has → the top, as before. Only
    `returned` resumes: a recalled document is a draft again and starts over.
    """
    if status != "returned":
        return 0
    step = (await db.execute(
        select(ApprovalEvent.step_idx)
        .where(ApprovalEvent.document_type == doc_type,
               ApprovalEvent.document_id == doc.id,
               ApprovalEvent.action == "return")
        .order_by(ApprovalEvent.created_at.desc())
        .limit(1)
    )).scalar_one_or_none()
    if step is None or not 0 <= step < len(workflow):
        return 0
    return step


async def submission_start(db: AsyncSession, doc_type: str, ctx: "ActionContext",
                           status: str) -> int:
    """resume_step against the list a submission walks. If the over-budget steps
    in front changed since the last submission (the PR crossed the budget line
    while it was returned), the recorded return index points into a different
    list — restart from the top rather than resume at the wrong step."""
    workflow = ctx.submit_workflow if ctx.submit_workflow is not None else ctx.workflow
    if doc_type == "pr":
        applied = getattr(ctx.doc, "ob_mode_applied", None)
        if applied is not None and ctx.submit_ob_mode is not None and \
                [n["id"] for n in _ob_prefix(applied)] != \
                [n["id"] for n in _ob_prefix(ctx.submit_ob_mode)]:
            return 0
    return await resume_step(db, doc_type, ctx.doc, status, workflow)


async def resolve_step_assignment(
    db: AsyncSession,
    doc_type: str,
    doc: Any,
    step: int,
    workflow: list[dict],
    rm: dict,
    dept_gm_opm: dict,
    routing_dept_id: uuid.UUID | None = None,
    director_uid: uuid.UUID | None = None,
    supervisor_uid: uuid.UUID | None = None,
    cross_dept_pa: bool = False,
) -> tuple[uuid.UUID | None, str]:
    """Who this step goes to: (assigned_user_id, assigned_role).

    A None user id means the task is BROADCAST to holders of assigned_role —
    that is deliberate for singleton posts, not a missing value. Raises
    ValueError when the step cannot be routed at all.

    Split out of _create_approve_task so a dry run can ask "would this route?"
    without creating anything. Both callers share this one implementation on
    purpose: a preflight that answered from its own copy of these rules would
    start disagreeing with what submission actually does.
    """
    wf = workflow[step]
    role = wf["role"]
    assigned_user_id: uuid.UUID | None = None
    assigned_role = role

    if role == "dept_manager":
        # routing_dept_id = the PR's selected department, else the requester's own
        # department — see _routing_department_id
        assigned_user_id = await _get_dept_manager_id(db, routing_dept_id)
        # dept_manager is a populous base JWT role. If we cannot resolve a
        # SPECIFIC manager (no routing department, or that department has no
        # active dept_manager), we must NOT leave assigned_user_id=NULL: get_for_role
        # would broadcast the task to EVERY department manager company-wide, and
        # _actor_can_approve would let none of them act (PR-20260620-0001). Fail
        # loudly so the document stays put until the department is configured.
        if assigned_user_id is None:
            # Which department the router landed on depends on the document —
            # the agreement's own (agreement, agreement-sourced PA), the one
            # selected on the PR, else the submitter's. Naming it "the
            # requester's" sent people to the wrong department's page.
            raise ValueError(
                "Cannot route approval: no active Department Manager is configured "
                "for this document's department (the department named on the "
                "agreement or the PR, falling back to the submitter's own). Assign "
                "a Department Manager to that department (Admin → Departments / "
                "Users) before this document can be submitted."
            )
    elif role == "gm_or_opm":
        # GM vs OPM is decided by the routing department (PR's selected
        # department, else the requester's own). BROADCAST (assigned_user_id=None)
        # to the resolved concrete post: gm/opm are SINGLETON posts, so the Task
        # Inbox (get_for_role role-union) and _actor_can_approve (post_holder_ids
        # membership) both resolve the CURRENT holder live. Pinning the task to the
        # holder-at-creation-time would orphan it when the post is reassigned
        # (prod incident: PO stuck on the previous OPM). Same treatment as the
        # other singleton posts (finance_manager / procurement_manager / …).
        assigned_role, _ = await _resolve_gm_or_opm(
            db, routing_dept_id, rm, dept_gm_opm, cross_dept_pa)
    elif role == "director":
        assigned_user_id = director_uid
    elif role == "supervisor":
        assigned_user_id = supervisor_uid
    elif role == "quality_manager" and doc_type == "vms_visit":
        # VMS-local: vms-api pre-selected the QM and stored on the visit.
        # See S2_ARCHITECTURE_REVIEW.md F2.
        assigned_user_id = getattr(doc, "quality_approver_id", None)

    return assigned_user_id, assigned_role


async def _create_approve_task(
    db: AsyncSession,
    doc_type: str,
    doc: Any,
    step: int,
    workflow: list[dict],
    meta: dict,
    rm: dict,
    dept_gm_opm: dict,
    routing_dept_id: uuid.UUID | None = None,
    director_uid: uuid.UUID | None = None,
    supervisor_uid: uuid.UUID | None = None,
    cross_dept_pa: bool = False,
) -> None:
    wf = workflow[step]
    assigned_user_id, assigned_role = await resolve_step_assignment(
        db, doc_type, doc, step, workflow, rm, dept_gm_opm,
        routing_dept_id=routing_dept_id, director_uid=director_uid,
        supervisor_uid=supervisor_uid, cross_dept_pa=cross_dept_pa)

    doc_number = getattr(doc, meta["number_attr"])
    amount = getattr(doc, meta["amount_attr"], None) if meta.get("amount_attr") else None
    vendor = getattr(doc, meta["vendor_attr"], None) if meta.get("vendor_attr") else None
    if doc_type == "budget_plan":
        vendor = await _cc_label_for_plan(db, doc, vendor)

    labels = meta.get("labels", {})
    doc_label = labels.get("doc", doc_type.upper())
    description = (f"Step {step + 1}/{len(workflow)}: {wf['label']} "
                   f"{labels.get('approve_noun', 'review')} required.")
    # OBG-002: enrich the over-budget pre-approval task with the requester's
    # justification so the approver sees it inline in the task inbox without
    # opening the PR detail page. Injected over-budget steps carry the ids
    # `ob_finance_manager` / `ob_gm_or_opm` (see execute_action — over-budget
    # injection block).
    if doc_type == "pr" and wf.get("id", "").startswith("ob_"):
        justification = (getattr(doc, "over_budget_justification", None) or "").strip()
        just_line = justification if justification else "(no justification provided)"
        description = (
            f"⚠️ OVER-BUDGET PRE-APPROVAL — PR amount: {amount}\n"
            f"Justification: {just_line}"
        )

    db.add(Task(
        type=meta["task_approve"],
        priority="normal",
        document_type=doc_type,
        document_id=doc.id,
        document_number=doc_number,
        assigned_role=assigned_role,
        assigned_user_id=assigned_user_id,
        title=f"{labels.get('approve_verb', 'Approve')} {doc_label}: {doc_number} — {doc.title}",
        description=description,
        amount=amount,
        vendor=vendor,
    ))


async def _create_revise_task(db: AsyncSession, doc_type: str, doc: Any, meta: dict) -> None:
    doc_number = getattr(doc, meta["number_attr"])
    amount = getattr(doc, meta["amount_attr"], None) if meta.get("amount_attr") else None
    vendor = getattr(doc, meta["vendor_attr"], None) if meta.get("vendor_attr") else None
    if doc_type == "budget_plan":
        vendor = await _cc_label_for_plan(db, doc, vendor)
    # Who gets the document back. Default is its creator, which is right for
    # every doc_type a person raised themselves. A doc_type whose _DOC_META
    # names `revise_assignee_attr` sends it elsewhere: an NC-imported PO's
    # created_by is the nc-sync service account, so returning its sign-off to
    # created_by would file the task in an inbox nobody logs into. Falls back to
    # created_by rather than leaving assigned_user_id NULL — a NULL here
    # broadcasts the task to every holder of assigned_role.
    assignee = getattr(doc, meta.get("revise_assignee_attr", "created_by"), None) or doc.created_by
    labels = meta.get("labels", {})
    revise_label = labels.get("revise", labels.get("doc", doc_type.upper()))
    db.add(Task(
        type=meta["task_revise"],
        priority="normal",
        document_type=doc_type,
        document_id=doc.id,
        document_number=doc_number,
        assigned_role="requester",
        assigned_user_id=assignee,
        title=f"Revise {revise_label}: {doc_number} — {doc.title}",
        description=f"Your {revise_label} has been returned for revision.",
        amount=amount,
        vendor=vendor,
    ))


# ── Post-approve side effects ─────────────────────────────────────────────────

async def _po_requester_id(db: AsyncSession, po: PurchaseOrder) -> uuid.UUID:
    """Resolve the ONE person who owns a PO's requester-side task.

    Never returns None. 'requester' is not a role pool: epms-api's notifier
    treats assigned_user_id=None + assigned_role="requester" as nobody to
    notify, suppresses the email fan-out and alerts the admins instead — while
    the Task Inbox still shows the task to every Requester in the company. So a
    NULL here costs an admin alert AND a company-wide broadcast, not just a
    missing email.
    PR creator → PO sign-off submitter → PO creator.
    """
    if po.pr_id:
        requester_id = (await db.execute(
            select(PurchaseRequest.created_by).where(PurchaseRequest.id == po.pr_id)
        )).scalar_one_or_none()
        if requester_id:
            return requester_id
    # An NC-imported PO's created_by is the nc-sync service account, whose inbox
    # nobody opens — prefer whoever actually submitted it for sign-off (the same
    # reasoning _create_revise_task's revise_assignee_attr already applies).
    return po.signoff_submitted_by or po.created_by


async def _post_approve_pr(db: AsyncSession, pr: PurchaseRequest) -> None:
    db.add(Task(
        type="create_po",
        priority="normal",
        document_type="pr",
        document_id=pr.id,
        document_number=pr.number,
        assigned_role="procurement_officer",
        title=f"Create PO: {pr.number} — {pr.title}",
        description=f"PR {pr.number} has been fully approved. Please create a Purchase Order.",
        amount=pr.amount,
        vendor=pr.vendor_name,
    ))


async def _post_approve_po(db: AsyncSession, po: PurchaseOrder) -> None:
    db.add(Task(
        type="place_order",
        priority="normal",
        document_type="po",
        document_id=po.id,
        document_number=po.number,
        assigned_role="procurement_officer",
        title=f"Place Order: {po.number} — {po.title}",
        description=f"PO {po.number} has been approved. Please place the order with the vendor.",
        amount=po.total,
        vendor=po.vendor_name,
    ))

    if po.is_prepaid:
        db.add(Task(
            type="create_prepayment_pa",
            priority="normal",
            document_type="po",
            document_id=po.id,
            document_number=po.number,
            assigned_role="requester",
            assigned_user_id=await _po_requester_id(db, po),
            title=f"Create Prepayment PA: {po.number} — {po.title}",
            description=(
                f"PO {po.number} has been approved and requires an advance payment. "
                "Please create a Prepayment Payment Application before goods are delivered."
            ),
            amount=po.total,
            vendor=po.vendor_name,
        ))


async def _post_approve_agr(db: AsyncSession, agr: PurchaseAgreement) -> None:
    """Final approval activates the agreement rather than marking it 'approved'.

    The invoice match candidate pool filters on status == "active" (plus the
    grace window), so leaving it at "approved" would approve an agreement that
    no invoice could ever be matched to.
    """
    agr.status = "active"


async def _post_approve_pa(db: AsyncSession, pa: PaymentApplication) -> None:
    """PA-PO: notify Payment Officer. Invoices are marked paid only on the process action."""
    db.add(Task(
        type="process_pa",
        priority="normal",
        document_type="pa",
        document_id=pa.id,
        document_number=pa.pa_number,
        # 付款执行已从 AP Clerk 拆出为专职附加角色(2026-08-13)。权限侧的隔离在
        # finance-api 的 _PAY_ROLES;这里只负责把任务派给对的人 —— 别再改回
        # ap_clerk。
        assigned_role="payment_officer",
        title=f"Process Payment: {pa.pa_number} — {pa.title}",
        description=f"PA {pa.pa_number} has been fully approved. Please process the payment.",
        amount=pa.payment_amount,
        vendor=pa.vendor_name,
    ))


async def _post_approve_pa_dir(db: AsyncSession, pa: PaymentApplication) -> None:
    """PA-DIR: notify Payment Officer only — invoices are in expense_invoices (OA-owned), not EPMS."""
    db.add(Task(
        type="process_pa",
        priority="normal",
        document_type="pa_dir",
        document_id=pa.id,
        document_number=pa.pa_number,
        # 付款执行已从 AP Clerk 拆出为专职附加角色(2026-08-13)。权限侧的隔离在
        # finance-api 的 _PAY_ROLES;这里只负责把任务派给对的人 —— 别再改回
        # ap_clerk。
        assigned_role="payment_officer",
        title=f"Process Direct Payment: {pa.pa_number} — {pa.title}",
        description=f"Direct PA {pa.pa_number} has been fully approved. Please process the payment.",
        amount=pa.payment_amount,
        vendor=pa.vendor_name,
    ))


async def _post_approve_exp(db: AsyncSession, claim: ExpenseClaim) -> None:
    """Expense claim (EXP / MIL / TRV / CFM): notify Finance BP to reimburse the employee."""
    db.add(Task(
        type="process_expense",
        priority="normal",
        document_type=claim.claim_type.lower(),
        document_id=claim.id,
        document_number=claim.claim_number,
        assigned_role="finance_bp",
        title=f"Process Reimbursement: {claim.claim_number} — {claim.employee_name or 'Employee'}",
        description=f"{claim.claim_type} claim {claim.claim_number} has been approved. Please process the reimbursement.",
        amount=claim.total_amount,
        vendor=claim.employee_name,
    ))


async def _post_approve_tra(db: AsyncSession, claim: ExpenseClaim) -> None:
    """Travel Application approved: no reimbursement, no money, no task. The
    approval itself unlocks TRV creation for the roster (enforced in expense-api).
    Deliberately a no-op — do NOT reuse _post_approve_exp, which would create a
    finance_bp reimbursement task for a non-financial document."""
    return None


async def _post_approve_budget_plan(db: AsyncSession, plan: BudgetPlan) -> None:
    """When a budget-plan revision is approved, flip is_current from parent → this row.

    - For v1 plans (parent_plan_id IS NULL): just ensure is_current=True.
    - For revisions (parent_plan_id is set): mark parent.is_current=False and
      this row .is_current=True. Old parent stays status='approved' for audit.
    """
    if plan.is_current:
        # Already current; nothing to do.
        return
    if plan.parent_plan_id is None:
        plan.is_current = True
        return
    parent = (await db.execute(
        select(BudgetPlan).where(BudgetPlan.id == plan.parent_plan_id)
    )).scalar_one_or_none()
    if parent is not None and parent.is_current:
        parent.is_current = False
    plan.is_current = True


async def _post_approve_vms_visit(db: AsyncSession, visit: VmsVisit) -> None:
    """Flip vms_visits.status (the VisitStatus enum) from pending_approval
    to confirmed once all approval steps have cleared.

    The engine writes `approval_status='approved'` via `_set_status` first;
    this callback then synchronizes the user-facing column so the rest of
    vms-api (badge-print guard, dashboard counters, etc.) sees the visit as
    ready to print.

    Implementation note: we use raw SQL because the thin Visit mirror in
    approval-api doesn't expose the `status` column at all — that column is
    owned by vms-api and uses an ENUM type we don't import here.
    """
    await db.execute(
        text("UPDATE vms_visits SET status = 'confirmed' WHERE id = :id"),
        {"id": visit.id},
    )


_POST_APPROVE: dict[str, Any] = {
    "pr":     _post_approve_pr,
    "po":     _post_approve_po,
    "agr":    _post_approve_agr,
    "pa":     _post_approve_pa,
    "pa_dir": _post_approve_pa_dir,
    "exp":    _post_approve_exp,
    "mil":    _post_approve_exp,
    "trv":    _post_approve_exp,
    "tra":    _post_approve_tra,
    "cfm":    _post_approve_exp,
    "budget_plan":  _post_approve_budget_plan,
    "vms_visit":    _post_approve_vms_visit,
}


# ── Conditional per-document step skipping ────────────────────────────────────

# Which steps are conditional, and what the condition is, in words.
#
# The workflow definition does not carry this — a step is skipped or not by the
# rules in _should_skip_step below, at runtime, per document. Anything
# describing the process to a person has to say so, or it will state that every
# step always happens: the assistant told a requester that a PR needs both a
# department manager AND a director, when most departments have no director and
# that step never runs for them.
#
# Kept next to the rules it describes, and test_workflow_conditional_roles
# asserts the two do not drift apart.
CONDITIONAL_ROLES: dict[str, str] = {
    "director": "Only runs when the department has a Director configured.",
    "supervisor": "Only runs when a Supervisor is assigned.",
    "quality_manager": ("Only runs when the visitor's access area requires "
                        "Quality Manager review."),
}


def _should_skip_step(role, doc_type, doc, director_uid, supervisor_uid,
                      dept_has_director, dept_has_supervisor,
                      cross_dept_pa=False):
    # A payment covering purchase orders from several departments has no single
    # department manager or director to route to — and picking the primary PO's
    # would put one department's approver in charge of another department's
    # spend without the other ever seeing it. Both levels step aside and the
    # GM/OPM step (resolved from the engine-wide setting rather than from a
    # department, see _resolve_gm_or_opm) approves for all of them.
    #
    # Skipped, not removed: the step stays in the workflow and the timeline
    # renders it struck through with this reason, so the approval history shows
    # that the level was consciously bypassed rather than never existing.
    if cross_dept_pa and role in ("dept_manager", "director"):
        return True, ("Auto-skipped (payment covers purchase orders from several "
                      "departments — approved at GM/OPM level instead)")
    if role == "quality_manager" and doc_type == "vms_visit":
        if getattr(doc, "quality_approver_id", None) is None:
            return True, "Auto-skipped (access area does not require Quality Manager review)"
    if role == "director" and director_uid is None:
        reason = ("Auto-skipped ⚠ configured Director is inactive/missing"
                  if dept_has_director else "Auto-skipped (department has no Director)")
        return True, reason
    if role == "supervisor" and supervisor_uid is None:
        reason = ("Auto-skipped ⚠ configured Supervisor is inactive/missing"
                  if dept_has_supervisor else "Auto-skipped (no Supervisor assigned)")
        return True, reason
    return False, ""


def _step_skip(node, doc_type, doc, director_uid, supervisor_uid,
               dept_has_director, dept_has_supervisor,
               cross_dept_pa=False, cfg_ob_mode=None):
    """_should_skip_step plus the over-budget waiver. Takes the workflow NODE,
    not its role: an over-budget step shares its role (finance_manager /
    gm_or_opm) with ordinary steps, only the node id tells them apart."""
    if doc_type == "pr" and cfg_ob_mode is not None:
        reason = _ob_step_waived(node, cfg_ob_mode)
        if reason:
            return True, reason
    return _should_skip_step(node["role"], doc_type, doc, director_uid,
                             supervisor_uid, dept_has_director,
                             dept_has_supervisor, cross_dept_pa)


# A claimant never approves their own OA claim (OA.md §7.3; expense-api's
# can_approve refuses the owner). The engine, unlike that rule, routes by role:
# a Department Manager's own claim lands its first step on the Department
# Manager — the claimant — and nobody can move it (TRA-20261020-0001/0002).
# When the claimant is the ONLY holder of a step, that step is skipped and the
# next level approves. A step someone else also holds stays with them.
CLAIMANT_SKIP_REASON = "Auto-skipped (claimant holds this step — cannot approve own claim)"
MSG_CLAIMANT_HOLDS_EVERY_STEP = (
    "Cannot submit: every approval step of this claim would be approved by the "
    "claimant themselves. Ask an administrator to adjust the approval workflow "
    "(Admin → Approval Workflows) so someone else approves it."
)


async def _claimant_step_skip(db: AsyncSession, doc_type: str, ctx: "ActionContext",
                              node: dict) -> str | None:
    """CLAIMANT_SKIP_REASON when the claimant is the sole holder of this step."""
    if ctx.meta["model"] is not ExpenseClaim:
        return None
    claimant = getattr(ctx.doc, "created_by", None)   # = expense_claims.employee_id
    if claimant is None:
        return None
    holders = await _step_holder_ids(
        db, node["role"], ctx.routing_dept_id, ctx.rm, ctx.dept_gm_opm,
        {uuid.UUID(u) for u in ctx.rm.get("finance_bp_user_ids", [])},
        ctx.doc, ctx.director_uid, ctx.supervisor_uid, ctx.cross_dept_pa)
    return CLAIMANT_SKIP_REASON if holders == {claimant} else None


async def submission_landing(
    db: AsyncSession, doc_type: str, ctx: "ActionContext",
    workflow: list[dict], start: int,
) -> tuple[int, list[tuple[int, str, str]], bool]:
    """first_actionable_step plus the claimant skip — where a submission lands.

    Returns (start, skipped, claimant_skipped). Shared by execution and
    preflight so the two cannot disagree about it.
    """
    skipped: list[tuple[int, str, str]] = []
    claimant_skipped = False
    while True:
        start, cond = first_actionable_step(
            doc_type, ctx.doc, workflow, ctx.director_uid, ctx.supervisor_uid,
            ctx.dept_has_director, ctx.dept_has_supervisor, ctx.cross_dept_pa,
            start=start, cfg_ob_mode=ctx.cfg_ob_mode or None)
        skipped.extend(cond)
        if start >= len(workflow):
            return start, skipped, claimant_skipped
        reason = await _claimant_step_skip(db, doc_type, ctx, workflow[start])
        if reason is None:
            return start, skipped, claimant_skipped
        skipped.append((start, workflow[start]["role"], reason))
        claimant_skipped = True
        start += 1


# ── Main execution entry point ────────────────────────────────────────────────

@dataclass
class ActionContext:
    """Everything an action needs to know about a document before deciding.

    Assembled by read-only queries, so a dry run can build the same context an
    execution would and reach the same conclusions without writing anything.
    That is the whole point: `preflight` and `execute_action` must not answer
    "can this be submitted" from two different pictures of the world.
    """
    meta: dict
    doc: Any
    step: int
    cfg: Any
    rm: dict
    dept_gm_opm: dict
    routing_uid: uuid.UUID | None
    routing_dept_id: uuid.UUID | None
    cross_dept_pa: bool
    director_uid: uuid.UUID | None
    supervisor_uid: uuid.UUID | None
    dept_has_director: bool
    dept_has_supervisor: bool
    workflow: list[dict]
    over_budget_mode: str
    # Budget Config's over-budget mode right now ("" for non-PR) — drives the
    # waiver of over-budget steps a looser mode no longer requires.
    cfg_ob_mode: str = ""
    # What a submit freezes (PR only): the mode, and the step list it yields.
    # A submission is the one moment the frozen mode is (re)decided, so it walks
    # this list rather than `workflow` (the list frozen by the LAST submission).
    submit_ob_mode: str | None = None
    submit_workflow: list[dict] | None = None


async def load_action_context(db: AsyncSession, doc_type: str,
                              doc_id: uuid.UUID) -> ActionContext:
    """Read-only. Raises LookupError when the document does not exist."""
    meta = _resolve_meta(doc_type)
    Model = meta["model"]

    result = await db.execute(select(Model).where(Model.id == doc_id))
    doc = result.scalar_one_or_none()
    if doc is None:
        raise LookupError(f"{doc_type.upper()} {doc_id} not found")

    cfg = await _get_config(db)
    rm = await get_role_management(db)
    dept_gm_opm = await get_dept_gm_opm_mapping(db)

    # Department-based routing (dept_manager / gm_or_opm / director) follows the
    # department SELECTED ON THE PR (department_id), falling back to the routing
    # user's own department for documents with no PR selection (legacy / direct
    # PO/PA). See _routing_user_id / _routing_department_id. The personal
    # supervisor stays keyed to the routing user (creator), never the selected
    # department — see _resolve_supervisor.
    routing_uid = await _routing_user_id(db, doc_type, doc)
    routing_dept_id = await _routing_department_id(db, doc_type, doc, routing_uid)
    cross_dept_pa = await _is_cross_department_pa(db, doc_type, doc)

    dept_director = await get_dept_director_mapping(db)
    dept_supervisor = await get_dept_supervisor_enabled(db)
    director_uid = await _resolve_director(db, routing_dept_id, dept_director)
    supervisor_uid = await _resolve_supervisor(db, routing_uid, dept_supervisor)
    # dept-level "configured?" flags — distinguish opt-out vs misconfig in skip reasons
    dept_has_director = bool(routing_dept_id) and str(routing_dept_id) in (dept_director or {})
    dept_has_supervisor = bool(routing_dept_id) and bool((dept_supervisor or {}).get(str(routing_dept_id)))

    # Build the effective workflow for this document: base workflow_defs plus the
    # over-budget steps frozen on the PR at submission. Optional supervisor/director
    # nodes stay in the list; skip is decided per-step at execution/render time.
    workflow = await build_effective_workflow(db, doc_type, doc, cfg)
    over_budget_mode = ""
    cfg_ob_mode = ""
    submit_ob_mode = None
    submit_workflow = workflow
    if doc_type == "pr":
        cfg_ob_mode = config_ob_mode(cfg)
        if getattr(doc, "over_budget", False):
            over_budget_mode = cfg_ob_mode
        submit_ob_mode = submission_ob_mode(doc, _status_of(meta, doc), cfg)
        submit_workflow = await build_effective_workflow(
            db, doc_type, doc, cfg, ob_mode=submit_ob_mode)

    return ActionContext(
        meta=meta, doc=doc, step=_step_of(meta, doc), cfg=cfg, rm=rm,
        dept_gm_opm=dept_gm_opm, routing_uid=routing_uid,
        routing_dept_id=routing_dept_id, cross_dept_pa=cross_dept_pa,
        director_uid=director_uid, supervisor_uid=supervisor_uid,
        dept_has_director=dept_has_director, dept_has_supervisor=dept_has_supervisor,
        workflow=workflow, over_budget_mode=over_budget_mode,
        cfg_ob_mode=cfg_ob_mode, submit_ob_mode=submit_ob_mode,
        submit_workflow=submit_workflow,
    )


@dataclass
class Check:
    """One gate, and what the person on the other end can do about it.

    `fixable_by_user` is the field the assistant actually speaks from: it decides
    between "go add a vendor" and "this isn't yours to fix — ask an admin".
    `owner` names who it belongs to when it isn't the user.
    """
    id: str
    layer: str            # rule | config   (field-level gates live in epms-api)
    passed: bool
    message: str | None = None
    fixable_by_user: bool = True
    owner: str | None = None


async def preflight_submit(db: AsyncSession, doc_type: str,
                           doc_id: uuid.UUID) -> list[Check]:
    """Every gate `submit` would hit, evaluated without writing anything.

    Execution stops at the first failure — that is right for execution, wrong
    here. Someone who adds the missing vendor only to hit an unconfigured
    approver, then a budget block, has been sent around three times for what
    could have been one message. So this runs them all.

    Each gate calls the same helper execution calls, and reports the same
    string, so the two cannot drift apart.
    """
    ctx = await load_action_context(db, doc_type, doc_id)
    checks: list[Check] = []

    status = _status_of(ctx.meta, ctx.doc)
    status_ok = status in ctx.meta["valid_submit"]
    checks.append(Check(
        id="status_allows_submit", layer="rule", passed=status_ok,
        message=None if status_ok else msg_cannot_submit(doc_type, status),
        # Already-submitted is the common case here; there is nothing to "fix",
        # the document has simply moved on.
        fixable_by_user=False,
    ))

    if doc_type == "pr":
        blocked = ctx.over_budget_mode == "hard_block"
        checks.append(Check(
            id="budget_hard_block", layer="rule", passed=not blocked,
            message=MSG_BUDGET_HARD_BLOCK if blocked else None,
            fixable_by_user=True,
        ))

    # Routing is evaluated even when an earlier gate already failed — an
    # unconfigured department manager is exactly the kind of thing worth knowing
    # about before you go fix something else and come back.
    sub_workflow = ctx.submit_workflow if ctx.submit_workflow is not None else ctx.workflow
    start, _skipped, claimant_skipped = await submission_landing(
        db, doc_type, ctx, sub_workflow, await submission_start(db, doc_type, ctx, status))
    if claimant_skipped and start >= len(sub_workflow):
        checks.append(Check(
            id="approver_routing", layer="config", passed=False,
            message=MSG_CLAIMANT_HOLDS_EVERY_STEP, fixable_by_user=False, owner="admin",
        ))
    elif start >= len(sub_workflow):
        # Whole workflow auto-skips: submission goes straight to approved.
        checks.append(Check(id="approver_routing", layer="config", passed=True))
    else:
        try:
            await resolve_step_assignment(
                db, doc_type, ctx.doc, start, sub_workflow, ctx.rm, ctx.dept_gm_opm,
                routing_dept_id=ctx.routing_dept_id, director_uid=ctx.director_uid,
                supervisor_uid=ctx.supervisor_uid, cross_dept_pa=ctx.cross_dept_pa)
            checks.append(Check(id="approver_routing", layer="config", passed=True))
        except ValueError as exc:
            checks.append(Check(
                id="approver_routing", layer="config", passed=False,
                message=str(exc),
                # Nobody can self-serve their way out of a department with no
                # manager assigned.
                fixable_by_user=False, owner="admin",
            ))

    return checks


async def execute_action(
    db: AsyncSession,
    doc_type: str,
    doc_id: uuid.UUID,
    action: str,
    actor_id: uuid.UUID,
    actor_role: str,
    comment: str | None = None,
    today: date | None = None,
) -> ActionResult:
    ctx = await load_action_context(db, doc_type, doc_id)
    meta = ctx.meta
    doc = ctx.doc
    step = ctx.step
    cfg = ctx.cfg
    rm = ctx.rm
    dept_gm_opm = ctx.dept_gm_opm
    routing_uid = ctx.routing_uid
    routing_dept_id = ctx.routing_dept_id
    cross_dept_pa = ctx.cross_dept_pa
    director_uid = ctx.director_uid
    supervisor_uid = ctx.supervisor_uid
    dept_has_director = ctx.dept_has_director
    dept_has_supervisor = ctx.dept_has_supervisor
    workflow = ctx.workflow
    over_budget_mode = ctx.over_budget_mode

    act = action.lower()
    now = datetime.now(timezone.utc)

    doc_number = getattr(doc, meta["number_attr"])
    recorded_role = actor_role
    auto_skipped: list[int] = []
    # Hoisted above the action branch so both approve and reject's authorization
    # checks AND the on-behalf-of annotation below can share one computation —
    # neither depends on which action is being taken, only on where the document
    # currently sits in its workflow.
    current_step_role = workflow[step]["role"] if step < len(workflow) else ""
    finance_bp_ids = {uuid.UUID(u) for u in rm.get("finance_bp_user_ids", [])}

    if act == "submit":
        if _status_of(meta, doc) not in meta["valid_submit"]:
            raise ValueError(msg_cannot_submit(doc_type, _status_of(meta, doc)))
        # hard_block: refuse over-budget PR submission outright (PRD OBG / Budget Config)
        if doc_type == "pr" and over_budget_mode == "hard_block":
            raise ValueError(MSG_BUDGET_HARD_BLOCK)
        # Read before the status flips — only a `returned` document resumes.
        resume_at = await submission_start(db, doc_type, ctx, _status_of(meta, doc))
        if ctx.submit_workflow is not None:
            workflow = ctx.submit_workflow
        if doc_type == "pr":
            # Freeze the over-budget mode this approval run walks — see _ob_prefix.
            doc.ob_mode_applied = ctx.submit_ob_mode
        start, skipped_prefix, claimant_skipped = await submission_landing(
            db, doc_type, ctx, workflow, resume_at)
        if claimant_skipped and start >= len(workflow):
            raise ValueError(MSG_CLAIMANT_HOLDS_EVERY_STEP)
        _set_status(meta, doc, "submitted")
        if hasattr(doc, "submitted_at"):
            doc.submitted_at = now
        for idx, skipped_role, reason in skipped_prefix:
            db.add(ApprovalEvent(
                document_type=doc_type, document_id=doc.id, document_number=doc_number,
                step_idx=idx, action="approve", actor_id=actor_id,
                actor_role=skipped_role, comment=reason,
            ))
            auto_skipped.append(idx)
        _set_step(meta, doc, start)
        if start < len(workflow):
            await _create_approve_task(db, doc_type, doc, step=start, workflow=workflow,
                                       meta=meta, rm=rm, dept_gm_opm=dept_gm_opm,
                                       routing_dept_id=routing_dept_id, director_uid=director_uid,
                                       supervisor_uid=supervisor_uid, cross_dept_pa=cross_dept_pa)
        else:
            # Workflow fully auto-skipped at submit → straight to approved.
            _set_status(meta, doc, "approved")
            if hasattr(doc, "approved_at"):
                doc.approved_at = now

    elif act == "approve":
        if _status_of(meta, doc) not in meta["valid_approve"]:
            raise ValueError(f"Cannot approve {doc_type.upper()} in status '{_status_of(meta, doc)}'")

        # Authorization: verify the actor is the assigned approver for this step
        authorized = await _actor_can_approve(
            db, current_step_role, actor_id, actor_role,
            routing_dept_id, rm, dept_gm_opm, finance_bp_ids,
            doc=doc, director_uid=director_uid, supervisor_uid=supervisor_uid,
            today=today, cross_dept_pa=cross_dept_pa,
        )
        if not authorized:
            raise ValueError(
                f"Not authorized to approve this step (requires role: {current_step_role})"
            )

        recorded_role = current_step_role if step < len(workflow) else actor_role
        await _complete_tasks(db, doc_type, doc.id)

        # Auto-skip resolution. For gm_or_opm, _actor_is_step_holder resolves
        # using the routing department (PR-selected, else the requester's own)
        # — not doc.created_by (PO/PA creator) and not doc.department_id (None
        # on PO). routing_dept_id was computed once at the top of
        # execute_action via _routing_department_id.

        next_step = step + 1
        while next_step < len(workflow):
            next_role = workflow[next_step]["role"]
            # Conditional skip (per-document) — fires before the same-approver
            # check so non-GMP visits don't even check who holds the QM role.
            # Includes an over-budget step Budget Config has since stopped
            # requiring (FM approved under fm_gm_opm, mode is now fm_only).
            cond_skip, reason = _step_skip(
                workflow[next_step], doc_type, doc, director_uid, supervisor_uid,
                dept_has_director, dept_has_supervisor, cross_dept_pa,
                ctx.cfg_ob_mode or None)
            if cond_skip:
                db.add(ApprovalEvent(
                    document_type=doc_type, document_id=doc.id, document_number=doc_number,
                    step_idx=next_step, action="approve",
                    actor_id=actor_id, actor_role=next_role,
                    comment=reason,
                ))
                auto_skipped.append(next_step)
                next_step += 1
                continue
            # Same-approver auto-skip. Uses the SAME identity check as
            # authorization (_actor_is_step_holder), so a multi-holder post is
            # recognised here exactly as it is there — the two used to diverge.
            if await _actor_is_step_holder(
                db, next_role, actor_id, routing_dept_id, rm, dept_gm_opm,
                finance_bp_ids, doc, director_uid, supervisor_uid,
            ):
                db.add(ApprovalEvent(
                    document_type=doc_type, document_id=doc.id, document_number=doc_number,
                    step_idx=next_step, action="approve",
                    actor_id=actor_id, actor_role=next_role,
                    comment="Auto-approved (same approver holds both roles)",
                ))
                auto_skipped.append(next_step)
                next_step += 1
                continue
            claimant_reason = await _claimant_step_skip(db, doc_type, ctx, workflow[next_step])
            if claimant_reason:
                db.add(ApprovalEvent(
                    document_type=doc_type, document_id=doc.id, document_number=doc_number,
                    step_idx=next_step, action="approve",
                    actor_id=actor_id, actor_role=next_role,
                    comment=claimant_reason,
                ))
                auto_skipped.append(next_step)
                next_step += 1
                continue
            break

        if next_step < len(workflow):
            _set_step(meta, doc, next_step)
            _set_status(meta, doc, "in_review")
            await _create_approve_task(db, doc_type, doc, step=next_step, workflow=workflow, meta=meta, rm=rm, dept_gm_opm=dept_gm_opm, routing_dept_id=routing_dept_id, director_uid=director_uid, supervisor_uid=supervisor_uid, cross_dept_pa=cross_dept_pa)
        else:
            _set_status(meta, doc, "approved")
            if hasattr(doc, "approved_at"):
                doc.approved_at = now
            post_fn = _POST_APPROVE.get(doc_type)
            if post_fn is None and doc_type.startswith("cfm_"):
                post_fn = _post_approve_exp
            if post_fn:
                await post_fn(db, doc)

    elif act == "return":
        if _status_of(meta, doc) not in meta["valid_return"]:
            raise ValueError(f"Cannot return {doc_type.upper()} in status '{_status_of(meta, doc)}'")

        # Authorization: sending a document back is an approval-step decision, so
        # it requires the SAME authority as approve/reject. This gate used to be
        # missing while reject's was present — any user who could merely SEE a
        # broadcast approve task (or simply knew the document id) could bounce
        # anyone's in-flight document back to its submitter. The calling services
        # do not cover for it either: epms-api's pr/po/pa action endpoints and
        # expense-api's expense_action/pa_action forward to this engine with no
        # authorization of their own.
        authorized = await _actor_can_approve(
            db, current_step_role, actor_id, actor_role,
            routing_dept_id, rm, dept_gm_opm, finance_bp_ids,
            doc=doc, director_uid=director_uid, supervisor_uid=supervisor_uid,
            today=today, cross_dept_pa=cross_dept_pa,
        )
        if not authorized:
            raise ValueError(
                f"Not authorized to return this step (requires role: {current_step_role})"
            )
        await _complete_tasks(db, doc_type, doc.id)
        _set_status(meta, doc, "returned")
        _set_step(meta, doc, 0)
        await _create_revise_task(db, doc_type, doc, meta)

    elif act == "reject":
        if _status_of(meta, doc) not in meta["valid_return"]:
            raise ValueError(f"Cannot reject {doc_type.upper()} in status '{_status_of(meta, doc)}'")

        # Authorization: reject is an approval-step decision, so it requires the
        # SAME authority as approve — the current step's approver (or system_admin).
        # Without this gate any user who could merely SEE a broadcast approve task
        # (get_for_role matches by assigned_role) could reject/cancel the document,
        # even when the same actor is (correctly) denied Approve.
        authorized = await _actor_can_approve(
            db, current_step_role, actor_id, actor_role,
            routing_dept_id, rm, dept_gm_opm, finance_bp_ids,
            doc=doc, director_uid=director_uid, supervisor_uid=supervisor_uid,
            today=today, cross_dept_pa=cross_dept_pa,
        )
        if not authorized:
            raise ValueError(
                f"Not authorized to reject this step (requires role: {current_step_role})"
            )
        await _complete_tasks(db, doc_type, doc.id)
        # PA types use "cancelled" instead of "rejected" (consistent with PA state machine)
        _set_status(meta, doc, "cancelled" if doc_type in ("pa", "pa_dir") else "rejected")

    elif act == "recall":
        if _status_of(meta, doc) not in ("submitted", "in_review"):
            raise ValueError(f"Cannot recall {doc_type.upper()} in status '{_status_of(meta, doc)}'")
        if actor_role != "system_admin" and actor_id != doc.created_by:
            raise ValueError(f"Only the submitter can recall this {doc_type.upper()}")
        await _complete_tasks(db, doc_type, doc.id)
        _set_status(meta, doc, "draft")
        _set_step(meta, doc, 0)

    elif act == "cancel":
        if _status_of(meta, doc) not in meta["valid_cancel"]:
            raise ValueError(f"Cannot cancel {doc_type.upper()} in status '{_status_of(meta, doc)}'")

        # Authorization: cancel is terminal — the document is dead afterwards and
        # its number is spent. It used to have no gate at all, so any logged-in
        # user could kill anyone's draft (and, for PR/PO/AGR/posign, anything
        # merely `submitted`). Three parties may legitimately do it: the person
        # whose document it is, an admin, and the approver currently holding it
        # (for whom cancel is the same call reject already lets them make).
        #
        # KNOWN NARROWING — VMS: vms-api lets a dept_manager cancel a visit in
        # their own department (api/v1/visits.py::cancel), which is broader than
        # the three parties above. It runs that check itself BEFORE forwarding
        # here and wraps this call in `except Exception: pass`, so a manager who
        # is not the current step's approver still cancels the visit locally —
        # the engine-side task is what goes stale. That is a degradation, not a
        # break, and it is preferable to leaving the gate open for every service.
        # If VMS needs the wider rule honoured end to end, the fix belongs there
        # (complete the task directly) or in an explicit per-doc_type cancel
        # policy here — not in reopening this.
        if actor_role != "system_admin" and actor_id != doc.created_by:
            authorized = await _actor_can_approve(
                db, current_step_role, actor_id, actor_role,
                routing_dept_id, rm, dept_gm_opm, finance_bp_ids,
                doc=doc, director_uid=director_uid, supervisor_uid=supervisor_uid,
                today=today, cross_dept_pa=cross_dept_pa,
            )
            if not authorized:
                raise ValueError(
                    f"Not authorized to cancel this {doc_type.upper()} — "
                    "only its owner, an approver currently holding it, or an "
                    "administrator can."
                )
        await _complete_tasks(db, doc_type, doc.id)
        _set_status(meta, doc, "cancelled")

    # NOTE (Phase 0-B1.5): the former "process" (payment) branch was removed.
    # Payment is not a workflow action — it is executed by finance-api's
    # unified executor (POST /finance/v1/payments/execute), which owns
    # can_pay, the status flip, payment_records, invoice marking and the
    # posting event. epms-api / expense-api forward their payment entries there.

    else:
        raise ValueError(f"Unknown action '{act}' for {doc_type.upper()}")

    if act in ("approve", "reject"):
        # Delegation annotation: if the actor reached this step only through
        # someone else's active delegation, name that delegator in the event
        # comment. The actor recorded on the event stays the person who
        # clicked — this is not a transfer of identity, just a note of whose
        # authority was borrowed. Reject is included alongside approve: it is
        # the same approval-step decision under the same authorization check
        # (see _actor_can_approve calls above), so a delegate's rejection
        # deserves the same audit trail as their approval. `current_step_role`
        # (not `recorded_role`) is used here because it is set uniformly by
        # both branches, whereas `recorded_role` only reflects the workflow
        # step role on the approve path.
        on_behalf_of = await _acting_on_behalf_of(
            db, current_step_role, actor_id, routing_dept_id, rm, dept_gm_opm,
            finance_bp_ids, doc, director_uid, supervisor_uid, today=today,
            cross_dept_pa=cross_dept_pa)
        if on_behalf_of is not None:
            delegator_name = (await db.execute(
                select(User.full_name).where(User.id == on_behalf_of))
            ).scalar_one_or_none() or "another approver"
            suffix = f"on behalf of {delegator_name}"
            comment = f"{comment} — {suffix}" if comment else suffix

    # Record the primary approval event
    db.add(ApprovalEvent(
        document_type=doc_type, document_id=doc.id, document_number=doc_number,
        step_idx=step, action=act,
        actor_id=actor_id, actor_role=recorded_role,
        comment=comment,
    ))

    await db.flush()
    await db.refresh(doc)

    new_status = _status_of(meta, doc)
    return ActionResult(
        doc_type=doc_type,
        doc_id=doc.id,
        doc_number=doc_number,
        new_status=new_status,
        approval_step_idx=_step_of(meta, doc),
        workflow_complete=new_status in ("approved", "rejected", "cancelled", "processed"),
        auto_skipped_steps=auto_skipped,
    )


# ── In-flight re-sync (config-change remediation) ─────────────────────────────
#
# When approval routing config changes (dept gm↔opm mapping, Director/Supervisor
# mapping, who holds a role) OR a workflow's step list changes, documents already
# in flight are NOT re-routed: their approval_step_idx can point past/wrong of the
# real pending step (→ step_role="" or the wrong step → _actor_can_approve denies
# everyone → 409 / "no permission"), and/or their open task stays assigned to the
# OLD approver. These helpers realign each stranded doc to the CURRENT config:
#   • the OPEN approve task's role is the reliable "real current step" (skip events
#     aren't always recorded, so replaying events undercounts) — realign
#     approval_step_idx to it;
#   • if that step is now a skippable optional (Director/Supervisor unconfigured),
#     skip-advance to the next real step;
#   • re-resolve the step's approver and reassign the task if it drifted.
# They mutate the session but DO NOT commit — the caller commits (endpoint) or
# rolls back (script dry-run).

_GM_OPM_ROLES = {"gm", "opm", "gm_or_opm"}
# Roles whose approver is a SPECIFIC person resolved from document context (the
# document's department / creator), so their tasks are PINNED (assigned_user_id
# set) and resync re-points them to the current resolved person. gm_or_opm is
# NOT here: gm/opm are singleton posts broadcast by role (assigned_user_id=None),
# handled by the broadcast branch in resync Case C — same as finance_manager etc.
_USER_SPECIFIC_ROLES = {"dept_manager", "director", "supervisor", "quality_manager"}


def _step_index_for_task_role(workflow: list[dict], task_role: str,
                              prefer: int | None = None) -> int | None:
    """Workflow index a task's role belongs to (gm/opm/gm_or_opm → the gm_or_opm
    node). None when the role isn't in this workflow (e.g. a legacy ap_clerk task).

    A role can sit on two nodes — the over-budget GM/OPM step and an ordinary
    GM/OPM step — so `prefer` (the stored step) wins when its node matches;
    otherwise the first match."""
    target = "gm_or_opm" if task_role in _GM_OPM_ROLES else task_role
    if prefer is not None and 0 <= prefer < len(workflow) \
            and workflow[prefer]["role"] == target:
        return prefer
    for i, node in enumerate(workflow):
        if node["role"] == target:
            return i
    return None


async def _resolved_assignee_for_step(
    db, doc_type, doc, step, workflow, rm, dept_gm_opm, routing_dept_id, director_uid, supervisor_uid,
    cross_dept_pa=False,
) -> tuple[str, uuid.UUID | None]:
    """(assigned_role, user_id) the engine WOULD assign this step now — mirrors
    _create_approve_task. user_id is None for broadcast (named) roles."""
    role = workflow[step]["role"]
    if role == "dept_manager":
        return role, await _get_dept_manager_id(db, routing_dept_id)
    if role == "gm_or_opm":
        return await _resolve_gm_or_opm(db, routing_dept_id, rm, dept_gm_opm, cross_dept_pa)
    if role == "director":
        return role, director_uid
    if role == "supervisor":
        return role, supervisor_uid
    if role == "quality_manager" and doc_type == "vms_visit":
        return role, getattr(doc, "quality_approver_id", None)
    return role, None  # broadcast


async def _resync_document(db: AsyncSession, doc_type: str, doc_id: uuid.UUID) -> dict | None:
    """Realign one in-flight document to the current config. Returns a summary of
    what changed, or None if it was already correct / not applicable."""
    meta = _resolve_meta(doc_type)
    Model = meta["model"]
    doc = (await db.execute(select(Model).where(Model.id == doc_id))).scalar_one_or_none()
    if doc is None:
        return None
    number = getattr(doc, meta["number_attr"])

    open_tasks = (await db.execute(select(Task).where(
        Task.document_type == doc_type, Task.document_id == doc.id,
        Task.type.like("approve%"), Task.is_completed.is_(False),
    ))).scalars().all()

    status = _status_of(meta, doc)
    if status not in ("submitted", "in_review"):
        # Terminal document (approved / issued / cancelled / …) with leftover OPEN
        # approve tasks — phantom "pending approvals" that 409 on click. Complete
        # just those approve tasks (never other task types like place_order /
        # create_pa, which are legitimate next-step work on a done document).
        if not open_tasks:
            return None
        now = datetime.now(timezone.utc)
        for t in open_tasks:
            t.is_completed = True
            t.completed_at = now
        await db.flush()
        return {"doc_type": doc_type, "number": number,
                "actions": [f"complete {len(open_tasks)} stale approve task(s) (status={status})"],
                "final_step": _step_of(meta, doc)}

    cfg = await _get_config(db)
    rm = await get_role_management(db)
    dept_gm_opm = await get_dept_gm_opm_mapping(db)
    routing_uid = await _routing_user_id(db, doc_type, doc)
    routing_dept_id = await _routing_department_id(db, doc_type, doc, routing_uid)
    cross_dept_pa = await _is_cross_department_pa(db, doc_type, doc)
    dept_director = await get_dept_director_mapping(db)
    dept_supervisor = await get_dept_supervisor_enabled(db)
    director_uid = await _resolve_director(db, routing_dept_id, dept_director)
    supervisor_uid = await _resolve_supervisor(db, routing_uid, dept_supervisor)
    dept_has_director = bool(routing_dept_id) and str(routing_dept_id) in (dept_director or {})
    dept_has_supervisor = bool(routing_dept_id) and bool((dept_supervisor or {}).get(str(routing_dept_id)))
    workflow = await build_effective_workflow(db, doc_type, doc, cfg)
    cfg_ob_mode = config_ob_mode(cfg) if doc_type == "pr" else None

    # Real current step: the open approve task's role (reliable) → else stored idx.
    if open_tasks:
        mapped = [(_step_index_for_task_role(workflow, t.assigned_role, _step_of(meta, doc)), t)
                  for t in open_tasks]
        mapped = [(i, t) for i, t in mapped if i is not None]
        if not mapped:
            return None  # only unknown-role tasks (e.g. ap_clerk) — leave alone
        true_step = min(i for i, _ in mapped)
    elif _step_of(meta, doc) < len(workflow):
        true_step = _step_of(meta, doc)
    else:
        return None  # no task and step out of range — can't infer, leave alone

    actions: list[str] = []

    # Case A — true step is now skippable: an optional level gone (Director /
    # Supervisor), or an over-budget step Budget Config no longer requires
    # (mode loosened to fm_only / allow — the FM's task moves on), or an OA
    # step whose only holder is the claimant (claims submitted before the
    # claimant skip existed sit there for good). Advance.
    claim_ctx = (await load_action_context(db, doc_type, doc.id)
                 if Model is ExpenseClaim else None)

    async def _skip_reason(i: int) -> str | None:
        sk, reason = _step_skip(
            workflow[i], doc_type, doc, director_uid, supervisor_uid,
            dept_has_director, dept_has_supervisor, cross_dept_pa, cfg_ob_mode)
        if sk:
            return reason
        if claim_ctx is not None:
            return await _claimant_step_skip(db, doc_type, claim_ctx, workflow[i])
        return None

    if await _skip_reason(true_step):
        reasons: list[tuple[int, str]] = []
        while true_step + len(reasons) < len(workflow):
            reason = await _skip_reason(true_step + len(reasons))
            if reason is None:
                break
            reasons.append((true_step + len(reasons), reason))
        if (true_step + len(reasons) >= len(workflow)
                and any(r == CLAIMANT_SKIP_REASON for _, r in reasons)):
            # Same refusal as submit: never approve a claim with nobody but the
            # claimant left to approve it.
            return {"doc_type": doc_type, "number": number, "final_step": _step_of(meta, doc),
                    "actions": [f"WARN claimant holds every remaining step from step{true_step} "
                                "— adjust the workflow, then re-sync"]}
        start = true_step
        skipped: list[str] = []
        for _i, reason in reasons:
            r = workflow[start]["role"]
            db.add(ApprovalEvent(
                document_type=doc_type, document_id=doc.id, document_number=number,
                step_idx=start, action="approve", actor_id=routing_uid, actor_role=r,
                comment=f"Re-synced (config change): {reason}"))
            skipped.append(r)
            start += 1
        await _complete_tasks(db, doc_type, doc.id)
        _set_step(meta, doc, start)
        if start < len(workflow):
            _set_status(meta, doc, "in_review")
            await _create_approve_task(
                db, doc_type, doc, step=start, workflow=workflow, meta=meta, rm=rm,
                dept_gm_opm=dept_gm_opm, routing_dept_id=routing_dept_id,
                director_uid=director_uid, supervisor_uid=supervisor_uid,
                cross_dept_pa=cross_dept_pa)
        else:
            _set_status(meta, doc, "approved")
            if hasattr(doc, "approved_at"):
                doc.approved_at = datetime.now(timezone.utc)
            post_fn = _POST_APPROVE.get(doc_type) or (_post_approve_exp if doc_type.startswith("cfm_") else None)
            if post_fn:
                await post_fn(db, doc)
        actions.append(f"skip {skipped}: step {true_step}->{start}")
        await db.flush()
        return {"doc_type": doc_type, "number": number, "actions": actions, "final_step": _step_of(meta, doc)}

    # Case B — realign the stored step index to the real (task-derived) step.
    if _step_of(meta, doc) != true_step:
        actions.append(f"step {_step_of(meta, doc)}->{true_step}")
        _set_step(meta, doc, true_step)

    # Case C — fix a drifted / stray-step assignee at the real step.
    des_role, des_uid = await _resolved_assignee_for_step(
        db, doc_type, doc, true_step, workflow, rm, dept_gm_opm, routing_dept_id, director_uid,
        supervisor_uid, cross_dept_pa)
    role = workflow[true_step]["role"]
    stray = any(_step_index_for_task_role(workflow, t.assigned_role, true_step) != true_step for t in open_tasks)
    if role in _USER_SPECIFIC_ROLES:
        if des_uid is None:
            actions.append(f"WARN {role} unresolved @step{true_step} — assign one in config, then re-sync")
        elif stray or not any(t.assigned_user_id == des_uid for t in open_tasks):
            await _complete_tasks(db, doc_type, doc.id)
            await _create_approve_task(
                db, doc_type, doc, step=true_step, workflow=workflow, meta=meta, rm=rm,
                dept_gm_opm=dept_gm_opm, routing_dept_id=routing_dept_id,
                director_uid=director_uid, supervisor_uid=supervisor_uid,
                cross_dept_pa=cross_dept_pa)
            actions.append(f"reissue step{true_step} -> {des_role}/{des_uid}")
    else:  # broadcast (named) role — the engine assigns these to NO specific user
        # (role-based: whoever currently holds the role sees & approves it). PMS
        # import PINNED some to a specific user, which stops them from following a
        # role change (e.g. a new Finance Manager). Re-broadcast any pinned one so
        # it tracks the current holder from now on (idempotent: broadcast tasks are
        # left alone on the next run).
        pinned = any(t.assigned_user_id is not None for t in open_tasks)
        if stray or pinned or not open_tasks:
            await _complete_tasks(db, doc_type, doc.id)
            await _create_approve_task(
                db, doc_type, doc, step=true_step, workflow=workflow, meta=meta, rm=rm,
                dept_gm_opm=dept_gm_opm, routing_dept_id=routing_dept_id,
                director_uid=director_uid, supervisor_uid=supervisor_uid,
                cross_dept_pa=cross_dept_pa)
            actions.append(f"reissue step{true_step} -> {des_role}/broadcast"
                           + (" (was pinned)" if pinned else ""))

    if not actions:
        return None
    await db.flush()
    return {"doc_type": doc_type, "number": number, "actions": actions, "final_step": _step_of(meta, doc)}


async def resync_inflight_approvals(db: AsyncSession, *, over_budget_prs_only: bool = False) -> dict:
    """Realign every in-flight document with an open approval task to the current
    routing config. Mutates the session; caller commits/rolls back. Each document
    is isolated in a SAVEPOINT so one bad doc can't corrupt the batch.

    `over_budget_prs_only` narrows it to over-budget PRs — what a Budget Config
    save can affect (a loosened over-budget mode waives their pending steps)."""
    q = (select(Task.document_type, Task.document_id)
         .where(Task.type.like("approve%"), Task.is_completed.is_(False)))
    if over_budget_prs_only:
        q = q.where(Task.document_type == "pr", Task.document_id.in_(
            select(PurchaseRequest.id).where(PurchaseRequest.over_budget.is_(True))))
    rows = (await db.execute(q.distinct())).all()
    resynced: list[dict] = []
    errors: list[dict] = []
    for doc_type, doc_id in rows:
        try:
            async with db.begin_nested():
                summary = await _resync_document(db, doc_type, doc_id)
            if summary:
                resynced.append(summary)
        except Exception as exc:  # keep going; report the offender
            errors.append({"doc_type": doc_type, "doc_id": str(doc_id),
                           "error": f"{type(exc).__name__}: {exc}"})
    return {"resynced": resynced, "errors": errors}
