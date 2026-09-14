"""Pydantic schemas for expense claims (EXP / MIL)."""
import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, model_validator

# Rounding slack when checking that a line's three amounts agree. The clients
# emit toFixed(2) strings so they reconcile exactly; OCR-filled lines derive
# net as total - tax and can land a cent out.
_TOTALS_TOLERANCE = Decimal("0.01")


# ── Line item (EXP) ───────────────────────────────────────────────────────────

class LineItemCreate(BaseModel):
    line_number: int
    expense_date: date
    description: str
    budget_account_id: uuid.UUID
    budget_account_code: str
    budget_account_name: str
    cost_center_id: Optional[uuid.UUID] = None
    cost_center_name: Optional[str] = None
    total_amount: Decimal
    tax_amount: Decimal
    net_amount: Decimal
    tax_code: Optional[str] = None  # mdm tax_codes code (Phase 0-B2, ITC groundwork)

    @model_validator(mode="after")
    def _amounts_must_reconcile(self):
        """total = net + tax, server-side.

        The server took all three numbers on trust and summed each column
        independently (`_compute_totals_exp`), so a client that sent
        net=10 / tax=1 / total=1000 produced a claim that DISPLAYS as ten
        dollars of expense and PAYS a thousand — payment reads total_amount.
        The MIL path never had this hole: `_compute_totals_mil` recomputes each
        trip from distance x rate and explains in its docstring why it will not
        trust a client figure.

        Deliberately NOT a non-negative check. Negative lines are allowed on
        purpose across this codebase — 0/negative unit prices on PR/PO, credit
        and discount lines on GR/invoices — and refusing them here would break
        a refund line on an otherwise ordinary claim.
        """
        expected = self.net_amount + self.tax_amount
        if abs(self.total_amount - expected) > _TOTALS_TOLERANCE:
            raise ValueError(
                f"line {self.line_number}: total_amount {self.total_amount} does not equal "
                f"net_amount {self.net_amount} + tax_amount {self.tax_amount} ({expected})"
            )
        return self


class LineItemResponse(LineItemCreate):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID


# ── Trip item (MIL) ───────────────────────────────────────────────────────────

class TripItemCreate(BaseModel):
    trip_number: int
    trip_date: date
    from_location: str
    to_location: str
    purpose: str
    is_round_trip: bool = False
    distance_km: Decimal
    rate_per_km: Decimal
    amount: Decimal
    budget_account_id: Optional[uuid.UUID] = None
    budget_account_code: Optional[str] = None
    budget_account_name: Optional[str] = None


class TripItemResponse(TripItemCreate):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID


# ── Traveler (TRA) ────────────────────────────────────────────────────────────

class TravelerCreate(BaseModel):
    user_id: uuid.UUID
    user_name: str
    seq: int = 0


class TravelerResponse(TravelerCreate):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID


# ── Attachment ────────────────────────────────────────────────────────────────

class AttachmentResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    file_id: str
    file_name: str
    file_size_bytes: int
    mime_type: Optional[str]
    uploaded_at: datetime


# ── Approval event ────────────────────────────────────────────────────────────

class ApprovalEventResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    actor_id: uuid.UUID
    actor_name: str
    action: str
    comment: Optional[str]
    from_status: str
    to_status: str
    created_at: datetime


# ── Claim create / update ─────────────────────────────────────────────────────

class ExpenseClaimCreate(BaseModel):
    claim_type: str  # EXP | MIL | TRV | CFM*
    submission_date: date
    currency: str = "CAD"
    project_id: Optional[uuid.UUID] = None
    notes: Optional[str] = None
    purpose: Optional[str] = None
    # MIL only
    vehicle_description: Optional[str] = None
    vehicle_owned_by: Optional[str] = None  # self | company
    # TRV only
    travel_from_date: Optional[date] = None
    travel_to_date: Optional[date] = None
    travel_destination: Optional[str] = None
    # TRA only
    transport_modes: list[str] = []
    leave_from_date: Optional[date] = None
    leave_to_date: Optional[date] = None
    travelers: list[TravelerCreate] = []
    # TRV only — reference to an approved TRA
    travel_application_id: Optional[uuid.UUID] = None
    # Line items (EXP + TRV use line_items; MIL uses trip_items)
    line_items: list[LineItemCreate] = []
    trip_items: list[TripItemCreate] = []


class ExpenseClaimUpdate(BaseModel):
    submission_date: Optional[date] = None
    currency: Optional[str] = None
    project_id: Optional[uuid.UUID] = None
    notes: Optional[str] = None
    purpose: Optional[str] = None
    vehicle_description: Optional[str] = None
    vehicle_owned_by: Optional[str] = None
    travel_from_date: Optional[date] = None
    travel_to_date: Optional[date] = None
    travel_destination: Optional[str] = None
    transport_modes: Optional[list[str]] = None
    leave_from_date: Optional[date] = None
    leave_to_date: Optional[date] = None
    travelers: Optional[list[TravelerCreate]] = None
    travel_application_id: Optional[uuid.UUID] = None
    line_items: Optional[list[LineItemCreate]] = None
    trip_items: Optional[list[TripItemCreate]] = None


# ── Action ────────────────────────────────────────────────────────────────────

class ExpenseActionRequest(BaseModel):
    # Literal, like PaActionRequest. A free-form string let any value through to
    # approval-api, which answered 409 for the unknown ones — a confusing way to
    # spell "that is not an action". `pay` is accepted here and rejected in the
    # handler with a pointer to POST /expenses/{id}/pay, which is a clearer
    # answer than a validation error for a verb that does exist.
    action: Literal["submit", "approve", "reject", "return", "recall", "cancel", "pay"]
    comment: Optional[str] = None


class PaymentRecordRequest(BaseModel):
    bank_account_id: uuid.UUID   # funding bank/card chosen in the modal


# ── Response ──────────────────────────────────────────────────────────────────

class ExpenseClaimResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    claim_number: str
    claim_type: str

    employee_id: uuid.UUID
    employee_name: str
    department_id: Optional[uuid.UUID]
    department_name: str

    submission_date: date
    currency: str
    project_id: Optional[uuid.UUID]
    notes: Optional[str]
    purpose: Optional[str]

    vehicle_description: Optional[str]
    vehicle_owned_by: Optional[str]
    total_km: Optional[Decimal]
    travel_from_date: Optional[date]
    travel_to_date: Optional[date]
    travel_destination: Optional[str]
    transport_modes: list[str] = []
    leave_from_date: Optional[date] = None
    leave_to_date: Optional[date] = None
    travel_application_id: Optional[uuid.UUID] = None

    total_amount: Decimal
    tax_amount: Decimal
    net_amount: Decimal

    status: str
    approval_step_idx: int
    is_over_budget: bool

    submitted_at: Optional[datetime]
    approved_at: Optional[datetime]
    paid_at: Optional[datetime]

    created_by: uuid.UUID
    created_at: datetime
    updated_at: datetime

    line_items: list[LineItemResponse] = []
    trip_items: list[TripItemResponse] = []
    travelers: list[TravelerResponse] = []
    attachments: list[AttachmentResponse] = []
    approval_events: list[ApprovalEventResponse] = []


class ExpenseClaimListItem(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    claim_number: str
    claim_type: str
    employee_name: str
    department_name: str
    submission_date: date
    travel_destination: Optional[str] = None
    total_amount: Decimal
    net_amount: Decimal
    currency: str
    status: str
    is_over_budget: bool
    submitted_at: Optional[datetime]
    approved_at: Optional[datetime]
    created_at: datetime
    # Stamped per row by list_expenses — the list page renders its delete
    # affordance from this and holds no permission logic of its own.
    can_delete: bool = False


class ExpenseClaimListResponse(BaseModel):
    items: list[ExpenseClaimListItem]
    total: int
