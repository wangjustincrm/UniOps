"""First version of the org chart, worked out from what the data already says.

EMPLOYEES.md §13. Job titles give the levels, the ADP department gives the team (it
splits Production into shifts), approval routing says who a department answers to.
It is a draft for people to correct: anything set by hand is never overwritten, and
a guess between equals is flagged for review. Nothing here touches users or approvals.
"""
import re
import uuid
from dataclasses import dataclass, field

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.department import Department
from app.models.employee import Employee, OrgNode, OrgUnit

ROOT_NAME = "Canada Royal Milk"
OPERATIONS_NAME = "Operations"
STAFF_LEVEL = 6
TEAM_LEAD_LEVEL = 5

# Words that say how senior a title is, not what the work is.
_RANK_WORDS = {"SUPERVISOR", "MANAGER", "DIRECTOR", "LEAD", "TEAM", "HAND", "CHIEF", "SENIOR", "ASSIST", "AND", "OF",
               "OFFICER", "SPECIALIST", "COORDINATOR", "ADMINISTRATOR", "TECHNICIAN", "CLASS", "ND", "TERM"}
_SAME_WORD = {"ENG": "ENGINEER", "ENGINEERING": "ENGINEER", "PROD": "PRODUCTION"}


def _trade(title: str | None) -> set[str]:
    words = re.findall(r"[A-Z]+", (title or "").upper())
    return {_SAME_WORD.get(w, w) for w in words} - _RANK_WORDS


def level(title: str | None) -> int:
    """1 top of the company ... 6 no one reports to them."""
    t = (title or "").upper()
    if "GENERAL MANAGER" in t or t.startswith("CHIEF ") and "ENG" not in t:
        return 1
    if "DIRECTOR" in t or "OPERATIONS MANAGER" in t:
        return 2
    if "MANAGER" in t and "KEY ACCOUNT" not in t and "PROJECT MANAGER" not in t:
        return 3
    if "SUPERVISOR" in t or t.startswith("CHIEF "):
        return 4
    if "TEAM LEAD" in t or "LEAD HAND" in t or t.endswith(" LEAD") or "TECHNICAL LEAD" in t:
        return TEAM_LEAD_LEVEL
    return STAFF_LEVEL


@dataclass
class Routing:
    gm: uuid.UUID | None = None
    opm: uuid.UUID | None = None
    boss_of: dict = field(default_factory=dict)     # department_id -> "gm" | "opm"
    heads: dict = field(default_factory=dict)       # department_id -> employee id


def suggest(people: list[dict], routing: Routing) -> dict:
    """{employee id: (manager id or None, needs review)}. Every step goes to a higher
    level or up to the GM/OPM, so the result cannot contain a loop."""
    lv = {p["id"]: level(p["job_title"]) for p in people}

    def nearest(person: dict, candidates: list[dict]):
        """Someone in the same trade first: an electrician goes to the electrical lead."""
        trade = _trade(person["job_title"])
        pool = [c for c in candidates if trade & _trade(c["job_title"])] or candidates
        top = max(lv[c["id"]] for c in pool)
        same = sorted((c for c in pool if lv[c["id"]] == top), key=lambda c: c["employee_number"])
        return same[0]["id"], len(same) > 1

    result = {}
    for p in people:
        pid = p["id"]
        boss = routing.opm if routing.boss_of.get(p["department_id"]) == "opm" else routing.gm
        if pid == routing.gm:
            result[pid] = (None, False)
            continue
        if pid == routing.opm:
            result[pid] = (routing.gm, routing.gm is None)
            continue
        team = [c for c in people if c["adp_department"] == p["adp_department"] and lv[c["id"]] < lv[pid]]
        department = [c for c in people if p["department_id"] and c["department_id"] == p["department_id"]
                      and lv[c["id"]] < lv[pid] and lv[c["id"]] < TEAM_LEAD_LEVEL]
        head = routing.heads.get(p["department_id"])
        if team:
            result[pid] = nearest(p, team)
        elif department:
            result[pid] = nearest(p, department)
        elif head and head != pid and head in lv and lv[head] < lv[pid]:
            result[pid] = (head, False)
        else:
            result[pid] = (boss if boss != pid else routing.gm, boss is None or not p["department_id"])
    return result


async def _exists(db: AsyncSession, table: str) -> bool:
    return bool((await db.execute(text("select to_regclass(:t)"), {"t": table})).scalar())


async def load_routing(db: AsyncSession, employees: list[Employee]) -> Routing:
    """identity and approval-api own these tables; mdm-api's test database has neither."""
    by_number = {e.employee_number: e.id for e in employees}
    routing = Routing()
    if await _exists(db, "user_roles"):
        rows = (await db.execute(text(
            "select r.role_code, u.erp_person_code from user_roles r join users u on u.id = r.user_id "
            "where r.role_code in ('gm', 'opm') "
            "union select u.role, u.erp_person_code from users u where u.is_active and u.role in ('gm', 'opm')"
        ))).all()
        for role, code in rows:
            setattr(routing, role, by_number.get(code))
    for e in employees:
        title = (e.job_title or "").upper()
        if routing.gm is None and title == "GENERAL MANAGER":
            routing.gm = e.id
        if routing.opm is None and title == "OPERATIONS MANAGER":
            routing.opm = e.id
    if await _exists(db, "approval_dept_routing"):
        rows = (await db.execute(text(
            "select r.dept_id, r.gm_or_opm, u.erp_person_code from approval_dept_routing r "
            "left join users u on u.id = r.director_user_id"))).all()
        for department_id, boss, code in rows:
            routing.boss_of[department_id] = boss
            if by_number.get(code):
                routing.heads[department_id] = by_number[code]
    managers = (await db.execute(text(
        "select department_id, erp_person_code from users "
        "where is_active and role = 'dept_manager' and department_id is not null"))).all()
    # An approval role is not the real structure: it only settles a tie on job level.
    approver = {department_id: by_number[code] for department_id, code in managers if by_number.get(code)}
    by_id = {e.id: e for e in employees}
    pools: dict = {}
    for e in employees:
        if e.department_id:
            pools.setdefault(e.department_id, set()).add(e.id)
    for department_id, employee_id in approver.items():
        pools.setdefault(department_id, set()).add(employee_id)
    for department_id, pool in pools.items():
        routing.heads.setdefault(department_id, min(pool, key=lambda i: (
            level(by_id[i].job_title), i != approver.get(department_id), by_id[i].employee_number)))
    return routing


async def generate(db: AsyncSession) -> dict:
    """Fills in what is missing. Hand-made boxes and hand-set reporting lines stay."""
    employees = list((await db.execute(select(Employee).where(Employee.status != "left"))).scalars())
    routing = await load_routing(db, employees)
    units = list((await db.execute(select(OrgUnit))).scalars())
    added = 0

    async def group(name: str, parent: OrgUnit | None, head) -> OrgUnit:
        nonlocal added
        unit = next((u for u in units if u.name == name and u.department_id is None), None)
        if unit is None:
            unit = OrgUnit(id=uuid.uuid4(), name=name, parent_id=parent.id if parent else None, head_employee_id=head)
            db.add(unit)
            await db.flush()
            units.append(unit)
            added += 1
        return unit

    root = await group(ROOT_NAME, None, routing.gm)
    operations = await group(OPERATIONS_NAME, root, routing.opm) if routing.opm else root
    placed = {u.department_id for u in units}
    departments = (await db.execute(select(Department).where(Department.is_active.is_(True)))).scalars()
    for department in departments:
        if department.id not in placed:
            parent = operations if routing.boss_of.get(department.id) == "opm" else root
            db.add(OrgUnit(id=uuid.uuid4(), name=department.name, department_id=department.id, parent_id=parent.id,
                           head_employee_id=routing.heads.get(department.id)))
            added += 1

    nodes = {n.employee_id: n for n in (await db.execute(
        select(OrgNode).where(OrgNode.employee_id.is_not(None)))).scalars()}
    for e in employees:
        if e.id not in nodes:
            nodes[e.id] = OrgNode(id=uuid.uuid4(), employee_id=e.id, source="auto")
            db.add(nodes[e.id])
    await db.flush()

    people = [{"id": e.id, "employee_number": e.employee_number, "job_title": e.job_title,
               "adp_department": e.adp_department, "department_id": e.department_id} for e in employees]
    suggestions = suggest(people, routing)
    parents = dict((await db.execute(select(OrgNode.id, OrgNode.parent_id))).all())

    def below(node_id, top) -> bool:
        while node_id is not None:
            if node_id == top:
                return True
            node_id = parents.get(node_id)
        return False

    assigned = review = 0
    for e in employees:
        node = nodes[e.id]
        if node.source == "manual":
            continue
        manager, node.review = suggestions[e.id]
        parent_id = nodes[manager].id if manager else None
        if below(parent_id, node.id):       # a line set by hand put them above this person: it wins
            node.review = True
            parent_id = node.parent_id
        node.parent_id = parents[node.id] = parent_id
        assigned += 1
        review += node.review
    await db.flush()
    return {"units_added": added, "people_set": assigned, "to_review": review}
