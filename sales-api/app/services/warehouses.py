"""Configuration → Warehouses (FR-OB-27): the 943's ship-from and ship-to,
no longer hard-coded (Zoho: Kingston, 1680 Venture Dr → Caledon, 12480
Coleraine Drive). CSV like the other configuration tables."""
from __future__ import annotations

from app.domain.config import WAREHOUSE_ROLES
from app.models.config import Warehouse
from app.platform.repository import Repository
from app.platform.services import CrudService
from app.platform.views import Preset
from app.schemas.warehouses import WarehouseCreate, WarehouseRead, WarehouseUpdate

READ = "sales.read"
CONFIG = "sales.config"


class WarehouseRepository(Repository[Warehouse]):
    model = Warehouse
    search_fields = ("code", "name", "city", "edi_id")
    list_fields = ("code", "name", "role", "edi_id_qualifier", "edi_id", "address1", "city", "province", "zip", "country",
                   "active", "created_at", "updated_at")
    labels = {"edi_id_qualifier": "EDI id qualifier", "edi_id": "EDI id", "address1": "Address", "zip": "Postal code"}
    enums = {"role": WAREHOUSE_ROLES}
    default_sort = ("code",)
    groupable = ("role", "province", "active")
    presets = (Preset("active", "Active", {"filters": [{"field": "active", "op": "eq", "value": "true"}]}),
               Preset("inactive", "Inactive", {"filters": [{"field": "active", "op": "eq", "value": "false"}]}),
               Preset("all", "All"))


class WarehouseService(CrudService[Warehouse, WarehouseRead, WarehouseCreate, WarehouseUpdate]):
    repo_cls = WarehouseRepository
    read_schema = WarehouseRead
    create_schema = WarehouseCreate
    update_schema = WarehouseUpdate
    read_permission = READ
    write_permission = CONFIG
    csv_key = ("code",)
    csv_columns = ("code", "name", "role", "edi_id_qualifier", "edi_id", "address1", "city", "province", "zip", "country",
                   "address_lines", "active")
    unique_constraints = {"sales_warehouses_code_key": ("warehouse.code_taken", "code")}
