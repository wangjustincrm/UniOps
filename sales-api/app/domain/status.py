"""The four status lines of PRD 9.1 as state machines (design §4.5 / §13.6).

Every status change in Order Bridge goes through one of these machines. The
tables are transcribed from PRD 9.1; tests/domain/test_status_vs_prd.py parses
the PRD tables and compares them pair by pair.

Where 9.1 describes a state as returning to "the status it held before", the
state is a hold state (see app.platform.state): `*_HOLD_ENTRIES` lists which
states may enter it. Those entries, and `FULFILLMENT_ADDITIONS`, are not in the
9.1 "Next" columns; they come from the requirements named next to each one
(product owner, 2026-10-27; noted under PRD 9.1).
"""
from __future__ import annotations

from enum import StrEnum

from app.platform.state import StateMachine


class Fulfillment(StrEnum):
    DRAFT = "Draft"
    REVIEW = "Review"
    PAYMENT_HOLD = "Payment Hold"
    PENDING = "Pending"
    ARCHIVE_SUPPRESSED = "Archive Suppressed"
    GENERATED_940 = "940 Generated"
    SENT_940 = "940 Sent"
    ACCEPTED_940 = "940 Accepted"
    REJECTED_940 = "940 Rejected"
    TIMEOUT_940 = "940 Timeout"
    FAILED_940 = "940 Failed"
    SHIPPED = "Shipped"
    ON_HOLD = "On Hold"
    CANCELLED = "Cancelled"


class Invoice(StrEnum):
    NOT_REQUIRED = "Not Required"
    PENDING = "Pending"
    AWAITING_REQUESTED_DATE = "Awaiting Requested Date"
    WAITING_FOR_DELIVERY_CONFIRMATION = "Waiting for Delivery Confirmation"
    READY = "Ready"
    QUEUED = "Queued"
    SENT = "Sent"
    FAILED = "Failed"
    BLOCKED = "Blocked"


class Transfer(StrEnum):
    DRAFT = "Draft"
    READY_TO_SEND = "Ready To Send"
    GENERATED_943 = "943 Generated"
    WAITING_FOR_997 = "Waiting for 997"
    WAITING_FOR_944 = "Waiting for 944"
    RECEIVED = "Received"
    PARTIALLY_RECEIVED = "Partially Received"
    DISCREPANCY = "Discrepancy"
    FAILED = "Failed"
    CANCELLED = "Cancelled"
    ON_HOLD = "On Hold"


F, Inv, T = Fulfillment, Invoice, Transfer

FULFILLMENT_TABLE = {
    F.DRAFT: {F.PENDING, F.CANCELLED},
    F.REVIEW: {F.PENDING, F.CANCELLED},
    F.PAYMENT_HOLD: {F.PENDING, F.CANCELLED},
    F.PENDING: {F.GENERATED_940, F.ARCHIVE_SUPPRESSED, F.CANCELLED},
    F.ARCHIVE_SUPPRESSED: {F.PENDING, F.CANCELLED},
    F.GENERATED_940: {F.SENT_940, F.FAILED_940},
    F.SENT_940: {F.ACCEPTED_940, F.REJECTED_940, F.TIMEOUT_940, F.SHIPPED},
    F.ACCEPTED_940: {F.SHIPPED},
    F.REJECTED_940: {F.GENERATED_940, F.CANCELLED},
    F.TIMEOUT_940: {F.GENERATED_940, F.CANCELLED},
    F.FAILED_940: {F.GENERATED_940, F.CANCELLED},
    F.SHIPPED: {F.ON_HOLD},
    F.ON_HOLD: {F.CANCELLED, F.SHIPPED},  # + the status it held before
    F.CANCELLED: set(),
}
FULFILLMENT_ADDITIONS = {
    # FR-OB-18: before the 940 is sent a source cancel is silent and the order
    # stops qualifying — 940 Generated is not sent yet. The generated outbound
    # file is cancelled with it.
    F.GENERATED_940: {F.CANCELLED},
    # A timeout means "a person should look", not "no answer can come": a late
    # 997 or the 945 still applies (the goods may have shipped). The open
    # timeout task is completed when the late answer arrives (P4, TaskService
    # resolve_problem).
    F.TIMEOUT_940: {F.ACCEPTED_940, F.REJECTED_940, F.SHIPPED},
    # FR-OB-15 + FR-OB-16 (product owner, 2026-10-29): a source update before
    # the 940 may make the order invalid (unmatched item, no conversion,
    # fractional EA, zero quantity) — it goes to Review, also from Payment Hold.
    # A resolved review of an unpaid PREPAID order goes to Payment Hold
    # (FR-OB-12), not to Pending, which would claim it waits only for the 940.
    F.PENDING: {F.REVIEW},
    F.PAYMENT_HOLD: {F.REVIEW},
    F.REVIEW: {F.PAYMENT_HOLD},
    # FR-OB-03c (P4): a confirmed replacement 940 after a source change starts
    # over from 940 Generated; the previous file becomes Replaced.
    F.SENT_940: {F.GENERATED_940},
    F.ACCEPTED_940: {F.GENERATED_940},
}
# User 2026-11-09: only a 945 proves shipment, and it always does: goods that left are Shipped also when
# no 940 of ours went (archived in Shopify, sent by hand, held for review or payment). Its checks still warn.
SHIPPED_WITHOUT_OUR_940 = (F.PENDING, F.REVIEW, F.PAYMENT_HOLD, F.ARCHIVE_SUPPRESSED, F.GENERATED_940, F.REJECTED_940,
                           F.FAILED_940)
for _s in SHIPPED_WITHOUT_OUR_940:
    FULFILLMENT_ADDITIONS[_s] = FULFILLMENT_ADDITIONS.get(_s, set()) | {F.SHIPPED}
FULFILLMENT_HOLD_ENTRIES = {
    # 9.1 lists Shipped → On Hold (second 945, FR-OB-25). FR-OB-18: a source
    # cancellation after the 940 is sent puts the order on hold.
    F.ON_HOLD: {F.SHIPPED, F.SENT_940, F.ACCEPTED_940, F.TIMEOUT_940},
}

INVOICE_TABLE = {
    Inv.NOT_REQUIRED: set(),
    Inv.PENDING: {Inv.AWAITING_REQUESTED_DATE, Inv.WAITING_FOR_DELIVERY_CONFIRMATION},
    Inv.AWAITING_REQUESTED_DATE: {Inv.WAITING_FOR_DELIVERY_CONFIRMATION},
    Inv.WAITING_FOR_DELIVERY_CONFIRMATION: {Inv.READY},
    Inv.READY: {Inv.QUEUED, Inv.SENT},
    Inv.QUEUED: {Inv.SENT, Inv.FAILED},
    Inv.SENT: {Inv.SENT},
    Inv.FAILED: {Inv.QUEUED},
    Inv.BLOCKED: set(),  # the status it held before, once cleared
}
INVOICE_HOLD_ENTRIES = {
    # Lot exception (FR-OB-25) or cancellation / return after shipment
    # (FR-OB-19) — from any status that still has an invoice to stop.
    Inv.BLOCKED: {
        Inv.PENDING, Inv.AWAITING_REQUESTED_DATE, Inv.WAITING_FOR_DELIVERY_CONFIRMATION,
        Inv.READY, Inv.QUEUED, Inv.FAILED, Inv.SENT,
    },
}

TRANSFER_TABLE = {
    T.DRAFT: {T.READY_TO_SEND, T.CANCELLED},
    T.READY_TO_SEND: {T.GENERATED_943, T.CANCELLED},
    T.GENERATED_943: {T.WAITING_FOR_997, T.CANCELLED},  # cancelled only before the 943 is sent
    T.WAITING_FOR_997: {T.WAITING_FOR_944, T.FAILED},  # Failed on 997 rejection
    T.WAITING_FOR_944: {T.RECEIVED, T.PARTIALLY_RECEIVED, T.DISCREPANCY},
    T.RECEIVED: set(),
    T.PARTIALLY_RECEIVED: set(),
    T.DISCREPANCY: set(),
    T.FAILED: set(),
    T.CANCELLED: set(),
    T.ON_HOLD: set(),  # the status it held before (D17)
}
TRANSFER_ADDITIONS = {
    # FR-OB-81: a failed 943 is resent by the user (FR-OB-24), or the transfer
    # is cancelled (FR-OB-80: before the 943 is sent, or after it was
    # rejected). An ACK timeout leaves the transfer in Waiting for 997; only
    # its ACK line goes to Timeout.
    T.FAILED: {T.GENERATED_943, T.CANCELLED},
    # FR-OB-81 / FR-OB-24 (P7, 2026-11-02): a 943 that fails its checks, or whose
    # put fails or is refused, is Failed — then resent by the user or cancelled.
    T.GENERATED_943: {T.FAILED},
    # User 2026-11-13 (transfers 00104 / 00106): a sent 943 that is wrong is amended and sent again
    # (a new 943 replaces it) until a 944 has answered it …
    T.WAITING_FOR_997: {T.GENERATED_943},
    T.WAITING_FOR_944: {T.GENERATED_943},
    # … and a receipt that differs is settled by a person, with why: corrected to what arrived, or
    # the difference accepted.
    T.DISCREPANCY: {T.RECEIVED},
    T.PARTIALLY_RECEIVED: {T.RECEIVED},
}
TRANSFER_HOLD_ENTRIES = {
    # On Hold when a second, different 944 arrives (FR-OB-80 to FR-OB-84).
    T.ON_HOLD: {T.RECEIVED, T.PARTIALLY_RECEIVED, T.DISCREPANCY},
}

FULFILLMENT = StateMachine(
    {s: FULFILLMENT_TABLE[s] | FULFILLMENT_ADDITIONS.get(s, set()) for s in FULFILLMENT_TABLE}, "fulfillment", field="fulfillment_status",
    initial={F.DRAFT, F.REVIEW, F.PAYMENT_HOLD, F.PENDING}, holds=FULFILLMENT_HOLD_ENTRIES,
)
INVOICE = StateMachine(
    INVOICE_TABLE, "invoice", field="invoice_status",
    initial={Inv.NOT_REQUIRED, Inv.PENDING}, holds=INVOICE_HOLD_ENTRIES,
)
TRANSFER = StateMachine(
    {s: TRANSFER_TABLE[s] | TRANSFER_ADDITIONS.get(s, set()) for s in TRANSFER_TABLE}, "transfer", field="status", initial={T.DRAFT}, holds=TRANSFER_HOLD_ENTRIES,
)


# ── File and ACK status ──────────────────────────────────────────────────────
# 9.1 gives the values and their order in prose; the reparse / resend / ignore
# actions are design §6 (documents).


class InboundFile(StrEnum):
    PENDING = "Pending"
    PROCESSING = "Processing"
    PROCESSED = "Processed"
    FAILED = "Failed"
    SKIPPED = "Skipped"
    DUPLICATE = "Duplicate"


class OutboundFile(StrEnum):
    READY_TO_SEND = "Ready To Send"
    SENDING = "Sending"
    SENT = "Sent"
    FAILED = "Failed"
    # Refused because its switch is off (dev / test; product owner,
    # 2026-10-28). A person reviews it; switching on sends nothing by itself.
    SUPPRESSED = "Suppressed"
    REPLACED = "Replaced"
    CANCELLED = "Cancelled"


class Ack(StrEnum):
    NOT_REQUIRED = "Not Required"
    WAITING = "Waiting"
    ACCEPTED = "Accepted"
    ACCEPTED_BY_945 = "Accepted by 945"
    REJECTED = "Rejected"
    TIMEOUT = "Timeout"


INBOUND_FILE_TABLE = {
    # PRD 9.1: "Pending → Processing → Processed / Failed / Skipped, plus Duplicate".
    InboundFile.PENDING: {InboundFile.PROCESSING, InboundFile.DUPLICATE},
    InboundFile.PROCESSING: {InboundFile.PROCESSED, InboundFile.FAILED, InboundFile.SKIPPED},
    InboundFile.PROCESSED: set(),
    InboundFile.FAILED: set(),
    InboundFile.SKIPPED: set(),
    InboundFile.DUPLICATE: set(),
}
INBOUND_FILE_ADDITIONS = {
    # Design §6 (documents): reparse / ignore a failed file.
    # FR-OB-62: a person reads a failed file again (it waits for the next pass) or marks it ignored.
    InboundFile.FAILED: {InboundFile.PROCESSING, InboundFile.SKIPPED, InboundFile.PENDING},
}
INBOUND_FILE = StateMachine(
    {s: INBOUND_FILE_TABLE[s] | INBOUND_FILE_ADDITIONS.get(s, set()) for s in INBOUND_FILE_TABLE},
    "inbound_file", field="processing_status", initial={InboundFile.PENDING},
)

OUTBOUND_FILE_TABLE = {
    # PRD 9.1: "Ready To Send → Sending → Sent / Failed / Suppressed, plus
    # Replaced … and Cancelled"; Suppressed is resent or cancelled by a person.
    OutboundFile.READY_TO_SEND: {OutboundFile.SENDING, OutboundFile.CANCELLED},
    OutboundFile.SENDING: {OutboundFile.SENT, OutboundFile.FAILED, OutboundFile.SUPPRESSED},
    OutboundFile.SENT: {OutboundFile.REPLACED},  # superseded by a confirmed replacement (FR-OB-03c)
    OutboundFile.FAILED: {OutboundFile.CANCELLED},
    OutboundFile.SUPPRESSED: {OutboundFile.SENDING, OutboundFile.CANCELLED},
    OutboundFile.REPLACED: set(),
    OutboundFile.CANCELLED: set(),
}
OUTBOUND_FILE = StateMachine(OUTBOUND_FILE_TABLE, "outbound_file", field="file_status",
                             initial={OutboundFile.READY_TO_SEND})

ACK_TABLE = {
    Ack.NOT_REQUIRED: set(),
    Ack.WAITING: {Ack.ACCEPTED, Ack.ACCEPTED_BY_945, Ack.REJECTED, Ack.TIMEOUT},
    Ack.ACCEPTED: set(),
    Ack.ACCEPTED_BY_945: set(),
    Ack.REJECTED: set(),
    Ack.TIMEOUT: set(),
}
ACK_ADDITIONS = {
    # A late 997 or 945 after the timeout still decides the acknowledgement.
    Ack.TIMEOUT: {Ack.ACCEPTED, Ack.REJECTED, Ack.ACCEPTED_BY_945},
}
ACK = StateMachine(
    {s: ACK_TABLE[s] | ACK_ADDITIONS.get(s, set()) for s in ACK_TABLE},
    "ack", field="ack_status", initial={Ack.NOT_REQUIRED, Ack.WAITING},
)

MACHINES = (FULFILLMENT, INVOICE, TRANSFER, INBOUND_FILE, OUTBOUND_FILE, ACK)
