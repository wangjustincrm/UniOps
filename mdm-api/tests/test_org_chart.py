"""Org chart, EMPLOYEES.md §13."""
import uuid

import pytest
from sqlalchemy import select

from app.models.department import Department
from app.models.employee import Employee, OrgNode, OrgUnit
from app.models.user import User
from app.services import org_chart
from app.services.org_chart import Routing, level, suggest


@pytest.mark.parametrize("title,expected", [
    ("GENERAL MANAGER", 1), ("Chief Commercial Officer", 1), ("Director, Quality Engineering", 2),
    ("OPERATIONS MANAGER", 2), ("PRODUCTION MANAGER", 3), ("Key Accounts Manager", 6),
    ("PROJECT MANAGER GB AUDIT", 6), ("AR/AP Supervisor", 4), ("CHIEF OPRTG ENG", 4),
    ("PROD TEAM LEAD", 5), ("MAINTENANCE LEAD HAND", 5), ("Digital Marketing Lead", 5),
    ("DRYER", 6), (None, 6),
])
def test_level_comes_from_the_title(title, expected):
    assert level(title) == expected


def p(number, title, adp="Team", department=None):
    return {"id": number, "employee_number": number, "job_title": title, "adp_department": adp, "department_id": department}


def test_staff_report_to_their_own_shift_lead_not_another_shift():
    people = [p("1", "PROD TEAM LEAD", "Shift A", "prod"), p("2", "PROD TEAM LEAD", "Shift B", "prod"),
              p("3", "DRYER", "Shift A", "prod"), p("4", "DRYER", "Shift B", "prod")]
    result = suggest(people, Routing())
    assert result["3"] == ("1", False) and result["4"] == ("2", False)


def test_a_shift_with_no_lead_goes_to_a_supervisor_not_another_shifts_lead():
    people = [p("1", "PRODUCTION SUPERVISOR", "Office", "prod"), p("2", "PROD TEAM LEAD", "Shift A", "prod"),
              p("3", "DRYER", "Shift C", "prod")]
    assert suggest(people, Routing())["3"] == ("1", False)


def test_a_choice_between_equals_is_flagged():
    people = [p("1", "PROD TEAM LEAD"), p("2", "PROD TEAM LEAD"), p("3", "DRYER")]
    assert suggest(people, Routing())["3"] == ("1", True)


def test_the_same_trade_comes_before_another_lead():
    people = [p("1", "MAINTENANCE LEAD HAND"), p("2", "ELECTRICIAN LEAD HAND"), p("3", "ELECTRICIAN"),
              p("4", "CHIEF OPRTG ENG"), p("5", "SHIFT ENGINEER")]
    result = suggest(people, Routing())
    assert result["3"] == ("2", False) and result["5"] == ("4", False)


def test_department_top_answers_to_the_routing_boss():
    people = [p("1", "GENERAL MANAGER", "GM", "gmo"), p("2", "OPERATIONS MANAGER", "GM", "gmo"),
              p("3", "PRODUCTION MANAGER", "Office", "prod"), p("4", "FINANCE OFFICER", "Finance", "fin")]
    routing = Routing(gm="1", opm="2", boss_of={"prod": "opm", "fin": "gm"})
    result = suggest(people, routing)
    assert result["1"] == (None, False) and result["2"] == ("1", False)
    assert result["3"] == ("2", False) and result["4"] == ("1", False)


def test_suggestions_never_loop():
    people = [p(str(i), t, a, d) for i, (t, a, d) in enumerate([
        ("GENERAL MANAGER", "GM", "gmo"), ("OPERATIONS MANAGER", "GM", "gmo"), ("MANAGER", "A", "x"),
        ("SUPERVISOR", "A", "x"), ("TEAM LEAD", "B", "x"), ("DRYER", "B", "x"), ("DRYER", "C", None)])]
    routing = Routing(gm="0", opm="1", boss_of={"x": "opm"}, heads={"x": "4"})
    result = suggest(people, routing)
    for person in result:
        seen, step = set(), person
        while step is not None:
            assert step not in seen
            seen.add(step)
            step = result[step][0]


async def make(db, number, title, adp="Team", department=None, **kw):
    e = Employee(id=uuid.uuid4(), employee_number=number, source="adp", payroll_name=number, display_name=f"P{number}",
                 job_title=title, adp_department=adp, department_id=department, **kw)
    db.add(e)
    await db.flush()
    return e


async def node_of(db, e):
    await db.flush()
    return (await db.execute(select(OrgNode).where(OrgNode.employee_id == e.id)
                             .execution_options(populate_existing=True))).scalar_one()


@pytest.mark.asyncio
async def test_generate_builds_boxes_and_lines(client, db_session):
    d = Department(code="0104", name="Production")
    db_session.add(d)
    await db_session.flush()
    gm = await make(db_session, "000001", "GENERAL MANAGER", "GM")
    lead = await make(db_session, "000002", "PROD TEAM LEAD", "Shift A", d.id)
    staff = await make(db_session, "000003", "DRYER", "Shift A", d.id)
    r = (await client.post("/mdm/v1/org/generate")).json()
    assert r["people_set"] == 3 and r["units_added"] >= 2
    assert (await node_of(db_session, staff)).parent_id == (await node_of(db_session, lead)).id
    assert (await node_of(db_session, lead)).parent_id == (await node_of(db_session, gm)).id
    unit = (await db_session.execute(select(OrgUnit).where(OrgUnit.department_id == d.id))).scalar_one()
    assert unit.head_employee_id == lead.id


@pytest.mark.asyncio
async def test_head_is_the_highest_title_not_the_approver(client, db_session):
    """2026-11-06: the HR Manager lost the head of HR to the approver and a Sanitation Supervisor."""
    d = Department(code="0101", name="Human Resources")
    db_session.add(d)
    await db_session.flush()
    manager = await make(db_session, "000768", "HR MANAGER", None, d.id)
    await make(db_session, "000440", "SENIOR HUMAN RESOURCES GENERALIST", "HR", d.id)
    await make(db_session, "000500", "SANITATION SUPERVISOR", "HR", d.id)
    db_session.add(User(email="rj@example.com", full_name="RJ", erp_person_code="000440",
                        role="dept_manager", department_id=d.id, is_active=True))
    await db_session.flush()
    await client.post("/mdm/v1/org/generate")
    unit = (await db_session.execute(select(OrgUnit).where(OrgUnit.department_id == d.id))).scalar_one()
    assert unit.head_employee_id == manager.id


@pytest.mark.asyncio
async def test_the_approver_settles_a_tie_on_level(client, db_session):
    d = Department(code="0103", name="Finance")
    db_session.add(d)
    await db_session.flush()
    await make(db_session, "000100", "AP SUPERVISOR", "Finance", d.id)
    approver = await make(db_session, "000200", "AR SUPERVISOR", "Finance", d.id)
    db_session.add(User(email="a@example.com", full_name="A", erp_person_code="000200",
                        role="dept_manager", department_id=d.id, is_active=True))
    await db_session.flush()
    await client.post("/mdm/v1/org/generate")
    unit = (await db_session.execute(select(OrgUnit).where(OrgUnit.department_id == d.id))).scalar_one()
    assert unit.head_employee_id == approver.id


@pytest.mark.asyncio
async def test_regenerate_keeps_what_a_person_set(client, db_session):
    gm = await make(db_session, "000001", "GENERAL MANAGER", "GM")
    await make(db_session, "000002", "TEAM LEAD")
    staff = await make(db_session, "000003", "DRYER")
    await client.post("/mdm/v1/org/generate")
    boss, node = await node_of(db_session, gm), await node_of(db_session, staff)
    r = await client.patch(f"/mdm/v1/org/nodes/{node.id}", json={"parent_id": str(boss.id)})
    assert r.status_code == 200
    await client.post("/mdm/v1/org/generate")
    kept = await node_of(db_session, staff)
    assert kept.parent_id == boss.id and kept.source == "manual"


@pytest.mark.asyncio
async def test_confirming_a_line_clears_the_flag(client, db_session):
    a = await make(db_session, "000001", "TEAM LEAD")
    await make(db_session, "000002", "TEAM LEAD")
    staff = await make(db_session, "000003", "DRYER")
    await client.post("/mdm/v1/org/generate")
    node = await node_of(db_session, staff)
    assert node.review is True
    await client.patch(f"/mdm/v1/org/nodes/{node.id}", json={"parent_id": str((await node_of(db_session, a)).id)})
    assert (await node_of(db_session, staff)).review is False


async def boxes(client):
    return {i["name"]: i for i in (await client.get("/mdm/v1/org/nodes")).json()["items"]}


@pytest.mark.asyncio
async def test_free_boxes_below_beside_and_on_the_line(client, db_session):
    """Someone with no employee record, in all three positions."""
    lee = (await client.post("/mdm/v1/org/nodes", json={"name": "Lee", "title": "Senior overseas leader"})).json()["id"]
    await client.post("/mdm/v1/org/nodes", json={"name": "Cathy", "parent_id": lee})
    await client.post("/mdm/v1/org/nodes", json={"name": "Han"})
    await client.post("/mdm/v1/org/nodes", json={"name": "Ann", "parent_id": lee, "relation": "assistant"})
    b = await boxes(client)
    assert b["Lee"]["title"] == "Senior overseas leader" and b["Lee"]["parent_id"] is None
    assert b["Cathy"]["parent_id"] == lee and b["Cathy"]["relation"] == "child"
    assert b["Han"]["parent_id"] is None
    assert b["Ann"]["parent_id"] == lee and b["Ann"]["relation"] == "assistant"


@pytest.mark.asyncio
async def test_an_employee_can_report_to_a_free_box(client, db_session):
    staff = await make(db_session, "000003", "DRYER")
    await client.post("/mdm/v1/org/generate")
    lee = (await client.post("/mdm/v1/org/nodes", json={"name": "Lee"})).json()["id"]
    node = await node_of(db_session, staff)
    assert (await client.patch(f"/mdm/v1/org/nodes/{node.id}", json={"parent_id": lee})).status_code == 200
    assert (await boxes(client))["P000003"]["parent_id"] == lee


@pytest.mark.asyncio
async def test_a_title_can_be_set_on_the_chart_and_cleared_back_to_adp(client, db_session):
    staff = await make(db_session, "000003", "Key Accounts Manager")
    await client.post("/mdm/v1/org/generate")
    node = await node_of(db_session, staff)
    await client.patch(f"/mdm/v1/org/nodes/{node.id}", json={"title": "KAM · Loblaw"})
    assert (await boxes(client))["P000003"]["title"] == "KAM · Loblaw"
    await db_session.refresh(staff)
    assert staff.job_title == "Key Accounts Manager"
    await client.patch(f"/mdm/v1/org/nodes/{node.id}", json={"title": ""})
    assert (await boxes(client))["P000003"]["title"] == "Key Accounts Manager"


@pytest.mark.asyncio
async def test_removing_a_free_box_moves_what_was_under_it_up(client, db_session):
    top = (await client.post("/mdm/v1/org/nodes", json={"name": "Top"})).json()["id"]
    middle = (await client.post("/mdm/v1/org/nodes", json={"name": "Mid", "parent_id": top})).json()["id"]
    await client.post("/mdm/v1/org/nodes", json={"name": "Low", "parent_id": middle})
    assert (await client.delete(f"/mdm/v1/org/nodes/{middle}")).status_code == 200
    assert (await boxes(client))["Low"]["parent_id"] == top


@pytest.mark.asyncio
async def test_an_employee_box_is_not_removed_or_renamed(client, db_session):
    staff = await make(db_session, "000003", "DRYER")
    await client.post("/mdm/v1/org/generate")
    node = await node_of(db_session, staff)
    assert (await client.delete(f"/mdm/v1/org/nodes/{node.id}")).status_code == 422
    assert (await client.patch(f"/mdm/v1/org/nodes/{node.id}", json={"name": "X"})).status_code == 422


async def names_under(client, path, parent):
    return [i["name"] for i in (await client.get(path)).json()["items"] if i["parent_id"] == parent]


@pytest.mark.asyncio
async def test_a_box_can_be_dragged_to_another_place_among_its_peers(client, db_session):
    """2026-11-06: Business Development to the far right."""
    root = (await client.post("/mdm/v1/org/units", json={"name": "Top"})).json()["id"]
    ids = {n: (await client.post("/mdm/v1/org/units", json={"name": n, "parent_id": root})).json()["id"] for n in "ABC"}
    assert await names_under(client, "/mdm/v1/org/units", root) == ["A", "B", "C"]
    r = await client.post(f"/mdm/v1/org/units/{ids['B']}/move", json={"target_id": ids["C"], "where": "after"})
    assert r.status_code == 200
    assert await names_under(client, "/mdm/v1/org/units", root) == ["A", "C", "B"]
    await client.post(f"/mdm/v1/org/units/{ids['B']}/move", json={"target_id": ids["A"], "where": "before"})
    assert await names_under(client, "/mdm/v1/org/units", root) == ["B", "A", "C"]


@pytest.mark.asyncio
async def test_dropping_beside_a_box_in_another_branch_joins_it(client, db_session):
    root = (await client.post("/mdm/v1/org/nodes", json={"name": "Top"})).json()["id"]
    a = (await client.post("/mdm/v1/org/nodes", json={"name": "A", "parent_id": root})).json()["id"]
    b = (await client.post("/mdm/v1/org/nodes", json={"name": "B", "parent_id": a})).json()["id"]
    await client.post(f"/mdm/v1/org/nodes/{b}/move", json={"target_id": a, "where": "after"})
    assert await names_under(client, "/mdm/v1/org/nodes", root) == ["A", "B"]


@pytest.mark.asyncio
async def test_dropping_on_the_middle_puts_it_below(client, db_session):
    root = (await client.post("/mdm/v1/org/nodes", json={"name": "Top"})).json()["id"]
    a = (await client.post("/mdm/v1/org/nodes", json={"name": "A", "parent_id": root})).json()["id"]
    b = (await client.post("/mdm/v1/org/nodes", json={"name": "B", "parent_id": root, "relation": "assistant"})).json()["id"]
    await client.post(f"/mdm/v1/org/nodes/{b}/move", json={"target_id": a, "where": "inside"})
    moved = (await boxes(client))["B"]
    assert moved["parent_id"] == a and moved["relation"] == "child"


async def chain(client):
    """{name: name of the box above it}, and every box must reach the top."""
    items = (await client.get("/mdm/v1/org/nodes")).json()["items"]
    by_id = {i["id"]: i for i in items}
    for i in items:
        seen, step = set(), i
        while step:
            assert step["id"] not in seen, "loop in the chart"
            seen.add(step["id"])
            step = by_id.get(step["parent_id"])
    return {i["name"]: by_id[i["parent_id"]]["name"] if i["parent_id"] else None for i in items}


async def abc(client):
    top = (await client.post("/mdm/v1/org/nodes", json={"name": "P"})).json()["id"]
    a = (await client.post("/mdm/v1/org/nodes", json={"name": "A", "parent_id": top})).json()["id"]
    b = (await client.post("/mdm/v1/org/nodes", json={"name": "B", "parent_id": a})).json()["id"]
    c = (await client.post("/mdm/v1/org/nodes", json={"name": "C", "parent_id": b})).json()["id"]
    return a, b, c


@pytest.mark.asyncio
async def test_a_box_can_go_under_someone_below_it(client, db_session):
    """2026-11-06: a wrong first version has to be turned round, not refused."""
    a, _, c = await abc(client)
    r = await client.post(f"/mdm/v1/org/nodes/{a}/move", json={"target_id": c, "where": "inside"})
    assert r.status_code == 200
    assert await chain(client) == {"P": None, "C": "P", "A": "C", "B": "A"}


@pytest.mark.asyncio
async def test_reports_to_in_the_panel_turns_round_the_same_way(client, db_session):
    a, b, _ = await abc(client)
    assert (await client.patch(f"/mdm/v1/org/nodes/{a}", json={"parent_id": b})).status_code == 200
    assert await chain(client) == {"P": None, "B": "P", "A": "B", "C": "B"}


@pytest.mark.asyncio
async def test_dropping_beside_your_own_report_makes_you_peers(client, db_session):
    a, b, _ = await abc(client)
    assert (await client.post(f"/mdm/v1/org/nodes/{a}/move", json={"target_id": b, "where": "after"})).status_code == 200
    assert await chain(client) == {"P": None, "A": "P", "B": "P", "C": "B"}


@pytest.mark.asyncio
async def test_department_boxes_turn_round_too(client, db_session):
    top = (await client.post("/mdm/v1/org/units", json={"name": "A"})).json()["id"]
    below = (await client.post("/mdm/v1/org/units", json={"name": "B", "parent_id": top})).json()["id"]
    assert (await client.post(f"/mdm/v1/org/units/{top}/move", json={"target_id": below, "where": "inside"})).status_code == 200
    items = {u["name"]: u for u in (await client.get("/mdm/v1/org/units")).json()["items"]}
    assert items["B"]["parent_id"] is None and items["A"]["parent_id"] == below


@pytest.mark.asyncio
async def test_regenerate_cannot_make_a_loop_with_a_line_set_by_hand(client, db_session):
    """The supervisor is put under their own team member by hand; the data still says the opposite."""
    gm = await make(db_session, "000001", "GENERAL MANAGER", "GM")
    boss = await make(db_session, "000002", "TEAM LEAD")
    staff = await make(db_session, "000003", "DRYER")
    await client.post("/mdm/v1/org/generate")
    b, s_ = await node_of(db_session, boss), await node_of(db_session, staff)
    await client.patch(f"/mdm/v1/org/nodes/{s_.id}", json={"parent_id": str((await node_of(db_session, gm)).id)})
    await client.patch(f"/mdm/v1/org/nodes/{b.id}", json={"parent_id": str(s_.id)})
    await db_session.execute(OrgNode.__table__.update().where(OrgNode.id == s_.id).values(source="auto"))
    await client.post("/mdm/v1/org/generate")
    lines = await chain(client)
    assert lines["P000002"] == "P000003" and lines["P000003"] == "P000001"


@pytest.mark.asyncio
async def test_reordering_peers_is_not_a_new_reporting_line(client, db_session):
    gm = await make(db_session, "000001", "GENERAL MANAGER", "GM")
    a = await make(db_session, "000002", "DRYER")
    b = await make(db_session, "000003", "DRYER")
    await client.post("/mdm/v1/org/generate")
    na, nb = await node_of(db_session, a), await node_of(db_session, b)
    assert na.parent_id == nb.parent_id == (await node_of(db_session, gm)).id
    await client.post(f"/mdm/v1/org/nodes/{na.id}/move", json={"target_id": str(nb.id), "where": "after"})
    assert (await node_of(db_session, a)).source == "auto"


@pytest.mark.asyncio
async def test_the_chart_never_touches_users(client, db_session):
    """§13: real supervisors are deliberately not approvers."""
    boss = await make(db_session, "000001", "GENERAL MANAGER", "GM")
    staff = await make(db_session, "000002", "DRYER")
    u = User(email="x@example.com", full_name="X", erp_person_code="000002", role="requester")
    db_session.add(u)
    await db_session.flush()
    await client.post("/mdm/v1/org/generate")
    node = await node_of(db_session, staff)
    await client.patch(f"/mdm/v1/org/nodes/{node.id}", json={"parent_id": str((await node_of(db_session, boss)).id)})
    await db_session.flush()
    fresh = await db_session.get(User, u.id, populate_existing=True)
    assert fresh.role == "requester" and fresh.department_id is None


@pytest.mark.asyncio
async def test_left_employees_are_not_on_the_chart(client, db_session):
    await make(db_session, "000001", "DRYER", status="left")
    await make(db_session, "000002", "DRYER")
    await client.post("/mdm/v1/org/generate")
    assert [i["employee_number"] for i in (await client.get("/mdm/v1/org/nodes")).json()["items"]] == ["000002"]


@pytest.mark.asyncio
async def test_boxes_can_be_added_moved_and_removed(client, db_session):
    await make(db_session, "000001", "GENERAL MANAGER", "GM")
    await client.post("/mdm/v1/org/generate")
    units = (await client.get("/mdm/v1/org/units")).json()["items"]
    root = next(u for u in units if u["parent_id"] is None)
    a = (await client.post("/mdm/v1/org/units", json={"name": "Plant", "parent_id": root["id"]})).json()["id"]
    b = (await client.post("/mdm/v1/org/units", json={"name": "Shift", "parent_id": a, "relation": "assistant"})).json()["id"]
    assert (await client.patch(f"/mdm/v1/org/units/{a}", json={"name": "Site", "tone": "teal"})).status_code == 200
    assert (await client.delete(f"/mdm/v1/org/units/{a}")).status_code == 200
    moved = next(u for u in (await client.get("/mdm/v1/org/units")).json()["items"] if u["id"] == b)
    assert moved["parent_id"] == root["id"] and moved["relation"] == "assistant"


@pytest.mark.asyncio
async def test_a_department_box_is_not_removed(client, db_session):
    d = Department(code="0104", name="Production")
    db_session.add(d)
    await db_session.flush()
    await client.post("/mdm/v1/org/generate")
    unit = next(u for u in (await client.get("/mdm/v1/org/units")).json()["items"] if u["name"] == "Production")
    assert (await client.delete(f"/mdm/v1/org/units/{unit['id']}")).status_code == 422


@pytest.mark.asyncio
async def test_a_head_can_be_cleared(client, db_session):
    gm = await make(db_session, "000001", "GENERAL MANAGER", "GM")
    await client.post("/mdm/v1/org/generate")
    root = next(u for u in (await client.get("/mdm/v1/org/units")).json()["items"] if u["parent_id"] is None)
    assert root["head_employee_id"] == str(gm.id)
    await client.patch(f"/mdm/v1/org/units/{root['id']}", json={"head_employee_id": None})
    root = next(u for u in (await client.get("/mdm/v1/org/units")).json()["items"] if u["id"] == root["id"])
    assert root["head_employee_id"] is None


@pytest.mark.parametrize("method,path,key", [
    ("get", "/mdm/v1/org/nodes", "employee.view"),
    ("get", "/mdm/v1/org/units", "employee.view"),
    ("post", "/mdm/v1/org/generate", "employee.edit"),
])
@pytest.mark.asyncio
async def test_chart_needs_its_key_and_the_key_admits(client, monkeypatch, method, path, key):
    import uniops_authz.core as core
    from app.core.deps import get_token_payload
    from app.main import app

    held: set[str] = set()

    async def _roles(db, user_id, base_role):
        return {base_role}

    async def _matrix(db):
        return {"requester": held}
    monkeypatch.setattr(core, "user_role_codes", _roles)
    monkeypatch.setattr(core, "_effective_matrix", _matrix)
    app.dependency_overrides[get_token_payload] = lambda: {"sub": str(uuid.uuid4()), "role": "requester"}

    assert (await getattr(client, method)(path)).status_code == 403
    held.add(key)
    assert (await getattr(client, method)(path)).status_code == 200
