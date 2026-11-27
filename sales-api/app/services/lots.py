"""Lots (traceability and recall; user 2026-11-03): the 945 rows with their
order and customer — searchable and groupable by lot and item, "Expiring
soon" and "By lot", and a recall: one lot or several (`lot:in:a|b`) gives
every order and customer it went to, as a CSV too."""
from __future__ import annotations


from app.models.lots import LotRow
from app.platform.query import FilterRule
from app.platform.record_types import Ref
from app.platform.repository import Repository
from app.platform.resource import Resource
from app.platform.services import ReadOnlyService
from app.platform.views import Preset
from app.domain.carriers import tracking_url
from app.schemas.lots import LotRead
from app.services.carriers import carriers_by_scac

READ = "sales.read"
EXPIRING_DAYS = 30  # "Expiring soon": expiring within this many days (user 2026-11-03)

CSV_COLUMNS = ("lot", "item_code", "expiry", "qty_shipped_ea", "ship_date", "order_ref", "order_number", "partner_code", "partner_name",
               "ship_to_city", "ship_to_postal", "tracking", "sscc", "bol", "our_reference", "document_id", "applied", "shadow")


class LotRepository(Repository[LotRow]):
    model = LotRow
    refs = {"lot": Ref("lot"), "item_code": Ref("item"), "order_id": Ref("order"), "order_ref": Ref("order", "order_id"), "order_number": Ref("order", "order_id")}
    search_fields = ("lot", "item_code", "order_ref", "partner_name", "tracking", "sscc", "ship_to_postal")
    search_numbers = ("order_number",)  # the Internal no., matched exactly
    list_fields = ("lot", "item_code", "expiry", "days_to_expiry", "qty_shipped_ea", "sscc", "tracking", "ship_date",
                   "bol", "our_reference", "applied", "shadow", "order_ref", "order_number", "partner_code", "partner_name",
                   "ship_to_city", "ship_to_postal", "document_id", "shipment_id")
    filterable = {"order_id": FilterRule(LotRow.order_id)}
    meta_hidden = ("shadow",)
    labels = {"item_code": "Item", "qty_shipped_ea": "Qty EA", "sscc": "Pallet (SSCC)", "days_to_expiry": "Days to expiry",
              "order_ref": "Order ref", "order_number": "Internal no.", "partner_name": "Partner", "partner_code": "Partner code",
              "ship_to_city": "Ship-to city", "ship_to_postal": "Ship-to postal code", "document_id": "945 document",
              "our_reference": "940 reference (N9*E1)", "bol": "BOL", "applied": "Applied"}
    suggestable = ("lot", "item_code", "partner_name", "order_ref")
    groupable = ("lot", "item_code", "expiry", "ship_date", "partner_name", "order_ref", "ship_to_city")
    summable = ("qty_shipped_ea",)
    default_sort = ("-ship_date", "lot")
    presets = (
        Preset("all", "All"),
        Preset("expiring_soon", f"Expiring soon ({EXPIRING_DAYS} days)", {
            "filters": [{"field": "days_to_expiry", "op": "between", "value": [0, EXPIRING_DAYS]}],
            "sort": [{"field": "expiry", "desc": False}]}),
        Preset("by_lot", "By lot", {"group": [{"field": "lot", "desc": False}], "sort": [{"field": "expiry", "desc": False}]}),
    )


class LotService(ReadOnlyService[LotRow, LotRead]):
    repo_cls = LotRepository
    read_schema = LotRead
    read_permission = READ
    record_type = "lot"  # a page of its own (platform/record_types.py)
    resource = Resource(path="/lots", tags=("lots",), get=False)

    async def enrich(self, rows: list[LotRead], entities) -> None:
        """Each row's parcel tracking link, when its carrier has a template."""
        carriers = await carriers_by_scac(self.uow.session, {r.carrier for r in rows})
        for r in rows:
            c = carriers.get((r.carrier or "").strip().upper())
            r.tracking_url = tracking_url(c.tracking_url if c else None, r.tracking)

    def _csv_export_columns(self) -> tuple[str, ...]:
        """All fields: the recall's columns (every order and customer the lots went to)."""
        return CSV_COLUMNS
