"""Pydantic schemas for Vendor."""
import uuid
from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel, EmailStr, Field

PAYMENT_TERMS = {"net15", "net30", "net60", "net90", "cod", "prepayment"}


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
    phone: str | None = Field(default=None, max_length=50)
    address: str | None = None
    payment_terms: str = Field(default="net30", max_length=20)
    max_prepayment_pct: Decimal | None = Field(default=None, ge=1, le=100)
    currency: str = Field(default="CAD", min_length=1, max_length=10)
    notes: str | None = None


class VendorUpdate(BaseModel):
    code: str | None = Field(default=None, min_length=1, max_length=50)
    erp_id: str | None = Field(default=None, max_length=100)
    name: str | None = Field(default=None, min_length=1, max_length=255)
    category: str | None = Field(default=None, min_length=1, max_length=100)
    contact_name: str | None = Field(default=None, min_length=1, max_length=255)
    contact_email: str | None = Field(default=None, max_length=255)
    phone: str | None = Field(default=None, max_length=50)
    address: str | None = None
    payment_terms: str | None = Field(default=None, max_length=20)
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
    phone: str | None = Field(default=None, max_length=50)
    address: str | None = None
    payment_terms: str = Field(default="net30", max_length=20)
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
    payment_terms: str = "net30"
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
