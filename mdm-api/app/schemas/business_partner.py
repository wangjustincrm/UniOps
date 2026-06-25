import uuid
from decimal import Decimal
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field


class PartnerBase(BaseModel):
    code: str = Field(min_length=1, max_length=50)
    erp_id: Optional[str] = Field(default=None, max_length=100)
    name: str = Field(min_length=1, max_length=255)
    category: str = "General"
    contact_name: str = ""
    contact_email: str = ""
    phone: Optional[str] = None
    address: Optional[str] = None
    payment_terms: str = "net30"
    max_prepayment_pct: Optional[Decimal] = None
    currency: str = "CAD"
    is_active: bool = True
    notes: Optional[str] = None
    is_supplier: bool = True
    is_customer: bool = False
    tax_number: Optional[str] = None
    customer_type: Optional[str] = None   # business | consumer | export
    province: Optional[str] = Field(default=None, min_length=2, max_length=2)
    credit_limit: Optional[Decimal] = None


class PartnerCreate(PartnerBase):
    pass


class PartnerUpdate(BaseModel):
    erp_id: Optional[str] = None
    name: Optional[str] = None
    category: Optional[str] = None
    contact_name: Optional[str] = None
    contact_email: Optional[str] = None
    phone: Optional[str] = None
    address: Optional[str] = None
    payment_terms: Optional[str] = None
    max_prepayment_pct: Optional[Decimal] = None
    currency: Optional[str] = None
    is_active: Optional[bool] = None
    notes: Optional[str] = None
    is_supplier: Optional[bool] = None
    is_customer: Optional[bool] = None
    tax_number: Optional[str] = None
    customer_type: Optional[str] = None
    province: Optional[str] = Field(default=None, min_length=2, max_length=2)
    credit_limit: Optional[Decimal] = None


class PartnerOut(PartnerBase):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    erp_id: Optional[str] = None


class PartnerListResponse(BaseModel):
    items: list[PartnerOut]
    total: int
