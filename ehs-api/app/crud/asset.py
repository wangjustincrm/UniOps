"""Safety equipment, and keeping its inspections on schedule.

The cycle is per asset, not per period, which is what separates this from the
form scheduler. `generate_due` walks assets whose next inspection falls inside
the lead window and creates the row; `chase_overdue` marks what nobody did.
Both are idempotent because the sweep runs every few minutes — the unique
constraint on (asset, due date) is the backstop, and the code checks first so
the common path never relies on catching an error.

A failed inspection does two things a passed one does not: it takes the asset
out of service, and it raises a corrective action. An extinguisher that failed
its check must stop counting as coverage for its area, and that is a state on
the asset rather than a note on the inspection.
"""
from __future__ import annotations

import secrets
import uuid
from datetime import date, datetime, timezone

from fastapi import HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.asset import AssetInspection, SafetyAsset
from app.models.mirrors import Department, Location, User
from app.services import tasks as task_service
from app.services.numbering import next_number
from app.services.recurrence import next_due

DOC_ASSET = task_service.DOC_ASSET

# How far ahead a due inspection is raised. A month's notice on an annual
# check is noise; a week is enough for any of these.
LEAD_DAYS = 7

# The most periods one sweep will catch up on. An asset registered with a date
# in the past, or one whose register sat unused, is genuinely overdue for each
# of those checks — but raising a hundred tasks the first time somebody opens
# the page helps nobody. Twelve is a year of monthly checks.
MAX_CATCHUP = 12

FREQUENCIES = ("monthly", "quarterly", "semiannual", "annual")


def _new_qr_token() -> str:
    return secrets.token_urlsafe(24)


async def list_assets(
    db: AsyncSession, *, status_in: list[str] | None = None,
    location_id: uuid.UUID | None = None, due_only: bool = False,
    today: date | None = None, limit: int = 500,
) -> list[SafetyAsset]:
    stmt = select(SafetyAsset)
    if status_in:
        stmt = stmt.where(SafetyAsset.status.in_(status_in))
    if location_id:
        stmt = stmt.where(SafetyAsset.location_id == location_id)
    if due_only:
        stmt = stmt.where(SafetyAsset.next_due_on.is_not(None),
                          SafetyAsset.next_due_on <= (today or date.today()))
    return list((await db.execute(
        stmt.order_by(SafetyAsset.location_path, SafetyAsset.name).limit(limit))).scalars())


async def get_asset(db: AsyncSession, asset_id: uuid.UUID) -> SafetyAsset:
    asset = await db.get(SafetyAsset, asset_id)
    if asset is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Equipment not found")
    return asset


async def by_qr_token(db: AsyncSession, token: str) -> SafetyAsset | None:
    return (await db.execute(
        select(SafetyAsset).where(SafetyAsset.qr_token == token))).scalar_one_or_none()


async def _location_fields(db: AsyncSession, location_id: uuid.UUID | None) -> tuple:
    if location_id is None:
        return None, None, None
    loc = await db.get(Location, location_id)
    if loc is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No such plant area")
    dept_name = (await db.execute(
        select(Department.name).where(Department.id == loc.department_id))).scalar()
    return loc.path, loc.department_id, dept_name


async def create_asset(db: AsyncSession, payload: dict) -> SafetyAsset:
    path, dept_id, dept_name = await _location_fields(db, payload.get("location_id"))
    asset = SafetyAsset(
        id=uuid.uuid4(),
        asset_no=await next_number(db, "safety_asset"),
        name=payload["name"],
        asset_type_id=payload.get("asset_type_id"),
        asset_type_label=payload.get("asset_type_label"),
        serial_no=payload.get("serial_no"),
        manufacturer=payload.get("manufacturer"),
        model=payload.get("model"),
        location_id=payload.get("location_id"),
        location_path=path,
        department_id=dept_id,
        department_name=dept_name,
        inspection_frequency=payload.get("inspection_frequency"),
        inspection_template_id=payload.get("inspection_template_id"),
        assigned_role=payload.get("assigned_role"),
        assigned_user_id=payload.get("assigned_user_id"),
        expires_on=payload.get("expires_on"),
        notes=payload.get("notes"),
        qr_token=_new_qr_token(),
    )
    # First check falls one period from today rather than immediately: a
    # register loaded with two hundred extinguishers must not open two hundred
    # tasks the same afternoon.
    if asset.inspection_frequency:
        asset.next_due_on = payload.get("next_due_on") or next_due(
            date.today(), asset.inspection_frequency)
    db.add(asset)
    await db.flush()
    return asset


async def update_asset(db: AsyncSession, asset: SafetyAsset, fields: dict) -> SafetyAsset:
    if "location_id" in fields:
        path, dept_id, dept_name = await _location_fields(db, fields["location_id"])
        asset.location_path, asset.department_id, asset.department_name = path, dept_id, dept_name
    for key, value in fields.items():
        setattr(asset, key, value)
    if asset.inspection_frequency and asset.next_due_on is None:
        asset.next_due_on = next_due(date.today(), asset.inspection_frequency)
    await db.flush()
    return asset


async def inspections_for(
    db: AsyncSession, *, asset_id: uuid.UUID | None = None,
    result_in: list[str] | None = None, limit: int = 200,
) -> list[AssetInspection]:
    stmt = select(AssetInspection)
    if asset_id:
        stmt = stmt.where(AssetInspection.asset_id == asset_id)
    if result_in:
        stmt = stmt.where(AssetInspection.result.in_(result_in))
    return list((await db.execute(
        stmt.order_by(AssetInspection.due_on.desc()).limit(limit))).scalars())


async def get_inspection(db: AsyncSession, inspection_id: uuid.UUID) -> AssetInspection:
    row = await db.get(AssetInspection, inspection_id)
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Inspection not found")
    return row


async def _open_inspection(
    db: AsyncSession, asset: SafetyAsset, due_on: date
) -> AssetInspection | None:
    """Create the inspection row and its task, unless it already exists."""
    existing = (await db.execute(
        select(AssetInspection).where(
            AssetInspection.asset_id == asset.id,
            AssetInspection.due_on == due_on,
        ))).scalar_one_or_none()
    if existing is not None:
        return None

    cycle = ((await db.execute(
        select(func.max(AssetInspection.cycle_index))
        .where(AssetInspection.asset_id == asset.id))).scalar() or 0) + 1

    inspection = AssetInspection(
        id=uuid.uuid4(), asset_id=asset.id, due_on=due_on, cycle_index=cycle)
    db.add(inspection)
    await db.flush()

    where = f" — {asset.location_path}" if asset.location_path else ""
    await task_service.open_task(
        db,
        task_type=task_service.TASK_ASSET_INSPECT,
        doc_type=DOC_ASSET,
        doc_id=inspection.id,
        doc_number=asset.asset_no,
        title=f"Inspect {asset.name}{where}",
        assigned_role=asset.assigned_role or "area_supervisor",
        assigned_user_id=asset.assigned_user_id,
        description=f"{asset.inspection_frequency or 'Periodic'} check, due "
                    f"{due_on.isoformat()}.",
        priority="normal",
    )
    return inspection


async def generate_due(db: AsyncSession, *, today: date | None = None) -> list[AssetInspection]:
    """Raise the checks that fall inside the lead window."""
    today = today or datetime.now(timezone.utc).date()
    horizon = date.fromordinal(today.toordinal() + LEAD_DAYS)

    assets = (await db.execute(
        select(SafetyAsset).where(
            SafetyAsset.status == "in_service",
            SafetyAsset.inspection_frequency.is_not(None),
            SafetyAsset.next_due_on.is_not(None),
            SafetyAsset.next_due_on <= horizon,
        ))).scalars().all()

    created: list[AssetInspection] = []
    for asset in assets:
        # Catch up in one pass rather than one period per sweep. An asset three
        # months behind owes three checks, and advancing once per sweep would
        # take three sweeps to say so — the register would under-report exactly
        # when it was most behind.
        for _ in range(MAX_CATCHUP):
            if asset.next_due_on > horizon:
                break
            inspection = await _open_inspection(db, asset, asset.next_due_on)
            if inspection is not None:
                created.append(inspection)
            # Advance whether or not the check was done: the next cycle is due
            # regardless, and a missed one stays behind as its own overdue row.
            asset.next_due_on = next_due(asset.next_due_on, asset.inspection_frequency)

    await db.flush()
    return created


async def chase_overdue(db: AsyncSession, *, today: date | None = None) -> list[AssetInspection]:
    """Mark what nobody did. Acts on the change, so it says so once."""
    today = today or datetime.now(timezone.utc).date()
    overdue = (await db.execute(
        select(AssetInspection).where(
            AssetInspection.result == "due",
            AssetInspection.due_on < today,
        ))).scalars().all()
    for inspection in overdue:
        inspection.result = "missed"
    await db.flush()
    return list(overdue)


async def record_inspection(
    db: AsyncSession, inspection: AssetInspection, *, result: str, notes: str | None,
    submission_id: uuid.UUID | None, user_id: uuid.UUID, performed_on: date | None = None,
) -> AssetInspection:
    """Record the outcome. A failure takes the asset out of service."""
    if inspection.result in ("passed", "failed"):
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "This check has already been recorded; the result is part of the "
            "register and is not overwritten")

    asset = await get_asset(db, inspection.asset_id)
    inspection.result = result
    inspection.notes = notes
    inspection.submission_id = submission_id
    inspection.performed_on = performed_on or date.today()
    inspection.performed_by = user_id
    inspection.performed_by_name = (await db.execute(
        select(User.full_name).where(User.id == user_id))).scalar()
    inspection.completed_at = datetime.now(timezone.utc)

    asset.last_inspected_on = inspection.performed_on
    if result == "failed":
        # Not a note on the inspection: this asset must stop being counted as
        # working equipment until somebody puts it back.
        asset.status = "out_of_service"

    await task_service.close_tasks_for(
        db, doc_type=DOC_ASSET, doc_id=inspection.id, completed_by=user_id)
    await db.flush()
    return inspection


async def return_to_service(db: AsyncSession, asset: SafetyAsset) -> SafetyAsset:
    """Put a repaired or replaced item back, and restart its cycle."""
    if asset.status == "retired":
        raise HTTPException(
            status.HTTP_409_CONFLICT, "A retired item is not returned to service")
    asset.status = "in_service"
    if asset.inspection_frequency:
        asset.next_due_on = next_due(date.today(), asset.inspection_frequency)
    await db.flush()
    return asset


async def coverage(db: AsyncSession) -> list[dict]:
    """Working equipment by type, and how much of it is unavailable.

    The question behind the register: an area with three extinguishers, one of
    which failed its check, has two.
    """
    rows = (await db.execute(
        select(SafetyAsset.asset_type_label, SafetyAsset.status, func.count())
        .where(SafetyAsset.status != "retired")
        .group_by(SafetyAsset.asset_type_label, SafetyAsset.status)
    )).all()

    grouped: dict[str, dict] = {}
    for label, state, count in rows:
        key = label or "Unclassified"
        entry = grouped.setdefault(key, {"label": key, "in_service": 0, "out_of_service": 0})
        entry[state] = entry.get(state, 0) + count
    out = list(grouped.values())
    out.sort(key=lambda r: -(r["in_service"] + r["out_of_service"]))
    return out
