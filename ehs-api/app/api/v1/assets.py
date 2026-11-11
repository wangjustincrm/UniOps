"""The safety equipment register and its inspection cycle.

Three keys, because three different people are involved: reading the register,
walking round with a clipboard, and deciding what is on it.
"""
from __future__ import annotations

import uuid
from datetime import date, datetime

from fastapi import APIRouter, HTTPException, Query, Response, status
from pydantic import BaseModel, ConfigDict, Field

from sqlalchemy import select

from app.api.v1.incidents import _user_id
from app.core.deps import SessionDep
from app.core.permissions import (
    CanInspectAsset, CanManageAsset, CanReadAsset, CanReportIncident, CanReturnAsset,
)
from app.crud import asset as crud

router = APIRouter(prefix="/safety-assets")

_FREQUENCY = "^(monthly|quarterly|semiannual|annual)$"


class AssetIn(BaseModel):
    # A key this model does not know is a mistake, not something to drop
    # quietly — see the same note on hazards.
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=200)
    asset_type_id: uuid.UUID | None = None
    asset_type_label: str | None = Field(default=None, max_length=200)
    serial_no: str | None = Field(default=None, max_length=100)
    manufacturer: str | None = Field(default=None, max_length=150)
    model: str | None = Field(default=None, max_length=150)
    location_id: uuid.UUID | None = None
    inspection_frequency: str | None = Field(default=None, pattern=_FREQUENCY)
    inspection_template_id: uuid.UUID | None = None
    assigned_role: str | None = Field(default=None, max_length=50)
    assigned_user_id: uuid.UUID | None = None
    next_due_on: date | None = None
    # For equipment with a service life: a harness is condemned at ten years
    # whatever its condition.
    expires_on: date | None = None
    notes: str | None = None


class AssetPatch(AssetIn):
    name: str | None = Field(default=None, max_length=200)
    status: str | None = Field(default=None, pattern="^(in_service|out_of_service|retired)$")


class AssetOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    asset_no: str
    name: str
    # The id as well as the label: the label keeps an old record readable after
    # the vocabulary is renamed, the id is what puts the picker back on the
    # right row when the record is reopened to be corrected.
    asset_type_id: uuid.UUID | None
    asset_type_label: str | None
    serial_no: str | None
    manufacturer: str | None
    model: str | None
    location_id: uuid.UUID | None
    location_path: str | None
    department_name: str | None
    inspection_frequency: str | None
    inspection_template_id: uuid.UUID | None
    assigned_role: str | None
    assigned_user_id: uuid.UUID | None
    next_due_on: date | None
    last_inspected_on: date | None
    expires_on: date | None
    status: str
    notes: str | None
    qr_token: str | None


class InspectionOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    asset_id: uuid.UUID
    due_on: date
    performed_on: date | None
    performed_by_name: str | None
    result: str
    notes: str | None
    submission_id: uuid.UUID | None
    action_id: uuid.UUID | None
    completed_at: datetime | None


class RepairActionIn(BaseModel):
    """Round 12, TC-19 and D16: the corrective action a failed check raises,
    filled in on the same form the incidents use, rather than written by the
    server with a default owner and no type of control."""

    title: str = Field(min_length=1, max_length=255)
    description: str | None = None
    owner_id: uuid.UUID
    due_date: date
    priority: str = Field(default="high", pattern="^(low|normal|high)$")
    hierarchy_of_control: str | None = Field(default=None, max_length=30)


class RecordIn(BaseModel):
    result: str = Field(pattern="^(passed|failed)$")
    notes: str | None = None
    performed_on: date | None = None
    # When the check was answered with a checklist rather than a pass/fail.
    submission_id: uuid.UUID | None = None
    action: RepairActionIn | None = None


class CoverageRow(BaseModel):
    label: str
    in_service: int
    out_of_service: int


@router.get("", response_model=list[AssetOut])
async def list_assets(
    db: SessionDep, user: CanReadAsset,  # noqa: ARG001
    status_in: list[str] | None = Query(default=None, alias="status"),
    location_id: uuid.UUID | None = None,
    due: bool = Query(default=False, description="Only items whose check is due"),
):
    return await crud.list_assets(
        db, status_in=status_in, location_id=location_id, due_only=due)


@router.get("/coverage", response_model=list[CoverageRow])
async def coverage(db: SessionDep, user: CanReadAsset):  # noqa: ARG001
    """Working equipment by type, and how much of it is currently unavailable."""
    return await crud.coverage(db)


class OutOfServiceRow(BaseModel):
    id: uuid.UUID
    asset_no: str
    name: str
    location_path: str | None
    since: datetime | None


@router.get("/out-of-service", response_model=list[OutOfServiceRow])
async def out_of_service(db: SessionDep, user: CanReportIncident):  # noqa: ARG001
    """★ Round 10 (open box): "list the item as being out of service on the my
    actions for all staff to read … List the equipment name and location."
    Every employee, so only what is posted on the item itself: its number,
    name, where it is, and since when."""
    return await crud.out_of_service(db)


class StatusEventOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    status: str
    at: datetime
    by_name: str | None
    reason: str | None
    action_id: uuid.UUID | None


@router.get("/{asset_id}/history", response_model=list[StatusEventOut])
async def status_history(asset_id: uuid.UUID, db: SessionDep, user: CanReadAsset):  # noqa: ARG001
    """When it went out of service and came back, by whom, and why."""
    from app.models.asset import AssetStatusEvent
    await crud.get_asset(db, asset_id)
    return list((await db.execute(select(AssetStatusEvent).where(
        AssetStatusEvent.asset_id == asset_id).order_by(AssetStatusEvent.at.desc()))).scalars())


@router.get("/inspections", response_model=list[InspectionOut])
async def list_inspections(
    db: SessionDep, user: CanReadAsset,  # noqa: ARG001
    asset_id: uuid.UUID | None = None,
    result_in: list[str] | None = Query(default=None, alias="result"),
):
    return await crud.inspections_for(db, asset_id=asset_id, result_in=result_in)


@router.post("", response_model=AssetOut, status_code=status.HTTP_201_CREATED)
async def create_asset(payload: AssetIn, db: SessionDep, user: CanManageAsset):  # noqa: ARG001
    return await crud.create_asset(db, payload.model_dump())


@router.get("/{asset_id}", response_model=AssetOut)
async def get_asset(asset_id: uuid.UUID, db: SessionDep, user: CanReadAsset):  # noqa: ARG001
    return await crud.get_asset(db, asset_id)


@router.patch("/{asset_id}", response_model=AssetOut)
async def update_asset(
    asset_id: uuid.UUID, payload: AssetPatch, db: SessionDep, user: CanManageAsset,
):
    asset = await crud.get_asset(db, asset_id)
    fields = payload.model_dump(exclude_unset=True)
    # Retiring by edit goes the same way as the Retire button: off its cycle,
    # owed checks closed, and in its history.
    if fields.get("status") == "retired":
        fields.pop("status")
        asset = await crud.update_asset(db, asset, fields)
        return await crud.retire(db, asset, user_id=_user_id(user), reason=None)
    return await crud.update_asset(db, asset, fields)


class ReturnIn(BaseModel):
    reason: str = Field(min_length=5, max_length=500)


@router.post("/{asset_id}/return-to-service", response_model=AssetOut)
async def return_to_service(
    asset_id: uuid.UUID, payload: ReturnIn, db: SessionDep, user: CanReturnAsset,
):
    """Put an item back by hand, saying why — recorded in its history beside the
    ones put back when their corrective actions were verified (round 10).

    ★ Round 12, FRM-04 / D11: a supervisor may, from My Actions — its own key,
    ehs.asset.return, so this is all they can do to the register."""
    return await crud.return_to_service(db, await crud.get_asset(db, asset_id),
                                        user_id=_user_id(user), reason=payload.reason.strip())


class RetireIn(BaseModel):
    reason: str | None = Field(default=None, max_length=500)


@router.post("/{asset_id}/retire", response_model=AssetOut)
async def retire_asset(asset_id: uuid.UUID, payload: RetireIn, db: SessionDep,
                       user: CanManageAsset):
    """Round 12, Q3: out of the register's working list and off its cycle;
    its checks and history stay."""
    return await crud.retire(db, await crud.get_asset(db, asset_id),
                             user_id=_user_id(user), reason=(payload.reason or "").strip() or None)


@router.post("/{asset_id}/reinstate", response_model=AssetOut)
async def reinstate_asset(asset_id: uuid.UUID, payload: RetireIn, db: SessionDep,
                          user: CanManageAsset):
    return await crud.reinstate(db, await crud.get_asset(db, asset_id),
                                user_id=_user_id(user), reason=(payload.reason or "").strip() or None)


@router.delete("/{asset_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_asset(asset_id: uuid.UUID, db: SessionDep, user: CanManageAsset):
    """Only an item nothing has been recorded against (Q3)."""
    await crud.delete(db, await crud.get_asset(db, asset_id), user_id=_user_id(user))
    return Response(status_code=status.HTTP_204_NO_CONTENT)


class InspectionFormOut(BaseModel):
    """Where to fill in this item's check, when it has a form."""

    submission_id: uuid.UUID
    submission_no: str


@router.post("/{asset_id}/inspection-form/start", response_model=InspectionFormOut)
async def start_inspection_form(
    asset_id: uuid.UUID, db: SessionDep, user: CanInspectAsset,
):
    """Open the form this item is checked on.

    ★ Round 4, TC-07. Refused rather than falling back to the generic dialog
    when no form is linked: the caller asked for this item's form and there is
    not one, and silently opening something else is how somebody records a
    check against the wrong thing.
    """
    asset = await crud.get_asset(db, asset_id)
    found = await crud.inspection_form_for(db, asset, user_id=_user_id(user))
    if found is None:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            f"{asset.name} is not linked to an inspection form. Choose one on the "
            "equipment record, or record the check directly.")
    submission_id, submission_no = found
    return InspectionFormOut(submission_id=submission_id, submission_no=submission_no)


@router.post("/{asset_id}/inspections", response_model=InspectionOut,
             status_code=status.HTTP_201_CREATED)
async def record_check_now(
    asset_id: uuid.UUID, payload: RecordIn, db: SessionDep, user: CanInspectAsset,
):
    """Record a check done now, whether or not one was due.

    The only way to record one was against an occurrence the sweep had already
    raised — so with nothing due on anything, pressing "record a check" saved
    nothing at all. The schedule says when a check is owed, not when one is
    allowed to happen.
    """
    asset = await crud.get_asset(db, asset_id)
    return await crud.record_ad_hoc(
        db, asset, result=payload.result, notes=payload.notes,
        user_id=_user_id(user), performed_on=payload.performed_on,
        action_draft=payload.action.model_dump() if payload.action else None)


@router.post("/inspections/{inspection_id}/record", response_model=InspectionOut)
async def record_inspection(
    inspection_id: uuid.UUID, payload: RecordIn, db: SessionDep, user: CanInspectAsset,
):
    """Record a check. A failure takes the item out of service."""
    inspection = await crud.get_inspection(db, inspection_id)
    return await crud.record_inspection(
        db, inspection, result=payload.result, notes=payload.notes,
        submission_id=payload.submission_id, user_id=_user_id(user),
        performed_on=payload.performed_on,
        action_draft=payload.action.model_dump() if payload.action else None)
