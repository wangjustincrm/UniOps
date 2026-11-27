"""Lots (traceability and recall; user 2026-11-03): the 945 rows with their
order and customer — searchable and groupable by lot and item, "Expiring
soon" and "By lot", and a recall: one lot or several (`lot:in:a|b`) gives
every order and customer it went to, as a CSV too."""
from __future__ import annotations


from app.models.lots import LotRow, ShippedRow
from app.platform.authorization import note_action, requires
from app.platform.errors import InvalidInput
from app.platform.query import FilterRule, QuerySpec, parse_sort
from app.platform.record_types import Ref
from app.platform.repository import Repository
from app.platform.resource import RecordFile, Resource
from app.platform.services import ReadOnlyService, csv_bytes
from app.platform.views import Preset
from app.domain.carriers import tracking_url
from app.schemas.lots import LotRead, ShippedRead
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


SHIPPED_CSV = ("ship_date", "customer", "order_ref", "order_number", "partner_name", "item_code", "lot", "expiry", "qty_shipped_ea",
               "delivery_confirmed", "ship_to_city", "ship_to_postal", "tracking", "sscc", "bol", "document_id")


class ShippedRepository(LotRepository):
    """Shipped: the applied 945 rows only (Lots keeps every 945 row, kept-only ones too)."""
    model = ShippedRow
    refs = LotRepository.refs
    search_fields = ("lot", "item_code", "order_ref", "customer", "tracking", "sscc", "ship_to_postal")
    list_fields = ("lot", "item_code", "expiry", "days_to_expiry", "qty_shipped_ea", "sscc", "tracking", "ship_date", "bol",
                   "order_ref", "order_number", "partner_code", "partner_name", "customer", "delivery_confirmed", "ship_to_city",
                   "ship_to_postal", "document_id", "shipment_id")
    # Its own columns (not the Lots list's, which a subclass would inherit as explicit entries).
    sortable = {}
    filterable = {"order_id": FilterRule(ShippedRow.order_id)}
    meta_hidden = ("applied", "shadow", "our_reference")
    labels = {**LotRepository.labels, "customer": "Customer", "delivery_confirmed": "Delivery confirmed"}
    suggestable = ("lot", "item_code", "order_ref")
    groupable = ("lot", "item_code", "expiry", "ship_date", "customer", "partner_name", "order_ref", "ship_to_city", "delivery_confirmed")
    presets = (Preset("all", "All"),
               Preset("by_customer", "By customer", {"group": [{"field": "customer", "desc": False}], "sort": [{"field": "ship_date", "desc": True}]}))


# The recall export's key (user 2026-11-27: its own permission). sales.lots.export needs an identity-api migration
# (the user's decision, asked 2026-11-27); until then the people who act on orders.
RECALL_EXPORT = "sales.order.operate"
# A recall's columns (user 2026-11-27): who got it, on which order, when, how much, delivered or not — a Shopify
# customer as an initial and the city only.
RECALL_COLUMNS = (("lot", "Lot"), ("item_code", "Item"), ("customer", "Customer"), ("order_ref", "Order"),
                  ("ship_date", "Ship date"), ("qty_shipped_ea", "EA"), ("delivery", "Delivery"))
RECALL_MAX_ROWS = 20_000


class ShippedService(LotService):
    """Shipped (user 2026-11-27): what went out to customers, from the applied 945s — an item's or a lot's (its scope),
    or all of them. A recall (one lot or several, in the scope or a filter) is a CSV of its own, with its own key."""
    repo_cls = ShippedRepository
    read_schema = ShippedRead
    resource = Resource(path="/shipped", tags=("shipped",), get=False,
                        files=(RecordFile("recall/customers.csv", "recall_csv", media_type="text/csv", inline=False, over="list",
                                          permission=RECALL_EXPORT),))

    @requires(READ)
    async def recall_csv(self, spec: QuerySpec) -> tuple[str, bytes]:
        """Every customer and order the lots went to — the lots named in the list's scope or filter (one at least);
        sales.read and the recall's own key."""
        await self._authorize(RECALL_EXPORT)
        lots = sorted({str(v) for f in [*spec.scope, *spec.filters] if f.field == "lot" and f.op in ("eq", "in")
                       for v in (f.value if isinstance(f.value, list) else [f.value])})
        if not lots:
            raise InvalidInput("recall.no_lot", "a recall names its lots: a lot in the scope or a filter", {"field": "lot"})
        note_action(type(self).__name__, "recall_csv")
        page = await self.repo.list(spec.model_copy(update={"page": 1, "page_size": RECALL_MAX_ROWS, "group": [],
                                                            "sort": [*spec.sort] or parse_sort("lot,ship_date,order_ref")}))
        if page.total > RECALL_MAX_ROWS:  # never a recall that silently leaves customers out
            raise InvalidInput("recall.too_many", f"more than {RECALL_MAX_ROWS} rows: name fewer lots", {"max": RECALL_MAX_ROWS})
        rows = page.items
        body = csv_bytes([label for _, label in RECALL_COLUMNS], (
            [("Confirmed" if r.delivery_confirmed else "Not confirmed") if c == "delivery" else getattr(r, c) for c, _ in RECALL_COLUMNS]
            for r in rows))
        name = f"recall-{lots[0]}.csv" if len(lots) == 1 else f"recall-{len(lots)}-lots.csv"
        return name, body

    def _csv_export_columns(self) -> tuple[str, ...]:
        return SHIPPED_CSV
