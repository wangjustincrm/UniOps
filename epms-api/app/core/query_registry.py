"""Entity registry for the assistant's controlled query layer.

One definition per entity serves three purposes at once:

  1. Security whitelist. A column absent from ``fields`` can never be selected,
     filtered on, grouped by, or returned — the planner cannot name it and the
     validator rejects it outright. Adding a field here is a deliberate act: it
     makes that column readable by anyone who can reach the assistant, subject
     only to the row scope below.
  2. Schema description. ``label`` / ``values`` are what the planner reads when
     deciding how to answer a question, so they are written for that reader
     rather than for us.
  3. Access binding. ``perm_key`` is the Access Control Matrix gate (may this
     user see this KIND of document at all) and ``apply_scope`` is the row-level
     filter (WHICH of them) — the same two gates every list endpoint applies.

``apply_scope`` is deliberately the SAME callable the crud layer uses, not a
second copy of the visibility rules: app/crud/{pr,po,gr}.py call these helpers
so a change to visibility reaches the list endpoints and the assistant in the
same commit. A registry entry without an ``apply_scope`` is a programming
error, not a "public" entity — see controlled_query.execute(), which refuses to
run an entity whose scope callable is missing rather than falling back to
unfiltered rows.
"""
from dataclasses import dataclass, field as dc_field
from typing import Callable

from sqlalchemy.sql import Select

from app.models.gr import GoodsReceipt
from app.models.po import PurchaseOrder
from app.models.pr import PurchaseRequest

# Field kinds. These drive both formatting on the way out and the operators the
# validator will accept on the way in (see controlled_query._OPS_BY_KIND).
TEXT = "text"
ENUM = "enum"
MONEY = "money"
DATE = "date"
DATETIME = "datetime"
BOOL = "bool"
INT = "int"


@dataclass(frozen=True)
class Field:
    kind: str
    label: str
    values: tuple[str, ...] = ()


@dataclass(frozen=True)
class Metric:
    fn: str  # sum | count | avg | min | max
    field: str  # column name, or "*" for count
    label: str


@dataclass(frozen=True)
class Entity:
    name: str
    model: type
    label: str
    perm_key: str
    apply_scope: Callable[[Select, dict], Select]
    date_field: str
    fields: dict[str, Field] = dc_field(default_factory=dict)
    metrics: dict[str, Metric] = dc_field(default_factory=dict)


# ── Row scope helpers ─────────────────────────────────────────────────────────
# A None subquery means "unrestricted" (the user sees everything of this kind);
# that is build_scope's contract, not an absent-value bug. Each helper mirrors
# exactly one line that used to live inline in the matching crud.get_all().


def scope_pr(q: Select, scope: dict) -> Select:
    subq = scope.get("pr_subq")
    return q if subq is None else q.where(PurchaseRequest.id.in_(subq))


def scope_po(q: Select, scope: dict) -> Select:
    subq = scope.get("po_subq")
    return q if subq is None else q.where(PurchaseOrder.id.in_(subq))


def scope_gr(q: Select, scope: dict) -> Select:
    # GRs are scoped through their PO, not by a GR subquery of their own.
    subq = scope.get("po_subq")
    return q if subq is None else q.where(GoodsReceipt.po_id.in_(subq))


# ── Entities ──────────────────────────────────────────────────────────────────

PR = Entity(
    name="purchase_request",
    model=PurchaseRequest,
    label="Purchase Request (PR) — the requisition that starts a purchase",
    perm_key="view_pr",
    apply_scope=scope_pr,
    date_field="created_at",
    fields={
        "number": Field(TEXT, "PR number"),
        "title": Field(TEXT, "Title"),
        "status": Field(ENUM, "Status", ("draft", "submitted", "approved", "rejected", "cancelled")),
        "amount": Field(MONEY, "Amount"),
        "currency": Field(TEXT, "Currency"),
        "vendor_name": Field(TEXT, "Vendor name"),
        "department_name": Field(TEXT, "Requesting department"),
        "cost_center_name": Field(TEXT, "Cost centre"),
        "budget_code": Field(TEXT, "Budget account code"),
        "project_code": Field(TEXT, "Project code"),
        "required_by": Field(DATE, "Date the requester needs it by"),
        "is_prepaid": Field(BOOL, "Prepaid purchase"),
        "over_budget": Field(BOOL, "Exceeds its budget account"),
        "submitted_at": Field(DATETIME, "Submitted at"),
        "created_at": Field(DATETIME, "Created at"),
    },
    metrics={
        "amount": Metric("sum", "amount", "Total requested amount"),
        "count": Metric("count", "*", "Number of PRs"),
        "avg_amount": Metric("avg", "amount", "Average PR amount"),
    },
)

PO = Entity(
    name="purchase_order",
    model=PurchaseOrder,
    label="Purchase Order (PO) — the order placed with a vendor",
    perm_key="view_po",
    apply_scope=scope_po,
    # placed_at is when the order actually went to the vendor, which is what
    # "POs in the last three months" means to a buyer. created_at would count
    # drafts that were never placed.
    date_field="placed_at",
    fields={
        "number": Field(TEXT, "PO number"),
        "title": Field(TEXT, "Title"),
        "status": Field(ENUM, "Status", ("draft", "submitted", "approved", "rejected", "cancelled", "closed")),
        "total": Field(MONEY, "Total including tax"),
        "subtotal": Field(MONEY, "Total before tax"),
        "tax_amount": Field(MONEY, "Tax amount"),
        "currency": Field(TEXT, "Currency"),
        "vendor_name": Field(TEXT, "Vendor name"),
        "budget_code": Field(TEXT, "Budget account code"),
        "pr_number": Field(TEXT, "Originating PR number"),
        "expected_delivery": Field(DATE, "Expected delivery date"),
        "placed_at": Field(DATETIME, "Date the order was placed with the vendor"),
        "is_prepaid": Field(BOOL, "Prepaid order"),
        "source": Field(TEXT, "Origin system (NC mirror or created here)"),
        "created_at": Field(DATETIME, "Created at"),
    },
    metrics={
        "amount": Metric("sum", "total", "Total ordered amount including tax"),
        "subtotal": Metric("sum", "subtotal", "Total ordered amount before tax"),
        "count": Metric("count", "*", "Number of POs"),
        "avg_amount": Metric("avg", "total", "Average PO value"),
    },
)

GR = Entity(
    name="goods_receipt",
    model=GoodsReceipt,
    label="Goods Receipt (GR) — proof that goods or services were received",
    perm_key="view_gr",
    apply_scope=scope_gr,
    date_field="received_at",
    fields={
        "number": Field(TEXT, "GR number"),
        "title": Field(TEXT, "Title"),
        "status": Field(ENUM, "Status", ("pending_ack", "acknowledged", "cancelled")),
        "po_number": Field(TEXT, "PO number"),
        "pr_number": Field(TEXT, "PR number"),
        "vendor_name": Field(TEXT, "Vendor name"),
        "gr_type": Field(TEXT, "Receipt type"),
        "currency": Field(TEXT, "Currency"),
        "storage_location": Field(TEXT, "Storage location"),
        "received_by": Field(TEXT, "Received by"),
        "received_at": Field(DATETIME, "Received at"),
        "created_at": Field(DATETIME, "Created at"),
    },
    metrics={
        "count": Metric("count", "*", "Number of goods receipts"),
    },
)

REGISTRY: dict[str, Entity] = {e.name: e for e in (PR, PO, GR)}


def get_entity(name: str) -> Entity | None:
    return REGISTRY.get(name)
