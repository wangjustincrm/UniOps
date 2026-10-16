import re
import uuid
from decimal import Decimal
from typing import Annotated, Optional

from pydantic import AfterValidator, BaseModel, ConfigDict, Field

# `net<N>` (N = 1..365 days after invoice date), `cod` or `prepayment`. Same
# rule as epms-api app/schemas/vendor.py; finance-api's AP cash-flow reads N
# out of the code. Enforced on writes only — PartnerOut must still serialise
# whatever an older writer left in the column.
MAX_NET_DAYS = 365
_NET_RE = re.compile(r"net\s*(\d{1,3})")


def normalize_payment_terms(v: str) -> str:
    s = v.strip().lower()
    if s in ("cod", "prepayment"):
        return s
    m = _NET_RE.fullmatch(s)
    if m and 1 <= int(m.group(1)) <= MAX_NET_DAYS:
        return f"net{int(m.group(1))}"
    raise ValueError(
        f"payment terms must be net<days> (1-{MAX_NET_DAYS}), cod or prepayment; got {v!r}")


PaymentTerms = Annotated[str, AfterValidator(normalize_payment_terms)]


class PartnerBase(BaseModel):
    code: str = Field(min_length=1, max_length=50)
    erp_id: Optional[str] = Field(default=None, max_length=100)
    name: str = Field(min_length=1, max_length=255)
    category: str = "General"
    contact_name: str = ""
    contact_email: str = ""
    remittance_email: Optional[str] = None
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
    payment_terms: PaymentTerms = "net30"


class PartnerUpdate(BaseModel):
    code: Optional[str] = Field(default=None, min_length=1, max_length=50)
    erp_id: Optional[str] = None
    name: Optional[str] = None
    category: Optional[str] = None
    contact_name: Optional[str] = None
    contact_email: Optional[str] = None
    remittance_email: Optional[str] = None
    phone: Optional[str] = None
    address: Optional[str] = None
    payment_terms: Optional[PaymentTerms] = None
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
