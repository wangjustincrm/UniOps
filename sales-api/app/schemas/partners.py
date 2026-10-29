"""Partner DTOs (design §3.1 `sales_partners`). Shape only: types, lengths
matching the columns, the closed sets of app/domain/config.py, non-negative
counts. Rules across fields or records live in app/domain/partners.py and
the service."""
from __future__ import annotations

import json
from datetime import datetime
from typing import Annotated, Any, Literal

from pydantic import AfterValidator, BaseModel, BeforeValidator, ConfigDict, Field, StringConstraints

from app.schemas.common import not_excel_number

Channel = Literal["SHOPIFY", "TRUECOMMERCE", "MANUAL"]
SettlementMode = Literal["PREPAID", "INVOICE"]
SendingMode = Literal["AUTOMATIC", "MANUAL"]
InvoiceMethod = Literal["810_JSON", "EMAIL", "NONE"]

EMAIL_PATTERN = r"^[^@\s]+@[^@\s]+\.[^@\s]+$"


def _json_object(v: Any) -> Any:
    """`carrier_defaults` arrives as an object (API) or as JSON text (CSV)."""
    if isinstance(v, str):
        try:
            v = json.loads(v)
        except ValueError as e:
            raise ValueError("must be a JSON object") from e
    if v is not None and not isinstance(v, dict):
        raise ValueError("must be a JSON object")
    return v


Code = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=50), AfterValidator(not_excel_number)]
Name = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=255)]
Short = Annotated[str, StringConstraints(strip_whitespace=True, max_length=50)]
LabelCode = Annotated[str, StringConstraints(strip_whitespace=True, max_length=50), AfterValidator(not_excel_number)]
Terms = Annotated[str, StringConstraints(strip_whitespace=True, max_length=100)]
Email = Annotated[str, StringConstraints(strip_whitespace=True, max_length=255, pattern=EMAIL_PATTERN)]
Text = Annotated[str, StringConstraints(strip_whitespace=True)]
JsonObject = Annotated[dict[str, Any] | None, BeforeValidator(_json_object)]
Count = Annotated[int, Field(ge=0)]


class PartnerCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    code: Code
    name: Name
    abbrev: Short | None = None
    channel: Channel
    settlement_mode: SettlementMode
    sending_mode: SendingMode
    delay_minutes: Count
    invoice_method: InvoiceMethod
    invoice_email: Email | None = None
    invoice_cc: Text | None = None
    payment_terms: Terms | None = None
    dc_routing_required: bool
    min_shelf_life_days: Count | None = None
    label_code: LabelCode | None = None
    carrier_defaults: JsonObject = None
    active: bool = True


class PartnerUpdate(BaseModel):
    """Only the fields sent are changed; an optional field sent as null is
    cleared, a required one sent as null is refused (its type has no None —
    the None default only marks it unset). The code is the partner's identity
    (CSV key, DC routing, documents) and is not changed after creation.
    `active` re-activates a deactivated partner."""
    model_config = ConfigDict(extra="forbid")

    name: Name = None  # type: ignore[assignment]
    abbrev: Short | None = None
    channel: Channel = None  # type: ignore[assignment]
    settlement_mode: SettlementMode = None  # type: ignore[assignment]
    sending_mode: SendingMode = None  # type: ignore[assignment]
    delay_minutes: Count = None  # type: ignore[assignment]
    invoice_method: InvoiceMethod = None  # type: ignore[assignment]
    invoice_email: Email | None = None
    invoice_cc: Text | None = None
    payment_terms: Terms | None = None
    dc_routing_required: bool = None  # type: ignore[assignment]
    min_shelf_life_days: Count | None = None
    label_code: LabelCode | None = None
    carrier_defaults: JsonObject = None
    active: bool = None  # type: ignore[assignment]


class PartnerRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    code: str
    name: str
    abbrev: str | None
    channel: Channel
    settlement_mode: SettlementMode
    sending_mode: SendingMode
    delay_minutes: int
    invoice_method: InvoiceMethod
    invoice_email: str | None
    invoice_cc: str | None
    payment_terms: str | None
    dc_routing_required: bool
    min_shelf_life_days: int | None
    label_code: str | None
    carrier_defaults: dict[str, Any] | None
    active: bool
    version: int
    created_at: datetime
    created_by_name: str
    updated_at: datetime | None
    updated_by_name: str | None
