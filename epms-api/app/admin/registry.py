"""EPMS entity registry: schema metadata + cascade handlers (Phase 1).

Each entity exposes:
- a render/edit schema (EntitySchema)
- cascade_preview(db, row): counts only, NO mutation
- cascade_delete(db, row): deletes the child subtree + polymorphic workflow refs
  (tasks/approval_events). The CALLER commits.

Cascade handlers run leaf-first so RESTRICT foreign keys never block the delete:
Invoice -> PA -> GR -> PO -> PR. Line items and *_attachment rows are removed
automatically by their ondelete=CASCADE FKs when the parent row is deleted.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Awaitable, Callable

from sqlalchemy import func, select, text, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.admin.cascade import count_polymorphic, purge_workflow_refs
from app.admin.fields import EntitySchema, FieldSpec
from app.models.approval import ApprovalEvent
from app.models.gr import GoodsReceipt
from app.models.invoice import Invoice
from app.models.pa import PaymentApplication
from app.models.po import PurchaseOrder
from app.models.pr import PurchaseRequest
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
    summary = await purge_workflow_refs(db, inv.id)
    await db.delete(inv)            # invoice has no child tables in epms
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
    _merge(summary, await purge_workflow_refs(db, pa.id))
    await db.delete(pa)             # pa_line_items + pa_attachment cascade via FK
    return _merge(summary, {"payment_applications": 1})


async def _pa_preview(db: AsyncSession, pa) -> dict[str, int]:
    summary = {"payment_applications": 1}
    pr_count = await _pa_count_payment_records(db, pa.id)
    if pr_count:
        summary["payment_records"] = pr_count
    return _merge(summary, await _wf_counts(db, pa.id))


# ── GR (blocked by Invoice.gr_id) ───────────────────────────────────────────────

async def _gr_delete(db: AsyncSession, gr) -> dict[str, int]:
    summary: dict[str, int] = {}
    for inv in await _children(db, Invoice, "gr_id", gr.id):
        _merge(summary, await _invoice_delete(db, inv))
    _merge(summary, await purge_workflow_refs(db, gr.id))
    await db.delete(gr)             # gr_line_items + gr_attachment cascade via FK
    return _merge(summary, {"goods_receipts": 1})


async def _gr_preview(db: AsyncSession, gr) -> dict[str, int]:
    summary: dict[str, int] = {"goods_receipts": 1}
    for inv in await _children(db, Invoice, "gr_id", gr.id):
        _merge(summary, await _invoice_preview(db, inv))
    return _merge(summary, await _wf_counts(db, gr.id))


# ── PO (blocked by GR/Invoice/PA on po_id) ──────────────────────────────────────

async def _po_delete(db: AsyncSession, po) -> dict[str, int]:
    summary: dict[str, int] = {}
    for inv in await _children(db, Invoice, "po_id", po.id):
        _merge(summary, await _invoice_delete(db, inv))
    for pa in await _children(db, PaymentApplication, "po_id", po.id):
        _merge(summary, await _pa_delete(db, pa))
    for gr in await _children(db, GoodsReceipt, "po_id", po.id):
        _merge(summary, await _gr_delete(db, gr))
    # Clear the originating PR's back-reference so it does not dangle.
    for pr in await _children(db, PurchaseRequest, "po_id", po.id):
        pr.po_id = None
        pr.po_number = None
    _merge(summary, await purge_workflow_refs(db, po.id))
    await db.delete(po)             # po_line_items + po_attachment cascade via FK
    return _merge(summary, {"purchase_orders": 1})


async def _po_preview(db: AsyncSession, po) -> dict[str, int]:
    summary: dict[str, int] = {"purchase_orders": 1}
    for inv in await _children(db, Invoice, "po_id", po.id):
        _merge(summary, await _invoice_preview(db, inv))
    for pa in await _children(db, PaymentApplication, "po_id", po.id):
        _merge(summary, await _pa_preview(db, pa))
    for gr in await _children(db, GoodsReceipt, "po_id", po.id):
        _merge(summary, await _gr_preview(db, gr))
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
    return _merge(summary, {"purchase_requests": 1})


async def _pr_preview(db: AsyncSession, pr) -> dict[str, int]:
    summary: dict[str, int] = {"purchase_requests": 1}
    for po in await _children(db, PurchaseOrder, "pr_id", pr.id):
        _merge(summary, await _po_preview(db, po))
    for gr in await _children(db, GoodsReceipt, "pr_id", pr.id):
        _merge(summary, await _gr_preview(db, gr))
    return _merge(summary, await _wf_counts(db, pr.id))


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
        FieldSpec("amount", "decimal", True),
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
    ],
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
        FieldSpec("subtotal", "decimal", True),
        FieldSpec("tax_rate", "decimal", True),
        FieldSpec("tax_code", "string", True),
        FieldSpec("tax_amount", "decimal", True),
        FieldSpec("total", "decimal", True),
        FieldSpec("created_by", "reference", True, label="Requester", ref_source="users"),
        FieldSpec("vendor_id", "reference", True, label="Vendor", ref_source="vendors", ref_name_field="vendor_name"),
        FieldSpec("vendor_name", "string", False),
        FieldSpec("budget_code", "string", True),
        FieldSpec("expected_delivery", "date", True),
        FieldSpec("delivery_address", "string", True),
        FieldSpec("is_prepaid", "bool", True),
        FieldSpec("notes", "string", True),
        FieldSpec("created_at", "datetime", False),
    ],
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
        FieldSpec("subtotal", "decimal", True),
        FieldSpec("tax_rate", "decimal", True),
        FieldSpec("tax_code", "string", True),
        FieldSpec("tax_amount", "decimal", True),
        FieldSpec("shipping_amount", "decimal", True),
        FieldSpec("other_charges", "decimal", True),
        FieldSpec("other_charges_note", "string", True),
        FieldSpec("payment_amount", "decimal", True),
        FieldSpec("created_by", "reference", True, label="PA Creator (AP Clerk)", ref_source="users"),
        FieldSpec("vendor_id", "reference", True, label="Vendor", ref_source="vendors", ref_name_field="vendor_name"),
        FieldSpec("vendor_name", "string", False),
        FieldSpec("notes", "string", True),
        FieldSpec("created_at", "datetime", False),
    ],
)

# ── Task Inbox (delete-only) ────────────────────────────────────────────────────
# A single task row. Delete removes ONLY this task by its own id — NOT by
# document_id (that would wrongly purge sibling tasks for the same document).

async def _task_delete(db: AsyncSession, task) -> dict[str, int]:
    await db.delete(task)
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
    "task": EntitySpec(_TASK_SCHEMA, Task, "epms", _task_preview, _task_delete),
}
