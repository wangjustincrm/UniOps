import uuid
from datetime import date
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field

# GST/HST/PST/RST/QST plus NONE (zero-rated / exempt placeholder).
_TAX_TYPE_RE = r"^(GST|HST|PST|RST|QST|NONE)$"


class TaxCodeOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    code: str
    name: str
    tax_type: str
    province: str | None
    rate: Decimal
    recoverable: bool
    effective_from: date
    effective_to: date | None
    active: bool


class TaxCodeCreate(BaseModel):
    code: str = Field(min_length=1, max_length=20)
    name: str = Field(min_length=1, max_length=100)
    tax_type: str = Field(pattern=_TAX_TYPE_RE)
    province: str | None = Field(default=None, min_length=2, max_length=2)
    rate: Decimal = Field(ge=0, le=1)  # fraction: 0.13 = 13%
    recoverable: bool = True
    effective_from: date
    effective_to: date | None = None
    active: bool = True


class TaxCodeUpdate(BaseModel):
    # `code` and `effective_from` form the rate-version identity and are immutable;
    # add a new effective-dated row instead of editing them.
    name: str | None = Field(default=None, min_length=1, max_length=100)
    tax_type: str | None = Field(default=None, pattern=_TAX_TYPE_RE)
    province: str | None = Field(default=None, min_length=2, max_length=2)
    rate: Decimal | None = Field(default=None, ge=0, le=1)
    recoverable: bool | None = None
    effective_to: date | None = None
    active: bool | None = None


class DeterminedCode(BaseModel):
    code: str
    name: str
    tax_type: str
    rate: Decimal
    recoverable: bool


class TaxDetermination(BaseModel):
    rule_id: uuid.UUID
    codes: list[DeterminedCode]
    combined_rate: Decimal
