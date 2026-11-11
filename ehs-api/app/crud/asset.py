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
from datetime import date, datetime, timedelta, timezone

from fastapi import HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.asset import AssetInspection, AssetStatusEvent, SafetyAsset
from app.models.mirrors import Department, Location, User
from app.services import tasks as task_service
from app.services.numbering import next_number
from app.services.recurrence import next_due
from app.services.day_range import plant_today

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
                          SafetyAsset.next_due_on <= (today or plant_today()))
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
            plant_today(), asset.inspection_frequency)
    db.add(asset)
    await db.flush()
    return asset


async def update_asset(db: AsyncSession, asset: SafetyAsset, fields: dict) -> SafetyAsset:
    if "location_id" in fields:
        path, dept_id, dept_name = await _location_fields(db, fields["location_id"])
        asset.location_path, asset.department_id, asset.department_name = path, dept_id, dept_name
    # Whether the cycle itself changed decides whether the next check moves,
    # so it has to be read before the new values are written on.
    cycle_changed = (
        "inspection_frequency" in fields
        and fields["inspection_frequency"] != asset.inspection_frequency
    )
    for key, value in fields.items():
        setattr(asset, key, value)

    if asset.inspection_frequency and asset.next_due_on is None:
        asset.next_due_on = next_due(plant_today(), asset.inspection_frequency)
    elif cycle_changed and asset.inspection_frequency and "next_due_on" not in fields:
        # Putting an item on a shorter cycle has to bring its next check
        # forward, or the change is cosmetic: an extinguisher moved from
        # quarterly to monthly would keep its date three months out and quietly
        # miss two checks. Counted from the last check that actually happened,
        # because that is what the new interval is an interval from.
        asset.next_due_on = next_due(
            asset.last_inspected_on or plant_today(), asset.inspection_frequency)
    elif cycle_changed and not asset.inspection_frequency:
        # Taken off a cycle entirely — nothing is owed, and a date left behind
        # would keep raising checks for an item nobody schedules any more.
        asset.next_due_on = None

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
    today = today or plant_today()
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
    today = today or plant_today()
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
    action_draft: dict | None = None,
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
    inspection.performed_on = performed_on or plant_today()
    inspection.performed_by = user_id
    inspection.performed_by_name = (await db.execute(
        select(User.full_name).where(User.id == user_id))).scalar()
    inspection.completed_at = datetime.now(timezone.utc)

    asset.last_inspected_on = inspection.performed_on
    if result == "failed":
        # Not a note on the inspection: this asset must stop being counted as
        # working equipment until somebody puts it back.
        if asset.status != "out_of_service":
            asset.status = "out_of_service"
            await status_event(
                db, asset, "out_of_service", by_id=user_id,
                reason=f"Failed its check on {inspection.performed_on:%-d %b %Y}"
                       + (f": {notes}" if notes else ""))
        # ★ Round 10 (open box): "Failing it … raises a corrective action." A
        # check on a checklist raises one per failed answer (crud/form); one
        # recorded without a checklist raised none. One is raised here, unless
        # the item already has one open.
        if submission_id is None:
            await _raise_repair_action(db, asset, inspection, user_id=user_id, notes=notes,
                                       draft=action_draft)

    await task_service.close_tasks_for(
        db, doc_type=DOC_ASSET, doc_id=inspection.id, completed_by=user_id)
    await db.flush()
    return inspection


async def inspection_form_for(
    db: AsyncSession, asset: SafetyAsset, *, user_id: uuid.UUID,
) -> tuple[uuid.UUID, str] | None:
    """The submission to fill in for this item, creating it if there is none.

    ★ Round 4, TC-07: "allow for an equipment qr code to be linked to a specific
    form. for example when a user scans the eye wash station qr code the eye wash
    inspection form shall open with the name of the station on the form
    automatic."

    `inspection_template_id` has been on this model since the first release, the
    API accepted it and the crud stored it — and nothing ever set it and nothing
    ever read it. Eleven items on the dev database, none of them linked. This is
    the read half; the equipment form is the write half.

    Returns None where the item has no form, which is most of them: a fire
    extinguisher check is four yes/no answers and the generic dialog is better
    for it than a template nobody wrote.

    An unsubmitted form for this item is returned rather than a second one being
    made. Two half-filled checks of the same eyewash is the same fault as two
    sets of minutes for one meeting — nothing on either says which is real.
    """
    from app.crud import form as form_crud
    from app.models.form import FormSubmission

    if asset.inspection_template_id is None:
        return None

    existing = (await db.execute(
        select(FormSubmission).where(
            FormSubmission.source_type == "asset",
            FormSubmission.source_id == asset.id,
            FormSubmission.status != "submitted",
        ).order_by(FormSubmission.created_at.desc()).limit(1)
    )).scalar_one_or_none()
    if existing is not None:
        return existing.id, existing.submission_no

    # ★ The person scanning it, not the person the item is assigned to.
    #
    # Two reasons, and the first one is fatal: with no assignee — which is every
    # item on the dev database — this passed a fresh random uuid, and
    # `start_submission` writes it to `submitted_by`, a foreign key to `users`.
    # So the main path this feature exists for answered 500.
    #
    # The second is why the assignee would be wrong even where there is one. The
    # name on an inspection record is who carried the inspection out. It is
    # evidence under Reg 851, and whoever is standing at the eyewash with a
    # phone is the only person it can honestly name.
    submission = await form_crud.start_submission(
        db, asset.inspection_template_id, user_id=user_id,
        location_id=asset.location_id, occurred_at=None)
    # ★ Which item it is for, which is what puts the station's name on the form.
    submission.source_type = "asset"
    submission.source_id = asset.id
    submission.source_ref = f"{asset.asset_no} · {asset.name}"
    await db.flush()
    return submission.id, submission.submission_no


async def record_ad_hoc(
    db: AsyncSession, asset: SafetyAsset, *, result: str, notes: str | None,
    user_id: uuid.UUID, performed_on: date | None = None,
    submission_id: uuid.UUID | None = None, action_draft: dict | None = None,
) -> AssetInspection:
    """A check somebody did, whether or not one was due.

    Recording only against an occurrence the sweep had already raised was the
    whole of it, and the second acceptance round found what that means on the
    floor: "record a check is not logged anywhere once complete." Nothing was
    due on anything — every asset's next check was weeks out — so there was no
    row to record against and the check went nowhere.

    That is not how equipment is checked. Somebody walks past an extinguisher,
    looks at it, and that is a check; the schedule says when one is *owed*, not
    when one may happen. So this makes the occurrence and records it in the
    same breath, and it counts: the asset's last-checked date moves, a failure
    takes it out of service, and the next due date is measured from today
    rather than from the date somebody missed.
    """
    when = performed_on or plant_today()

    # One row per asset per day, enforced by the database. A second check on
    # the same item on the same day therefore cannot be a second row, and
    # inserting one answered 500 — which is what the third acceptance round
    # hit: "internal server error on pass or fail entries". Two cases, and
    # neither of them is a crash.
    inspection = (await db.execute(
        select(AssetInspection).where(
            AssetInspection.asset_id == asset.id,
            AssetInspection.due_on == when,
        )
    )).scalar_one_or_none()

    if inspection is not None and inspection.result in ("passed", "failed"):
        # Already checked today, and a recorded result is not overwritten —
        # the register is the evidence that it was looked at and what was
        # found. Said plainly rather than as a server error.
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            f"{asset.asset_no} was already checked on {when:%-d %B %Y} and the "
            f"result was recorded as {inspection.result}. A result on the "
            f"register is not replaced; record the next check on its own day.",
        )

    if inspection is None:
        # Nothing owed today — this is somebody walking past and looking at it,
        # which is a check. The occurrence is made and recorded in one breath.
        inspection = AssetInspection(
            id=uuid.uuid4(),
            asset_id=asset.id,
            due_on=when,
            result="due",
        )
        db.add(inspection)
        await db.flush()

    await record_inspection(
        db, inspection, result=result, notes=notes,
        submission_id=submission_id, user_id=user_id, performed_on=when,
        action_draft=action_draft)

    # Checking early moves the cycle rather than leaving a second check owed
    # next week for something just looked at.
    if asset.inspection_frequency:
        asset.next_due_on = next_due(when, asset.inspection_frequency)
    await db.flush()
    return inspection


async def status_event(db: AsyncSession, asset: SafetyAsset, new_status: str, *,
                       by_id: uuid.UUID | None, reason: str | None,
                       action_id: uuid.UUID | None = None) -> AssetStatusEvent:
    """One row of the item's history: out of service or back, when, by whom, why."""
    row = AssetStatusEvent(
        id=uuid.uuid4(), asset_id=asset.id, status=new_status,
        at=datetime.now(timezone.utc), by_id=by_id,
        by_name=(await db.execute(select(User.full_name).where(User.id == by_id))).scalar()
        if by_id else None,
        reason=reason, action_id=action_id)
    db.add(row)
    await db.flush()
    return row


async def _raise_repair_action(db: AsyncSession, asset: SafetyAsset, inspection,
                               *, user_id: uuid.UUID, notes: str | None,
                               draft: dict | None = None) -> None:
    """The action a failed check raises. ★ Round 12, TC-19: as the person
    recording it filled it in, when they did (the same form as incidents);
    otherwise the default below. Never a second one while one is open."""
    from app.crud.action import OPEN_STATUSES, create as create_action
    from app.models.action import Action
    from app.schemas.action import ActionCreate
    already = (await db.execute(select(Action.id).where(
        Action.asset_id == asset.id, Action.status.in_(OPEN_STATUSES)).limit(1))).first()
    if already:
        return
    if draft:
        action = await create_action(db, ActionCreate(
            source_type="asset", source_id=asset.id, title=draft["title"],
            description=draft.get("description"), owner_id=draft["owner_id"],
            due_date=draft["due_date"], priority=draft.get("priority") or "high",
            hierarchy_of_control=draft.get("hierarchy_of_control")),
            source_ref=asset.asset_no, raised_by=user_id)
        action.asset_id = asset.id
        await db.flush()
        return
    action = await create_action(db, ActionCreate(
        source_type="asset", source_id=asset.id,
        title=f"Repair or replace {asset.name} — failed its check",
        description=(f"{asset.asset_no} {asset.name}"
                     + (f" at {asset.location_path}" if asset.location_path else "")
                     + f" failed its check on {inspection.performed_on:%-d %b %Y}"
                     + (f": {notes}" if notes else ".")
                     + " It is out of service until this is verified."),
        owner_id=asset.assigned_user_id or user_id,
        due_date=plant_today() + timedelta(days=7),
        priority="high"),
        source_ref=asset.asset_no, raised_by=user_id)
    action.asset_id = asset.id
    await db.flush()


async def back_in_service_if_done(db: AsyncSession, action, *, user_id: uuid.UUID) -> None:
    """★ Round 10 (open box): "when the safety equipment capa has been completed
    and verified, put the equipment back into service and log the date and time
    and by whom." Only once every corrective action on the item is closed (or
    cancelled) — one fixed with another still open is not fixed (review,
    uniops-8b).

    Called on an effective verification. Actions cannot be cancelled today
    (the update takes open, in progress or awaiting verification only); if
    that is added, call this there too — but a cancelled action is not a
    repair, so require at least one action on the item to have been verified
    before putting it back automatically."""
    from app.crud.action import OPEN_STATUSES
    from app.models.action import Action
    if action.asset_id is None:
        return
    asset = await db.get(SafetyAsset, action.asset_id)
    if asset is None or asset.status != "out_of_service":
        return
    still = (await db.execute(select(Action.id).where(
        Action.asset_id == asset.id, Action.status.in_(OPEN_STATUSES),
        Action.id != action.id).limit(1))).first()
    if still:
        return
    await return_to_service(
        db, asset, user_id=user_id, action_id=action.id,
        reason=f"Corrective action {action.action_no} verified")


async def return_to_service(db: AsyncSession, asset: SafetyAsset, *,
                            user_id: uuid.UUID | None = None, reason: str | None = None,
                            action_id: uuid.UUID | None = None) -> SafetyAsset:
    """Put a repaired or replaced item back, and restart its cycle."""
    if asset.status == "retired":
        raise HTTPException(
            status.HTTP_409_CONFLICT, "A retired item is not returned to service")
    if asset.status != "in_service":
        await status_event(db, asset, "in_service", by_id=user_id, reason=reason,
                           action_id=action_id)
    asset.status = "in_service"
    if asset.inspection_frequency:
        asset.next_due_on = next_due(plant_today(), asset.inspection_frequency)
    await db.flush()
    return asset


async def retire(db: AsyncSession, asset: SafetyAsset, *,
                 user_id: uuid.UUID, reason: str | None) -> SafetyAsset:
    """Round 12, Q3: retire, applied to safety items too ("Include this function
    on all equipment and safety items").

    The checks it was still owed go with it — a retired extinguisher left with
    an open "inspect this" task is a task nobody can complete. Checks already
    done stay: they are its history.
    """
    if asset.status == "retired":
        return asset
    owed = list((await db.execute(select(AssetInspection).where(
        AssetInspection.asset_id == asset.id, AssetInspection.result == "due"))).scalars())
    # ★ Not stamped with whoever retired it: the task history then read as if
    # they had done the check (B2 review ④). Closed by the system, and says why.
    note = f"Closed without a check: the item was retired on {plant_today():%Y-%m-%d}" + (
        f" ({reason})" if reason else "") + "."
    for inspection in owed:
        await task_service.close_tasks_for(
            db, doc_type=DOC_ASSET, doc_id=inspection.id, completed_by=None, note=note)
        await db.delete(inspection)
    asset.status = "retired"
    asset.next_due_on = None
    await status_event(db, asset, "retired", by_id=user_id, reason=reason)
    await db.flush()
    return asset


async def reinstate(db: AsyncSession, asset: SafetyAsset, *,
                    user_id: uuid.UUID, reason: str | None) -> SafetyAsset:
    """Undo a retirement made by mistake. Back in service, on its cycle again."""
    if asset.status != "retired":
        return asset
    asset.status = "in_service"
    await status_event(db, asset, "in_service", by_id=user_id, reason=reason)
    if asset.inspection_frequency:
        asset.next_due_on = next_due(plant_today(), asset.inspection_frequency)
    await db.flush()
    return asset


async def references(db: AsyncSession, asset: SafetyAsset) -> list[tuple[str, int]]:
    """What would lose this item if it were deleted. Checks still only owed are
    not history and do not count; a check done, a status change, a corrective
    action or a form filled in about it does."""
    from sqlalchemy import text
    out = []
    for what, sql in (
        ("checks recorded", "select count(*) from ehs_asset_inspections"
                            " where asset_id = :id and result <> 'due'"),
        ("status changes", "select count(*) from ehs_asset_status_events where asset_id = :id"),
        ("corrective actions", "select count(*) from ehs_actions where asset_id = :id"),
        ("forms", "select count(*) from ehs_form_submissions"
                  " where source_type = 'asset' and source_id = :id"),
    ):
        n = (await db.execute(text(sql), {"id": asset.id})).scalar() or 0
        if n:
            out.append((what, n))
    return out


async def delete(db: AsyncSession, asset: SafetyAsset, *, user_id: uuid.UUID) -> None:
    used = await references(db, asset)
    if used:
        named = ", ".join(f"{n} {what}" for what, n in used)
        raise HTTPException(status.HTTP_409_CONFLICT,
                            f"{asset.name} has {named}. Retire it instead — it keeps "
                            "that history.")
    owed = list((await db.execute(select(AssetInspection).where(
        AssetInspection.asset_id == asset.id))).scalars())
    for inspection in owed:
        await task_service.close_tasks_for(
            db, doc_type=DOC_ASSET, doc_id=inspection.id, completed_by=user_id)
    await db.delete(asset)
    await db.flush()


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


async def out_of_service(db: AsyncSession) -> list[dict]:
    """Items out of service, and since when (the latest time each went out)."""
    since = (select(AssetStatusEvent.asset_id, func.max(AssetStatusEvent.at).label("at"))
             .where(AssetStatusEvent.status == "out_of_service")
             .group_by(AssetStatusEvent.asset_id).subquery())
    rows = (await db.execute(
        select(SafetyAsset, since.c.at).outerjoin(since, since.c.asset_id == SafetyAsset.id)
        .where(SafetyAsset.status == "out_of_service")
        .order_by(SafetyAsset.location_path.nulls_last(), SafetyAsset.name))).all()
    return [{"id": a.id, "asset_no": a.asset_no, "name": a.name,
             "location_path": a.location_path, "since": at} for a, at in rows]
