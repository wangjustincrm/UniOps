"""Vendor endpoints."""
import csv
import io
import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, UploadFile, status
from fastapi.responses import StreamingResponse

from app.core.deps import BearerToken, CurrentUserPayload, SessionDep, require_permission
from app.crud import vendor as vendor_crud
from app.schemas.vendor import (
    ErpVendorImportError,
    ErpVendorImportRequest,
    ErpVendorImportResponse,
    VendorCreate,
    VendorCsvRow,
    VendorImportResult,
    VendorListResponse,
    VendorResponse,
    VendorUpdate,
)
from app.services.mdm_client import MdmClient, MdmError

router = APIRouter(prefix="/vendors", tags=["vendors"])

WriteDep = Annotated[dict, Depends(require_permission("vendor_master"))]


# Vendor master is OWNED by mdm-api (B3 / P1): EPMS authorizes the caller
# (vendor_master matrix permission) then forwards writes to mdm /partners —
# the single writer of business_partners. Reads stay local (shared DB).
def _partner_payload(body) -> dict:
    """Map a Vendor create/CSV row to an mdm PartnerCreate payload (supplier)."""
    data = body.model_dump(exclude_none=True)
    data["is_supplier"] = True
    return data


def _mdm_http(exc: MdmError) -> HTTPException:
    code = exc.status if exc.status in (403, 404, 409, 422) else 502
    return HTTPException(status_code=code, detail=str(exc))


@router.get("", response_model=VendorListResponse)
async def list_vendors(
    db: SessionDep,
    _: CurrentUserPayload,
    search: str | None = Query(default=None),
    category: str | None = Query(default=None),
    active_only: bool = False,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=5, le=500),
):
    items, total = await vendor_crud.get_all(
        db, search=search, category=category, active_only=active_only,
        page=page, page_size=page_size,
    )
    return VendorListResponse(items=items, total=total)


@router.post("", response_model=VendorResponse, status_code=status.HTTP_201_CREATED)
async def create_vendor(body: VendorCreate, db: SessionDep, _: WriteDep, token: BearerToken):
    async with MdmClient(bearer_token=token) as mdm:
        try:
            created = await mdm.create_partner(_partner_payload(body))
        except MdmError as e:
            raise _mdm_http(e)
    # mdm committed to the shared table; read it back through the EPMS view
    vendor = await vendor_crud.get_by_id(db, uuid.UUID(created["id"]))
    return vendor


# ── CSV export ─────────────────────────────────────────────────────────────────

@router.get("/export")
async def export_vendors(db: SessionDep, _: CurrentUserPayload):
    """Download all vendors as CSV."""
    vendors = await vendor_crud.get_all_for_export(db)

    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow([
        "code", "erpId", "name", "category", "contactName", "contactEmail",
        "remittanceEmail", "phone", "address", "paymentTerms", "currency",
        "notes", "isActive",
    ])
    for v in vendors:
        writer.writerow([
            v.code, v.erp_id or "", v.name, v.category, v.contact_name, v.contact_email,
            v.remittance_email or "", v.phone or "", v.address or "", v.payment_terms,
            v.currency, v.notes or "", str(v.is_active).lower(),
        ])

    buf.seek(0)
    return StreamingResponse(
        iter([buf.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=vendors.csv"},
    )


# ── CSV import ─────────────────────────────────────────────────────────────────

@router.post("/import", response_model=VendorImportResult)
async def import_vendors(file: UploadFile, db: SessionDep, _: WriteDep, token: BearerToken):
    """
    Upload a CSV to upsert vendors. Updates existing by code, creates new ones.

    Required columns: code, name, category
    Optional columns: contactName, contactEmail, remittanceEmail, phone, address, paymentTerms, currency, notes, isActive
    """
    if not file.filename or not file.filename.lower().endswith(".csv"):
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="File must be a .csv")

    content = await file.read()
    try:
        text = content.decode("utf-8-sig")
    except UnicodeDecodeError:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="File must be UTF-8 encoded")

    reader = csv.DictReader(io.StringIO(text))
    required = {"code", "name", "category"}
    if reader.fieldnames is None or not required.issubset(set(reader.fieldnames)):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Missing required columns: {sorted(required)}",
        )

    rows: list[VendorCsvRow] = []
    parse_errors: list[str] = []
    for i, raw in enumerate(reader, start=2):
        try:
            is_active_raw = raw.get("isActive", "true").strip().lower()
            rows.append(VendorCsvRow(
                code=raw["code"].strip(),
                erp_id=raw.get("erpId", "").strip() or None,
                name=raw["name"].strip(),
                category=raw["category"].strip(),
                contact_name=raw.get("contactName", "").strip(),
                contact_email=raw.get("contactEmail", "").strip(),
                remittance_email=raw.get("remittanceEmail", "").strip(),
                phone=raw.get("phone", "").strip() or None,
                address=raw.get("address", "").strip() or None,
                payment_terms=raw.get("paymentTerms", "net30").strip() or "net30",
                currency=raw.get("currency", "CAD").strip() or "CAD",
                notes=raw.get("notes", "").strip() or None,
                is_active=is_active_raw not in ("false", "0", "no", "inactive"),
            ))
        except Exception as e:
            parse_errors.append(f"Row {i}: {e}")

    # Upsert through mdm (the owner): match by code → PATCH, else POST.
    created = updated = 0
    write_errors: list[str] = []
    async with MdmClient(bearer_token=token) as mdm:
        for row in rows:
            try:
                existing = await mdm.find_partner_by_code(row.code)
                if existing:
                    await mdm.update_partner(existing["id"], row.model_dump(exclude_none=True))
                    updated += 1
                else:
                    await mdm.create_partner(_partner_payload(row))
                    created += 1
            except MdmError as e:
                write_errors.append(f"{row.code}: {e}")

    return VendorImportResult(created=created, updated=updated,
                              errors=parse_errors + write_errors)


# ── Single vendor ──────────────────────────────────────────────────────────────

@router.get("/{vendor_id}", response_model=VendorResponse)
async def get_vendor(vendor_id: uuid.UUID, db: SessionDep, _: CurrentUserPayload):
    vendor = await vendor_crud.get_by_id(db, vendor_id)
    if vendor is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Vendor not found")
    return vendor


@router.patch("/{vendor_id}", response_model=VendorResponse)
async def update_vendor(vendor_id: uuid.UUID, body: VendorUpdate, db: SessionDep,
                        _: WriteDep, token: BearerToken):
    if await vendor_crud.get_by_id(db, vendor_id) is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Vendor not found")
    async with MdmClient(bearer_token=token) as mdm:
        try:
            await mdm.update_partner(str(vendor_id), body.model_dump(exclude_none=True))
        except MdmError as e:
            raise _mdm_http(e)
    return await vendor_crud.get_by_id(db, vendor_id)


# ── ERP MDM import ─────────────────────────────────────────────────────────────

@router.post("/import-from-erp", response_model=ErpVendorImportResponse)
async def import_vendors_from_erp(
    body: ErpVendorImportRequest,
    db: SessionDep,
    _: WriteDep,
    token: BearerToken,
):
    created = 0
    errors: list[ErpVendorImportError] = []

    async with MdmClient(bearer_token=token) as mdm:
        for code in body.erp_supplier_codes:
            try:
                supplier = await mdm.get_supplier(code)
            except Exception as e:
                errors.append(ErpVendorImportError(erp_supplier_code=code, reason=f"mdm error: {e}"))
                continue
            if not supplier:
                errors.append(ErpVendorImportError(erp_supplier_code=code, reason="ERP supplier not in mdm-api mirror"))
                continue
            # Dedup by ERP id (the exact ERP supplier code we store in erp_id),
            # NOT get_by_code: get_by_code strips leading zeros, so an ERP code like
            # "0000415" would falsely match an unrelated vendor whose internal code
            # is "415" and wrongly block the import.
            if await vendor_crud.get_by_erp_id(db, code):
                errors.append(ErpVendorImportError(erp_supplier_code=code, reason=f"vendor already exists for erp_id={code}"))
                continue
            try:
                await mdm.create_partner({
                    "code": code, "erp_id": code,
                    "name": supplier.get("supplier_name") or code,
                    "category": body.defaults.category,
                    "contact_name": supplier.get("supplier_name") or code,
                    "contact_email": f"{code}@erp.local",
                    "phone": supplier.get("supplier_tel"),
                    "address": supplier.get("supplier_address"),
                    "payment_terms": body.defaults.payment_terms,
                    "currency": body.defaults.currency,
                    "is_active": True, "is_supplier": True,
                })
                created += 1
            except MdmError as e:
                errors.append(ErpVendorImportError(erp_supplier_code=code, reason=str(e)))

    return ErpVendorImportResponse(created=created, errors=errors)
