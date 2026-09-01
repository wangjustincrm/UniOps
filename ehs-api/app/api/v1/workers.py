"""Worker safety profiles and the position matrix.

Two permission boundaries here, not one.

`ehs.worker.read` opens the profile. `ehs.incident.medical.read` opens the
medical note inside it — a restriction relevant to return-to-work planning is
PHIPA material, and it is stripped from the response rather than merely hidden
in the UI, because a field the API returns is a field that has been disclosed.
"""
from __future__ import annotations

import uuid
from datetime import date

from fastapi import APIRouter, Query, status
from pydantic import BaseModel, ConfigDict, Field

from app.api.v1.incidents import _user_id
from app.core.deps import SessionDep
from app.core.permissions import CanReadTraining, CanReadWorker, CanWriteWorker
from app.crud import worker as crud
from uniops_authz import has_permission

router = APIRouter()


class ProfileIn(BaseModel):
    employee_no: str | None = Field(default=None, max_length=30)
    hire_date: date | None = None
    employment_type: str | None = Field(default=None, max_length=20)
    shift_code: str | None = Field(default=None, max_length=20)
    phone_mobile: str | None = Field(default=None, max_length=30)
    primary_location_id: uuid.UUID | None = None
    # {"name": ..., "relationship": ..., "phone": ...}
    emergency_contact: dict | None = None
    # PHIPA-sensitive. Only writable and readable with ehs.incident.medical.read.
    medical_notes: str | None = None
    is_safety_sensitive: bool | None = None


class ProfileOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    user_id: uuid.UUID
    employee_no: str | None
    hire_date: date | None
    employment_type: str | None
    shift_code: str | None
    phone_mobile: str | None
    primary_location_id: uuid.UUID | None
    emergency_contact: dict
    is_safety_sensitive: bool

    # PHIPA material. `medical_visible` says which of the two nulls this is:
    # "there are no restrictions" and "you may not see whether there are" are
    # very different statements, and a bare null tells a supervisor the first
    # when the truth is the second.
    medical_visible: bool = False
    medical_notes: str | None = None


class PositionIn(BaseModel):
    code: str = Field(min_length=1, max_length=40)
    name: str = Field(min_length=1, max_length=200)


class PositionOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    code: str
    name: str
    is_active: bool
    holders: int = 0
    requirements: int = 0


class RequirementIn(BaseModel):
    requirement_type: str = Field(default="training", pattern="^(training|ppe|medical)$")
    course_id: uuid.UUID | None = None
    vocabulary_item_id: uuid.UUID | None = None
    label: str | None = Field(default=None, max_length=200)
    is_mandatory: bool = True


class RequirementsIn(BaseModel):
    requirements: list[RequirementIn] = Field(default_factory=list, max_length=50)


class RequirementOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    requirement_type: str
    course_id: uuid.UUID | None
    vocabulary_item_id: uuid.UUID | None
    label: str | None
    is_mandatory: bool


class HolderOut(BaseModel):
    assignment_id: uuid.UUID
    user_id: uuid.UUID
    user_name: str
    effective_from: date


class AssignIn(BaseModel):
    user_id: uuid.UUID
    effective_from: date | None = None


async def _profile_out(db, user: dict, profile) -> ProfileOut:
    """Never let a medical note out to someone without the medical key."""
    may_see = await has_permission(
        db, _user_id(user), user.get("role", ""), "ehs.incident.medical.read")
    out = ProfileOut.model_validate(profile)
    out.medical_visible = may_see
    if not may_see:
        out.medical_notes = None
    return out


# ── Profiles ─────────────────────────────────────────────────────────────────

@router.get("/workers/{user_id}/profile", response_model=ProfileOut | None)
async def get_profile(user_id: uuid.UUID, db: SessionDep, user: CanReadWorker):
    profile = await crud.get_profile(db, user_id)
    if profile is None:
        return None
    return await _profile_out(db, user, profile)


@router.put("/workers/{user_id}/profile", response_model=ProfileOut)
async def put_profile(
    user_id: uuid.UUID, payload: ProfileIn, db: SessionDep, user: CanWriteWorker,
):
    fields = payload.model_dump(exclude_unset=True)
    if "medical_notes" in fields and not await has_permission(
        db, _user_id(user), user.get("role", ""), "ehs.incident.medical.read"
    ):
        fields.pop("medical_notes")
    profile = await crud.upsert_profile(db, user_id, fields)
    return await _profile_out(db, user, profile)


# ── Positions ────────────────────────────────────────────────────────────────

@router.get("/positions", response_model=list[PositionOut])
async def list_positions(
    db: SessionDep, user: CanReadTraining,  # noqa: ARG001
    include_retired: bool = False,
):
    """The job positions, with how many people hold each and what each needs.

    The two counts are what makes the page readable at a glance: a position
    with requirements and no holders, or holders and no requirements, is the
    shape of a matrix somebody started and did not finish.
    """
    out: list[PositionOut] = []
    for position in await crud.list_positions(db, active_only=not include_retired):
        item = PositionOut.model_validate(position)
        item.holders = len(await crud.holders_of(db, position.id))
        item.requirements = len(await crud.requirements_for(db, position.id))
        out.append(item)
    return out


@router.post("/positions", response_model=PositionOut, status_code=status.HTTP_201_CREATED)
async def create_position(payload: PositionIn, db: SessionDep, user: CanWriteWorker):  # noqa: ARG001
    return PositionOut.model_validate(
        await crud.create_position(db, code=payload.code, name=payload.name))


@router.post("/positions/{position_id}/retire", response_model=PositionOut)
async def retire_position(position_id: uuid.UUID, db: SessionDep, user: CanWriteWorker):  # noqa: ARG001
    position = await crud.get_position(db, position_id)
    return PositionOut.model_validate(await crud.retire_position(db, position))


@router.get("/positions/{position_id}/requirements", response_model=list[RequirementOut])
async def get_requirements(
    position_id: uuid.UUID, db: SessionDep, user: CanReadTraining,  # noqa: ARG001
):
    await crud.get_position(db, position_id)
    return await crud.requirements_for(db, position_id)


@router.put("/positions/{position_id}/requirements", response_model=list[RequirementOut])
async def put_requirements(
    position_id: uuid.UUID, payload: RequirementsIn, db: SessionDep, user: CanWriteWorker,  # noqa: ARG001
):
    """What this position requires. Drives the training gap report."""
    position = await crud.get_position(db, position_id)
    return await crud.set_requirements(
        db, position, [r.model_dump() for r in payload.requirements])


@router.get("/positions/{position_id}/holders", response_model=list[HolderOut])
async def get_holders(
    position_id: uuid.UUID, db: SessionDep, user: CanReadTraining,  # noqa: ARG001
    on: date | None = Query(default=None, description="As at this date; today by default"),
):
    await crud.get_position(db, position_id)
    return [
        HolderOut(assignment_id=row.id, user_id=row.user_id, user_name=name,
                  effective_from=row.effective_from)
        for row, name in await crud.holders_of(db, position_id, today=on)
    ]


@router.post("/positions/{position_id}/holders", response_model=HolderOut,
             status_code=status.HTTP_201_CREATED)
async def assign_holder(
    position_id: uuid.UUID, payload: AssignIn, db: SessionDep, user: CanWriteWorker,  # noqa: ARG001
):
    """Put somebody in this position — which is what makes its required
    training required of them."""
    row = await crud.assign(
        db, position_id=position_id, user_id=payload.user_id,
        effective_from=payload.effective_from)
    from sqlalchemy import select

    from app.models.mirrors import User
    name = (await db.execute(select(User.full_name).where(User.id == row.user_id))).scalar()
    return HolderOut(assignment_id=row.id, user_id=row.user_id,
                     user_name=name or "Unknown", effective_from=row.effective_from)


@router.delete("/positions/{position_id}/holders/{assignment_id}",
               status_code=status.HTTP_204_NO_CONTENT)
async def unassign_holder(
    position_id: uuid.UUID, assignment_id: uuid.UUID,  # noqa: ARG001
    db: SessionDep, user: CanWriteWorker,  # noqa: ARG001
):
    """Ends the assignment today rather than deleting it — somebody who held
    the forklift position last year genuinely needed the licence then."""
    await crud.unassign(db, assignment_id)
