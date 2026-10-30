"""Item Master DTOs (design §3.1: sales_items, sales_item_units,
sales_item_partner_codes, sales_dc_routing). Shape only; rules in
app/domain/items.py and the services."""
from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Annotated, Any

from pydantic import AfterValidator, BaseModel, BeforeValidator, ConfigDict, Field, StringConstraints

from app.domain.items import parse_units
from app.schemas.common import barcode, not_excel_number

Code = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=50), AfterValidator(not_excel_number)]
Barcode = Annotated[str, StringConstraints(strip_whitespace=True, max_length=50), AfterValidator(barcode)]
# Checked as written, stored upper-case (`case` is CASE).
UnitName = Annotated[str, StringConstraints(strip_whitespace=True, to_upper=True, min_length=1, max_length=20,
                                            pattern=r"^[A-Za-z0-9][A-Za-z0-9-]*$")]
Factor = Annotated[Decimal, Field(gt=0, max_digits=12, decimal_places=4)]
Weight = Annotated[Decimal, Field(ge=0, max_digits=12, decimal_places=4)]
Text = Annotated[str, StringConstraints(strip_whitespace=True)]
BuyerPart = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=100), AfterValidator(not_excel_number)]


# One Shopify variant title meaning the unit; `, ; [ ]` separate the units text.
ShopifyTitle = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=100,
                                                pattern=r"^[^,;\[\]]+$")]


class UnitIn(BaseModel):
    unit: UnitName
    factor_ea: Factor
    shopify_titles: list[ShopifyTitle] = []


def _units(v: Any) -> Any:
    """A list (API) or `EA=1; CASE=6` (CSV, form)."""
    return parse_units(v) if isinstance(v, str) else v


Units = Annotated[list[UnitIn], BeforeValidator(_units)]


class ItemCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    item_code: Code
    description: Text | None = None
    sku: Code | None = None
    upc: Barcode | None = None
    gtin: Barcode | None = None
    gross_weight_lb_per_ea: Weight | None = None
    warehouse_held: bool
    units: Units
    active: bool = True


class ItemUpdate(BaseModel):
    """`units` replaces the whole set (a unit left out is deactivated, never deleted)."""
    model_config = ConfigDict(extra="forbid")

    description: Text | None = None
    sku: Code | None = None
    upc: Barcode | None = None
    gtin: Barcode | None = None
    gross_weight_lb_per_ea: Weight | None = None
    warehouse_held: bool = None  # type: ignore[assignment]
    units: Units = None  # type: ignore[assignment]
    active: bool = None  # type: ignore[assignment]


class UnitRead(BaseModel):
    unit: str
    factor_ea: Decimal
    shopify_titles: list[str] = []


class ItemRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    item_code: str
    description: str | None
    sku: str | None
    upc: str | None
    gtin: str | None
    gross_weight_lb_per_ea: Decimal | None
    warehouse_held: bool
    units: list[UnitRead] = []
    active: bool
    version: int
    created_at: datetime
    created_by_name: str
    updated_at: datetime | None
    updated_by_name: str | None


class PartnerCodeCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    partner_code: Code
    buyer_part: BuyerPart
    item_code: Code


class PartnerCodeUpdate(BaseModel):
    """The partner and buyer part are the key; only the item changes."""
    model_config = ConfigDict(extra="forbid")

    item_code: Code = None  # type: ignore[assignment]


class PartnerCodeRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    partner_code: str
    buyer_part: str
    item_code: str
    version: int
    created_at: datetime
    created_by_name: str
    updated_at: datetime | None
    updated_by_name: str | None


class DcRoutingCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    partner_code: Code
    ship_to_gln: Code
    dc_number: Code
    notes: Text | None = None
    active: bool = True


class DcRoutingUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    dc_number: Code = None  # type: ignore[assignment]
    notes: Text | None = None
    active: bool = None  # type: ignore[assignment]


class DcRoutingRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    partner_code: str
    ship_to_gln: str
    dc_number: str
    notes: str | None
    active: bool
    version: int
    created_at: datetime
    created_by_name: str
    updated_at: datetime | None
    updated_by_name: str | None
