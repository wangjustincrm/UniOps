"""The safety equipment register and its inspection cycle.

Three keys, because three different people are involved: reading the register,
walking round with a clipboard, and deciding what is on it.
"""
from __future__ import annotations

import uuid
from datetime import date, datetime

from fastapi import APIRouter, Query, status
from pydantic import BaseModel, ConfigDict, Field

from app.api.v1.incidents import _user_id
from app.core.deps import SessionDep
from app.core.permissions import CanInspectAsset, CanManageAsset, CanReadAsset
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


class RecordIn(BaseModel):
    result: str = Field(pattern="^(passed|failed)$")
    notes: str | None = None
    performed_on: date | None = None
    # When the check was answered with a checklist rather than a pass/fail.
    submission_id: uuid.UUID | None = None


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
    asset_id: uuid.UUID, payload: AssetPatch, db: SessionDep, user: CanManageAsset,  # noqa: ARG001
):
    asset = await crud.get_asset(db, asset_id)
    return await crud.update_asset(db, asset, payload.model_dump(exclude_unset=True))


@router.post("/{asset_id}/return-to-service", response_model=AssetOut)
async def return_to_service(asset_id: uuid.UUID, db: SessionDep, user: CanManageAsset):  # noqa: ARG001
    return await crud.return_to_service(db, await crud.get_asset(db, asset_id))


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
        user_id=_user_id(user), performed_on=payload.performed_on)


@router.post("/inspections/{inspection_id}/record", response_model=InspectionOut)
async def record_inspection(
    inspection_id: uuid.UUID, payload: RecordIn, db: SessionDep, user: CanInspectAsset,
):
    """Record a check. A failure takes the item out of service."""
    inspection = await crud.get_inspection(db, inspection_id)
    return await crud.record_inspection(
        db, inspection, result=payload.result, notes=payload.notes,
        submission_id=payload.submission_id, user_id=_user_id(user),
        performed_on=payload.performed_on)
