from datetime import datetime
from decimal import Decimal
from pydantic import BaseModel


class ErpMaterialResponse(BaseModel):
    erp_part_no: str
    description: str | None
    unit_meas: str | None
    dim_quality: str | None
    weight_net: Decimal | None
    weight_gross: Decimal | None
    volume: Decimal | None
    part_status: str | None
    item_mes_type: str | None
    erp_rowversion: datetime | None
    synced_at: datetime

    model_config = {"from_attributes": True}


class ErpSupplierResponse(BaseModel):
    erp_supplier_code: str
    supplier_name: str
    supplier_address: str | None
    supplier_tel: str | None
    supplier_fax: str | None
    supplier_type: str | None
    erp_rowversion: datetime | None
    synced_at: datetime

    model_config = {"from_attributes": True}


class ErpPersonResponse(BaseModel):
    erp_person_code: str
    person_name: str
    company_code: str | None
    company_name: str | None
    department_code: str | None
    department_name: str | None
    is_valid: bool
    pk_psndoc: str | None
    erp_rowversion: datetime | None
    synced_at: datetime

    model_config = {"from_attributes": True}


class ErpSyncStateResponse(BaseModel):
    kind: str
    last_ts: datetime | None
    last_synced_at: datetime | None
    last_status: str | None
    last_message: str | None
    last_row_count: int

    model_config = {"from_attributes": True}


class ErpSyncResultResponse(BaseModel):
    kind: str
    mode: str
    total: int
    inserted: int
    updated: int
    last_ts: str
    status: str
    message: str


class ErpMaterialListResponse(BaseModel):
    items: list[ErpMaterialResponse]
    total: int


class ErpSupplierListResponse(BaseModel):
    items: list[ErpSupplierResponse]
    total: int


class ErpPersonListResponse(BaseModel):
    items: list[ErpPersonResponse]
    total: int
