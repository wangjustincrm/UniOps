"""OA entity registry: schema metadata + cascade handlers.

Each entity exposes:
- a render/edit schema (EntitySchema)
- cascade_preview(db, row): counts only, NO mutation
- cascade_delete(db, row): deletes child rows + polymorphic workflow refs. The CALLER commits.

Cascade is simple for OA: every child table is ondelete=CASCADE at DB level,
so db.delete(parent) removes line items / trips / attachments automatically.
The only manual purge is shared polymorphic refs: tasks + approval_events by document_id.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Awaitable, Callable

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.admin.cascade import purge_shared_refs
from app.admin.fields import EntitySchema, FieldSpec
from app.models.approval_event_mirror import ApprovalEventMirror
from app.models.expense import ExpenseClaim
from app.models.invoice import ExpenseInvoice
from app.models.pa import PaymentApplication
from app.models.task_mirror import TaskMirror

CascadeFn = Callable[[AsyncSession, object], Awaitable[dict[str, int]]]


@dataclass
class EntitySpec:
    schema: EntitySchema
    model: type
    system: str
    cascade_preview: CascadeFn
    cascade_delete: CascadeFn


# ── shared count helper ───────────────────────────────────────────────────────

async def _count_refs(db: AsyncSession, document_id) -> dict[str, int]:
    tasks = int((await db.execute(
        select(func.count()).select_from(TaskMirror).where(TaskMirror.document_id == document_id)
    )).scalar_one())
    events = int((await db.execute(
        select(func.count()).select_from(ApprovalEventMirror).where(ApprovalEventMirror.document_id == document_id)
    )).scalar_one())
    return {"tasks": tasks, "approval_events": events}


# ── ExpenseClaim ──────────────────────────────────────────────────────────────

async def _claim_preview(db: AsyncSession, claim) -> dict[str, int]:
    refs = await _count_refs(db, claim.id)
    return {"expense_claims": 1, **refs}


async def _claim_delete(db: AsyncSession, claim) -> dict[str, int]:
    # Shared with the OA-facing DELETE /expenses/{id} (Travel Applications) so
    # the two cannot drift on what a claim delete has to clean up.
    from app.crud.expense import delete_claim

    return await delete_claim(db, claim)


# ── ExpenseInvoice ────────────────────────────────────────────────────────────

async def _invoice_preview(db: AsyncSession, invoice) -> dict[str, int]:
    refs = await _count_refs(db, invoice.id)
    return {"expense_invoices": 1, **refs}


async def _invoice_delete(db: AsyncSession, invoice) -> dict[str, int]:
    refs = await purge_shared_refs(db, invoice.id)
    await db.delete(invoice)  # expense_invoice_lines cascades via FK
    return {"expense_invoices": 1, **refs}


# ── PaymentApplication ────────────────────────────────────────────────────────

async def _pa_preview(db: AsyncSession, pa) -> dict[str, int]:
    refs = await _count_refs(db, pa.id)
    return {"payment_applications": 1, **refs}


async def _pa_delete(db: AsyncSession, pa) -> dict[str, int]:
    refs = await purge_shared_refs(db, pa.id)
    await db.delete(pa)  # pa_line_items / pa_attachment cascade via FK
    return {"payment_applications": 1, **refs}


# ── Schemas ───────────────────────────────────────────────────────────────────

_CLAIM_SCHEMA = EntitySchema(
    key="expense_claim", label="Expense Claim", number_field="claim_number",
    list_columns=["claim_number", "claim_type", "employee_name", "status", "total_amount", "created_at"],
    search_fields=["claim_number", "employee_name", "department_name"], order_by="created_at desc",
    fields=[
        FieldSpec("claim_number", "string", False),
        FieldSpec("claim_type", "string", True),
        FieldSpec("employee_name", "string", True),
        FieldSpec("department_name", "string", True),
        FieldSpec("submission_date", "date", True),
        FieldSpec("currency", "string", True),
        FieldSpec("total_amount", "decimal", True),
        FieldSpec("tax_amount", "decimal", True),
        FieldSpec("net_amount", "decimal", True),
        FieldSpec("status", "string", True),
        FieldSpec("purpose", "string", True),
        FieldSpec("notes", "string", True),
        FieldSpec("created_at", "datetime", False),
    ],
)

_INVOICE_SCHEMA = EntitySchema(
    key="expense_invoice", label="Expense Invoice", number_field="invoice_number",
    list_columns=["invoice_number", "vendor_name", "status", "total_amount", "created_at"],
    search_fields=["invoice_number", "vendor_name"], order_by="created_at desc",
    fields=[
        FieldSpec("invoice_number", "string", False),
        FieldSpec("vendor_name", "string", True),
        FieldSpec("invoice_date", "date", True),
        FieldSpec("due_date", "date", True),
        FieldSpec("currency", "string", True),
        FieldSpec("subtotal", "decimal", True),
        FieldSpec("tax_amount", "decimal", True),
        FieldSpec("total_amount", "decimal", True),
        FieldSpec("status", "string", True),
        FieldSpec("created_at", "datetime", False),
    ],
)

_PA_SCHEMA = EntitySchema(
    key="pa", label="Payment Application", number_field="pa_number",
    list_columns=["pa_number", "vendor_name", "status", "payment_amount", "created_at"],
    search_fields=["pa_number", "vendor_name", "title"], order_by="created_at desc",
    fields=[
        FieldSpec("pa_number", "string", False),
        FieldSpec("title", "string", True),
        FieldSpec("vendor_name", "string", True),
        FieldSpec("pa_type", "string", True),
        FieldSpec("payment_amount", "decimal", True),
        FieldSpec("currency", "string", True),
        FieldSpec("status", "string", True),
        FieldSpec("notes", "string", True),
        FieldSpec("created_at", "datetime", False),
    ],
)

REGISTRY: dict[str, EntitySpec] = {
    "expense_claim": EntitySpec(_CLAIM_SCHEMA, ExpenseClaim, "oa", _claim_preview, _claim_delete),
    "expense_invoice": EntitySpec(_INVOICE_SCHEMA, ExpenseInvoice, "oa", _invoice_preview, _invoice_delete),
    "pa": EntitySpec(_PA_SCHEMA, PaymentApplication, "oa", _pa_preview, _pa_delete),
}
