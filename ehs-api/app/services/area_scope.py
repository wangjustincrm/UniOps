"""Whose part of the plant a piece of work belongs to.

Work handed to a role rather than to a person reaches everybody holding that
role. For `area_supervisor` that is one plant-wide queue: an unassigned
equipment check appeared on every supervisor's list at once, and a queue
everybody can see is a queue nobody owns.

So a role task is narrowed to the area it happened in. An area is a node of the
`locations` tree and carries its children: somebody named for "103 – Dryer" is
named for Line 1 and Line 2 underneath it, because that is how the plant is
talked about and because listing every leaf goes stale the day a line is added.

Three rules, and the third is the one that matters:

  * Area work goes to whoever is down for the area it happened in, and to
    nobody else.
  * Area work whose area nobody is down for goes to HSE. Not to every
    supervisor — that is the queue this exists to remove — and not to nobody,
    because a task no screen shows is the failure this module is meant to
    prevent. It also surfaces as a count, which is the pressure to finish the
    mapping.
  * ★ HSE gets the unowned work and not the owned. Handing them every area task
    as well would rebuild the same undifferentiated queue on their screen
    instead of the supervisors\', which is not an improvement — it is the same
    mistake one desk along.

★ The same function decides who is emailed. Narrowing the list without
narrowing the notice would mean every supervisor still gets the mail and then
finds nothing on their screen, which is worse than either on its own.
"""
from __future__ import annotations

import uuid

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

# Roles whose work is the whole site rather than a part of it.
#
# ★ A list, because nothing in the data says which roles are area-scoped:
# `area_supervisor` holds the same register permissions the HSE roles do, so no
# permission test tells them apart. Keeping it here, named, is better than
# spreading the assumption across three call sites. When HSE wants to change it
# this is what moves into Settings.
SITE_WIDE_ROLES = frozenset({"ehs_manager", "ehs_coordinator", "system_admin"})

# Roles whose work is narrowed to the areas they are down for. Only these.
#
# ★ Everything else keeps reaching whoever holds the role. That matters more
# than it looks: the statutory clocks are `ehs_manager` tasks hanging off SDS
# revisions, committee recommendations and certificates, none of which have a
# location at all — narrowing those would file every one of them as
# "unattributed" and drown the number that is supposed to mean "somebody still
# has to be named for this part of the plant".
AREA_SCOPED_ROLES = frozenset({"area_supervisor"})

# Which table each kind of task's document lives in. Read as one query per kind
# rather than one per task.
#
# A document with no location column at all is not in here, and its tasks are
# unattributable by definition — a policy to sign belongs to the plant, not to a
# corner of it.
#
# ★ Every one of these is asked for its `location_id` and the path is taken from
# `locations`, never from the `location_path` snapshot beside it. Both sides of
# this comparison are snapshots written at different times: rename "105 –
# Warehouse" in the master data and a document filed last year no longer matches
# an area assigned last week, so the work slides quietly into "unattributed".
# The tree is the live answer; the snapshots are for display, and for reading an
# old record after the tree has moved on.
SOURCE_TABLE: dict[str, str] = {
    "ehs_act": "ehs_actions",
    "ehs_inc": "ehs_incidents",
    "ehs_asset": "ehs_safety_assets",
    "ehs_sds": "ehs_chemicals",
    "ehs_ptw": "ehs_permits",
    "ehs_run": "ehs_form_schedule_runs",
    "ehs_tbt": "ehs_tbt_deliveries",
}


async def areas_for(db: AsyncSession, user_id: uuid.UUID) -> list[str]:
    """The location paths this person is responsible for, as the tree has them
    now — not as they read when the assignment was made."""
    return list((await db.execute(text(
        "select l.path from ehs_area_owners o"
        "  join locations l on l.id = o.location_id"
        " where o.user_id = :uid"), {"uid": user_id})).scalars().all())


def covers(areas: list[str], path: str | None) -> bool:
    """Whether one of these areas contains that location.

    A prefix match on the materialised path with the separator required. The
    separator is not decoration: this plant names siblings by continuation —
    "102 – Pretreatment – Dosing and Tipping" sits beside "102 – Pretreatment –
    Milk Receiving" — so a bare `startswith` would hand somebody named for one
    of them the other one's work as well.
    """
    if not path:
        return False
    return any(path == a or path.startswith(f"{a}/") for a in areas)


async def locations_of(
    db: AsyncSession, wanted: dict[str, set[uuid.UUID]]
) -> dict[uuid.UUID, str | None]:
    """The location path of each document, by id.

    `wanted` maps a task's document type to the document ids of that type. One
    query per type, and one join to `locations` for the two kinds that keep only
    an id.
    """
    out: dict[uuid.UUID, str | None] = {}
    for doc_type, ids in wanted.items():
        table = SOURCE_TABLE.get(doc_type)
        if table is None or not ids:
            continue
        # The live path, through the id. Table names come from the map above,
        # never from a caller.
        sql = (f"select s.id, l.path from {table} s"  # noqa: S608
               "  left join locations l on l.id = s.location_id"
               " where s.id = any(:ids)")
        for doc_id, path in (await db.execute(text(sql), {"ids": list(ids)})).all():
            out[doc_id] = path
    return out


async def split_by_area(
    db: AsyncSession, tasks: list, *, user_id: uuid.UUID, roles: set[str]
) -> tuple[list, list]:
    """Which of these role tasks are this person's, and which belong to nobody.

    Returns the tasks to show, and the unattributed ones. The second list is
    only meaningful to HSE — they are the ones it falls to and the ones who can
    fix the mapping — so it comes back separately and the caller decides what to
    do with it.
    """
    # Named to a person, or given to a role that is not area-scoped: neither is
    # this function's business. A task with somebody's name on it was meant for
    # them wherever it happened.
    passthrough = [
        t for t in tasks
        if t.assigned_user_id is not None or t.assigned_role not in AREA_SCOPED_ROLES
    ]
    role_only = [
        t for t in tasks
        if t.assigned_user_id is None and t.assigned_role in AREA_SCOPED_ROLES
    ]
    if not role_only:
        return passthrough, []

    wanted: dict[str, set[uuid.UUID]] = {}
    for t in role_only:
        wanted.setdefault(t.document_type, set()).add(t.document_id)
    paths = await locations_of(db, wanted)

    # Which areas somebody can actually be reached about.
    #
    # ★ Not simply "has a row in ehs_area_owners". An area assigned to somebody
    # who does not hold an area-scoped role, or whose account has been
    # deactivated, is not covered — they will never be shown its work, because
    # the list only fetches role tasks for holders of that role. Counting them
    # as covered is the exact failure this whole mechanism exists to remove: the
    # area looks owned, so it never falls to HSE, and nobody sees it.
    covered = list((await db.execute(text(
        "select l.path from ehs_area_owners o"
        "  join locations l on l.id = o.location_id"
        "  join users u on u.id = o.user_id and u.is_active"
        " where u.role = any(:roles)"
        "    or exists (select 1 from user_roles ur"
        "                 join role_defs rd on rd.code = ur.role_code and rd.is_active"
        "                where ur.user_id = o.user_id"
        "                  and ur.role_code = any(:roles))"),
        {"roles": list(AREA_SCOPED_ROLES)})).scalars().all())
    mine = await areas_for(db, user_id)
    is_hse = bool(roles & SITE_WIDE_ROLES)

    keep, orphans = list(passthrough), []
    for t in role_only:
        path = paths.get(t.document_id)
        owned = covers(covered, path)
        if not owned:
            orphans.append(t)
        if covers(mine, path):
            # Theirs, whoever they are — an HSE Manager who is also down for an
            # area gets that area's work like anybody else.
            keep.append(t)
        elif is_hse and not owned:
            keep.append(t)
    return keep, orphans
