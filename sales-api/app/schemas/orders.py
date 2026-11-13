"""Order DTOs: the order list and detail (read), review resolution and
manual orders (FR-OB-19a)."""
from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, ValidationInfo, field_validator

from app.domain.address import CA_POSTAL, CA_PROVINCES, postal_mismatch

Code = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=50)]
Unit = Annotated[str, StringConstraints(strip_whitespace=True, to_upper=True, min_length=1, max_length=20)]
Qty = Annotated[Decimal, Field(gt=0, max_digits=14, decimal_places=4)]
Text = Annotated[str, StringConstraints(strip_whitespace=True)]
Reason = Annotated[str, StringConstraints(strip_whitespace=True, min_length=3, max_length=2000)]


class OrderRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    order_number: int
    partner_code: str
    partner_name: str | None = None  # the partner's name (lists show it; the code in its tooltip)
    source_type: str
    shipping_method: str | None = None  # Shopify's shipping line; a manual order's carrier (Purolator / LTL)
    source_order_id: str
    source_order_number: str | None
    source_reference: str | None
    order_date: date | None
    requested_delivery_date: date | None
    # the PO's last dates (retailers): cancel the PO after / do not deliver after (Open retailers)
    cancel_date: date | None = None
    do_not_deliver_after: date | None = None
    ship_to: dict[str, Any] | None
    currency: str
    total: Decimal | None
    total_amount: Decimal | None = None  # the source's total, else the lines at the PO price
    delivery_due_date: date | None = None  # the requested date; dc routing: do-not-deliver-after
    fulfillment_status: str
    invoice_status: str
    order_state: str = ""           # internal (D17): what eligibility reads; filled by the service
    # What people see (app/domain/order_status.py, filled by the service): the Status, On
    # Hold's reason, the label of both, the Short shipped mark and the Detail with its time.
    order_status: str = ""
    on_hold_reason: str | None = None
    order_status_label: str = ""
    short_shipped: bool = False
    status_detail: str | None = None
    status_detail_at: datetime | date | None = None
    status_detail_kind: str | None = None  # "countdown": a time that is coming, late once it passes
    hold_reason: str | None
    review_reason: str | None
    historical: bool
    shadow: bool = False
    first_received_at: datetime
    paid_at: datetime | None
    archived_in_shopify_at: datetime | None
    archive_suppressed: bool
    shopify_financial_status: str | None
    shopify_fulfillment_status: str | None = None
    source_cancelled_at: datetime | None = None
    # Shopify finance (sales17): the sales channel and tags ("groups"), the original
    # total, the successful refunds and the net (original − refunded)
    source_name: str | None = None
    tags: str | None = None
    original_total: Decimal | None = None
    total_refunded: Decimal | None = None
    net_amount: Decimal | None = None
    returned_ea: int | None = None  # what the customer returned of what shipped (none when nothing)
    backordered_ea: int | None = None  # what the applied 945 left unshipped (none before a 945)
    source_updated_at: datetime | None
    version: int
    created_at: datetime
    updated_at: datetime | None


class PackingRead(BaseModel):
    """A 945 row packed for an order line, with its shipment's date and
    tracking (the line's expandable detail; lots for a recall)."""
    shipment_id: int
    document_id: int
    sscc: str | None
    lot: str | None
    expiry: date | None
    qty_shipped_ea: int
    tracking: str | None
    ship_date: date | None
    # Applied, or kept only: a shadow-run 945, a second 945 (FR-OB-25)
    applied: bool
    shadow: bool


class LineRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    line_no: int
    item_code: str | None
    source: dict[str, Any]
    ordered_qty: Decimal
    order_unit: str | None
    factor_ea: Decimal | None
    qty_ea: int | None
    unit_price: Decimal | None
    invoice_unit_price: Decimal | None = None  # the line's own invoice price per order unit (None = the PO price)
    line_status: str
    removed: bool
    problem: str | None
    tax_amount: Decimal | None = None       # Shopify: the line's taxes (sales17)
    discount_amount: Decimal | None = None  # Shopify: the line's discounts
    tax_lines: list[dict[str, Any]] | None = None
    shipped_ea: int | None = None     # P5: from the 945
    delivered_qty: int | None = None  # P6: confirmed delivery (EA) = shipped − returned
    returned_ea: int = 0              # sales25: returned / refused by the customer, entered before the invoice goes
    # The detail's lifecycle (filled by the service): the invoice's lines for
    # this line, its state, and the 945 rows (pallet, lot, expiry) packed for it.
    invoiced_qty: int | None = None
    invoice_unit: str | None = None
    invoice_price: Decimal | None = None   # the invoice's (per invoice unit); before it, the draft's per order unit
    invoice_amount: Decimal | None = None
    state: str | None = None
    backordered_ea: int | None = None  # ordered − shipped EA once an applied 945 set shipped_ea
    # The shadow run's 945s (not applied, Zoho is live): what their rows linked to this line shipped,
    # and what that leaves backordered — derived, never written into shipped_ea.
    shipped_shadow_ea: int | None = None
    backordered_shadow_ea: int | None = None
    packing: list[PackingRead] = []


class OrderDocumentRead(BaseModel):
    """A source document, or an outbound 940 with its file and 997 status
    (kept apart: Sent is not Accepted, FR-OB-22)."""
    id: int
    kind: str
    direction: str
    remote_name: str | None
    created_at: datetime
    file_status: str | None = None
    ack_status: str | None = None
    st02: str | None = None
    sent_at: datetime | None = None
    error: str | None = None
    ack_detail: str | None = None


class TimelineEntry(BaseModel):
    at: datetime
    user_name: str
    entity: str
    action: str
    before: dict[str, Any] | None
    after: dict[str, Any] | None
    reason: str | None


class ShipmentRowRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    lx_index: int
    w12_index: int
    sscc: str | None
    item_code: str | None
    lot: str | None
    expiry: date | None
    qty_ordered_ea: int | None
    qty_shipped_ea: int
    tracking: str | None
    needs_review: bool
    # The line it was matched to, what the Item Master says, and the parcel's link (filled by the service).
    lx_number: str | None = None
    upc: str | None = None
    order_line_id: int | None = None
    line_no: int | None = None
    description: str | None = None
    case_factor: int | None = None    # EA in a case: the cases shipped, when known
    tracking_url: str | None = None
    matching_lines: list[int] = []    # a row to resolve: the order lines of its own item


class TrackingLinkRead(BaseModel):
    number: str
    url: str | None = None  # from the carrier's template (Configuration → Carriers); none for an unknown carrier


class ShipmentRead(BaseModel):
    """A 945 as applied (or kept: a second 945, a shadow one), its rows and
    checks, and the Shopify write-back (FR-OB-11 / 25 / 25b / 25d)."""
    model_config = ConfigDict(from_attributes=True)

    id: int
    document_id: int
    order_key_received: str | None
    ship_date: date | None
    ship_date_source: str | None
    carrier: str | None
    mode: str | None
    tracking: list[str] | None
    bol: str | None
    our_reference: str | None = None  # header N9*E1: the reference the 940 was sent with
    applied: bool
    shadow: bool
    checks: list[dict[str, Any]] | None
    shopify_status: str
    shopify_error: str | None
    rows: list[ShipmentRowRead] = []
    # The 945 as received (filled by the service): ship-to (N1 ST), the carrier's name, its tracking links,
    # its control numbers, when it came, and the part of a split shipment ("a", "b" from the key).
    ship_to: dict[str, Any] | None = None
    carrier_name: str | None = None
    tracking_links: list[TrackingLinkRead] = []
    set_control: str | None = None
    isa13: str | None = None
    gs06: str | None = None
    document_name: str | None = None
    received_at: datetime | None = None
    part: str | None = None
    ack_sent: str | None = None  # the 997 Sales sent back for it (none: Sales does not send 997s for 945s)


class PaymentRead(BaseModel):
    """A Shopify transaction (sale, authorization, capture, refund, void). No card data."""
    model_config = ConfigDict(from_attributes=True)

    id: int
    shopify_id: int
    parent_id: int | None
    refund_id: int | None
    kind: str
    gateway: str | None
    status: str | None
    amount: Decimal
    currency: str | None
    processed_at: datetime | None
    authorization: str | None
    payment_id: str | None
    error_code: str | None
    test: bool


class RefundLineRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    line_item_id: int | None
    order_line_id: int | None
    sku: str | None
    title: str | None
    quantity: int
    subtotal: Decimal
    total_tax: Decimal
    restock_type: str | None


class RefundAdjustmentRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    kind: str | None
    reason: str | None
    amount: Decimal
    tax_amount: Decimal


class RefundRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    shopify_id: int
    refunded_at: datetime | None
    processed_at: datetime | None
    note: str | None
    restock: bool | None
    lines_amount: Decimal
    shipping_amount: Decimal
    adjustments_amount: Decimal
    amount: Decimal
    lines: list[RefundLineRead] = []
    adjustments: list[RefundAdjustmentRead] = []


class OrderDetail(OrderRead):
    bill_to: dict[str, Any] | None = None
    contact: dict[str, Any] | None = None
    notes: str | None = None
    subtotal: Decimal | None = None
    tax: Decimal | None = None
    shipping: Decimal | None = None
    discount: Decimal | None = None
    department: str | None = None
    order_type: str | None = None
    edi_mailbox: str | None = None  # the buyer's TrueCommerce mailbox (850 EDIMailbox), sent back in the 810
    agreement_id: str | None = None  # the 850's "Agreement ID", sent back in the 810 (2026-11-13)
    lines: list[LineRead] = []
    has_invoice: bool = False  # the draft exists: invoice prices are edited on the invoice
    # The DC a retailer ship-to GLN routes to (Walmart; FR-OB-03), shown with the address.
    dc_number: str | None = None
    # Non-blocking warnings about the ship-to (a PO box), shown with it.
    ship_to_flags: list[str] = []
    documents: list[OrderDocumentRead] = []
    shipments: list[ShipmentRead] = []
    cancellable: bool = False                # Cancel order is offered
    send_940: str | None = None              # Send 940 offers "send" / "replace"; nothing once it shipped
    hold_exits: list[str] = []               # where Release hold can take it (none: not On Hold, or a shadow order)
    # Shopify finance (sales17): gateways, discount codes, the order's tax lines,
    # its transactions and refunds
    payment_gateways: list[str] | None = None
    discount_codes: list[dict[str, Any]] | None = None
    tax_lines: list[dict[str, Any]] | None = None
    payments: list[PaymentRead] = []
    refunds: list[RefundRead] = []
    timeline: list[TimelineEntry] = []


class ManualLineIn(BaseModel):
    item_code: Code
    quantity: Qty
    unit: Unit


Line = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=255)]
OptLine = Annotated[str, StringConstraints(strip_whitespace=True, max_length=255)] | None


class AddressIn(BaseModel):
    """A ship-to (or bill-to) entered by hand: what the 940 needs (name,
    address1, city, province, postal code) is required, so a manual order is
    refused here, field by field — never by its 940 later (FR-OB-29a). In
    Canada the province is a code and the postal code A1A 1A1 in that
    province; the phone is required (carriers need it). A PO box is not
    refused here (the form warns)."""
    model_config = ConfigDict(extra="forbid")

    country: Annotated[str, StringConstraints(strip_whitespace=True, to_upper=True, min_length=2, max_length=2)] = "CA"
    name: Line
    attention: OptLine = None
    phone: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=40)]
    address1: Line
    address2: OptLine = None
    city: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=100)]
    province: Annotated[str, StringConstraints(strip_whitespace=True, to_upper=True, min_length=1, max_length=10)]
    zip: Annotated[str, StringConstraints(strip_whitespace=True, to_upper=True, min_length=1, max_length=20)]

    @field_validator("province")
    @classmethod
    def _province(cls, v: str, info: ValidationInfo) -> str:
        if info.data.get("country") == "CA" and v not in CA_PROVINCES:
            raise ValueError("a Canadian province code (e.g. ON, QC)")
        return v

    @field_validator("zip")
    @classmethod
    def _zip(cls, v: str, info: ValidationInfo) -> str:
        if info.data.get("country") == "CA":
            if not CA_POSTAL.match(v):
                raise ValueError("a Canadian postal code: A1A 1A1")
            v = f"{v.replace(' ', '')[:3]} {v.replace(' ', '')[3:]}"
            if why := postal_mismatch(info.data.get("province"), v):
                raise ValueError(f"the {why}")
        return v


class ManualOrderIn(BaseModel):
    """A manual order (sample, phone order, staff purchase) for a MANUAL partner."""
    model_config = ConfigDict(extra="forbid")

    partner_code: Code
    customer_reference: Annotated[str, StringConstraints(strip_whitespace=True, max_length=50)] | None = None
    requested_delivery_date: date | None = None
    ship_to: AddressIn  # required: the 940 cannot go without it
    notes: Text | None = None
    lines: Annotated[list[ManualLineIn], Field(min_length=1, max_length=200)]
    # How it ships (user 2026-11-13): Purolator (parcel) or LTL; required to submit.
    carrier: Literal["Purolator", "Purolator Express", "LTL"] | None = None


class CancelOrderIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reason: Reason


class ReleaseHoldIn(BaseModel):
    """Where the order goes: one of the detail's `hold_exits`."""
    model_config = ConfigDict(extra="forbid")

    to: str
    reason: Reason


class ResolveRowIn(BaseModel):
    """A 945 row matching no line: link it to a line, or (no line) accept it, saying why."""
    model_config = ConfigDict(extra="forbid")

    order_line_id: int | None = None
    reason: Text | None = None


class OrderFrom945In(BaseModel):
    """Who the order is for, when the 945's reference does not say (or a person corrects it)."""
    model_config = ConfigDict(extra="forbid")

    partner_code: Code | None = None


class OrderFrom945Line(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    item_code: str | None
    received: str
    qty_ea: int
    shipped_ea: int
    problem: str | None


class OrderFrom945Plan(BaseModel):
    """A 945 with no order, and what making its order would do (FR-OB-25c)."""
    model_config = ConfigDict(from_attributes=True)

    shipment_id: int
    document_id: int
    order_key: str | None
    reference: str | None
    partner_code: str | None
    partner_name_received: str | None
    po: str | None
    ship_date: date | None
    ship_to: dict[str, Any] | None
    lines: list[OrderFrom945Line]
    order_id: int | None
    order_ref: str | None
    refused: str | None
    refused_message: str | None


class ReturnedLineIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: int
    returned_ea: Annotated[int, Field(ge=0)]


class AmendLineIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: int
    item_code: Annotated[str, Field(min_length=1, max_length=50)] | None = None
    ordered_qty: Annotated[Decimal, Field(gt=0, max_digits=14, decimal_places=4)] | None = None
    removed: bool = False


class AmendOrderIn(BaseModel):
    """An order sent wrong, corrected and sent again (user 2026-11-13): its lines as they should be, and why."""
    model_config = ConfigDict(extra="forbid")
    reason: Annotated[str, Field(min_length=3, max_length=500)]
    lines: Annotated[list[AmendLineIn], Field(min_length=1, max_length=500)]
    carrier: Literal["Purolator", "Purolator Express", "LTL"] | None = None  # a manual order's carrier, changed with the amend


class ReturnedIn(BaseModel):
    """What the customer returned (or refused) per order line, before the invoice goes; why, in a few words."""
    model_config = ConfigDict(extra="forbid")
    reason: Annotated[str, Field(min_length=3, max_length=500)]
    lines: Annotated[list[ReturnedLineIn], Field(min_length=1, max_length=500)]


class InvoicePriceIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: int
    # per order unit; null (or the PO price) = the PO price
    invoice_unit_price: Annotated[Decimal, Field(ge=0, max_digits=14, decimal_places=5)] | None


class InvoicePricesIn(BaseModel):
    """The order lines' invoice prices before the invoice exists; a price that
    differs from the PO needs `reason` (≥ 3 characters)."""
    model_config = ConfigDict(extra="forbid")
    reason: Annotated[str, StringConstraints(strip_whitespace=True, max_length=500)] | None = None
    lines: Annotated[list[InvoicePriceIn], Field(min_length=1, max_length=500)]


class LinePreview(BaseModel):
    item_code: str
    quantity: Decimal
    unit: str
    qty_ea: int | None
    problem: str | None
    # The line's gross weight (EA × the item's lb per EA): the entry screen suggests a carrier from the total.
    weight_lb: Decimal | None = None


class ReviewResult(BaseModel):
    order: OrderRead
    remaining: list[str]
