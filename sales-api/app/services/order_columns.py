"""The Orders page's own columns, as an export writes them (user 2026-11-24): what the cell shows, nothing more.
Ship to: the place only for a Shopify order (the customer's name is never written), the place's name before it for
another source (a DC) — as sales/src/features/orders/OrdersPage.tsx and platform-ui's PlaceCell."""
from datetime import date, datetime

from app.platform.clock import clock
from app.platform.resource import Computed
from app.schemas.orders import OrderRead


def status_detail(o: OrderRead) -> str:
    """"Sends 2026-11-04 15:00": the message and the moment it names (Toronto)."""
    at = o.status_detail_at
    if not o.status_detail or at is None:
        return o.status_detail or ""
    when = f"{clock().to_business(clock().normalize(at)):%Y-%m-%d %H:%M}" if isinstance(at, datetime) else at.isoformat() if isinstance(at, date) else str(at)
    return f"{o.status_detail} {when}"


def shopify(o: OrderRead) -> str:
    """Shopify's facts, as the badges: only what has a value."""
    def words(s: str) -> str:
        t = s.replace("_", " ")
        return t[:1].upper() + t[1:]
    facts = [words(o.shopify_financial_status) if o.shopify_financial_status else None,
             words(o.shopify_fulfillment_status) if o.shopify_fulfillment_status else None,
             "Archived" if o.archived_in_shopify_at else None, "Cancelled" if o.source_cancelled_at else None]
    return "; ".join(f for f in facts if f)


def ship_to_place(o: OrderRead) -> str:
    a = o.ship_to or {}

    def text(k: str) -> str:
        return a[k].strip() if isinstance(a.get(k), str) else ""
    where = ", ".join(x for x in (text("city"), text("province_code") or text("province")) if x)
    name = text("name") if o.source_type != "SHOPIFY" else ""
    return " — ".join(x for x in (name, where) if x)


ORDER_COLUMNS = {"status_detail": Computed("Detail", status_detail), "shopify": Computed("Shopify", shopify),
                 "ship_to_place": Computed("Ship to", ship_to_place)}
