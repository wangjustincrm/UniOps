"""Configuration → Carriers (sales19): the SCAC a 945 names (W27-02), the
carrier's name, and the link that follows a parcel ({tracking} in it). CSV like
the other configuration tables."""
from __future__ import annotations

from sqlalchemy import select

from app.models.config import Carrier
from app.platform.repository import Repository
from app.platform.services import CrudService
from app.platform.views import Preset
from app.schemas.carriers import CarrierCreate, CarrierRead, CarrierUpdate

READ = "sales.read"
CONFIG = "sales.config"


class CarrierRepository(Repository[Carrier]):
    model = Carrier
    search_fields = ("scac", "name")
    list_fields = ("scac", "name", "tracking_url", "active", "created_at", "updated_at")
    labels = {"scac": "SCAC", "tracking_url": "Tracking link"}
    default_sort = ("scac",)
    groupable = ("active",)
    presets = (Preset("active", "Active", {"filters": [{"field": "active", "op": "eq", "value": "true"}]}),
               Preset("all", "All"))


class CarrierService(CrudService[Carrier, CarrierRead, CarrierCreate, CarrierUpdate]):
    repo_cls = CarrierRepository
    read_schema = CarrierRead
    create_schema = CarrierCreate
    update_schema = CarrierUpdate
    read_permission = READ
    write_permission = CONFIG
    csv_key = ("scac",)
    csv_columns = ("scac", "name", "tracking_url", "active")
    unique_constraints = {"sales_carriers_scac_key": ("carrier.scac_taken", "scac")}


async def carriers_by_scac(s, scacs) -> dict[str, Carrier]:
    """The active carriers among these SCACs (a 945's W27-02), by SCAC."""
    codes = {c.strip().upper() for c in scacs if c}
    if not codes:
        return {}
    rows = (await s.execute(select(Carrier).where(Carrier.scac.in_(codes), Carrier.active.is_(True)))).scalars()
    return {c.scac: c for c in rows}
