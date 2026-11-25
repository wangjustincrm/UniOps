"""Issuing, running and closing a work permit.

The whole of this module is one idea: a permit is a set of conditions, and it
is only valid while every one of them holds. So `issue` refuses rather than
warns, and `effective` recomputes rather than trusts.

What issue refuses on, and why each one:

* **an unticked precaution** — the list is the reason the permit exists;
* **a failing gas reading** — the standard decided, not the entrant;
* **a stale gas reading** — a two-hour-old test does not describe the space now;
* **a missing signature** — the capacities on the type are legal
  responsibilities, and a permit issued without an attendant named has nobody
  outside the space;
* **no end time** — a permit that never expires is not a permit.
"""
from __future__ import annotations

import secrets
import uuid
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.mirrors import Department, Location, User
from app.models.permit import Permit, PermitGasReading, PermitSignature, PermitType
from app.services import permit as rules
from app.services import tasks as task_service
from app.services.numbering import next_number
from app.services.day_range import plant_today

DOC_PERMIT = "ehs_ptw"


def _new_qr_token() -> str:
    return secrets.token_urlsafe(24)


async def _name(db: AsyncSession, user_id: uuid.UUID | None) -> str | None:
    if user_id is None:
        return None
    return (await db.execute(select(User.full_name).where(User.id == user_id))).scalar()


async def _place(db: AsyncSession, location_id: uuid.UUID | None) -> tuple:
    if location_id is None:
        return None, None, None
    loc = await db.get(Location, location_id)
    if loc is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No such plant area")
    dept = (await db.execute(
        select(Department.name).where(Department.id == loc.department_id))).scalar()
    return loc.path, loc.department_id, dept


# ── Types ────────────────────────────────────────────────────────────────────

async def list_types(db: AsyncSession, *, active_only: bool = True) -> list[PermitType]:
    stmt = select(PermitType)
    if active_only:
        stmt = stmt.where(PermitType.is_active.is_(True))
    return list((await db.execute(stmt.order_by(PermitType.name))).scalars())


async def get_type(db: AsyncSession, type_id: uuid.UUID) -> PermitType:
    row = await db.get(PermitType, type_id)
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Permit type not found")
    return row


# ── Permits ──────────────────────────────────────────────────────────────────

def effective(permit: Permit, now: datetime | None = None) -> str:
    return rules.effective_status(permit.status, permit.valid_to, now, permit.valid_from)


async def list_permits(
    db: AsyncSession, *, status_in: list[str] | None = None,
    location_id: uuid.UUID | None = None, requester_id: uuid.UUID | None = None,
    active_now: bool = False, limit: int = 200,
) -> list[Permit]:
    stmt = select(Permit)
    if location_id:
        stmt = stmt.where(Permit.location_id == location_id)
    if requester_id:
        stmt = stmt.where(Permit.requested_by == requester_id)
    rows = list((await db.execute(
        stmt.order_by(Permit.created_at.desc()).limit(limit))).scalars())

    # Filtered on the computed status, not the stored one — asking for what is
    # live right now must not return a permit that ran out an hour ago just
    # because the sweep has not been round yet.
    if active_now:
        rows = [p for p in rows if effective(p) == "issued"]
    if status_in:
        rows = [p for p in rows if effective(p) in status_in]
    return rows


async def get_permit(db: AsyncSession, permit_id: uuid.UUID) -> Permit:
    permit = await db.get(Permit, permit_id)
    if permit is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Permit not found")
    return permit


async def by_qr_token(db: AsyncSession, token: str) -> Permit | None:
    return (await db.execute(
        select(Permit).where(Permit.qr_token == token))).scalar_one_or_none()


async def _lists(db: AsyncSession, fields: dict, permit: Permit | None = None) -> None:
    """Round 12, PMT-01: the picks, snapshotted; those the permit already had
    keep their wording (B3/B4 review M2)."""
    from app.api.v1.risk import _snapshot
    for ids_key, out_key, vocabulary in (("hazard_ids", "hazard_items", "hazard"),
                                         ("control_ids", "control_items", "hazard_control")):
        ids = fields.pop(ids_key, None)
        if ids is not None:
            fields[out_key] = await _snapshot(db, vocabulary, ids,
                                              getattr(permit, out_key) if permit else None)


async def create_permit(db: AsyncSession, payload: dict, *, requester_id: uuid.UUID) -> Permit:
    payload = dict(payload)
    await _lists(db, payload)
    permit_type = await get_type(db, payload["type_id"])
    path, dept_id, dept_name = await _place(db, payload.get("location_id"))
    from app.services import equipment_ref
    await equipment_ref.check(db, payload.get("equipment_id"))

    permit = Permit(
        id=uuid.uuid4(),
        permit_no=await next_number(db, "permit"),
        type_id=permit_type.id,
        type_label=permit_type.name,
        work_description=payload["work_description"],
        location_id=payload.get("location_id"),
        location_path=path, department_id=dept_id, department_name=dept_name,
        equipment_id=payload.get("equipment_id"),
        jsa_id=payload.get("jsa_id"),
        contractor_company=payload.get("contractor_company"),
        requested_by=requester_id,
        requested_by_name=await _name(db, requester_id),
        valid_from=payload.get("valid_from"),
        valid_to=payload.get("valid_to"),
        # ★ Named explicitly: this constructor copies fields one by one, and a
        # column left out is silently dropped on create (findings, round 5).
        hazard_items=payload.get("hazard_items") or [],
        control_items=payload.get("control_items") or [],
        qr_token=_new_qr_token(),
    )
    db.add(permit)
    await db.flush()
    return permit


async def update_permit(db: AsyncSession, permit: Permit, fields: dict) -> Permit:
    if effective(permit) not in ("draft", "suspended"):
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            f"{permit.permit_no} is {effective(permit)} and cannot be edited. "
            "Close it and raise a new one.")
    fields = dict(fields)
    await _lists(db, fields, permit)
    if "location_id" in fields:
        (permit.location_path, permit.department_id,
         permit.department_name) = await _place(db, fields["location_id"])
    if "equipment_id" in fields:
        from app.services import equipment_ref
        await equipment_ref.check(db, fields["equipment_id"], permit.equipment_id)
    for key, value in fields.items():
        setattr(permit, key, value)
    await db.flush()
    return permit


# ── Gas readings ─────────────────────────────────────────────────────────────

async def readings_for(db: AsyncSession, permit_id: uuid.UUID) -> list[PermitGasReading]:
    return list((await db.execute(
        select(PermitGasReading).where(PermitGasReading.permit_id == permit_id)
        .order_by(PermitGasReading.taken_at.desc()))).scalars())


async def latest_reading(db: AsyncSession, permit_id: uuid.UUID) -> PermitGasReading | None:
    rows = await readings_for(db, permit_id)
    return rows[0] if rows else None


async def record_reading(
    db: AsyncSession, permit: Permit, payload: dict, *, user_id: uuid.UUID,
) -> PermitGasReading:
    """Log a test. The verdict is computed here, never taken from the caller."""
    if effective(permit) in ("closed", "cancelled"):
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            f"{permit.permit_no} is {effective(permit)}: its gas tests are part of the record now")
    permit_type = await get_type(db, permit.type_id) if permit.type_id else None
    def dec(value) -> Decimal | None:
        return Decimal(str(value)) if value is not None else None

    verdict = rules.assess_gas(
        oxygen_pct=dec(payload.get("oxygen_pct")),
        lel_pct=dec(payload.get("lel_pct")),
        h2s_ppm=dec(payload.get("h2s_ppm")),
        co_ppm=dec(payload.get("co_ppm")),
        require_all=rules.requires_all_gases(permit_type.code if permit_type else None),
    )
    reading = PermitGasReading(
        id=uuid.uuid4(), permit_id=permit.id,
        taken_at=payload.get("taken_at") or datetime.now(timezone.utc),
        taken_by=user_id, taken_by_name=await _name(db, user_id),
        oxygen_pct=dec(payload.get("oxygen_pct")),
        lel_pct=dec(payload.get("lel_pct")),
        h2s_ppm=dec(payload.get("h2s_ppm")),
        co_ppm=dec(payload.get("co_ppm")),
        other_note=payload.get("other_note"),
        instrument=payload.get("instrument"),
        is_acceptable=verdict.acceptable,
        failed=verdict.failed,
    )
    db.add(reading)
    await db.flush()

    # A failing test on a permit that is already live stops the work now. That
    # is the entire point of continuous monitoring, and leaving it to somebody
    # to notice and press a button is the failure mode.
    if not verdict.acceptable and effective(permit) in ("issued", "scheduled"):
        permit.status = "suspended"
        permit.suspended_reason = "; ".join(verdict.failed)[:300]
        await db.flush()

    return reading


# ── Signatures ───────────────────────────────────────────────────────────────

async def signatures_for(
    db: AsyncSession, permit_id: uuid.UUID, *, phase: str | None = None
) -> list[PermitSignature]:
    stmt = select(PermitSignature).where(PermitSignature.permit_id == permit_id)
    if phase:
        stmt = stmt.where(PermitSignature.phase == phase)
    return list((await db.execute(stmt.order_by(PermitSignature.signed_at))).scalars())


async def sign(
    db: AsyncSession, permit: Permit, payload: dict, *, phase: str,
) -> PermitSignature:
    row = PermitSignature(
        id=uuid.uuid4(), permit_id=permit.id, phase=phase,
        capacity=payload["capacity"],
        user_id=payload.get("user_id"),
        person_name=payload["person_name"],
        person_role=payload.get("person_role"),
        external_company=payload.get("external_company"),
        signature=payload.get("signature"),
        signed_on=plant_today(),
    )
    db.add(row)
    await db.flush()
    return row


# ── The gate ─────────────────────────────────────────────────────────────────

async def blockers(db: AsyncSession, permit: Permit) -> list[str]:
    """Everything standing between this permit and being issued.

    All of them at once rather than the first: somebody preparing a confined
    space entry should be told the whole list, not sent round the loop five
    times.
    """
    problems: list[str] = []
    permit_type = await get_type(db, permit.type_id) if permit.type_id else None

    if permit.valid_to is None:
        problems.append("No end time. A permit that never expires is not a permit.")
    elif permit.valid_from and permit.valid_to <= permit.valid_from:
        problems.append("The end time is not after the start time.")
    elif permit_type:
        span = permit.valid_to - (permit.valid_from or datetime.now(timezone.utc))
        if span > timedelta(hours=permit_type.max_duration_hours):
            problems.append(
                f"{permit_type.name} may not run longer than "
                f"{permit_type.max_duration_hours} hours.")

    if permit.valid_to is not None and permit.valid_to <= datetime.now(timezone.utc):
        # Issued after its own end, it was expired the moment it was signed —
        # and the record said somebody authorised work that had already run.
        problems.append("The end time has passed — set a new one before issuing.")

    if permit_type:
        for label in rules.missing_precautions(
            permit_type.precautions, permit.precautions_confirmed or {}
        ):
            problems.append(f"Not confirmed: {label}")

        signed = {s.capacity for s in await signatures_for(db, permit.id, phase="issue")}
        for capacity in permit_type.required_signatures or []:
            if capacity not in signed:
                problems.append(f"No signature from the {capacity.replace('_', ' ')}.")

        if permit_type.requires_gas_test:
            reading = await latest_reading(db, permit.id)
            if reading is None:
                problems.append("No atmospheric test recorded.")
            elif not reading.is_acceptable:
                problems.append("The last gas test failed: "
                                + "; ".join(reading.failed or ["not acceptable"]))
            elif rules.requires_all_gases(permit_type.code) and (missing := rules.unmeasured(
                    oxygen_pct=reading.oxygen_pct, lel_pct=reading.lel_pct,
                    h2s_ppm=reading.h2s_ppm, co_ppm=reading.co_ppm)):
                # A test recorded before all four were asked for.
                problems.append("The last gas test is not complete: " + "; ".join(missing))
            elif not rules.reading_is_fresh(reading.taken_at):
                problems.append(
                    f"The last gas test is more than "
                    f"{rules.GAS_READING_VALID_MINUTES} minutes old — retest before entry.")

    return problems


async def issue(db: AsyncSession, permit: Permit, *, user_id: uuid.UUID) -> Permit:
    if effective(permit) in ("issued", "scheduled"):
        return permit
    if effective(permit) not in ("draft", "suspended"):
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            f"{permit.permit_no} is {effective(permit)} and cannot be issued")

    problems = await blockers(db, permit)
    if problems:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            "This permit cannot be issued yet:\n• " + "\n• ".join(problems))

    # ★ Round 7: the "before work starts" list is now HSE's to edit. A permit
    # records the wording it was issued against — {key: label} where it held
    # {key: true} — so changing the list later does not rewrite what an issued
    # or closed permit says was confirmed.
    permit_type = await get_type(db, permit.type_id) if permit.type_id else None
    if permit_type:
        confirmed = dict(permit.precautions_confirmed or {})
        for item in permit_type.precautions or []:
            if isinstance(item, dict) and confirmed.get(item.get("key")):
                confirmed[item["key"]] = item.get("label") or item["key"]
        permit.precautions_confirmed = confirmed

    permit.status = "issued"
    permit.suspended_reason = None
    permit.issued_at = datetime.now(timezone.utc)
    permit.issued_by = user_id
    permit.issued_by_name = await _name(db, user_id)
    permit.valid_from = permit.valid_from or permit.issued_at
    await db.flush()

    # A task on the issuer, so an open permit is visible in somebody's list
    # rather than only on a board in the workshop.
    await task_service.open_task(
        db,
        task_type=task_service.TASK_CLOSE_PERMIT,
        doc_type=DOC_PERMIT,
        doc_id=permit.id,
        doc_number=permit.permit_no,
        title=f"Close when finished: {permit.type_label}",
        assigned_role="area_supervisor",
        assigned_user_id=user_id,
        description=f"{permit.work_description[:200]} — expires "
                    f"{permit.valid_to.isoformat(timespec='minutes') if permit.valid_to else 'unset'}",
        priority="high",
    )
    return permit


async def suspend(db: AsyncSession, permit: Permit, *, reason: str) -> Permit:
    if effective(permit) not in ("issued", "scheduled"):
        raise HTTPException(
            status.HTTP_409_CONFLICT, "Only an issued permit can be suspended")
    permit.status = "suspended"
    permit.suspended_reason = reason
    await db.flush()
    return permit


async def close(
    db: AsyncSession, permit: Permit, *, user_id: uuid.UUID, note: str,
    signature: dict | None = None,
) -> Permit:
    """Hand the area back.

    Closing needs a signature, because what is being attested is that everybody
    is out and the area is safe — and an unsigned close is a permit that was
    left open and quietly tidied away.
    """
    if permit.status in ("closed", "cancelled"):
        raise HTTPException(status.HTTP_409_CONFLICT, f"{permit.permit_no} is already closed")
    if permit.status == "draft":
        # Nothing was authorised, so there is nothing to hand back — and a
        # closed permit nobody ever issued would read as work that was done.
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            f"{permit.permit_no} has not been issued, so there is nothing to close")

    if signature:
        await sign(db, permit, signature, phase="close")
    if not await signatures_for(db, permit.id, phase="close"):
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            "Closing needs a signature — it attests that everyone is out and the "
            "area has been left safe.")

    permit.status = "closed"
    permit.closed_at = datetime.now(timezone.utc)
    permit.closed_by = user_id
    permit.closed_by_name = await _name(db, user_id)
    permit.closeout_note = note
    await task_service.close_tasks_for(
        db, doc_type=DOC_PERMIT, doc_id=permit.id, completed_by=user_id)
    await db.flush()
    return permit


async def expire_lapsed(db: AsyncSession, *, now: datetime | None = None) -> list[Permit]:
    """Reconcile the stored status with the clock.

    Not what makes a permit expire — `effective_status` already does that on
    every read. This writes the change down so the register agrees with what
    everybody has been shown, and raises the task that says an expired permit
    was never closed out, which is a finding in its own right.
    """
    now = now or datetime.now(timezone.utc)
    lapsed = (await db.execute(
        select(Permit).where(
            Permit.status.in_(("issued", "suspended")),
            Permit.valid_to.is_not(None),
            Permit.valid_to < now,
        ))).scalars().all()

    for permit in lapsed:
        permit.status = "expired"
        await task_service.open_task(
            db,
            task_type=task_service.TASK_CLOSE_PERMIT,
            doc_type=DOC_PERMIT,
            doc_id=permit.id,
            doc_number=permit.permit_no,
            title=f"Expired without being closed: {permit.type_label}",
            assigned_role="ehs_manager",
            description=f"{permit.location_path or 'Unrecorded area'} — expired "
                        f"{permit.valid_to.isoformat(timespec='minutes')}. "
                        "Confirm the area was left safe.",
            priority="high",
        )
    await db.flush()
    return list(lapsed)
