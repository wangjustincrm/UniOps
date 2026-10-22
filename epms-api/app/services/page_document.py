"""The document open on screen, as something the planner can query by (ISS-0068).

The panel sends the page's document as a type and an id. The schema the
planner queries by is keyed on numbers, so the id alone answers nothing: on
GR-20261020-0001 "这张收货单是哪个 PO 的？收了多少？" was refused twice ("I can
only track PR / PO / PA approvals", then "no GR number was given"), the
invoice page said it had no access to invoice data, the agreement page said
to open the agreement, and the agreement-receipt page was taken for a goods
receipt. Only PR, PO and PA pages had their number added.

Every detail page with a document now gets the same treatment. For the pages
added here visibility is the ontology's own — the entity's permission gate and
row scope, exactly as a query for that document applies them. PR, PO and PA
keep the check they already had (workflow_view.resolve, the detail pages'
own rule). A document this person may not see adds nothing, the same as no
document.
"""
import uuid
from dataclasses import dataclass

from sqlalchemy import select

from app.core.ontology import REGISTRY, may_view
from app.services import workflow_view


@dataclass(frozen=True)
class PageDoc:
    entity: str          # ontology entity the page shows
    field: str           # its number field, as the planner names it
    attr: str            # the model attribute holding that number
    lines: tuple[str, str] | None = None   # (line entity, field on it naming the parent)


# doc_type as the panel sends it -> how to name it. The EPMS front end derives
# doc_type from the route (epms/src/components/AssistantMount.tsx).
PAGE_DOCUMENTS: dict[str, PageDoc] = {
    "pr": PageDoc("purchase_request", "number", "number", ("pr_line", "request.number")),
    "po": PageDoc("purchase_order", "number", "number", ("po_line", "order.number")),
    "pa": PageDoc("payment_application", "pa_number", "pa_number"),
    "gr": PageDoc("goods_receipt", "number", "number", ("gr_line", "receipt.number")),
    "invoice": PageDoc("invoice", "internal_ref", "internal_ref",
                       ("invoice_allocation", "invoice.internal_ref")),
    "agreement": PageDoc("purchase_agreement", "number", "number",
                         ("agreement_receipt", "agreement.number")),
    # An agreement receipt has no number of its own: it is found by its
    # reference together with its agreement's number.
    "agreement_receipt": PageDoc("agreement_receipt", "receipt_ref", "receipt_ref"),
}


async def _visible(db, scope: dict, entity_name: str, doc_id: uuid.UUID):
    entity = REGISTRY.get(entity_name)
    if entity is None or not may_view(entity, scope.get("perms") or {}):
        return None
    stmt = await entity.apply_scope(
        select(entity.model).where(entity.model.id == doc_id), scope, db)
    return (await db.execute(stmt)).scalar_one_or_none()


async def resolve(db, scope: dict, doc_type: str | None, doc_id: str | None) -> dict:
    """What to add to the planner's context for the document on screen —
    empty when there is none, or none this person may see."""
    spec = PAGE_DOCUMENTS.get(doc_type or "")
    if spec is None or not doc_id:
        return {}
    try:
        parsed = uuid.UUID(str(doc_id))
    except ValueError:
        return {}
    if doc_type in workflow_view.SUPPORTED:
        doc = await workflow_view.resolve(db, scope, doc_type, None, str(parsed))
    else:
        doc = await _visible(db, scope, spec.entity, parsed)
    if doc is None:
        return {}
    number = getattr(doc, spec.attr, None)
    filters = [{"field": spec.field, "value": number}] if number else []
    out: dict = {"doc_entity": spec.entity}
    if number:
        out.update(doc_number=number, doc_number_field=spec.field)
    if spec.lines and number:
        out["doc_lines"] = f"{spec.lines[0]} filtered on {spec.lines[1]} = {number}"
    if doc_type == "agreement_receipt":
        agreement = await _visible(db, scope, "purchase_agreement", doc.agreement_id)
        if agreement is not None:
            filters.append({"field": "agreement.number", "value": agreement.number})
        if not number and doc.receipt_date:
            filters.append({"field": "receipt_date", "value": doc.receipt_date.isoformat()})
    if not filters:
        return {}
    out["doc_filters"] = filters
    return out
