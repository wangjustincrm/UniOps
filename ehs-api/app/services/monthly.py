"""What a supervisor owes this month, and next.

Round 5, answer box 11: "It is one of each per supervisor per calendar month,
reset on the 1st. an unmet item in a month is simply missed. a hazard assessment
is a hazard register." And round 4, TC-08: "supervisors require a tbt, hazard
assessment and a near miss to be completed each month. on the my action reference
page provide them a way to see what is due this month and due next month."

★ Derived from the records, not stored. No task is raised and no obligation row
is written. HSE's own words settle it: an unmet month is simply missed — so there
is nothing to chase after the 1st, nothing to close, and nothing that can outlive
the month it belonged to. "Reset on the 1st" is not an operation; it is a
different month being asked about. Raising three tasks per supervisor per month
would build exactly the pile of things nobody can cleanly close that this module
keeps having to clean up.

★ Counted by when the thing was *recorded*, not by the date written on it. A
hazard back-dated to last month, recorded today, is this month's assessment — if
it counted for last month, recording late would quietly "catch up" a month HSE
has said is simply missed. The same for a near miss: its submission, not its
occurrence.

★ Months are Ontario months. The dashboard once drew its year boundary at
midnight UTC — 8pm here — and a report filed on the evening of the last day
landed in the next period.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import date, datetime, time

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.hazard import Hazard
from app.models.incident import Incident
from app.models.toolbox import TbtDelivery, ToolboxTalk
from app.services import notify, statutory
from app.services.tasks import DOC_PATHS

# Who owes the three. The same set toolbox talks are handed to, read from one
# place, so the people a talk goes to and the people asked for it are the same.
from app.crud.toolbox import SUPERVISORY_ROLES


@dataclass
class Item:
    key: str
    label: str
    done: bool
    detail: str
    # Where to go to do it, or to see it done.
    path: str
    count: int = 0


@dataclass
class Month:
    month: str          # yyyy-mm
    label: str          # "October 2026"
    is_current: bool
    items: list[Item] = field(default_factory=list)


def _month_start(d: date) -> date:
    return d.replace(day=1)


def _next_month(d: date) -> date:
    return (d.replace(day=28) + __import__("datetime").timedelta(days=4)).replace(day=1)


def _bounds(first: date) -> tuple[datetime, datetime]:
    """The month as two instants, in Ontario time."""
    start = datetime.combine(first, time.min, tzinfo=statutory.ONTARIO)
    end = datetime.combine(_next_month(first), time.min, tzinfo=statutory.ONTARIO)
    return start, end


async def is_supervisor(db: AsyncSession, user_id: uuid.UUID, role: str) -> bool:
    held = await notify.roles_held_by(db, user_id, role)
    return bool(held & set(SUPERVISORY_ROLES))


async def _toolbox(db: AsyncSession, user_id: uuid.UUID, first: date) -> Item:
    # ★ A month can hold more than one talk: period_month is not unique, and
    # HSE may republish, correct or add one, all landing on the 1st. Box 11 says
    # one per month, so any delivered talk meets it; otherwise point at the
    # earliest one still open. created_at breaks the tie, so the answer does not
    # depend on which row the database happens to return first.
    # ★ Review U2 (UAT 15, K): a talk held again belongs to the month it was
    # held, as the tracker counts it — one clause, shared, so they agree.
    from app.crud.toolbox import _in_month
    rows = (await db.execute(
        select(TbtDelivery, ToolboxTalk)
        .join(ToolboxTalk, ToolboxTalk.id == TbtDelivery.talk_id)
        .where(TbtDelivery.supervisor_id == user_id, _in_month(first))
        .order_by(ToolboxTalk.period_month, ToolboxTalk.created_at,
                  TbtDelivery.id))).all()
    if not rows:
        # ★ Not the supervisor's miss, and said so. HSE publishes the topic each
        # month; until they have, there is nothing for a supervisor to hold.
        return Item("toolbox_talk", "Toolbox talk", False,
                    "No talk has been published for this month yet.", "/toolbox-talks")
    held = [r for r in rows if r[0].status == "delivered"]
    delivery, talk = held[0] if held else rows[0]
    done = bool(held)
    template = DOC_PATHS.get("ehs_tbt", "/toolbox-talks")
    return Item(
        "toolbox_talk", "Toolbox talk", done,
        (f"“{talk.title}” — held and signed." if done
         else f"“{talk.title}” — hold it with your crew and collect signatures."),
        template.format(id=delivery.id) if "{id}" in template else template,
    )


async def _hazards(db: AsyncSession, user_id: uuid.UUID, first: date) -> Item:
    start, end = _bounds(first)
    n = (await db.execute(
        select(func.count()).select_from(Hazard)
        .where(Hazard.created_by == user_id,
               Hazard.created_at >= start, Hazard.created_at < end))).scalar() or 0
    return Item(
        "hazard_assessment", "Hazard assessment", n > 0,
        (f"{n} recorded on the hazard register." if n
         else "Record one on the hazard register."),
        "/hazards", count=n)


async def _near_misses(db: AsyncSession, user_id: uuid.UUID, first: date) -> Item:
    """★★ Only ever shown to the person themselves — do not aggregate this.

    An anonymous report keeps `reported_by` for administrative recovery and drops
    only the name (`crud/incident.py`), so a supervisor's own anonymous near miss
    counts here. On their own panel that is right: it is theirs. But a roll-up of
    "which supervisors met the three this month" built on this function would
    reveal who filed every anonymous near miss in the plant, which is the one
    promise the anonymous option makes. Anybody building that report has to count
    anonymous reports in a total and never against a person. Raised in review by
    uniops-41.
    """
    start, end = _bounds(first)
    n = (await db.execute(
        select(func.count()).select_from(Incident)
        .where(Incident.reported_by == user_id,
               Incident.form_kind == "near_miss",
               # Submitted, not drafted: a draft has not been reported to anybody.
               Incident.submitted_at.is_not(None),
               Incident.submitted_at >= start, Incident.submitted_at < end))).scalar() or 0
    return Item(
        "near_miss", "Near miss", n > 0,
        (f"{n} reported." if n else "Report one — a near miss or a good catch."),
        "/report", count=n)


async def for_user(
    db: AsyncSession, user_id: uuid.UUID, role: str, *, today: date | None = None,
) -> list[Month]:
    """This month and next, for a supervisor. Empty for anybody else.

    Empty rather than refused, because My Actions is a page everyone opens and a
    worker has nothing here — which is a fact about them, not an error.
    """
    if not await is_supervisor(db, user_id, role):
        return []
    today = today or datetime.now(statutory.ONTARIO).date()
    out: list[Month] = []
    for first, current in ((_month_start(today), True),
                           (_next_month(_month_start(today)), False)):
        out.append(Month(
            month=first.strftime("%Y-%m"),
            label=first.strftime("%B %Y"),
            is_current=current,
            items=[
                await _toolbox(db, user_id, first),
                await _hazards(db, user_id, first),
                await _near_misses(db, user_id, first),
            ],
        ))
    return out
