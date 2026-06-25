"""Pydantic schemas for expense claims (EXP / MIL)."""
import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Optional

from pydantic import BaseModel, ConfigDict


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
    line_items: Optional[list[LineItemCreate]] = None
    trip_items: Optional[list[TripItemCreate]] = None


# ── Action ────────────────────────────────────────────────────────────────────

class ExpenseActionRequest(BaseModel):
    action: str  # submit | approve | reject | return | pay
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
    total_amount: Decimal
    net_amount: Decimal
    currency: str
    status: str
    is_over_budget: bool
    submitted_at: Optional[datetime]
    approved_at: Optional[datetime]
    created_at: datetime


class ExpenseClaimListResponse(BaseModel):
    items: list[ExpenseClaimListItem]
    total: int
