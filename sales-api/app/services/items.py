"""Item Master (FR-OB-02): items with their units, partner buyer parts, DC
routing — configuration screens on the platform's CrudService (P3)."""
from __future__ import annotations

from decimal import Decimal
from typing import Any

from sqlalchemy import select

from app.domain.items import check_units, factor, normalise_unit, titles_column, titles_list, units_text
from app.models.config import DcRouting, Item, ItemPartnerCode, ItemUnit, Partner
from app.platform.errors import InvalidInput
from app.platform.repository import FilterRule, Repository, parse_bool
from app.platform.services import CrudService
from app.schemas.items import (
    DcRoutingCreate, DcRoutingRead, DcRoutingUpdate, ItemCreate, ItemRead, ItemUpdate, PartnerCodeCreate,
    PartnerCodeRead, PartnerCodeUpdate, UnitRead,
)

READ = "sales.read"
CONFIG = "sales.config"


def _active_units(item: Item) -> list[dict[str, Any]]:
    return [{"unit": u.unit, "factor_ea": u.factor_ea, "shopify_titles": titles_list(u.shopify_titles)}
            for u in item.units if u.active]


def _unit_set(units: list[dict[str, Any]]) -> set[tuple[str, Decimal, str | None]]:
    return {(normalise_unit(u["unit"]), factor(u["factor_ea"]), titles_column(list(u.get("shopify_titles") or [])))
            for u in units}


async def _require(session, model, column, value, field: str, what: str) -> None:
    if value is None:
        return
    found = (await session.execute(select(model.id).where(column == value).limit(1))).scalar_one_or_none()
    if found is None:
        raise InvalidInput(f"{what}.unknown", f"no {what.replace('_', ' ')} {value!r}", {"field": field})


class ItemRepository(Repository[Item]):
    model = Item
    search_fields = ("item_code", "description", "sku", "upc", "gtin")
    sortable = {"item_code": Item.item_code, "description": Item.description, "active": Item.active}
    filterable = {"active": FilterRule(Item.active, ops=frozenset({"eq"}), cast=parse_bool),
                  "warehouse_held": FilterRule(Item.warehouse_held, ops=frozenset({"eq"}), cast=parse_bool)}
    default_sort = ("item_code",)


class ItemService(CrudService[Item, ItemRead, ItemCreate, ItemUpdate]):
    repo_cls = ItemRepository
    read_schema = ItemRead
    create_schema = ItemCreate
    update_schema = ItemUpdate
    read_permission = READ
    write_permission = CONFIG
    csv_key = "item_code"
    csv_columns = ("item_code", "description", "sku", "upc", "gtin", "gross_weight_lb_per_ea", "warehouse_held",
                   "units", "active")
    unique_constraints = {"sales_items_item_code_key": ("item.code_taken", "item_code")}

    def to_read(self, entity: Item) -> ItemRead:
        return ItemRead.model_validate(
            {**{k: getattr(entity, k) for k in ItemRead.model_fields if k != "units"},
             "units": [UnitRead(**u) for u in _active_units(entity)]})

    def csv_value(self, entity: Item, column: str) -> Any:
        return units_text(_active_units(entity)) if column == "units" else super().csv_value(entity, column)

    def _same(self, entity: Item, key: str, value: Any) -> bool:
        if key == "units":
            return _unit_set(_active_units(entity)) == _unit_set(value)
        return super()._same(entity, key, value)

    async def validate_create(self, data: ItemCreate) -> None:
        check_units(data.model_dump()["units"])
        taken = (await self.uow.session.execute(select(Item.item_code).where(Item.item_code == data.item_code))).scalar_one_or_none()
        if taken is not None:
            raise InvalidInput("item.code_taken", f"item {taken!r} already exists", {"field": "item_code"})

    async def before_update(self, entity: Item, changes: dict[str, Any]) -> None:
        if "units" in changes:
            check_units(changes["units"])

    def build(self, values: dict[str, Any]) -> Item:
        units = values.pop("units")
        item = Item(**values)
        item.units = [ItemUnit(item_code=item.item_code, unit=normalise_unit(u["unit"]), factor_ea=factor(u["factor_ea"]),
                               shopify_titles=titles_column(list(u.get("shopify_titles") or [])), version=1, active=True)
                      for u in units]
        return item

    async def apply_changes(self, entity: Item, changes: dict[str, Any]) -> None:
        changes = dict(changes)
        units = changes.pop("units", None)
        await super().apply_changes(entity, changes)
        if units is None:
            return
        wanted = {normalise_unit(u["unit"]): (factor(u["factor_ea"]), titles_column(list(u.get("shopify_titles") or [])))
                  for u in units}
        by_unit = {u.unit: u for u in entity.units}
        for name, unit in by_unit.items():
            if name not in wanted and unit.active:
                unit.active = False  # never deleted: orders may refer to it (§13.1)
        for name, (f, titles) in wanted.items():
            unit = by_unit.get(name)
            if unit is None:
                entity.units.append(ItemUnit(item_code=entity.item_code, unit=name, factor_ea=f, shopify_titles=titles,
                                             version=1, active=True))
            else:
                if not unit.active:
                    unit.active = True
                if factor(unit.factor_ea) != f:
                    unit.factor_ea = f
                if unit.shopify_titles != titles:
                    unit.shopify_titles = titles


class PartnerCodeRepository(Repository[ItemPartnerCode]):
    model = ItemPartnerCode
    search_fields = ("partner_code", "buyer_part", "item_code")
    sortable = {"partner_code": ItemPartnerCode.partner_code, "buyer_part": ItemPartnerCode.buyer_part,
                "item_code": ItemPartnerCode.item_code}
    filterable = {"partner_code": FilterRule(ItemPartnerCode.partner_code), "item_code": FilterRule(ItemPartnerCode.item_code)}
    default_sort = ("partner_code", "buyer_part")


class PartnerCodeService(CrudService[ItemPartnerCode, PartnerCodeRead, PartnerCodeCreate, PartnerCodeUpdate]):
    """FR-OB-02: a buyer part number belongs to one partner."""
    repo_cls = PartnerCodeRepository
    read_schema = PartnerCodeRead
    create_schema = PartnerCodeCreate
    update_schema = PartnerCodeUpdate
    read_permission = READ
    write_permission = CONFIG
    csv_key = ("partner_code", "buyer_part")
    csv_columns = ("partner_code", "buyer_part", "item_code")
    unique_constraints = {"uq_sales_item_partner_codes_partner_part": ("partner_code.taken", "buyer_part")}

    async def validate_create(self, data: PartnerCodeCreate) -> None:
        s = self.uow.session
        await _require(s, Partner, Partner.code, data.partner_code, "partner_code", "partner")
        await _require(s, Item, Item.item_code, data.item_code, "item_code", "item")

    async def before_update(self, entity: ItemPartnerCode, changes: dict[str, Any]) -> None:
        await _require(self.uow.session, Item, Item.item_code, changes.get("item_code"), "item_code", "item")


class DcRoutingRepository(Repository[DcRouting]):
    model = DcRouting
    search_fields = ("partner_code", "ship_to_gln", "dc_number", "notes")
    sortable = {"partner_code": DcRouting.partner_code, "ship_to_gln": DcRouting.ship_to_gln,
                "dc_number": DcRouting.dc_number, "active": DcRouting.active}
    filterable = {"partner_code": FilterRule(DcRouting.partner_code),
                  "active": FilterRule(DcRouting.active, ops=frozenset({"eq"}), cast=parse_bool)}
    default_sort = ("partner_code", "ship_to_gln")


class DcRoutingService(CrudService[DcRouting, DcRoutingRead, DcRoutingCreate, DcRoutingUpdate]):
    """FR-OB-03: DC routing references the partner directly."""
    repo_cls = DcRoutingRepository
    read_schema = DcRoutingRead
    create_schema = DcRoutingCreate
    update_schema = DcRoutingUpdate
    read_permission = READ
    write_permission = CONFIG
    csv_key = ("partner_code", "ship_to_gln")
    csv_columns = ("partner_code", "ship_to_gln", "dc_number", "notes", "active")
    unique_constraints = {"uq_sales_dc_routing_partner_gln": ("dc_routing.taken", "ship_to_gln")}

    async def validate_create(self, data: DcRoutingCreate) -> None:
        await _require(self.uow.session, Partner, Partner.code, data.partner_code, "partner_code", "partner")
