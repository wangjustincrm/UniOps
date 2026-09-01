"""EPMS entity registry: schema metadata + cascade handlers (Phase 1).

Each entity exposes:
- a render/edit schema (EntitySchema)
- cascade_preview(db, row): counts only, NO mutation
- cascade_delete(db, row): deletes the child subtree + polymorphic workflow refs
  (tasks/approval_events). The CALLER commits.

Cascade handlers run leaf-first so RESTRICT foreign keys never block the delete:
Invoice -> PA -> GR -> PO -> PR. Line items and *_attachment rows are removed
automatically by their ondelete=CASCADE FKs when the parent row is deleted.

"Leaf-first" is only true if each leaf's DELETE actually REACHES the database
before its parent's does. The session runs with autoflush=False, so without an
explicit flush every db.delete() in a cascade merely queues, and the whole tree
goes out in one flush whose statement order SQLAlchemy derives from mapper
relationships — of which there are NONE between these documents (Invoice.gr_id,
Invoice.po_id, GoodsReceipt.po_id ... are plain FK columns, no relationship()).
The unit of work therefore has no idea invoices must go before goods_receipts,
and emitted the parent DELETE first: deleting a GR that carried an invoice died
on `invoices_gr_id_fkey`, i.e. a 500 the confirm dialog showed as "Failed to
fetch" (an unhandled 500 bypasses the CORS middleware). Hence: every handler
flushes right after deleting its own row, so it is on disk before the caller
touches the parent. The caller still owns the commit.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Awaitable, Callable

from sqlalchemy import and_, func, or_, select, text, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.admin.cascade import count_polymorphic, purge_workflow_refs
from app.admin.fields import EntitySchema, FieldSpec, ChildSchema
from app.crud.gr import resync_po_received_qty
from app.crud.invoice import (
    _apply_gr_selection,
    _recompute_consumed,
    _release_agreement_evidence,
    gr_unlink_blocker,
)
from app.models.agreement import PurchaseAgreement
from app.models.agreement_attachment import AgreementAttachment
from app.models.agreement_receipt import AgreementReceipt
from app.models.agreement_receipt_attachment import AgreementReceiptAttachment
from app.models.agreement_schedule import AgreementPaymentSchedule
from app.models.approval import ApprovalEvent
from app.models.gr import GoodsReceipt
from app.models.invoice import Invoice
from app.models.pa import PaymentApplication, PaLineItem
from app.models.po import PurchaseOrder, PoLineItem
from app.models.pr import PurchaseRequest, PrLineItem
from app.models.task import Task

CascadeFn = Callable[[AsyncSession, object], Awaitable[dict[str, int]]]


@dataclass
class EntitySpec:
    schema: EntitySchema
    model: type
    system: str
    cascade_preview: CascadeFn
    cascade_delete: CascadeFn


# ── helpers ──────────────────────────────────────────────────────────────────

def _merge(into: dict, add: dict) -> dict:
    for k, v in add.items():
        into[k] = into.get(k, 0) + v
    return into


async def _children(db: AsyncSession, model, attr: str, value):
    return (await db.execute(select(model).where(getattr(model, attr) == value))).scalars().all()


async def _count_children(db: AsyncSession, model, attr: str, value) -> int:
    stmt = select(func.count()).select_from(model).where(getattr(model, attr) == value)
    return int((await db.execute(stmt)).scalar_one())


async def _wf_counts(db: AsyncSession, did) -> dict[str, int]:
    return {
        "tasks": await count_polymorphic(db, Task, did),
        "approval_events": await count_polymorphic(db, ApprovalEvent, did),
    }


# ── Invoice (leaf) ───────────────────────────────────────────────────────────

async def _invoice_delete(db: AsyncSession, inv) -> dict[str, int]:
    # Whole-branch review finding: agreement_payment_schedule.invoice_id has
    # no FK back to invoices (a shared table three other services also touch,
    # so no cross-service FK was ever declared) — deleting the invoice out
    # from under a claimed schedule row used to strand it "received" pointing
    # at a now-nonexistent invoice: never re-claimable (status never returns
    # to "pending"/"overdue") and never swept by the overdue sweep (which
    # only ever touches "pending" rows). Release it back first, the same
    # helper every other detach path (route switch, match_review reject) uses
    # — it also releases any claimed agreement receipts (Task 4), for the
    # same reason on the house_account side.
    await _release_agreement_evidence(db, inv)
    agreement_id = inv.agreement_id
    summary = await purge_workflow_refs(db, inv.id)
    await db.delete(inv)            # invoice has no child tables in epms
    await db.flush()                # leaf-first: land it before any parent DELETE
    # consumed_amount is DERIVED from the invoice set (crud.invoice._recompute_consumed)
    # and drives the NTE warning banner. crud.invoice.delete recomputes it on every
    # hard delete for exactly that reason; this cascade never did, so deleting an
    # agreement-matched invoice through Data Maintenance left the agreement
    # permanently over-consumed by a row that no longer exists. The flush above
    # is what lets the sum run without still seeing the pending delete.
    if agreement_id is not None:
        await _recompute_consumed(db, agreement_id)
    return _merge(summary, {"invoices": 1})


async def _invoice_preview(db: AsyncSession, inv) -> dict[str, int]:
    return _merge({"invoices": 1}, await _wf_counts(db, inv.id))


# ── PA ────────────────────────────────────────────────────────────────────────

async def _table_exists(db: AsyncSession, name: str) -> bool:
    return (await db.execute(text("SELECT to_regclass(:n)"), {"n": name})).scalar_one() is not None


async def _pa_count_payment_records(db: AsyncSession, pa_id) -> int:
    # finance-api owns payment_records (same shared DB). Absent in the epms test DB,
    # so guard on existence; count via raw SQL in prod.
    if not await _table_exists(db, "payment_records"):
        return 0
    return int((await db.execute(
        text("SELECT count(*) FROM payment_records WHERE pa_id = :pid"), {"pid": pa_id}
    )).scalar_one())


async def _pa_delete(db: AsyncSession, pa) -> dict[str, int]:
    summary: dict[str, int] = {}
    # Clear self-referential prepayment links (prepayment_pa_id is RESTRICT) so a PA
    # used as another PA's prepayment can still be deleted.
    await db.execute(
        update(PaymentApplication)
        .where(PaymentApplication.prepayment_pa_id == pa.id)
        .values(prepayment_pa_id=None)
    )
    # Delete finance payment_records that reference this PA (no ondelete → would
    # block the delete). Bank-recon rows reference payment_records with SET NULL,
    # so they detach automatically.
    pr_count = await _pa_count_payment_records(db, pa.id)
    if pr_count:
        await db.execute(text("DELETE FROM payment_records WHERE pa_id = :pid"), {"pid": pa.id})
        summary["payment_records"] = pr_count
    # Self-heal (mirror of crud.pa._complete_create_pa_tasks): creating a PA
    # COMPLETES the PO's create_pa task(s). If this is the LAST PA for the PO,
    # deleting it means no PA covers the PO anymore — reopen those tasks so the
    # requester keeps an entry point to create a PA. Otherwise Data Maintenance
    # delete strands the PO with a done create_pa task and no PA, and a requester
    # (whose only path is that task) can no longer create one. Skip when another
    # PA still covers the PO. During a PO cascade delete the reopened task is
    # purged with the PO anyway, so this is harmless there.
    if pa.po_id is not None:
        others = (await db.execute(
            select(func.count()).select_from(PaymentApplication)
            .where(PaymentApplication.po_id == pa.po_id, PaymentApplication.id != pa.id)
        )).scalar_one()
        if others == 0:
            gr_ids = select(GoodsReceipt.id).where(GoodsReceipt.po_id == pa.po_id)
            reopened = (await db.execute(
                update(Task)
                .where(Task.type == "create_pa", Task.is_completed.is_(True),
                       or_(and_(Task.document_type == "po", Task.document_id == pa.po_id),
                           and_(Task.document_type == "gr", Task.document_id.in_(gr_ids))))
                .values(is_completed=False, completed_at=None)
            )).rowcount
            if reopened:
                summary["create_pa_tasks_reopened"] = reopened
    _merge(summary, await purge_workflow_refs(db, pa.id))
    await db.delete(pa)             # pa_line_items + pa_attachment cascade via FK
    await db.flush()
    return _merge(summary, {"payment_applications": 1})


async def _pa_preview(db: AsyncSession, pa) -> dict[str, int]:
    summary = {"payment_applications": 1}
    pr_count = await _pa_count_payment_records(db, pa.id)
    if pr_count:
        summary["payment_records"] = pr_count
    return _merge(summary, await _wf_counts(db, pa.id))


# ── GR (invoices are UNLINKED, never deleted) ──────────────────────────────────
# User decision (2026-09-01): deleting a goods receipt must not take an invoice
# with it. A GR and an invoice are MATCHED, not parent and child — the invoice is
# a financial record that outlives a mis-keyed receipt, and the system already has
# a name for what this needs: unmatch-gr, which withdraws the receipt evidence and
# leaves the invoice, its status and its PO match standing. So the cascade does
# exactly that, and refuses the whole delete when withdrawing would be unsafe
# (crud.invoice.gr_unlink_blocker — the same three conditions the AP-facing
# Unmatch button is gated on, so this cannot become a back door around them).


def _blocker_sentence(b) -> str:
    if b.code == "agreement":
        return (f"invoice {b.invoice_ref} is matched to an agreement — unlink it from the "
                "agreement's own receipt panel first")
    if b.code == "in_payment":
        return (f"invoice {b.invoice_ref} has already entered payment (status "
                f"'{b.invoice_status}')")
    return (f"invoice {b.invoice_ref} is claimed by Payment Application {b.pa_number} "
            f"({b.pa_status})")


async def _gr_invoices(db: AsyncSession, gr, seen_invoices: set | None) -> list:
    """The invoices this GR delete has to deal with.

    `seen_invoices` is how a PO cascade says "these are already being deleted by
    the PO branch" — they are neither unlinked nor a reason to refuse, and
    counting them again is what used to make the dialog say "2 invoices" for one
    row. In the DELETE path the same exclusion happens for free: the PO branch
    deletes and flushes first, so this query no longer returns them.
    """
    return [inv for inv in await _children(db, Invoice, "gr_id", gr.id)
            if seen_invoices is None or inv.id not in seen_invoices]


async def _gr_invoice_blockers(db: AsyncSession, gr, seen_invoices: set | None = None) -> list:
    out = []
    for inv in await _gr_invoices(db, gr, seen_invoices):
        b = await gr_unlink_blocker(db, inv)
        if b is not None:
            out.append(b)
    return out


async def _gr_delete(db: AsyncSession, gr, *, resync_po: bool = True) -> dict[str, int]:
    summary: dict[str, int] = {}
    po_id, gr_id = gr.po_id, gr.id
    blockers = await _gr_invoice_blockers(db, gr)
    if blockers:
        raise ValueError(
            "Cannot delete this goods receipt: " + "; ".join(_blocker_sentence(b) for b in blockers)
            + ". Resolve that first — deleting the receipt would pull the evidence out from "
            "under it."
        )
    unlinked = 0
    for inv in await _children(db, Invoice, "gr_id", gr.id):
        # Clears gr_id / gr_ids / gr_number / gr_value. The PO match, the
        # allocations and the invoice's status are deliberately untouched —
        # same helper, same effect as crud.invoice.unmatch_gr.
        await _apply_gr_selection(db, inv, [])
        unlinked += 1
    if unlinked:
        summary["invoices_unlinked"] = unlinked
    _merge(summary, await purge_workflow_refs(db, gr.id))
    await db.flush()                # land the unlink before the GR row goes
    await db.delete(gr)             # gr_line_items + gr_attachment cascade via FK
    await db.flush()                # also lets the GR's lines leave before the resync below
    if resync_po:
        # po_line_items.received_qty only ever accumulated, so without this the PO
        # keeps crediting itself for goods this receipt brought: the outstanding
        # quantity a replacement GR needs stays eaten, and a PO left at
        # fully_received can never accept one (api/v1/gr.py gates on the status).
        changed = await resync_po_received_qty(db, po_id, exclude_gr_ids=(gr_id,))
        if changed:
            _merge(summary, {"po_lines_received_qty_resynced": changed})
    return _merge(summary, {"goods_receipts": 1})


async def _gr_preview(db: AsyncSession, gr, *, seen_invoices: set | None = None) -> dict[str, int]:
    summary: dict[str, int] = {"goods_receipts": 1}
    n = len(await _gr_invoices(db, gr, seen_invoices))
    if n:
        summary["invoices_unlinked"] = n
    # Surfaced in the SAME preview the confirm dialog renders, so the admin sees
    # the refusal before clicking rather than after it — the shape the agreement
    # entity already uses for its blockers.
    for b in await _gr_invoice_blockers(db, gr, seen_invoices):
        _merge(summary, {f"blocked_by_{b.code}_invoices": 1})
    return _merge(summary, await _wf_counts(db, gr.id))


# ── PO (blocked by GR/Invoice/PA on po_id) ──────────────────────────────────────

async def _po_delete(db: AsyncSession, po) -> dict[str, int]:
    summary: dict[str, int] = {}
    for inv in await _children(db, Invoice, "po_id", po.id):
        _merge(summary, await _invoice_delete(db, inv))
    for pa in await _children(db, PaymentApplication, "po_id", po.id):
        _merge(summary, await _pa_delete(db, pa))
    for gr in await _children(db, GoodsReceipt, "po_id", po.id):
        # The PO and its lines are going away with this cascade — there is nothing
        # left to give the quantities back to.
        _merge(summary, await _gr_delete(db, gr, resync_po=False))
    # Clear the originating PR's back-reference so it does not dangle.
    for pr in await _children(db, PurchaseRequest, "po_id", po.id):
        pr.po_id = None
        pr.po_number = None
    _merge(summary, await purge_workflow_refs(db, po.id))
    await db.delete(po)             # po_line_items + po_attachment cascade via FK
    await db.flush()
    return _merge(summary, {"purchase_orders": 1})


async def _po_preview(db: AsyncSession, po, *, seen_grs: set | None = None) -> dict[str, int]:
    summary: dict[str, int] = {"purchase_orders": 1}
    seen_invoices: set = set()
    for inv in await _children(db, Invoice, "po_id", po.id):
        seen_invoices.add(inv.id)
        _merge(summary, await _invoice_preview(db, inv))
    for pa in await _children(db, PaymentApplication, "po_id", po.id):
        _merge(summary, await _pa_preview(db, pa))
    for gr in await _children(db, GoodsReceipt, "po_id", po.id):
        if seen_grs is not None:
            seen_grs.add(gr.id)
        _merge(summary, await _gr_preview(db, gr, seen_invoices=seen_invoices))
    return _merge(summary, await _wf_counts(db, po.id))


# ── PR (blocked by PO.pr_id, GR.pr_id) ──────────────────────────────────────────

async def _pr_delete(db: AsyncSession, pr) -> dict[str, int]:
    summary: dict[str, int] = {}
    for po in await _children(db, PurchaseOrder, "pr_id", pr.id):
        _merge(summary, await _po_delete(db, po))
    # GRs tied directly to the PR but not via a (now-deleted) PO.
    for gr in await _children(db, GoodsReceipt, "pr_id", pr.id):
        _merge(summary, await _gr_delete(db, gr))
    _merge(summary, await purge_workflow_refs(db, pr.id))
    await db.delete(pr)             # pr_line_items + pr_attachment cascade via FK
    await db.flush()
    return _merge(summary, {"purchase_requests": 1})


async def _pr_preview(db: AsyncSession, pr) -> dict[str, int]:
    summary: dict[str, int] = {"purchase_requests": 1}
    # Same double-count on the level above: a GR usually carries BOTH pr_id and
    # po_id, so it is reachable through the PO branch and again directly.
    seen_grs: set = set()
    for po in await _children(db, PurchaseOrder, "pr_id", pr.id):
        _merge(summary, await _po_preview(db, po, seen_grs=seen_grs))
    for gr in await _children(db, GoodsReceipt, "pr_id", pr.id):
        if gr.id in seen_grs:
            continue
        _merge(summary, await _gr_preview(db, gr))
    return _merge(summary, await _wf_counts(db, pr.id))


# ── Agreement Receipt (leaf) ────────────────────────────────────────────────

async def _receipt_release_claim(db: AsyncSession, receipt) -> int:
    """Drop a receipt out of the invoice that claims it.

    invoices.receipt_ids and agreement_receipts.invoice_id are two halves of one
    link with no FK between them (receipt_ids is a JSONB array), so nothing in the
    schema keeps them consistent — the codebase has already been burned by exactly
    that drift, which is why crud.invoice._release_agreement_evidence exists to own
    the release from the invoice side. This is the mirror: the receipt is going
    away, so the id must come out of the array. Leaving it behind points every
    reader of that array (reconciliation totals, the receipts panel) at a row that
    no longer exists.

    Only the invoice this receipt actually names is touched, and only if the array
    really lists it — the same defensive shape as the helper on the other side.
    """
    if receipt.invoice_id is None:
        return 0
    inv = (await db.execute(
        select(Invoice).where(Invoice.id == receipt.invoice_id)
    )).scalar_one_or_none()
    if inv is None:
        return 0
    ids = [str(x) for x in (inv.receipt_ids or [])]
    if str(receipt.id) not in ids:
        return 0
    remaining = [x for x in ids if x != str(receipt.id)]
    inv.receipt_ids = remaining or None
    if not remaining:
        # The variance note describes a set of receipts that no longer exists.
        inv.receipt_variance_reason = None
    return 1


async def _receipt_delete(db: AsyncSession, receipt) -> dict[str, int]:
    summary: dict[str, int] = {}
    released = await _receipt_release_claim(db, receipt)
    if released:
        summary["invoice_claims_released"] = released
    att = await _count_children(db, AgreementReceiptAttachment, "receipt_id", receipt.id)
    if att:
        summary["agreement_receipt_attachments"] = att
    _merge(summary, await purge_workflow_refs(db, receipt.id))
    await db.delete(receipt)        # agreement_receipt_attachments cascade via FK
    await db.flush()
    return _merge(summary, {"agreement_receipts": 1})


async def _receipt_preview(db: AsyncSession, receipt) -> dict[str, int]:
    summary: dict[str, int] = {"agreement_receipts": 1}
    att = await _count_children(db, AgreementReceiptAttachment, "receipt_id", receipt.id)
    if att:
        summary["agreement_receipt_attachments"] = att
    if receipt.invoice_id is not None:
        summary["invoice_claims_released"] = 1
    return _merge(summary, await _wf_counts(db, receipt.id))


# ── Agreement (refuses while invoices / PAs reference it) ───────────────────

async def _agreement_blockers(db: AsyncSession, agr) -> dict[str, int]:
    """Downstream documents whose FK to the agreement is RESTRICT."""
    out: dict[str, int] = {}
    n = await _count_children(db, Invoice, "agreement_id", agr.id)
    if n:
        out["blocked_by_invoices"] = n
    n = await _count_children(db, PaymentApplication, "agreement_id", agr.id)
    if n:
        out["blocked_by_payment_applications"] = n
    return out


async def _agreement_delete(db: AsyncSession, agr) -> dict[str, int]:
    # User decision (2026-08-14): unlike PR/PO/GR this does NOT cascade into the
    # documents below it. invoices.agreement_id and payment_applications.agreement_id
    # are RESTRICT and those rows are real financial records — an admin removing a
    # mis-keyed agreement should not silently take an invoice or a payment with it.
    blockers = await _agreement_blockers(db, agr)
    if blockers:
        parts = []
        if blockers.get("blocked_by_invoices"):
            parts.append(f"{blockers['blocked_by_invoices']} invoice(s)")
        if blockers.get("blocked_by_payment_applications"):
            parts.append(f"{blockers['blocked_by_payment_applications']} payment application(s)")
        raise ValueError(
            f"Cannot delete: {' and '.join(parts)} still reference this agreement. "
            "Delete those records first."
        )

    summary: dict[str, int] = {}
    # Receipts go through the receipt handler rather than the DB's ondelete=CASCADE:
    # the cascade would drop the rows without releasing the invoice claim, leaving
    # dangling ids in invoices.receipt_ids. One handler, one口径.
    for r in await _children(db, AgreementReceipt, "agreement_id", agr.id):
        _merge(summary, await _receipt_delete(db, r))

    # Same shape for schedule rows: an invoice can hold schedule_id without an
    # agreement link surviving on it (a detach path that only cleared one side).
    # The blocker check above makes this near-unreachable; it costs one UPDATE and
    # removes the last way this delete can strand a pointer.
    sched_ids = select(AgreementPaymentSchedule.id).where(
        AgreementPaymentSchedule.agreement_id == agr.id)
    await db.execute(
        update(Invoice).where(Invoice.schedule_id.in_(sched_ids)).values(schedule_id=None))

    sched = await _count_children(db, AgreementPaymentSchedule, "agreement_id", agr.id)
    if sched:
        summary["agreement_payment_schedule"] = sched
    att = await _count_children(db, AgreementAttachment, "agreement_id", agr.id)
    if att:
        summary["agreement_attachments"] = att

    _merge(summary, await purge_workflow_refs(db, agr.id))
    await db.delete(agr)   # schedule + attachments cascade via FK
    await db.flush()
    return _merge(summary, {"purchase_agreements": 1})


async def _agreement_preview(db: AsyncSession, agr) -> dict[str, int]:
    summary: dict[str, int] = {"purchase_agreements": 1}
    for r in await _children(db, AgreementReceipt, "agreement_id", agr.id):
        _merge(summary, await _receipt_preview(db, r))
    sched = await _count_children(db, AgreementPaymentSchedule, "agreement_id", agr.id)
    if sched:
        summary["agreement_payment_schedule"] = sched
    att = await _count_children(db, AgreementAttachment, "agreement_id", agr.id)
    if att:
        summary["agreement_attachments"] = att
    _merge(summary, await _wf_counts(db, agr.id))
    # Surfaced in the SAME preview the confirm dialog renders, so the admin sees why
    # the delete will be refused before clicking it rather than after a 400.
    return _merge(summary, await _agreement_blockers(db, agr))


# ── Line-item child schemas ─────────────────────────────────────────────────
# line_total is server-computed (recompute.py) → read-only in each child schema.

_PR_CHILD = ChildSchema(
    table_label="Line Items", model=PrLineItem, fk_field="pr_id",
    fields=[
        FieldSpec("description", "string", True),
        FieldSpec("material_id", "string", True),
        FieldSpec("supplier_item_id", "string", True),
        FieldSpec("qty", "decimal", True),
        FieldSpec("unit", "string", True),
        FieldSpec("unit_price", "decimal", True),
        FieldSpec("line_total", "decimal", False),   # server-computed
        FieldSpec("notes", "string", True),
        FieldSpec("sort_order", "number", True),
    ],
)
_PO_CHILD = ChildSchema(
    table_label="Line Items", model=PoLineItem, fk_field="po_id",
    fields=[
        FieldSpec("description", "string", True),
        FieldSpec("material_id", "string", True),
        FieldSpec("supplier_item_id", "string", True),
        FieldSpec("qty", "decimal", True),
        FieldSpec("unit", "string", True),
        FieldSpec("unit_price", "decimal", True),
        FieldSpec("line_total", "decimal", False),
        # Editable as the escape hatch for POs whose received_qty drifted before
        # deleting a GR gave it back. Any later GR delete or status change on this
        # PO recomputes from the receipts and overwrites a hand-entered value —
        # the GRs are the record of what arrived, this is only a repair tool.
        FieldSpec("received_qty", "decimal", True, label="Received Qty"),
        FieldSpec("notes", "string", True),
        FieldSpec("sort_order", "number", True),
    ],
)
_PA_CHILD = ChildSchema(
    table_label="Line Items", model=PaLineItem, fk_field="pa_id",
    fields=[
        FieldSpec("description", "string", True),
        FieldSpec("qty", "decimal", True),
        FieldSpec("unit", "string", True),
        FieldSpec("unit_price", "decimal", True),
        FieldSpec("line_total", "decimal", False),
        FieldSpec("notes", "string", True),
        FieldSpec("sort_order", "number", True),
    ],
)

# ── Schemas ──────────────────────────────────────────────────────────────────
# Field names verified against the ORM models. Identity/number/created_by/totals
# are read-only; status, names, amounts, notes, dates are editable.

_PR_SCHEMA = EntitySchema(
    key="pr", label="Purchase Request", number_field="number",
    list_columns=["number", "title", "status", "amount", "vendor_name", "created_at"],
    search_fields=["number", "title", "vendor_name"], order_by="created_at desc",
    fields=[
        FieldSpec("number", "string", False),
        FieldSpec("title", "string", True),
        FieldSpec("status", "enum", True, options=["draft", "submitted", "in_review", "approved", "returned", "rejected", "paid"]),
        FieldSpec("type", "number", True),
        FieldSpec("currency", "string", True),
        FieldSpec("amount", "decimal", False),
        FieldSpec("created_by", "reference", True, label="Requester", ref_source="users"),
        FieldSpec("vendor_id", "reference", True, label="Vendor", ref_source="vendors", ref_name_field="vendor_name"),
        FieldSpec("vendor_name", "string", False),
        FieldSpec("cost_center_id", "reference", True, label="Cost Center", ref_source="cost_centers", ref_name_field="cost_center_name"),
        FieldSpec("cost_center_name", "string", False),
        FieldSpec("department_name", "string", True),
        FieldSpec("budget_code", "string", True),
        FieldSpec("project_code", "string", True),
        FieldSpec("delivery_address", "string", True),
        FieldSpec("is_prepaid", "bool", True),
        FieldSpec("over_budget", "bool", True),
        FieldSpec("over_budget_justification", "string", True),
        FieldSpec("notes", "string", True),
        FieldSpec("required_by", "date", True),
        FieldSpec("created_at", "datetime", False),
        FieldSpec("approval_step_idx", "number", False),
    ],
    child=_PR_CHILD,
)

_PO_SCHEMA = EntitySchema(
    key="po", label="Purchase Order", number_field="number",
    list_columns=["number", "title", "status", "total", "vendor_name", "created_at"],
    search_fields=["number", "title", "vendor_name"], order_by="created_at desc",
    fields=[
        FieldSpec("number", "string", False),
        FieldSpec("title", "string", True),
        FieldSpec("status", "enum", True, options=["draft", "submitted", "in_review", "approved", "issued", "partially_received", "fully_received", "closed", "cancelled"]),
        FieldSpec("type", "number", True),
        FieldSpec("currency", "string", True),
        FieldSpec("subtotal", "decimal", False),
        FieldSpec("tax_rate", "decimal", True),
        FieldSpec("tax_code", "string", True),
        FieldSpec("tax_amount", "decimal", False),
        FieldSpec("total", "decimal", False),
        FieldSpec("created_by", "reference", True, label="Requester", ref_source="users"),
        FieldSpec("vendor_id", "reference", True, label="Vendor", ref_source="vendors", ref_name_field="vendor_name"),
        FieldSpec("vendor_name", "string", False),
        FieldSpec("budget_code", "string", True),
        FieldSpec("expected_delivery", "date", True),
        FieldSpec("delivery_address", "string", True),
        FieldSpec("is_prepaid", "bool", True),
        FieldSpec("notes", "string", True),
        FieldSpec("created_at", "datetime", False),
        FieldSpec("approval_step_idx", "number", False),
    ],
    child=_PO_CHILD,
)

_GR_SCHEMA = EntitySchema(
    key="gr", label="Goods Receipt", number_field="number",
    list_columns=["number", "title", "status", "vendor_name", "created_at"],
    search_fields=["number", "title", "vendor_name"], order_by="created_at desc",
    fields=[
        FieldSpec("number", "string", False),
        FieldSpec("title", "string", True),
        FieldSpec("status", "enum", True, options=["pending_ack", "collection_pending", "collected", "confirmed", "discrepancy", "cancelled"]),
        FieldSpec("gr_type", "string", True),
        FieldSpec("vendor_name", "string", True),
        FieldSpec("notes", "string", True),
        FieldSpec("created_at", "datetime", False),
    ],
)

_INVOICE_SCHEMA = EntitySchema(
    key="invoice", label="Invoice", number_field="internal_ref",
    list_columns=["internal_ref", "vendor_invoice_number", "status", "total_amount", "vendor_name", "created_at"],
    search_fields=["internal_ref", "vendor_invoice_number", "vendor_name"], order_by="created_at desc",
    fields=[
        FieldSpec("internal_ref", "string", False),
        FieldSpec("vendor_invoice_number", "string", True),
        FieldSpec("status", "enum", True, options=["unmatched", "matched", "exception", "approved", "paid"]),
        FieldSpec("currency", "string", True),
        FieldSpec("amount", "decimal", True),
        FieldSpec("tax_amount", "decimal", True),
        FieldSpec("total_amount", "decimal", True),
        FieldSpec("vendor_name", "string", True),
        FieldSpec("invoice_date", "date", True),
        FieldSpec("due_date", "date", True),
        FieldSpec("notes", "string", True),
        FieldSpec("created_at", "datetime", False),
    ],
)

_PA_SCHEMA = EntitySchema(
    key="pa", label="Payment Application", number_field="pa_number",
    list_columns=["pa_number", "title", "status", "payment_amount", "vendor_name", "created_at"],
    search_fields=["pa_number", "title", "vendor_name"], order_by="created_at desc",
    fields=[
        FieldSpec("pa_number", "string", False),
        FieldSpec("title", "string", True),
        FieldSpec("status", "enum", True, options=["draft", "submitted", "in_review", "approved", "processed", "returned", "cancelled"]),
        FieldSpec("pa_type", "string", True),
        FieldSpec("currency", "string", True),
        FieldSpec("subtotal", "decimal", False),
        FieldSpec("tax_rate", "decimal", True),
        FieldSpec("tax_code", "string", True),
        FieldSpec("tax_amount", "decimal", False),
        FieldSpec("shipping_amount", "decimal", True),
        FieldSpec("other_charges", "decimal", True),
        FieldSpec("other_charges_note", "string", True),
        FieldSpec("payment_amount", "decimal", False),
        FieldSpec("created_by", "reference", True, label="PA Creator (AP Clerk)", ref_source="users"),
        FieldSpec("source_requester_id", "reference", True, label="Source Requester (PR creator)", ref_source="users"),
        FieldSpec("vendor_id", "reference", True, label="Vendor", ref_source="vendors", ref_name_field="vendor_name"),
        FieldSpec("vendor_name", "string", False),
        # The PA's only record of what it settles. finance-api's payment executor
        # closes these invoices and their ap_invoices rows from this array, the
        # remittance advice reads the vendor invoice numbers off it, and the
        # create-PA screen locks an invoice against a second PA by it — an empty
        # array silently no-ops all three. Editable HERE and nowhere else once the
        # PA leaves draft/returned (see pa.py::update_pa), which is why an
        # approved PA that links nothing was previously unrepairable.
        FieldSpec("invoice_ids", "reference_list", True, label="Linked Invoices",
                  ref_source="invoices"),
        FieldSpec("gr_ids", "reference_list", True, label="Linked Goods Receipts",
                  ref_source="grs"),
        FieldSpec("notes", "string", True),
        FieldSpec("created_at", "datetime", False),
        FieldSpec("approval_step_idx", "number", False),
    ],
    child=_PA_CHILD,
)

_AGREEMENT_SCHEMA = EntitySchema(
    key="agreement", label="Purchase Agreement", number_field="number",
    list_columns=["number", "title", "agreement_type", "status", "vendor_name",
                  "valid_to", "created_at"],
    search_fields=["number", "title", "vendor_name", "vendor_reference", "contract_no"],
    order_by="created_at desc",
    fields=[
        FieldSpec("number", "string", False),
        FieldSpec("title", "string", True),
        FieldSpec("agreement_type", "enum", True,
                  options=["house_account", "recurring", "milestone"]),
        # Terminal state is "active", NOT "approved" (the agr workflow differs from
        # pr/po/pa here); "returned" is produced by the engine's return action.
        FieldSpec("status", "enum", True,
                  options=["draft", "in_review", "returned", "active",
                           "expired", "closed", "cancelled"]),
        FieldSpec("contract_no", "string", True),
        FieldSpec("contact_email", "string", True),
        FieldSpec("vendor_id", "reference", True, label="Vendor", ref_source="vendors",
                  ref_name_field="vendor_name"),
        FieldSpec("vendor_name", "string", False),
        FieldSpec("vendor_reference", "string", True, label="Vendor Reference (legacy PO no.)"),
        FieldSpec("valid_from", "date", True),
        FieldSpec("valid_to", "date", True),
        FieldSpec("grace_days", "number", True),
        FieldSpec("not_to_exceed", "decimal", True),
        # Derived from the invoice set by crud.invoice._recompute_consumed — a typed
        # value here is overwritten by the next invoice write.
        FieldSpec("consumed_amount", "decimal", False),
        FieldSpec("currency", "string", True),
        FieldSpec("tax_code", "string", True),
        FieldSpec("tax_rate", "decimal", True),
        FieldSpec("department_id", "reference", True, label="Department",
                  ref_source="departments"),
        FieldSpec("budget_code", "string", True),
        FieldSpec("cost_center_id", "reference", True, label="Cost Center",
                  ref_source="cost_centers"),
        FieldSpec("owner_id", "reference", True, label="Agreement Owner", ref_source="users"),
        FieldSpec("created_by", "reference", True, label="Created By", ref_source="users"),
        # recurring-only block
        FieldSpec("recurring_type", "enum", True,
                  options=["weekly", "monthly", "quarterly", "yearly"]),
        FieldSpec("expected_invoice_day", "number", True),
        FieldSpec("anchor_month", "number", True),
        FieldSpec("schedule_start_date", "date", True),
        FieldSpec("expected_amount_per_period", "decimal", True),
        FieldSpec("tolerance_pct", "decimal", True),
        FieldSpec("overdue_after_days", "number", True),
        FieldSpec("notes", "string", True),
        FieldSpec("created_at", "datetime", False),
        FieldSpec("approval_step_idx", "number", False),   # → approval-state panel
    ],
)

_AGREEMENT_RECEIPT_SCHEMA = EntitySchema(
    key="agreement_receipt", label="Agreement Receipt", number_field="receipt_ref",
    list_columns=["receipt_ref", "receipt_type", "receipt_date", "vendor_name",
                  "total_amount", "status", "created_at"],
    search_fields=["receipt_ref", "vendor_name"], order_by="created_at desc",
    fields=[
        # Which agreement owns it and which invoice claims it are both maintained by
        # the match flow; hand-editing either desyncs invoices.receipt_ids from
        # agreement_receipts.invoice_id. Delete + re-record instead.
        FieldSpec("agreement_id", "uuid", False),
        FieldSpec("invoice_id", "uuid", False),
        FieldSpec("receipt_type", "enum", True,
                  options=["counter_slip", "delivery", "service"]),
        FieldSpec("receipt_date", "date", True),
        FieldSpec("receipt_ref", "string", True, label="Receipt Reference"),
        FieldSpec("vendor_id", "reference", True, label="Merchant on the slip",
                  ref_source="vendors", ref_name_field="vendor_name"),
        FieldSpec("vendor_name", "string", False),
        # Nullable on purpose for delivery/service receipts — those carry no amounts
        # at all, and 0 would be read as a zero-dollar receipt during reconciliation.
        FieldSpec("amount", "decimal", True),
        FieldSpec("tax_amount", "decimal", True),
        FieldSpec("total_amount", "decimal", True),
        FieldSpec("status", "enum", True,
                  options=["pending_ap_review", "open", "reconciled", "voided", "rejected"]),
        FieldSpec("received_by", "reference", True, label="Handed in by", ref_source="users"),
        FieldSpec("missing_receipt_reason", "string", True),
        FieldSpec("ap_reviewed_by", "reference", True, label="AP Reviewer", ref_source="users"),
        FieldSpec("ap_reviewed_at", "datetime", True),
        FieldSpec("notes", "string", True),
        FieldSpec("created_by", "reference", True, label="Recorded By", ref_source="users"),
        FieldSpec("created_at", "datetime", False),
    ],
)


# ── Task Inbox (delete-only) ────────────────────────────────────────────────────
# A single task row. Delete removes ONLY this task by its own id — NOT by
# document_id (that would wrongly purge sibling tasks for the same document).

async def _task_delete(db: AsyncSession, task) -> dict[str, int]:
    await db.delete(task)
    await db.flush()
    return {"tasks": 1}


async def _task_preview(db: AsyncSession, task) -> dict[str, int]:
    return {"tasks": 1}


_TASK_SCHEMA = EntitySchema(
    key="task", label="Task Inbox", number_field="document_number",
    list_columns=["document_type", "document_number", "type", "assigned_role", "is_completed", "created_at"],
    search_fields=["document_number"], order_by="created_at desc",
    allow_edit=False,
    fields=[
        FieldSpec("document_type", "string", False),
        FieldSpec("document_number", "string", False),
        FieldSpec("type", "string", False),
        FieldSpec("assigned_role", "string", False),
        FieldSpec("is_completed", "bool", False),
        FieldSpec("created_at", "datetime", False),
    ],
)

REGISTRY: dict[str, EntitySpec] = {
    "pr": EntitySpec(_PR_SCHEMA, PurchaseRequest, "epms", _pr_preview, _pr_delete),
    "po": EntitySpec(_PO_SCHEMA, PurchaseOrder, "epms", _po_preview, _po_delete),
    "gr": EntitySpec(_GR_SCHEMA, GoodsReceipt, "epms", _gr_preview, _gr_delete),
    "invoice": EntitySpec(_INVOICE_SCHEMA, Invoice, "epms", _invoice_preview, _invoice_delete),
    "pa": EntitySpec(_PA_SCHEMA, PaymentApplication, "epms", _pa_preview, _pa_delete),
    "agreement": EntitySpec(_AGREEMENT_SCHEMA, PurchaseAgreement, "epms",
                            _agreement_preview, _agreement_delete),
    "agreement_receipt": EntitySpec(_AGREEMENT_RECEIPT_SCHEMA, AgreementReceipt, "epms",
                                    _receipt_preview, _receipt_delete),
    "task": EntitySpec(_TASK_SCHEMA, Task, "epms", _task_preview, _task_delete),
}
