"""Pydantic schemas for Visit resource."""
import uuid
from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from app.models.visit import (
    AccessArea,
    HealthDeclStatus,
    VisitPurpose,
    VisitStatus,
)
from app.schemas.visitor import VisitorResponse


# ── PPE request (host-specified at visit creation) ──────────────────────────-

# Standard sizes — `other` opens a free-text field for edge cases.
ClothingSize = Literal["XS", "S", "M", "L", "XL", "XXL", "other"]
ShoeSize = Literal["7", "8", "9", "10", "11", "12", "13", "14", "other"]
FootwearKind = Literal["shoes", "shoe_covers"]


class PpeItem(BaseModel):
    """Per-visitor PPE specification. One of these per person on the visit."""
    visitor_id: uuid.UUID
    clothing_size: ClothingSize
    clothing_size_other: str | None = Field(default=None, max_length=60)
    footwear: FootwearKind
    # Required only when `footwear == "shoes"`; UI hides the dropdown for
    # `shoe_covers`. We don't enforce server-side because shoe_covers visits
    # still need clothing size and that's the dominant validation point.
    shoe_size: ShoeSize | None = None
    shoe_size_other: str | None = Field(default=None, max_length=20)


class PpeRequest(BaseModel):
    """Host's PPE order at visit creation — one item per visitor on the
    appointment (primary + companions). Janitor receives a formatted
    summary after the visit is approved (or immediately on auto-confirm)."""
    items: list[PpeItem] = Field(default_factory=list)
    notes: str | None = Field(default=None, max_length=500)


class VisitCreate(BaseModel):
    """Host pre-registers a visit. Status starts at `confirmed` (no approval
    needed) or `pending_approval` (per access_area, see PRD §6.2.1).

    `visitor_id` is the primary (badge-holder #1) visitor; companions go in
    `additional_visitor_ids`. Single-visitor visits leave that list empty.
    """
    visitor_id: uuid.UUID
    additional_visitor_ids: list[uuid.UUID] = Field(default_factory=list)
    host_id: uuid.UUID
    visit_date: date
    planned_arrival: datetime
    planned_departure: datetime | None = None
    visit_purpose: VisitPurpose
    access_area: AccessArea
    accompanying_count: int | None = Field(default=None, ge=0, le=100)
    vehicle_plate: str | None = Field(default=None, max_length=20)
    notes: str | None = None
    # Optional — None when the Host didn't tick "PPE needed".
    ppe_requested: PpeRequest | None = None


class VisitUpdate(BaseModel):
    """Host edits an appointment before check-in."""
    visit_date: date | None = None
    planned_arrival: datetime | None = None
    planned_departure: datetime | None = None
    visit_purpose: VisitPurpose | None = None
    access_area: AccessArea | None = None
    accompanying_count: int | None = Field(default=None, ge=0, le=100)
    vehicle_plate: str | None = Field(default=None, max_length=20)
    notes: str | None = None
    # PPE issued is tracked at badge-print / check-in time.
    ppe_issued: dict | None = None


class VisitCheckOut(BaseModel):
    """Payload for POST /visits/{id}/check-out."""
    badge_returned: bool = True
    ppe_returned: bool = True
    notes: str | None = None


class VisitResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    visitor_id: uuid.UUID
    additional_visitor_ids: list[uuid.UUID] = []
    host_id: uuid.UUID
    created_by: uuid.UUID

    visit_date: date
    planned_arrival: datetime
    planned_departure: datetime | None
    actual_arrival: datetime | None
    actual_departure: datetime | None

    visit_purpose: VisitPurpose
    access_area: AccessArea
    status: VisitStatus

    health_decl_status: HealthDeclStatus | None
    safety_training_confirmed: bool
    badge_returned: bool
    ppe_issued: dict | None

    accompanying_count: int | None
    vehicle_plate: str | None
    notes: str | None

    ppe_requested: PpeRequest | None = None
    ppe_notified_at: datetime | None = None

    approval_status: str | None
    approval_step_idx: int | None
    submitted_at: datetime | None
    visit_title: str
    quality_approver_id: uuid.UUID | None
    host_notified_at: datetime | None

    created_at: datetime
    updated_at: datetime

    # Optional embedded visitor; populated when caller requests detail view.
    visitor: VisitorResponse | None = None
    additional_visitors: list[VisitorResponse] = []


class VisitListResponse(BaseModel):
    items: list[VisitResponse]
    total: int
