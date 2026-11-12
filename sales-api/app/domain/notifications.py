"""The notification event types (FR-OB-56; design §8): what each one is called
on the rules page, whether it needs someone to act, and which ones the go-live
gate requires (PRD section 11, AC-OB-71).

Every event maps to the task type `sales_<event>`. An event that needs someone
to act (`action`) always raises its Task Inbox task (FR-OB-06) — its rule only
adds email and chooses who; a notify-only event (`notify`) follows its rule's
channel. With no rule: the task goes to the operator (X2) and no email is sent
(AC-OB-52: a fresh deployment sends no email until recipients are set)."""
from __future__ import annotations

from dataclasses import dataclass

ACTION, NOTIFY = "action", "notify"


@dataclass(frozen=True)
class EventType:
    key: str
    label: str
    kind: str = ACTION
    gate: bool = False  # required before writing is turned on for a channel (PRD section 11 step 3)


EVENT_TYPES: tuple[EventType, ...] = (
    # intake and connections (FR-OB-50 / 51 / 70 / 74)
    EventType("unreadable_file", "Unreadable inbound file"),
    EventType("sftp_fetch_failed", "SFTP fetch failed"),
    EventType("sftp_auth_failed", "SFTP authentication failed", gate=True),
    EventType("file_not_stored", "Inbound file not stored"),
    EventType("shopify_fetch_failed", "Shopify fetch failed"),
    EventType("shopify_auth_failed", "Shopify authentication failed", gate=True),
    EventType("order_file_bad", "Order file could not be read"),
    # orders (FR-OB-15 / 03d / 16)
    EventType("order_review", "Order needs review (unmapped item, unit, partner, DC, address)"),
    EventType("order_archived", "Paid order archived in Shopify before its 940", gate=True),
    EventType("changed_after_940", "Order changed after its 940"),
    # 940 / 997 (FR-OB-22a / 23 / 24)
    EventType("940_failed", "940 failed", gate=True),
    EventType("940_rejected", "940 rejected by the warehouse (997)", gate=True),
    EventType("940_timeout", "No 997 for a 940 (ACK timeout)", gate=True),
    EventType("997_unmatched", "997 matches no file"),
    EventType("send_refused", "A send was refused by the write guard"),
    # 945 and Shopify write-back (FR-OB-25 / 25a / 25c / 25d / 11)
    EventType("945_validation", "945 validation check", kind=NOTIFY),
    EventType("short_shipment", "Short shipment"),
    EventType("second_945", "A second, different 945"),
    EventType("945_no_order", "945 with no order"),
    EventType("945_line", "945 line matches no order line"),
    EventType("shopify_write", "Shopify write-back failed"),
    # invoicing, cancellation and returns (FR-OB-18 / 19 / 30)
    EventType("invoice_hold", "Invoice on hold"),
    EventType("invoice_failed", "Invoice not sent"),
    EventType("order_return", "Return or cancellation after shipment"),
    EventType("cancel_after_940", "Cancelled at source after the 940", gate=True),
    # transfers (FR-OB-81…84)
    EventType("transfer_failed", "Transfer 943 failed"),
    EventType("943_rejected", "943 rejected by the warehouse (997)"),
    EventType("943_timeout", "No 997 for a 943 (ACK timeout)"),
    EventType("transfer_discrepancy", "Transfer discrepancy"),
    EventType("partial_receipt", "Transfer partially received", kind=NOTIFY),
    EventType("second_944", "A second, different 944"),
    EventType("944_no_transfer", "944 with no transfer"),
    # configuration (user 2026-11-03)
    EventType("partner_no_abbrev", "Partner without an abbreviation"),
)
BY_KEY = {e.key: e for e in EVENT_TYPES}
EVENT_KEYS = tuple(BY_KEY)
GATE_EVENTS = tuple(e.key for e in EVENT_TYPES if e.gate)
