"""Lots (traceability and recall; user 2026-11-03): the 945 rows with their
order and customer — searchable and groupable by lot and item, "Expiring
soon" and "By lot", and a recall: one lot or several (`lot:in:a|b`) gives
every order and customer it went to, as a CSV too."""
from __future__ import annotations

import csv
import io

from app.models.lots import LotRow
from app.platform.authorization import requires
from app.platform.query import FilterRule, QuerySpec
from app.platform.repository import Repository
from app.platform.services import EXPORT_PAGE, ReadOnlyService, _csv_cell
from app.platform.views import Preset
from app.schemas.lots import LotRead

READ = "sales.read"
EXPIRING_DAYS = 30  # "Expiring soon": expiring within this many days (user 2026-11-03)

CSV_COLUMNS = ("lot", "item_code", "expiry", "qty_shipped_ea", "ship_date", "order_ref", "partner_code", "partner_name",
               "ship_to_city", "ship_to_postal", "tracking", "sscc", "bol", "our_reference", "document_id", "applied", "shadow")


class LotRepository(Repository[LotRow]):
    model = LotRow
    search_fields = ("lot", "item_code", "order_ref", "partner_name", "tracking", "sscc", "ship_to_postal")
    list_fields = ("lot", "item_code", "expiry", "days_to_expiry", "qty_shipped_ea", "sscc", "tracking", "ship_date",
                   "bol", "our_reference", "applied", "shadow", "order_ref", "partner_code", "partner_name",
                   "ship_to_city", "ship_to_postal", "document_id", "shipment_id")
    filterable = {"order_id": FilterRule(LotRow.order_id)}
    labels = {"item_code": "Item", "qty_shipped_ea": "Qty EA", "sscc": "Pallet (SSCC)", "days_to_expiry": "Days to expiry",
              "order_ref": "Order ref", "partner_name": "Partner", "partner_code": "Partner code",
              "ship_to_city": "Ship-to city", "ship_to_postal": "Ship-to postal code", "document_id": "945 document",
              "our_reference": "940 reference (N9*E1)", "bol": "BOL", "applied": "Applied", "shadow": "Shadow run"}
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

    @requires(READ)
    async def export_csv(self, spec: QuerySpec) -> bytes:
        """The filtered rows (a recall: every order and customer the lots went
        to), all of them, in CSV."""
        out = io.StringIO()
        w = csv.writer(out, lineterminator="\n")
        w.writerow(CSV_COLUMNS)
        page_no = 1
        while True:
            page = await self.repo.list(spec.model_copy(update={"page": page_no, "page_size": EXPORT_PAGE}))
            for e in page.items:
                w.writerow([_csv_cell(getattr(e, c)) for c in CSV_COLUMNS])
            if page_no * EXPORT_PAGE >= page.total:
                break
            page_no += 1
        return out.getvalue().encode("utf-8")
