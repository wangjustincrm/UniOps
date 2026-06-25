import uuid
from pydantic import BaseModel


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
    currency: str
    is_active: bool
    notes: str | None
    model_config = {"from_attributes": True}


class VendorListResponse(BaseModel):
    items: list[VendorResponse]
    total: int
