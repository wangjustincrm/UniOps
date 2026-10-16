"""Pydantic schemas for Vendor."""
import re
import uuid
from datetime import datetime
from decimal import Decimal
from typing import Annotated

from pydantic import AfterValidator, BaseModel, EmailStr, Field

# `net<N>` = due N days after the invoice date (any N in 1..365 — vendors on
# Net 14 / Net 21 exist, the form's presets are just the common ones), plus
# the two non-day terms. finance-api's AP cash-flow forecast reads N straight
# out of the code, so anything outside this pattern would forecast as
# "no term on file". Keep in step with mdm-api and epms/src/lib/paymentTerms.ts.
MAX_NET_DAYS = 365
NON_DAY_TERMS = {"cod", "prepayment"}
_NET_RE = re.compile(r"net\s*(\d{1,3})")


def normalize_payment_terms(v: str) -> str:
    s = v.strip().lower()
    if s in NON_DAY_TERMS:
        return s
    m = _NET_RE.fullmatch(s)   # also takes "Net 21" from a hand-made CSV
    if m and 1 <= int(m.group(1)) <= MAX_NET_DAYS:
        return f"net{int(m.group(1))}"
    raise ValueError(
        f"payment terms must be net<days> (1-{MAX_NET_DAYS}), cod or prepayment; got {v!r}")


PaymentTerms = Annotated[str, AfterValidator(normalize_payment_terms)]


class VendorCreate(BaseModel):
    code: str = Field(min_length=1, max_length=50)
    erp_id: str | None = Field(default=None, max_length=100)
    name: str = Field(min_length=1, max_length=255)
    category: str = Field(min_length=1, max_length=100)
    # Contact is optional in the UI (no required-field marker); allow empty so
    # a vendor can be saved with only POID/name/category. Matches VendorCsvRow
    # and mdm PartnerBase, both of which default these to "".
    contact_name: str = Field(default="", max_length=255)
    contact_email: str = Field(default="", max_length=255)
    remittance_email: str = Field(default="", max_length=255)
    phone: str | None = Field(default=None, max_length=50)
    address: str | None = None
    payment_terms: PaymentTerms = "net30"
    max_prepayment_pct: Decimal | None = Field(default=None, ge=1, le=100)
    currency: str = Field(default="CAD", min_length=1, max_length=10)
    notes: str | None = None
    # Honor "Inactive" chosen at creation. Flows into the mdm partner payload via
    # _partner_payload (model_dump(exclude_none=True) — is_active is never None).
    is_active: bool = True


class VendorUpdate(BaseModel):
    code: str | None = Field(default=None, min_length=1, max_length=50)
    erp_id: str | None = Field(default=None, max_length=100)
    name: str | None = Field(default=None, min_length=1, max_length=255)
    category: str | None = Field(default=None, min_length=1, max_length=100)
    # No min_length — symmetric with VendorCreate (which allows empty), so
    # editing a vendor with a blank contact name doesn't 422.
    contact_name: str | None = Field(default=None, max_length=255)
    contact_email: str | None = Field(default=None, max_length=255)
    remittance_email: str | None = Field(default=None, max_length=255)
    phone: str | None = Field(default=None, max_length=50)
    address: str | None = None
    payment_terms: PaymentTerms | None = None
    max_prepayment_pct: Decimal | None = Field(default=None, ge=1, le=100)
    currency: str | None = Field(default=None, max_length=10)
    notes: str | None = None
    is_active: bool | None = None


class VendorResponse(BaseModel):
    id: uuid.UUID
    code: str
    erp_id: str | None
    name: str
    category: str
    contact_name: str
    contact_email: str
    remittance_email: str | None = None
    phone: str | None
    address: str | None
    payment_terms: str
    max_prepayment_pct: Decimal | None
    currency: str
    is_active: bool
    notes: str | None
    created_at: datetime

    model_config = {"from_attributes": True}


class VendorCsvRow(BaseModel):
    """One parsed row from a vendor CSV import."""
    code: str = Field(min_length=1, max_length=50)
    erp_id: str | None = Field(default=None, max_length=100)
    name: str = Field(min_length=1, max_length=255)
    category: str = Field(min_length=1, max_length=100)
    contact_name: str = Field(default="", max_length=255)
    contact_email: str = Field(default="", max_length=255)
    remittance_email: str = Field(default="", max_length=255)
    phone: str | None = Field(default=None, max_length=50)
    address: str | None = None
    payment_terms: PaymentTerms = "net30"
    currency: str = Field(default="CAD", max_length=10)
    notes: str | None = None
    is_active: bool = True


class VendorListResponse(BaseModel):
    items: list[VendorResponse]
    total: int


class VendorImportResult(BaseModel):
    created: int
    updated: int
    errors: list[str]


class VendorBrief(BaseModel):
    id: uuid.UUID
    code: str
    name: str
    currency: str
    payment_terms: str
    is_active: bool

    model_config = {"from_attributes": True}


class ErpVendorImportDefaults(BaseModel):
    category: str = "other"
    payment_terms: PaymentTerms = "net30"
    currency: str = "CAD"


class ErpVendorImportRequest(BaseModel):
    erp_supplier_codes: list[str]
    defaults: ErpVendorImportDefaults = ErpVendorImportDefaults()


class ErpVendorImportError(BaseModel):
    erp_supplier_code: str
    reason: str


class ErpVendorImportResponse(BaseModel):
    created: int
    errors: list[ErpVendorImportError]
