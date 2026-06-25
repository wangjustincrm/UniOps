import uuid
from datetime import date
from pydantic import BaseModel


class CompanyCreate(BaseModel):
    code: str
    legal_name: str
    display_name: str
    tagline: str | None = None
    logo_data_url: str | None = None
    logo_file_name: str | None = None
    registration_number: str | None = None
    tax_id: str | None = None
    incorporated_date: date | None = None
    jurisdiction: str | None = None
    primary_address: str = ""
    delivery_address: str | None = None
    phone: str | None = None
    website: str | None = None
    functional_currency: str = "CAD"
    fiscal_year_start: str | None = None
    fiscal_year_end: str | None = None
    erp_id: str | None = None


class CompanyUpdate(BaseModel):
    legal_name: str | None = None
    display_name: str | None = None
    tagline: str | None = None
    logo_data_url: str | None = None
    logo_file_name: str | None = None
    registration_number: str | None = None
    tax_id: str | None = None
    incorporated_date: date | None = None
    jurisdiction: str | None = None
    primary_address: str | None = None
    delivery_address: str | None = None
    phone: str | None = None
    website: str | None = None
    functional_currency: str | None = None
    fiscal_year_start: str | None = None
    fiscal_year_end: str | None = None
    is_active: bool | None = None
    erp_id: str | None = None


class CompanyResponse(BaseModel):
    id: uuid.UUID
    code: str
    legal_name: str
    display_name: str
    tagline: str | None
    logo_data_url: str | None
    logo_file_name: str | None
    registration_number: str | None
    tax_id: str | None
    incorporated_date: date | None
    jurisdiction: str | None
    primary_address: str
    delivery_address: str | None
    phone: str | None
    website: str | None
    functional_currency: str
    fiscal_year_start: str | None
    fiscal_year_end: str | None
    is_active: bool
    erp_id: str | None
    model_config = {"from_attributes": True}
