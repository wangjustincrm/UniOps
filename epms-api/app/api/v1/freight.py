"""Courier freight payment (Freight) endpoints.

Three permission keys (design §8.2):
  epms.freight.register — register / edit / void one's own shipments
  epms.freight.manage   — everything on shipments and invoices, raise Freight PAs
  epms.freight.admin    — maintain carriers
"""
import uuid
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from uniops_authz import has_permission

from app.core.access_scope import build_scope
from app.core.deps import BearerToken, CurrentUserPayload, SessionDep
from app.crud import freight as crud
from app.crud.freight import FreightError
from app.models.freight import FREIGHT_BUDGET_CODE, FreightShipment
from app.models.invoice import Invoice
from app.schemas.freight import (
    CarrierCreate, CarrierResponse, CarrierUpdate, DefaultCostCenterResponse,
    DuplicateCheckResponse, FreightInvoiceCreate, FreightInvoiceListResponse,
    FreightInvoiceResponse, FreightLineUpdate, FreightPaCreate, FreightPaCreated,
    ShipmentCreate, ShipmentListResponse, ShipmentResponse, ShipmentUpdate,
)
from app.services import budget_client, finance_sync

router = APIRouter(prefix="/freight", tags=["freight"])


async def _perms(db, user: dict) -> dict[str, bool]:
    uid, role = uuid.UUID(user["sub"]), user.get("role") or ""
    return {k: await has_permission(db, uid, role, f"epms.freight.{k}")
            for k in ("register", "manage", "admin")}


async def _need(db, user: dict, *keys: str) -> dict[str, bool]:
    p = await _perms(db, user)
    if not any(p[k] for k in keys):
        raise HTTPException(status_code=403, detail="You do not have access to Freight")
    return p


async def _need_upload(db, user: dict) -> None:
    """A courier invoice arrives through Invoices → Upload Invoice like any other
    invoice, so creating one takes the ordinary invoice-upload permission (or the
    freight manager's key). Matching, finalising and paying stay on
    epms.freight.manage."""
    uid, role = uuid.UUID(user["sub"]), user.get("role") or ""
    if await has_permission(db, uid, role, "invoice_upload"):
        return
    await _need(db, user, "manage")


async def _can_match(db, user: dict, inv: Invoice) -> bool:
    """epms.freight.manage, or the person AP assigned this invoice to (an open
    match_invoice task) — the same rule as any other invoice's matching."""
    if (await _perms(db, user))["manage"]:
        return True
    from app.api.v1.invoices import _has_open_match_task
    return await _has_open_match_task(db, uuid.UUID(user["sub"]), inv.id)


async def _need_match(db, user: dict, inv: Invoice) -> None:
    if not await _can_match(db, user, inv):
        raise HTTPException(status_code=403, detail="You do not have access to Freight")


async def _detail(db, user: dict, inv: Invoice) -> dict:
    from app.api.v1.invoices import _attach_match_assignees
    await _attach_match_assignees(db, [inv])
    return {**await crud.invoice_detail(db, inv), "can_match": await _can_match(db, user, inv),
            "match_assignee_id": getattr(inv, "match_assignee_id", None),
            "match_assignee_name": getattr(inv, "match_assignee_name", None)}


def _err(e: FreightError) -> HTTPException:
    return HTTPException(status_code=e.status, detail=e.detail)


async def _freight_invoice(db, invoice_id: uuid.UUID) -> Invoice:
    inv = await db.get(Invoice, invoice_id)
    if inv is None or inv.match_route != "freight":
        raise HTTPException(status_code=404, detail="Freight invoice not found")
    return inv


# ── Me ────────────────────────────────────────────────────────────────────────

@router.get("/me")
async def my_access(db: SessionDep, user: CurrentUserPayload) -> dict:
    """What this caller may do here — drives the page's tabs and buttons."""
    return await _perms(db, user)


# ── Carriers ──────────────────────────────────────────────────────────────────

@router.get("/carriers", response_model=list[CarrierResponse])
async def list_carriers(db: SessionDep, user: CurrentUserPayload,
                        include_inactive: bool = False):
    # Invoice uploaders read it too: the upload form recognises a courier
    # invoice by its vendor being a carrier's.
    uid, role = uuid.UUID(user["sub"]), user.get("role") or ""
    if not await has_permission(db, uid, role, "invoice_upload"):
        await _need(db, user, "register", "manage", "admin")
    return [CarrierResponse.model_validate(c).model_copy(update={"vendor_name": n})
            for c, n in await crud.list_carriers(db, include_inactive)]


@router.post("/carriers", response_model=CarrierResponse, status_code=201)
async def create_carrier(body: CarrierCreate, db: SessionDep, user: CurrentUserPayload):
    await _need(db, user, "admin")
    try:
        c = await crud.create_carrier(db, body)
    except FreightError as e:
        raise _err(e)
    return CarrierResponse.model_validate(c)


@router.patch("/carriers/{carrier_id}", response_model=CarrierResponse)
async def update_carrier(carrier_id: uuid.UUID, body: CarrierUpdate, db: SessionDep,
                         user: CurrentUserPayload):
    await _need(db, user, "admin")
    try:
        c = await crud.update_carrier(db, await crud.get_carrier(db, carrier_id), body)
    except FreightError as e:
        raise _err(e)
    return CarrierResponse.model_validate(c)


# ── Shipments ─────────────────────────────────────────────────────────────────

@router.get("/default-cost-center", response_model=DefaultCostCenterResponse)
async def default_cost_center(department_id: uuid.UUID, db: SessionDep,
                              user: CurrentUserPayload, token: BearerToken):
    await _need(db, user, "register", "manage")

    async def balance(cc_id, year):
        return await budget_client.get_balance(token, cc_id, year, account_code=FREIGHT_BUDGET_CODE)

    return await crud.default_cost_center(db, department_id, balance)


@router.get("/shipments", response_model=ShipmentListResponse)
async def list_shipments(
    db: SessionDep, user: CurrentUserPayload,
    status: str | None = None, carrier_id: uuid.UUID | None = None,
    search: str | None = None, mine: bool = False,
    sort: Annotated[str | None, Query(description="Key of crud.freight._shipment_sortable(); unknown -> 422")] = None,
    dir: Annotated[Literal["asc", "desc"], Query()] = "asc",
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=200)] = 50,
):
    p = await _need(db, user, "register", "manage")
    # Registrars see their own; managers see all (and may narrow to their own).
    only = uuid.UUID(user["sub"]) if (mine or not p["manage"]) else None
    items, total = await crud.list_shipments(db, created_by=only, status=status,
                                             carrier_id=carrier_id, search=search,
                                             sort=sort, dir=dir,
                                             page=page, page_size=page_size)
    return {"items": items, "total": total}


@router.post("/shipments", response_model=ShipmentResponse, status_code=201)
async def create_shipment(body: ShipmentCreate, db: SessionDep, user: CurrentUserPayload):
    await _need(db, user, "register", "manage")
    try:
        s = await crud.create_shipment(db, body, uuid.UUID(user["sub"]))
    except FreightError as e:
        raise _err(e)
    return (await crud.shipment_dicts(db, [s]))[0]


async def _own_shipment(db, user: dict, shipment_id: uuid.UUID) -> FreightShipment:
    p = await _need(db, user, "register", "manage")
    s = await db.get(FreightShipment, shipment_id)
    if s is None or (not p["manage"] and s.created_by != uuid.UUID(user["sub"])):
        raise HTTPException(status_code=404, detail="Shipment not found")
    return s


@router.patch("/shipments/{shipment_id}", response_model=ShipmentResponse)
async def update_shipment(shipment_id: uuid.UUID, body: ShipmentUpdate, db: SessionDep,
                          user: CurrentUserPayload):
    s = await _own_shipment(db, user, shipment_id)
    try:
        await crud.update_shipment(db, s, body)
    except FreightError as e:
        raise _err(e)
    return (await crud.shipment_dicts(db, [s]))[0]


@router.post("/shipments/{shipment_id}/void", response_model=ShipmentResponse)
async def void_shipment(shipment_id: uuid.UUID, db: SessionDep, user: CurrentUserPayload):
    s = await _own_shipment(db, user, shipment_id)
    try:
        await crud.void_shipment(db, s)
    except FreightError as e:
        raise _err(e)
    return (await crud.shipment_dicts(db, [s]))[0]


# ── Invoices ──────────────────────────────────────────────────────────────────

@router.get("/invoices/duplicate-check", response_model=DuplicateCheckResponse)
async def duplicate_check(carrier_id: uuid.UUID, vendor_invoice_number: str,
                          db: SessionDep, user: CurrentUserPayload):
    await _need_upload(db, user)
    try:
        carrier = await crud.get_carrier(db, carrier_id)
    except FreightError as e:
        raise _err(e)
    ref = await crud.find_duplicate_invoice(db, carrier.vendor_id, vendor_invoice_number)
    return {"duplicate": ref is not None, "detail": ref}


@router.get("/invoices", response_model=FreightInvoiceListResponse)
async def list_invoices(
    db: SessionDep, user: CurrentUserPayload,
    status: str | None = None, carrier_id: uuid.UUID | None = None,
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=200)] = 50,
):
    await _need(db, user, "manage")
    items, total = await crud.list_invoices(db, status=status, carrier_id=carrier_id,
                                            page=page, page_size=page_size)
    return {"items": items, "total": total}


@router.post("/invoices", response_model=FreightInvoiceResponse, status_code=201)
async def create_invoice(body: FreightInvoiceCreate, db: SessionDep, user: CurrentUserPayload,
                         token: BearerToken):
    await _need_upload(db, user)
    try:
        inv = await crud.create_invoice(db, body, uuid.UUID(user["sub"]))
    except FreightError as e:
        raise _err(e)
    await finance_sync.sync_ap_invoice(db, inv, token)
    return await _detail(db, user, inv)


@router.get("/invoices/{invoice_id}", response_model=FreightInvoiceResponse)
async def get_invoice(invoice_id: uuid.UUID, db: SessionDep, user: CurrentUserPayload):
    """Managers work the invoice; anyone else who may read it on the Invoices
    page (AP, finance — the invoice read gate) sees the same panel read-only.
    Matching (auto-match, lines, finalize) is open to epms.freight.manage and
    to the person AP assigned it to; reopen, delete and the PA stay on manage."""
    inv = await _freight_invoice(db, invoice_id)
    # Whoever may match it may read it — including an assignee whose role has
    # no invoice view of its own (the assignment is the reason they are here).
    if not await _can_match(db, user, inv):
        from app.api.v1.invoices import _can_read_invoice
        if not await _can_read_invoice(db, user, inv, await build_scope(db, user)):
            raise HTTPException(status_code=404, detail="Freight invoice not found")
    return await _detail(db, user, inv)


@router.post("/invoices/{invoice_id}/auto-match", response_model=FreightInvoiceResponse)
async def auto_match(invoice_id: uuid.UUID, db: SessionDep, user: CurrentUserPayload):
    inv = await _freight_invoice(db, invoice_id)
    await _need_match(db, user, inv)
    if inv.status != "unmatched":
        raise HTTPException(status_code=409, detail="Reopen the invoice before matching again")
    await crud.auto_match(db, inv)
    return await _detail(db, user, inv)


@router.patch("/invoices/{invoice_id}/lines/{line_id}", response_model=FreightInvoiceResponse)
async def update_line(invoice_id: uuid.UUID, line_id: uuid.UUID, body: FreightLineUpdate,
                      db: SessionDep, user: CurrentUserPayload):
    inv = await _freight_invoice(db, invoice_id)
    await _need_match(db, user, inv)
    try:
        ln = await crud.get_line(db, line_id)
        if ln.invoice_id != inv.id:
            raise FreightError(404, "Invoice line not found")
        await crud.update_line(db, inv, ln, body)
    except FreightError as e:
        raise _err(e)
    return await _detail(db, user, inv)


@router.post("/invoices/{invoice_id}/finalize", response_model=FreightInvoiceResponse)
async def finalize(invoice_id: uuid.UUID, db: SessionDep, user: CurrentUserPayload):
    inv = await _freight_invoice(db, invoice_id)
    await _need_match(db, user, inv)
    actor = uuid.UUID(user["sub"])
    try:
        await crud.finalize(db, inv, actor)
    except FreightError as e:
        raise _err(e)
    # Matched: the assignment (if any) is done, whoever finalised it.
    from app.api.v1.invoices import _close_open_match_tasks
    await _close_open_match_tasks(db, inv.id, actor)
    await db.commit()
    return await _detail(db, user, inv)


@router.post("/invoices/{invoice_id}/reopen", response_model=FreightInvoiceResponse)
async def reopen(invoice_id: uuid.UUID, db: SessionDep, user: CurrentUserPayload):
    await _need(db, user, "manage")
    inv = await _freight_invoice(db, invoice_id)
    try:
        await crud.reopen(db, inv)
    except FreightError as e:
        raise _err(e)
    return await _detail(db, user, inv)


@router.delete("/invoices/{invoice_id}", status_code=204)
async def delete_invoice(invoice_id: uuid.UUID, db: SessionDep, user: CurrentUserPayload,
                         token: BearerToken):
    await _need(db, user, "manage")
    inv = await _freight_invoice(db, invoice_id)
    if inv.status != "unmatched":
        raise HTTPException(status_code=409, detail="Reopen the invoice before deleting it")
    await finance_sync.sync_ap_invoice(db, inv, token, void=True)
    # An assignment to match a deleted invoice can never be done — close it
    # in the same commit as the delete.
    from app.api.v1.invoices import _close_open_match_tasks
    await _close_open_match_tasks(db, inv.id, uuid.UUID(user["sub"]))
    try:
        await crud.delete_invoice(db, inv)
    except FreightError as e:
        raise _err(e)


# ── Freight PA ────────────────────────────────────────────────────────────────

@router.post("/pa", response_model=FreightPaCreated, status_code=201)
async def create_pa(body: FreightPaCreate, db: SessionDep, user: CurrentUserPayload,
                    token: BearerToken):
    await _need(db, user, "manage")
    await budget_client.ensure_known_budget_code(token, FREIGHT_BUDGET_CODE)
    try:
        pa = await crud.create_pa(db, body, uuid.UUID(user["sub"]))
    except FreightError as e:
        raise _err(e)
    return {"id": pa.id, "pa_number": pa.pa_number}
