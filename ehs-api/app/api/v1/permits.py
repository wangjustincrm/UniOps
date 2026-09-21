"""Work permits.

Three permissions, because three different people are involved and conflating
them would be the defect. Anyone may request one — the person who needs the
work done is usually not the person authorised to allow it. Issuing is a
distinct authority. Closing is done by whoever hands the area back, which in
practice is the issuer or their relief, so it rides on the issue key.

Every response reports the *computed* status, not the stored one. A permit that
ran out at 18:00 reads as expired at 18:01 whether or not the sweep has been
round, because "the board still says ACTIVE" is how a crew re-enters a space on
yesterday's authorisation.
"""
from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal

from fastapi import APIRouter, HTTPException, Query, status
from pydantic import BaseModel, ConfigDict, Field

from app.api.v1.incidents import _user_id
from app.core.deps import SessionDep
from uniops_authz import has_permission

from app.core.permissions import CanIssuePermit, CanReadPermit, CanRequestPermit
from app.crud import permit as crud
from app.services import permit as rules

router = APIRouter()


class PermitTypeOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    code: str
    name: str
    description: str | None
    precautions: list
    requires_gas_test: bool
    required_signatures: list
    max_duration_hours: int


class PermitIn(BaseModel):
    type_id: uuid.UUID
    work_description: str = Field(min_length=1)
    location_id: uuid.UUID | None = None
    equipment_id: uuid.UUID | None = None
    jsa_id: uuid.UUID | None = None
    contractor_company: str | None = Field(default=None, max_length=200)
    valid_from: datetime | None = None
    valid_to: datetime | None = None


class PermitPatch(BaseModel):
    work_description: str | None = None
    location_id: uuid.UUID | None = None
    equipment_id: uuid.UUID | None = None
    jsa_id: uuid.UUID | None = None
    contractor_company: str | None = Field(default=None, max_length=200)
    valid_from: datetime | None = None
    valid_to: datetime | None = None
    # {precaution_key: true}
    precautions_confirmed: dict | None = None


class GasReadingIn(BaseModel):
    # Nullable and assessed, not defaulted. "We did not measure it" and "it was
    # fine" must not produce the same verdict on a confined space.
    oxygen_pct: Decimal | None = Field(default=None, ge=0, le=100)
    lel_pct: Decimal | None = Field(default=None, ge=0, le=100)
    h2s_ppm: Decimal | None = Field(default=None, ge=0)
    co_ppm: Decimal | None = Field(default=None, ge=0)
    other_note: str | None = Field(default=None, max_length=200)
    instrument: str | None = Field(default=None, max_length=100)
    taken_at: datetime | None = None


class GasReadingOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    taken_at: datetime
    taken_by_name: str | None
    oxygen_pct: Decimal | None
    lel_pct: Decimal | None
    h2s_ppm: Decimal | None
    co_ppm: Decimal | None
    other_note: str | None
    instrument: str | None
    is_acceptable: bool
    failed: list
    # Computed on read: a passing test goes stale, and a two-hour-old reading
    # does not describe the space now.
    is_fresh: bool = True


class SignatureIn(BaseModel):
    capacity: str = Field(pattern="^(issuer|receiver|attendant|gas_tester|fire_watch)$")
    user_id: uuid.UUID | None = None
    person_name: str = Field(min_length=1, max_length=255)
    person_role: str | None = Field(default=None, max_length=150)
    external_company: str | None = Field(default=None, max_length=200)
    signature: str | None = None


class SignatureOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    phase: str
    capacity: str
    person_name: str
    person_role: str | None
    external_company: str | None
    signed_at: datetime


class CloseIn(BaseModel):
    closeout_note: str = Field(min_length=1)
    signature: SignatureIn


class SuspendIn(BaseModel):
    reason: str = Field(min_length=1, max_length=300)


class PermitOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    permit_no: str
    type_id: uuid.UUID | None
    type_label: str
    work_description: str
    location_id: uuid.UUID | None
    location_path: str | None
    department_name: str | None
    equipment_id: uuid.UUID | None
    jsa_id: uuid.UUID | None
    contractor_company: str | None
    requested_by_name: str | None
    valid_from: datetime | None
    valid_to: datetime | None
    precautions_confirmed: dict
    issued_at: datetime | None
    issued_by_name: str | None
    suspended_reason: str | None
    closed_at: datetime | None
    closed_by_name: str | None
    closeout_note: str | None
    qr_token: str | None

    # What it actually is now, versus what the row last said. The stored value
    # is deliberately not exposed — nobody should be reading it.
    status: str
    blockers: list[str] = []


async def _out(db, permit, *, with_blockers: bool = False) -> PermitOut:
    item = PermitOut.model_validate(permit)
    item.status = crud.effective(permit)
    if with_blockers and item.status in ("draft", "suspended"):
        item.blockers = await crud.blockers(db, permit)
    return item


@router.get("/permit-types", response_model=list[PermitTypeOut])
async def list_types(db: SessionDep, user: CanRequestPermit):  # noqa: ARG001
    return await crud.list_types(db)


async def _may_browse(db, payload: dict) -> bool:
    """Whether this caller may see permits other than their own."""
    return await has_permission(db, _user_id(payload), payload.get("role", ""),
                                "ehs.permit.read")


@router.get("/permits", response_model=list[PermitOut])
async def list_permits(
    db: SessionDep, user: CanRequestPermit,
    status_in: list[str] | None = Query(default=None, alias="status"),
    location_id: uuid.UUID | None = None,
    mine: bool = Query(default=False, description="Only the ones I asked for"),
    live: bool = Query(default=False, description="Only what is valid right now"),
):
    """The permits this caller may see.

    Gated on requesting rather than reading, and narrowed to your own when you
    hold only the first. Every role can raise a permit — the person who needs a
    tank cleaned is not the person authorised to allow entry — but reading the
    register is narrower, so the list refused outright and somebody who had
    just raised one was shown nothing. It looked exactly like the permit had
    not saved, which is what the second acceptance round reported: "it does not
    save once the raise it button is pressed." It had saved. They could not see
    it. The same rule the incident register already follows.
    """
    only_mine = mine or not await _may_browse(db, user)
    rows = await crud.list_permits(
        db, status_in=status_in, location_id=location_id,
        requester_id=_user_id(user) if only_mine else None, active_now=live)
    return [await _out(db, p) for p in rows]


@router.post("/permits", response_model=PermitOut, status_code=status.HTTP_201_CREATED)
async def create_permit(payload: PermitIn, db: SessionDep, user: CanRequestPermit):
    permit = await crud.create_permit(
        db, payload.model_dump(), requester_id=_user_id(user))
    return await _out(db, permit, with_blockers=True)


@router.get("/permits/{permit_id}", response_model=PermitOut)
async def get_permit(permit_id: uuid.UUID, db: SessionDep, user: CanRequestPermit):
    """One permit. Your own always; anyone else's needs the register."""
    permit = await crud.get_permit(db, permit_id)
    if permit.requested_by != _user_id(user) and not await _may_browse(db, user):
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            "You can only open permits you raised")
    return await _out(db, permit, with_blockers=True)


@router.patch("/permits/{permit_id}", response_model=PermitOut)
async def update_permit(
    permit_id: uuid.UUID, payload: PermitPatch, db: SessionDep, user: CanRequestPermit,  # noqa: ARG001
):
    permit = await crud.get_permit(db, permit_id)
    updated = await crud.update_permit(db, permit, payload.model_dump(exclude_unset=True))
    return await _out(db, updated, with_blockers=True)


@router.get("/permits/{permit_id}/gas", response_model=list[GasReadingOut])
async def list_readings(permit_id: uuid.UUID, db: SessionDep, user: CanReadPermit):  # noqa: ARG001
    await crud.get_permit(db, permit_id)
    out = []
    for row in await crud.readings_for(db, permit_id):
        item = GasReadingOut.model_validate(row)
        item.is_fresh = rules.reading_is_fresh(row.taken_at)
        out.append(item)
    return out


@router.post("/permits/{permit_id}/gas", response_model=GasReadingOut,
             status_code=status.HTTP_201_CREATED)
async def record_reading(
    permit_id: uuid.UUID, payload: GasReadingIn, db: SessionDep, user: CanRequestPermit,
):
    """Log an atmospheric test. The verdict is computed from the standard's
    limits — a failing test on a live permit suspends it immediately."""
    permit = await crud.get_permit(db, permit_id)
    row = await crud.record_reading(
        db, permit, payload.model_dump(), user_id=_user_id(user))
    item = GasReadingOut.model_validate(row)
    item.is_fresh = rules.reading_is_fresh(row.taken_at)
    return item


@router.get("/permits/{permit_id}/signatures", response_model=list[SignatureOut])
async def list_signatures(permit_id: uuid.UUID, db: SessionDep, user: CanReadPermit):  # noqa: ARG001
    await crud.get_permit(db, permit_id)
    return await crud.signatures_for(db, permit_id)


@router.post("/permits/{permit_id}/signatures", response_model=SignatureOut,
             status_code=status.HTTP_201_CREATED)
async def add_signature(
    permit_id: uuid.UUID, payload: SignatureIn, db: SessionDep, user: CanRequestPermit,  # noqa: ARG001
):
    """Sign in one of the capacities the permit type requires."""
    permit = await crud.get_permit(db, permit_id)
    return await crud.sign(db, permit, payload.model_dump(), phase="issue")


@router.post("/permits/{permit_id}/issue", response_model=PermitOut)
async def issue_permit(permit_id: uuid.UUID, db: SessionDep, user: CanIssuePermit):
    """Authorise the work. Refuses with the whole list of what is missing."""
    permit = await crud.get_permit(db, permit_id)
    issued = await crud.issue(db, permit, user_id=_user_id(user))
    return await _out(db, issued)


@router.post("/permits/{permit_id}/suspend", response_model=PermitOut)
async def suspend_permit(
    permit_id: uuid.UUID, payload: SuspendIn, db: SessionDep, user: CanIssuePermit,  # noqa: ARG001
):
    permit = await crud.get_permit(db, permit_id)
    return await _out(db, await crud.suspend(db, permit, reason=payload.reason),
                      with_blockers=True)


@router.post("/permits/{permit_id}/close", response_model=PermitOut)
async def close_permit(
    permit_id: uuid.UUID, payload: CloseIn, db: SessionDep, user: CanIssuePermit,
):
    """Hand the area back. Needs a signature attesting everyone is out."""
    permit = await crud.get_permit(db, permit_id)
    closed = await crud.close(
        db, permit, user_id=_user_id(user), note=payload.closeout_note,
        signature=payload.signature.model_dump())
    return await _out(db, closed)
