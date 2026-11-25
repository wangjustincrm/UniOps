"""One record through its record action, as a person does it: the permission, the rule, the version, the reason and the work of
the route people reach. What was left out or failed is raised, with the rule's code and words."""
from types import SimpleNamespace

import sqlalchemy as sa

from app.models.documents import Document, DocumentLink
from app.models.orders import Order
from app.platform.bulk_actions import ActionIn, run_action
from app.platform.commands import ANY_VERSION, Stale
from app.platform.errors import BusinessError, NotFound
from app.services.count_actions import COUNT_ACTIONS
from app.services.document_actions import DOCUMENT_ACTIONS
from app.services.invoice_actions import INVOICE_ACTIONS
from app.services.order_actions import ORDER_ACTIONS
from app.services.transfer_actions import TRANSFER_ACTIONS

# A result has the code and the words, not the details: which state a send is in, from its words.
STATES_IN_WORDS = (("already queued", "pending"), ("being sent", "sending"), ("never recorded", "unknown"))


async def one(u, registry, action, id, version=ANY_VERSION, *, rt=None, reason=None, input=None):
    """In the caller's unit of work (it commits). ANY_VERSION: the version it has now."""
    model = registry.repo_cls.model
    if version is ANY_VERSION:
        version = (await u.session.execute(sa.select(model.version).where(model.id == id))).scalar_one_or_none() or 1 if registry[action].versioned else None
    out = await run_action(u, registry, action, ActionIn(ids={id: version}, reason=reason, input=input), rt)
    [r] = out.results
    if r.status == "done":
        return r
    if r.code == "entity.stale":
        raise Stale("this record", r.version or 0, version or 0)
    if r.code == "entity.not_found":
        raise NotFound(model.__name__, id)
    state = next((s for words, s in STATES_IN_WORDS if words in (r.reason or "")), None)
    raise BusinessError(r.code or f"action.{r.status}", r.reason or r.status, {"state": state} if state else {})


# ── orders ───────────────────────────────────────────────────────────────────

async def send_940(u, outbound, id, version=ANY_VERSION, *, replace=False, reason=None) -> dict:
    r = await one(u, ORDER_ACTIONS, "replace_940" if replace else "send_940", id, version, rt=SimpleNamespace(outbound=outbound), reason=reason)
    o = await u.session.get(Order, id)
    doc = (await u.session.execute(sa.select(Document).join(DocumentLink, DocumentLink.document_id == Document.id).where(
        DocumentLink.entity_type == "order", DocumentLink.entity_id == id, Document.kind == "940_EDI").order_by(Document.id.desc()).limit(1))).scalar_one()
    return {"order": {"fulfillment_status": o.fulfillment_status}, "document_id": doc.id, "file_status": doc.file_status, "control": doc.st02,
            "note": r.note, "problem": r.problem, "errors": (doc.body_json or {}).get("errors", [])}


async def fulfil(u, ships, id, version=ANY_VERSION):
    return await one(u, ORDER_ACTIONS, "fulfil_in_shopify", id, version, rt=SimpleNamespace(shipments=ships))


async def cancel_order(u, tasks, id, reason, version=ANY_VERSION):
    return await one(u, ORDER_ACTIONS, "cancel", id, version, rt=SimpleNamespace(tasks=tasks), reason=reason)


async def release_hold(u, tasks, id, to, reason, version=ANY_VERSION):
    return await one(u, ORDER_ACTIONS, "release_hold", id, version, rt=SimpleNamespace(tasks=tasks), reason=reason, input={"to": to})


async def recheck(u, tasks, id, shopify=None, version=ANY_VERSION) -> list[str]:
    """What is still wrong; none: resolved."""
    rt = SimpleNamespace(intake=SimpleNamespace(tasks=tasks), container=SimpleNamespace(shopify=shopify))
    note = (await one(u, ORDER_ACTIONS, "recheck", id, version, rt=rt)).note
    return note.removeprefix("still in review: ").split("; ") if note else []


async def submit(u, id, version=ANY_VERSION) -> Order:
    await one(u, ORDER_ACTIONS, "submit_draft", id, version)
    return await u.session.get(Order, id, populate_existing=True)


# ── invoices ─────────────────────────────────────────────────────────────────

async def send_invoice(u, invoicing, id, version=ANY_VERSION, *, again=False, reason=None):
    return await one(u, INVOICE_ACTIONS, "send_again" if again else "send", id, version, rt=SimpleNamespace(invoicing=invoicing), reason=reason)


# ── transfers ────────────────────────────────────────────────────────────────

async def transfer(u, transfers, action, id, version=ANY_VERSION, reason=None):
    from app.services.transfer_views import TransferService
    await one(u, TRANSFER_ACTIONS, action, id, version, rt=SimpleNamespace(transfers=transfers), reason=reason)
    return await TransferService(u)._detail_after(id)


# ── documents and counts ─────────────────────────────────────────────────────

async def document(u, action, id, rt, reason=None) -> Document:
    await one(u, DOCUMENT_ACTIONS, action, id, rt=rt, reason=reason)
    return await u.session.get(Document, id)


async def send_again(u, id, rt, reason=None):
    return await one(u, DOCUMENT_ACTIONS, "send_again", id, rt=rt, reason=reason)


async def count(u, action, id, version=ANY_VERSION, reason=None):
    from app.services.stock_counts import StockCountService
    await one(u, COUNT_ACTIONS, action, id, version, reason=reason)
    return await StockCountService(u).get(id)
