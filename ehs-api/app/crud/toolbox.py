"""Toolbox talks: publish a month's topic, deliver it, collect the signatures."""
import uuid
from datetime import date, datetime, timedelta, timezone

from fastapi import HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.crud.incident import _resolve_name
from app.models.mirrors import Department, User
from app.models.toolbox import TbtDelivery, TbtSignature, ToolboxTalk
from app.services import tasks as task_service
from app.services.numbering import next_number

# HSE: "Biweekly email reminders should be sent until the topic has been signed
# off as completed."
REMINDER_EVERY_DAYS = 14


def month_start(day: date) -> date:
    return day.replace(day=1)


async def list_talks(db: AsyncSession, *, limit: int = 24) -> list[ToolboxTalk]:
    return list((await db.execute(
        select(ToolboxTalk).order_by(ToolboxTalk.period_month.desc()).limit(limit)
    )).scalars().all())


async def get_talk(db: AsyncSession, talk_id: uuid.UUID) -> ToolboxTalk:
    t = await db.get(ToolboxTalk, talk_id)
    if t is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Toolbox talk not found")
    return t


async def create_talk(
    db: AsyncSession, *, title: str, topic: str | None, period_month: date,
    creator_id: uuid.UUID,
) -> ToolboxTalk:
    period = month_start(period_month)
    clash = (await db.execute(
        select(ToolboxTalk).where(ToolboxTalk.period_month == period))).scalar_one_or_none()
    if clash is not None:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            f"There is already a toolbox talk for {period:%B %Y} ({clash.talk_no})",
        )
    talk = ToolboxTalk(
        id=uuid.uuid4(), talk_no=await next_number(db, "toolbox_talk"),
        title=title, topic=topic, period_month=period, created_by=creator_id,
    )
    db.add(talk)
    await db.flush()
    return talk


async def publish_talk(db: AsyncSession, talk: ToolboxTalk) -> list[TbtDelivery]:
    """Hand the talk to every supervisor, and put it in their to-do list.

    A delivery per supervisor rather than one shared record: each team meets
    separately, and completion is measured per team. Idempotent — publishing
    twice does not double anyone's list.
    """
    supervisors = (await db.execute(
        select(User).where(User.is_active.is_(True), User.role.in_(
            ("area_supervisor", "dept_manager", "supervisor")))
    )).scalars().all()

    existing = {
        d.supervisor_id for d in (await db.execute(
            select(TbtDelivery).where(TbtDelivery.talk_id == talk.id))).scalars().all()
    }

    created: list[TbtDelivery] = []
    for sup in supervisors:
        if sup.id in existing:
            continue
        dept_name = (await db.execute(
            select(Department.name).where(Department.id == sup.department_id))).scalar()
        delivery = TbtDelivery(
            id=uuid.uuid4(), talk_id=talk.id, supervisor_id=sup.id,
            supervisor_name=sup.full_name, department_id=sup.department_id,
            department_name=dept_name,
        )
        db.add(delivery)
        await db.flush()
        created.append(delivery)

        await task_service.open_task(
            db,
            task_type=task_service.TASK_TOOLBOX_TALK,
            doc_type="ehs_tbt",
            doc_id=delivery.id,
            doc_number=talk.talk_no,
            title=f"Toolbox talk: {talk.title}",
            assigned_role="area_supervisor",
            assigned_user_id=sup.id,
            description=f"Discuss with your team during {talk.period_month:%B %Y} "
                        "and collect signatures.",
            priority="normal",
        )

    talk.is_published = True
    await db.flush()
    return created


async def get_delivery(db: AsyncSession, delivery_id: uuid.UUID) -> TbtDelivery:
    d = await db.get(TbtDelivery, delivery_id)
    if d is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Delivery not found")
    return d


async def list_deliveries(
    db: AsyncSession, *, talk_id: uuid.UUID | None = None,
    supervisor_id: uuid.UUID | None = None, status_in: list[str] | None = None,
) -> list[TbtDelivery]:
    q = select(TbtDelivery)
    if talk_id:
        q = q.where(TbtDelivery.talk_id == talk_id)
    if supervisor_id:
        q = q.where(TbtDelivery.supervisor_id == supervisor_id)
    if status_in:
        q = q.where(TbtDelivery.status.in_(status_in))
    return list((await db.execute(q.order_by(TbtDelivery.created_at))).scalars().all())


async def signatures_for(db: AsyncSession, delivery_id: uuid.UUID) -> list[TbtSignature]:
    return list((await db.execute(
        select(TbtSignature).where(TbtSignature.delivery_id == delivery_id)
        .order_by(TbtSignature.signed_at)
    )).scalars().all())


async def add_signatures(
    db: AsyncSession, delivery: TbtDelivery, people: list[dict], *, held_at: datetime | None,
) -> TbtDelivery:
    """Record the meeting: everyone who was there signs, on the one device.

    Signatures accumulate rather than replace — a latecomer signs afterwards
    without wiping the rest — and someone who has already signed is skipped
    rather than duplicated.
    """
    if delivery.status == "delivered" and held_at:
        raise HTTPException(
            status.HTTP_409_CONFLICT, "This talk has already been signed off")

    already = {s.user_id for s in await signatures_for(db, delivery.id) if s.user_id}

    for person in people:
        user_id = person.get("user_id")
        if user_id and uuid.UUID(str(user_id)) in already:
            continue
        db.add(TbtSignature(
            id=uuid.uuid4(), delivery_id=delivery.id,
            user_id=uuid.UUID(str(user_id)) if user_id else None,
            person_name=person["person_name"],
            person_role=person.get("person_role"),
            signature=person["signature"],
        ))

    await db.flush()
    delivery.signed_count = (await db.execute(
        select(func.count()).select_from(TbtSignature)
        .where(TbtSignature.delivery_id == delivery.id))).scalar() or 0

    if held_at:
        delivery.held_at = held_at
        delivery.status = "delivered"
        await task_service.close_tasks_for(db, doc_type="ehs_tbt", doc_id=delivery.id)

    await db.flush()
    return delivery


async def chase_outstanding(db: AsyncSession, *, today: date | None = None) -> list[TbtDelivery]:
    """Fortnightly nudge for talks not yet signed off.

    Driven by `last_reminded_on` rather than by day-of-month arithmetic: the
    sweep runs several times an hour, and anything based on "is it the 1st or
    the 15th" fires repeatedly on those days and never on the others.
    """
    today = today or datetime.now(timezone.utc).date()
    cutoff = today - timedelta(days=REMINDER_EVERY_DAYS)

    due = (await db.execute(
        select(TbtDelivery).where(
            TbtDelivery.status == "pending",
            (TbtDelivery.last_reminded_on.is_(None)) | (TbtDelivery.last_reminded_on <= cutoff),
        )
    )).scalars().all()

    for delivery in due:
        delivery.last_reminded_on = today

    await db.flush()
    return list(due)


async def completion(db: AsyncSession, talk_id: uuid.UUID) -> dict:
    """The number the management dashboard shows."""
    rows = (await db.execute(
        select(TbtDelivery.status, func.count())
        .where(TbtDelivery.talk_id == talk_id).group_by(TbtDelivery.status)
    )).all()
    counts = {s: n for s, n in rows}
    total = sum(counts.values())
    delivered = counts.get("delivered", 0)
    return {
        "total": total,
        "delivered": delivered,
        "pending": counts.get("pending", 0),
        "percent": round(delivered * 100 / total) if total else 0,
    }
