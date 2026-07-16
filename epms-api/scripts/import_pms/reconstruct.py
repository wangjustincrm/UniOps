"""Phase C — reconstruct approval_events for imported PR / PO / PA.

The legacy PMS did not preserve *who* approved each document, so the audit
trail cannot be recovered exactly. Instead we reconstruct it from two facts we
DO have — the document's creator and the configured approval workflow — and
attribute each approval step to the CURRENT holder of that workflow role.

That attribution is a best-effort approximation, NOT ground truth: every
reconstructed event is tagged ``[reconstructed]`` in its ``comment`` so it can
always be told apart from genuine, live-captured audit records.

Role → actor resolution mirrors the live approval engine:
  * dept_manager        → the active dept_manager in the *requester's*
                          department (PO/PA route via the linked PR), else GM.
  * gm_or_opm           → dept_gm_opm_mapping[requester's dept] (gm/opm), else GM.
  * procurement_manager / finance_bp / finance_manager → role_management config.
  * ap_clerk (PA only)  → the legacy ``HoldBy`` text names the AP clerk who
                          handled the PA; matched to an existing EPMS user via
                          the Applier crosswalk. Blank/unmatched → ZHENG SUE.

Idempotent: a document that already has any approval_events row is skipped.
In dry-run nothing is written; counts still reflect what *would* be created.
"""
from __future__ import annotations

import uuid
from datetime import timedelta

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.approval import ApprovalEvent
from app.models.config import CompanyConfig
from app.models.pa import PaymentApplication
from app.models.po import PurchaseOrder
from app.models.pr import PurchaseRequest
from app.models.task import Task
from app.models.user import User

from . import mappings as M

RECON_TAG = "[reconstructed]"
SUE_EMAIL = "zhengsue@canadaroyalmilk.com"

# Statuses at/after which every workflow step has been fully approved.
_FULLY_APPROVED = {
    "pr": {"approved", "paid", "closed"},
    "po": {"approved", "issued", "partially_received", "fully_received", "closed"},
    "pa": {"approved", "processed"},
}
# Statuses that never reached submission → no events at all.
_NO_EVENTS = {"draft"}


def _uid(val) -> uuid.UUID | None:
    if not val:
        return None
    if isinstance(val, uuid.UUID):
        return val
    try:
        return uuid.UUID(str(val))
    except (ValueError, AttributeError):
        return None


def _existing_user(res, name: str | None) -> uuid.UUID | None:
    """Resolve a person name to an EXISTING EPMS user (crosswalk / full_name /
    <name>@domain), without auto-creating one. Returns None if no match."""
    if not name or not name.strip():
        return None
    email = M.resolve_user_email(name)
    if email:
        uid = res.user_by_email.get(email.strip().lower())
        if uid:
            return uid
    n = name.strip()
    uid = res.user_by_fullname.get(n.lower())
    if uid:
        return uid
    if " " not in n:
        uid = res.user_by_email.get(f"{n}@canadaroyalmilk.com".lower())
        if uid:
            return uid
    return None


def _completed_steps(doc_type: str, status: str, wf_len: int, step_idx: int) -> tuple[int, bool]:
    """Return (number of fully-approved steps, was_rejected).

    Fully-approved statuses get the whole chain; rejected gets a terminal reject
    event at step 0. For in_review the importer sets approval_step_idx to the step
    currently awaiting action, so the prior steps are the completed ones (e.g. a PO
    at GM/OPM APPROVING has step_idx=1 → Procurement Manager already approved).
    """
    if status in _FULLY_APPROVED.get(doc_type, set()):
        return wf_len, False
    if status == "rejected":
        return 0, True
    if status == "in_review":
        return max(0, min(step_idx, wf_len)), False
    return 0, False


class _Recon:
    def __init__(self, db, adder, res, dry_run):
        self.db, self.adder, self.res, self.dry_run = db, adder, res, dry_run
        self.wf: dict = {}
        self.rm: dict = {}
        self.dept_gm_opm: dict = {}
        self.user_dept: dict[uuid.UUID, uuid.UUID | None] = {}
        self.dept_mgr: dict[uuid.UUID, uuid.UUID] = {}
        self.pr_creator: dict[uuid.UUID, uuid.UUID] = {}
        self.po_pr: dict[uuid.UUID, uuid.UUID | None] = {}
        self.gm: uuid.UUID | None = None
        self.sue: uuid.UUID | None = None
        self.fallback: uuid.UUID | None = None
        self.existing: set[uuid.UUID] = set()
        self.stats: dict[str, int] = {
            "docs": 0, "skipped_existing": 0, "submit": 0,
            "approve": 0, "reject": 0, "tasks_created": 0,
            "ap_from_holdby": 0, "ap_to_sue": 0,
        }

    async def prime(self) -> None:
        cfg = (await self.db.execute(select(CompanyConfig).limit(1))).scalar_one_or_none()
        self.wf = (cfg.workflow_defs if cfg else {}) or {}
        # DELIBERATE EXEMPTION from the phase-3 retirement of role_management /
        # dept_gm_opm_mapping (every live consumer now reads identity's user_roles
        # and approval_dept_routing instead — see the phase-3 design doc).
        #
        # This reconstructor infers who approved PMS-era documents that carry no
        # actor of their own. Those JSONB columns are kept precisely as a frozen
        # snapshot of who held each post back then, which is closer to the truth
        # for historical events than today's live assignments would be. It is an
        # offline repair tool, never on a request path.
        #
        # => Do NOT drop company_config.role_management / dept_gm_opm_mapping
        #    without first re-pointing or retiring this script.
        self.rm = (cfg.role_management if cfg else {}) or {}
        self.dept_gm_opm = (cfg.dept_gm_opm_mapping if cfg else {}) or {}

        self.user_dept = {
            uid: dept for uid, dept in (await self.db.execute(
                select(User.id, User.department_id))).all()
        }
        for dept, uid in (await self.db.execute(
            select(User.department_id, User.id).where(
                User.role == "dept_manager", User.is_active.is_(True))
        )).all():
            if dept is not None:
                self.dept_mgr.setdefault(dept, uid)
        self.pr_creator = {
            pid: cby for pid, cby in (await self.db.execute(
                select(PurchaseRequest.id, PurchaseRequest.created_by))).all()
        }
        self.po_pr = {
            oid: prid for oid, prid in (await self.db.execute(
                select(PurchaseOrder.id, PurchaseOrder.pr_id))).all()
        }
        self.gm = _uid(self.rm.get("gm_user_id"))
        self.sue = (await self.db.execute(
            select(User.id).where(func.lower(User.email) == SUE_EMAIL))).scalar_one_or_none()
        self.fallback = self.gm or self.res.system_user_id
        self.existing = set((await self.db.execute(
            select(ApprovalEvent.document_id).where(
                ApprovalEvent.document_type.in_(["pr", "po", "pa"])))).scalars().all())

    def _routing_dept(self, doc_type: str, doc) -> uuid.UUID | None:
        """Department that drives dept_manager / gm_or_opm routing — always the
        originating PR requester's department (PO→PR, PA→PO→PR)."""
        routing_user = doc.created_by
        if doc_type == "po":
            pr_id = self.po_pr.get(doc.id)
            routing_user = self.pr_creator.get(pr_id, doc.created_by) if pr_id else doc.created_by
        elif doc_type == "pa":
            pr_id = self.po_pr.get(doc.po_id)
            routing_user = self.pr_creator.get(pr_id, doc.created_by) if pr_id else doc.created_by
        return self.user_dept.get(routing_user)

    def _actor_for(self, role: str, routing_dept, holdby: str | None) -> uuid.UUID | None:
        if role == "dept_manager":
            return self.dept_mgr.get(routing_dept) or self.gm or self.fallback
        if role == "gm_or_opm":
            resolved = self.dept_gm_opm.get(str(routing_dept), "gm")
            return _uid(self.rm.get(f"{resolved}_user_id")) or self.gm or self.fallback
        if role == "procurement_manager":
            return _uid(self.rm.get("procurement_manager_user_id")) or self.fallback
        if role == "finance_bp":
            ids = self.rm.get("finance_bp_user_ids") or []
            return _uid(ids[0]) if ids else self.fallback
        if role == "finance_manager":
            return _uid(self.rm.get("finance_manager_user_id")) or self.fallback
        if role == "ap_clerk":
            # HoldBy is resolved via the importer's Applier crosswalk (USER_XWALK in
            # mappings.py), which already maps the short labels (Raghav/Cindy/Sue/
            # SUNQI/…) to real users. Unmapped/blank → ZHENG SUE.
            u = _existing_user(self.res, holdby) if holdby else None
            if u:
                self.stats["ap_from_holdby"] += 1
                return u
            self.stats["ap_to_sue"] += 1
            return self.sue or self.fallback
        return self.fallback

    async def _emit(self, ev: ApprovalEvent, key: str) -> None:
        await self.adder.add(ev)
        self.stats[key] += 1

    async def process(self, doc_type: str, doc, holdby: str | None = None) -> None:
        if doc.id in self.existing:
            self.stats["skipped_existing"] += 1
            return
        status = doc.status
        if status in _NO_EVENTS:
            return
        workflow = self.wf.get(doc_type, [])
        n_done, rejected = _completed_steps(
            doc_type, status, len(workflow), getattr(doc, "approval_step_idx", 0) or 0)
        number = getattr(doc, "number", None) or getattr(doc, "pa_number", "")
        created = doc.created_at
        updated = doc.updated_at or created
        span = (updated - created) if updated > created else timedelta(0)
        self.stats["docs"] += 1

        # 1) submit — the creator, always known.
        await self._emit(ApprovalEvent(
            document_type=doc_type, document_id=doc.id, document_number=number,
            step_idx=0, action="submit", actor_id=doc.created_by,
            actor_role="requester", comment=RECON_TAG, created_at=created,
        ), "submit")

        routing_dept = self._routing_dept(doc_type, doc)
        # 2) approve — one per fully-completed workflow step.
        for i in range(n_done):
            role = workflow[i]["role"]
            actor = self._actor_for(role, routing_dept, holdby)
            if actor is None:
                continue
            ts = created + span * ((i + 1) / (n_done + 1)) if span else created + timedelta(seconds=i + 1)
            await self._emit(ApprovalEvent(
                document_type=doc_type, document_id=doc.id, document_number=number,
                step_idx=i, action="approve", actor_id=actor,
                actor_role=role, comment=RECON_TAG, created_at=ts,
            ), "approve")

        # 3) reject — terminal, attributed to the step-0 approver.
        if rejected and workflow:
            actor = self._actor_for(workflow[0]["role"], routing_dept, holdby)
            if actor is not None:
                await self._emit(ApprovalEvent(
                    document_type=doc_type, document_id=doc.id, document_number=number,
                    step_idx=0, action="reject", actor_id=actor,
                    actor_role=workflow[0]["role"], comment=RECON_TAG, created_at=updated,
                ), "reject")

        # 4) pending approval task — docs still mid-approval (submitted/in_review)
        #    get an approve_{pr|po|pa} task at the CURRENT step, targeted at the
        #    approver who should act on it (mirrors the live engine's _create_task
        #    so it lands in that person's Task Inbox).
        if status in ("submitted", "in_review") and n_done < len(workflow):
            cur = workflow[n_done]
            approver = self._actor_for(cur["role"], routing_dept, holdby)
            amount = (doc.amount if doc_type == "pr"
                      else doc.total if doc_type == "po"
                      else getattr(doc, "payment_amount", None))
            await self.adder.add(Task(
                type=f"approve_{doc_type}", priority="normal",
                document_type=doc_type, document_id=doc.id, document_number=number,
                assigned_role=cur["role"], assigned_user_id=approver,
                title=f"Approve {doc_type.upper()}: {number} — {doc.title}",
                description=f"Step {n_done + 1}/{len(workflow)}: {cur['label']} review required.",
                amount=amount, vendor=getattr(doc, "vendor_name", None),
            ))
            self.stats["tasks_created"] += 1


async def reconstruct_events(
    db: AsyncSession,
    adder,
    res,
    *,
    dry_run: bool,
    pa_holdby: dict[str, str | None] | None = None,
    only: set[str] | None = None,
) -> dict[str, int]:
    """Reconstruct approval_events for every imported PR/PO/PA lacking them.

    ``pa_holdby`` maps pa_number → legacy HoldBy text (for ap_clerk attribution).
    ``adder`` is the loader's batching adder (a no-op in dry-run).
    """
    pa_holdby = pa_holdby or {}
    recon = _Recon(db, adder, res, dry_run)
    await recon.prime()

    run_all = not only
    if run_all or "pr" in only:
        for pr in (await db.execute(select(PurchaseRequest))).scalars():
            await recon.process("pr", pr)
    if run_all or "po" in only:
        for po in (await db.execute(select(PurchaseOrder))).scalars():
            await recon.process("po", po)
    if run_all or "pa" in only:
        for pa in (await db.execute(select(PaymentApplication))).scalars():
            await recon.process("pa", pa, holdby=pa_holdby.get(pa.pa_number))
    await adder.flush()

    s = recon.stats
    print(
        "\nApproval-event reconstruction "
        f"({'DRY-RUN' if dry_run else 'COMMITTED'}):\n"
        f"  docs processed:        {s['docs']}\n"
        f"  skipped (had events):  {s['skipped_existing']}\n"
        f"  submit events:         {s['submit']}\n"
        f"  approve events:        {s['approve']}\n"
        f"  reject events:         {s['reject']}\n"
        f"  pending approval tasks:{s['tasks_created']}\n"
        f"  ap_clerk from HoldBy:  {s['ap_from_holdby']}\n"
        f"  ap_clerk → ZHENG SUE:  {s['ap_to_sue']}"
    )
    return s
