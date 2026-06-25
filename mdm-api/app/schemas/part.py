import uuid
from decimal import Decimal
from pydantic import BaseModel


class PartResponse(BaseModel):
    id: uuid.UUID
    code: str
    category: str
    name: str
    description: str | None
    supplier: str
    supplier_part_no: str
    supplier_item_id: str | None
    unit_price: Decimal
    unit: str
    is_active: bool
    model_config = {"from_attributes": True}


class PartListResponse(BaseModel):
    items: list[PartResponse]
    total: int
