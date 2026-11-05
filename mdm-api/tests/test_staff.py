"""Staff: ADP roster import, department mapping, NC to-dos, read-back. EMPLOYEES.md."""
import io
import uuid
from datetime import date, datetime, timedelta, timezone

import pytest
from openpyxl import Workbook
from sqlalchemy import select

from app.models.department import Department
from app.models.employee import AdpDepartmentMap, AdpImport, Employee, NcTodo
from app.models.task import Task
from app.models.user import User
from app.services import adp_roster, nc_readback, staff

HEADER = list(adp_roster.HEADERS.values())


def roster(*rows, header=HEADER) -> bytes:
    workbook = Workbook()
    sheet = workbook.active
    sheet.append(header)
    for row in rows:
        sheet.append(list(row))
    buffer = io.BytesIO()
    workbook.save(buffer)
    return buffer.getvalue()


def row(number="000052", status="Active", name="QIN, PENG FRANK", title="Manager",
        dept="Finance", email="qin@example.com"):
    return (f"ZK7{number}", status, name, title, dept, email)


# ── Parsing ──────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("payroll,expected", [
    ("QIN, PENG FRANK", "Peng Frank Qin"),
    ("Smith,William", "William Smith"),
    ("NOCOMMA", "Nocomma"),
])
def test_display_name(payroll, expected):
    assert adp_roster.display_name(payroll) == expected


def test_parse_normalises_and_finds_columns_by_name():
    reordered = list(reversed(HEADER))
    [parsed] = adp_roster.parse_roster(roster(tuple(reversed(row(email=" NOFTALL@EXAMPLE.COM "))), header=reordered))
    assert parsed.employee_number == "000052"
    assert parsed.status == "active"
    assert parsed.work_email == "noftall@example.com"
    assert parsed.errors == []


def test_parse_leave_is_on_leave():
    [parsed] = adp_roster.parse_roster(roster(row(status="Leave")))
    assert parsed.status == "on_leave"


def test_parse_missing_header_names_it():
    with pytest.raises(adp_roster.RosterFileError, match="Payroll Name"):
        adp_roster.parse_roster(roster(header=[h for h in HEADER if h != "Payroll Name"]))


def test_parse_not_a_workbook():
    with pytest.raises(adp_roster.RosterFileError):
        adp_roster.parse_roster(b"not a zip")


def test_bad_row_is_reported_and_does_not_stop_the_others():
    rows = adp_roster.parse_roster(roster(("X1", "Active", "A, B", "", "", ""), row(status="Terminated"), row("000053")))
    groups = adp_roster.classify(rows, [], {}, {})
    assert [e["row"] for e in groups["errors"]] == [2, 3]
    assert [n["employee_number"] for n in groups["new"]] == ["000053"]


# ── Classification ───────────────────────────────────────────────────────────

def emp(number="000052", email="qin@example.com", source="adp", status="active", **kw):
    base = {"id": uuid.uuid4(), "employee_number": number, "source": source, "status": status,
            "payroll_name": "QIN, PENG FRANK", "display_name": "Peng Frank Qin", "work_email": email,
            "job_title": "Manager", "adp_department": "Finance"}
    return base | kw


def classify(*rows, employees=(), users=None, dmap=None):
    return adp_roster.classify(adp_roster.parse_roster(roster(*rows)), list(employees), users or {}, dmap or {})


def test_same_data_is_unchanged():
    groups = classify(row(), employees=[emp()])
    assert len(groups["unchanged"]) == 1 and not groups["changed"]


def test_change_lists_old_and_new():
    groups = classify(row(title="Director"), employees=[emp()])
    assert groups["changed"][0]["changes"] == [{"field": "job_title", "old": "Manager", "new": "Director"}]


def test_email_owned_by_another_users_number_is_a_conflict_not_an_update():
    """2026-11-02: Smith's account held Mu's number. A number match alone would
    have written Mu's details over Smith."""
    groups = classify(row("000998", email="mu@example.com"),
                      users={"mu@example.com": "000190"})
    assert groups["conflicts"][0]["kind"] == "email_other_user"
    assert not groups["new"]


def test_email_of_the_same_persons_user_is_not_a_conflict():
    groups = classify(row(), users={"qin@example.com": "000052"})
    assert len(groups["new"]) == 1 and not groups["conflicts"]


def test_new_number_with_another_employees_email_is_a_conflict():
    groups = classify(row("000053"), employees=[emp()])
    assert groups["conflicts"][0]["kind"] == "email_other_employee"


def test_same_number_twice_errors_both_rows():
    groups = classify(row(), row(email="other@example.com"))
    assert len(groups["errors"]) == 2 and not groups["new"]


def test_same_email_twice_conflicts_both_rows():
    groups = classify(row("000052"), row("000053"))
    assert len(groups["conflicts"]) == 2


def test_non_adp_on_the_roster_is_a_conflict():
    groups = classify(row(), employees=[emp(source="non_adp")])
    assert groups["conflicts"][0]["kind"] == "non_adp"


def test_missing_lists_adp_only_and_not_those_already_left():
    groups = classify(row(), employees=[
        emp(), emp("000060", "a@example.com"),
        emp("000061", "b@example.com", source="non_adp"),
        emp("000062", "c@example.com", status="left"),
    ])
    assert [m["employee_number"] for m in groups["missing"]] == ["000060"]


def test_a_person_with_a_bad_row_is_not_missing():
    groups = classify(row(status="Terminated"), employees=[emp()])
    assert not groups["missing"]


def test_unmapped_departments():
    groups = classify(row(dept="New Dept"), row("000053", dept="", email="b@example.com"),
                      dmap={"": {"department_id": None, "choose_per_employee": True}})
    assert groups["unmapped"] == ["New Dept"]


# ── Import through the API ───────────────────────────────────────────────────

async def upload(client, data: bytes, roster_date="2026-11-01"):
    return await client.post("/mdm/v1/staff/imports", files={"file": ("roster.xlsx", data)},
                             data={"roster_date": roster_date})


@pytest.fixture
def nc(monkeypatch):
    """NC's person list as the read-back sees it."""
    persons: list[dict] = []

    async def _fetch(client=None):
        return list(persons)
    monkeypatch.setattr(nc_readback, "fetch_persons", _fetch)
    return persons


def person(code="000052", dept="0103", pk=None):
    return {"pk": pk or f"pk{code}", "code": code, "name": "X", "department_code": dept, "department_name": "Finance"}


async def dept(db, code="0103", name="Finance"):
    d = Department(code=code, name=name)
    db.add(d)
    await db.flush()
    return d


async def add_user(db, email="qin@example.com", code="000052", **kw):
    u = User(email=email, full_name=kw.pop("full_name", "Frank Qin"), erp_person_code=code, **kw)
    db.add(u)
    await db.flush()
    return u


async def do_import(client, data, **kw):
    r = await upload(client, data, **kw)
    assert r.status_code == 201, r.text
    applied = await client.post(f"/mdm/v1/staff/imports/{r.json()['id']}/apply")
    assert applied.status_code == 200, applied.text
    return applied.json()


async def employee(db, number="000052"):
    await db.flush()
    return (await db.execute(select(Employee).where(Employee.employee_number == number).execution_options(populate_existing=True))).scalar_one()


async def todos(db, status="open"):
    await db.flush()
    return list((await db.execute(select(NcTodo).where(NcTodo.status == status).execution_options(populate_existing=True))).scalars())


@pytest.mark.asyncio
async def test_preview_writes_nothing(client, db_session, nc):
    r = await upload(client, roster(row()))
    assert r.json()["counts"]["new"] == 1
    assert not (await db_session.execute(select(Employee))).first()


@pytest.mark.asyncio
async def test_only_xlsx_is_accepted(client, nc):
    r = await client.post("/mdm/v1/staff/imports", files={"file": ("roster.csv", b"a,b")})
    assert r.status_code == 422


@pytest.mark.asyncio
async def test_apply_creates_and_reimport_changes_nothing(client, db_session, nc):
    await dept(db_session)
    await do_import(client, roster(row()))
    created = await employee(db_session)
    assert created.display_name == "Peng Frank Qin"
    second = await do_import(client, roster(row()), roster_date="2026-11-15")
    assert second["counts"]["unchanged"] == 1 and second["counts"]["new"] == 0
    assert not await todos(db_session)                  # IMP-010


@pytest.mark.asyncio
async def test_new_employee_raises_no_todo_in_or_out_of_nc(client, db_session, nc):
    nc.append(person())
    await do_import(client, roster(row(), row("000053", email="b@example.com")))
    assert not await todos(db_session)
    assert (await employee(db_session)).nc_state == "in_step"
    assert (await employee(db_session, "000053")).nc_state == "not_in_nc"


@pytest.mark.asyncio
async def test_apply_never_touches_the_user(client, db_session, nc):
    u = await add_user(db_session, full_name="Frank Qin")
    await do_import(client, roster(row(name="QIN, PENG FRANK")))
    await do_import(client, roster(row(name="QIN, PENG", title="Director")), roster_date="2026-11-15")
    await db_session.flush()
    fresh = await db_session.get(User, u.id, populate_existing=True)
    assert fresh.full_name == "Frank Qin"
    assert (await employee(db_session)).display_name == "Peng Qin"


@pytest.mark.asyncio
async def test_second_upload_is_refused_until_discarded(client, nc):
    first = await upload(client, roster(row()))
    assert (await upload(client, roster(row()))).status_code == 409
    await client.post(f"/mdm/v1/staff/imports/{first.json()['id']}/discard")
    assert (await upload(client, roster(row()))).status_code == 201


@pytest.mark.asyncio
async def test_stale_preview_must_be_regenerated(client, db_session, nc):
    r = await upload(client, roster(row()))
    imp = await db_session.get(AdpImport, uuid.UUID(r.json()["id"]))
    imp.previewed_at = datetime.now(timezone.utc) - timedelta(hours=25)
    await db_session.flush()
    assert (await client.post(f"/mdm/v1/staff/imports/{imp.id}/apply")).status_code == 409
    assert (await client.post(f"/mdm/v1/staff/imports/{imp.id}/regenerate")).status_code == 200
    assert (await client.post(f"/mdm/v1/staff/imports/{imp.id}/apply")).status_code == 200


@pytest.mark.asyncio
async def test_earlier_roster_date_warns(client, nc):
    await do_import(client, roster(row()), roster_date="2026-11-15")
    r = await upload(client, roster(row()), roster_date="2026-11-01")
    assert "earlier" in r.json()["warnings"][0]


@pytest.mark.asyncio
async def test_missing_is_never_marked_left_by_itself(client, db_session, nc):
    nc.append(person())
    await do_import(client, roster(row()))
    result = await do_import(client, roster(row("000053", email="b@example.com")), roster_date="2026-11-15")
    assert (await employee(db_session)).status == "active"
    missing = result["missing"][0]
    assert missing["resolution"] is None and result["unresolved_missing"] == 1

    url = f"/mdm/v1/staff/imports/{result['id']}/missing/{missing['employee_id']}"
    assert (await client.post(url, json={"action": "keep"})).status_code == 422
    assert (await client.post(url, json={"action": "left"})).status_code == 422
    r = await client.post(url, json={"action": "left", "left_on": "2026-11-10"})
    assert r.json()["unresolved_missing"] == 0
    left = await employee(db_session)
    assert left.status == "left" and left.left_on == date(2026, 11, 10)
    assert "terminate" in {t.type for t in await todos(db_session)}


@pytest.mark.asyncio
async def test_left_employee_back_on_the_roster_is_rehired(client, db_session, nc):
    nc.append(person())
    await do_import(client, roster(row()))
    e = await employee(db_session)
    await client.post(f"/mdm/v1/staff/{e.id}/leave", json={"left_on": "2026-11-02"})
    nc.clear()
    await client.post("/mdm/v1/staff/nc/check")          # terminate closes
    await do_import(client, roster(row()), roster_date="2026-11-15")
    back = await employee(db_session)
    assert back.status == "active" and back.left_on is None
    assert {t.type for t in await todos(db_session)} == {"rehire"}


# ── Department mapping ───────────────────────────────────────────────────────

async def map_dept(db, adp, department=None, choose=False):
    db.add(AdpDepartmentMap(adp_department=adp, department_id=department and department.id, choose_per_employee=choose))
    await db.flush()


@pytest.mark.asyncio
async def test_unmapped_updates_adp_department_and_flags(client, db_session, nc):
    await do_import(client, roster(row(dept="Brand New")))
    e = await employee(db_session)
    assert e.adp_department == "Brand New" and e.department_id is None and e.needs_department


@pytest.mark.asyncio
async def test_hand_chosen_department_survives_a_reimport(client, db_session, nc):
    finance, sales = await dept(db_session), await dept(db_session, "0110", "Sales")
    await map_dept(db_session, "Finance", finance)
    await do_import(client, roster(row()))
    e = await employee(db_session)
    assert e.department_id == finance.id
    await client.patch(f"/mdm/v1/staff/{e.id}", json={"department_id": str(sales.id)})
    await do_import(client, roster(row(title="Director")), roster_date="2026-11-15")
    assert (await employee(db_session)).department_id == sales.id


@pytest.mark.asyncio
async def test_adp_department_change_applies_the_map_and_raises_a_todo(client, db_session, nc):
    finance, sales = await dept(db_session), await dept(db_session, "0110", "Sales")
    await map_dept(db_session, "Finance", finance)
    await map_dept(db_session, "Sales", sales)
    nc.append(person())
    await do_import(client, roster(row()))
    await do_import(client, roster(row(dept="Sales")), roster_date="2026-11-15")
    assert (await employee(db_session)).department_id == sales.id
    [todo] = await todos(db_session)
    assert todo.type == "change_department" and todo.target["nc_department_code"] == "0110"


@pytest.mark.asyncio
async def test_choose_per_employee_is_never_set_by_import(client, db_session, nc):
    finance = await dept(db_session)
    await map_dept(db_session, "Marketing and Sales", choose=True)
    await do_import(client, roster(row(dept="Marketing and Sales")))
    e = await employee(db_session)
    await client.patch(f"/mdm/v1/staff/{e.id}", json={"department_id": str(finance.id)})
    await do_import(client, roster(row(dept="Marketing and Sales", title="X")), roster_date="2026-11-15")
    assert (await employee(db_session)).department_id == finance.id


@pytest.mark.asyncio
async def test_map_page_lists_mapped_seen_and_pending_departments(client, db_session, nc):
    finance = await dept(db_session)
    await map_dept(db_session, "Finance", finance)
    await do_import(client, roster(row(), row("000053", dept=None, email="b@example.com")))
    await upload(client, roster(row(dept="Brand New")), roster_date="2026-11-15")
    items = {i["adp_department"]: i for i in (await client.get("/mdm/v1/staff/department-map")).json()["items"]}
    assert items["Finance"]["mapped"] and items["Finance"]["employees"] == 1
    assert not items[""]["mapped"] and items[""]["employees"] == 1
    assert not items["Brand New"]["mapped"] and items["Brand New"]["employees"] == 0


@pytest.mark.asyncio
async def test_map_needs_a_department_or_choose(client):
    r = await client.put("/mdm/v1/staff/department-map", json={"adp_department": "X"})
    assert r.status_code == 422


# ── Employee rules ───────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_adp_fields_are_read_only(client, db_session, nc):
    await do_import(client, roster(row()))
    e = await employee(db_session)
    r = await client.patch(f"/mdm/v1/staff/{e.id}", json={"work_email": "x@example.com"})
    assert r.status_code == 422
    ok = await client.patch(f"/mdm/v1/staff/{e.id}", json={"notes": "hello"})
    assert ok.status_code == 200


@pytest.mark.asyncio
async def test_non_adp_create_and_number_rules(client, db_session, nc):
    body = {"employee_number": "000700", "display_name": "Yuping Huang"}
    assert (await client.post("/mdm/v1/staff", json=body | {"employee_number": "700"})).status_code == 422
    assert (await client.post("/mdm/v1/staff", json=body)).status_code == 201
    assert (await client.post("/mdm/v1/staff", json=body)).status_code == 409
    assert (await employee(db_session, "000700")).source == "non_adp"


@pytest.mark.asyncio
async def test_correct_number_moves_the_user_and_raises_change_code(client, db_session, nc):
    nc.append(person(pk="pkA"))
    u = await add_user(db_session, code="000052")
    await do_import(client, roster(row()))
    e = await employee(db_session)
    r = await client.post(f"/mdm/v1/staff/{e.id}/correct-number", json={"new_number": "000995", "reason": "typo"})
    assert r.status_code == 200
    await db_session.flush()
    assert (await db_session.get(User, u.id, populate_existing=True)).erp_person_code == "000995"
    assert "change_code" in {t.type for t in await todos(db_session)}


@pytest.mark.asyncio
async def test_correct_number_refuses_a_number_in_use(client, db_session, nc):
    await add_user(db_session, email="other@example.com", code="000999")
    await do_import(client, roster(row()))
    e = await employee(db_session)
    r = await client.post(f"/mdm/v1/staff/{e.id}/correct-number", json={"new_number": "000999", "reason": "x"})
    assert r.status_code == 409


@pytest.mark.asyncio
async def test_detail_shows_user_differences_and_list_filters_them(client, db_session, nc):
    await add_user(db_session, full_name="Frank Qin")
    await do_import(client, roster(row()))
    e = await employee(db_session)
    detail = (await client.get(f"/mdm/v1/staff/{e.id}")).json()
    assert [d["field"] for d in detail["differences"]] == ["full_name"]
    assert detail["user"]["email"] == "qin@example.com"
    assert (await client.get("/mdm/v1/staff?differs=true")).json()["total"] == 1
    assert (await client.get("/mdm/v1/staff?has_user=false")).json()["total"] == 0


@pytest.mark.asyncio
async def test_list_sorts_on_the_server_and_rejects_unknown_columns(client, db_session, nc):
    await do_import(client, roster(row(name="ZED, A"), row("000053", name="ABLE, B", email="b@example.com")))
    r = await client.get("/mdm/v1/staff?sort=display_name&dir=asc")
    assert [i["employee_number"] for i in r.json()["items"]] == ["000052", "000053"]
    r = await client.get("/mdm/v1/staff?sort=display_name&dir=desc")
    assert [i["employee_number"] for i in r.json()["items"]] == ["000053", "000052"]
    assert (await client.get("/mdm/v1/staff?sort=nope")).status_code == 422


@pytest.mark.asyncio
async def test_exports_are_workbooks(client, nc):
    await do_import(client, roster(row()))
    for path in ("/mdm/v1/staff/export", "/mdm/v1/staff/nc-todos/export"):
        r = await client.get(path)
        assert r.status_code == 200 and r.content[:2] == b"PK"


@pytest.mark.asyncio
async def test_unlinked_users_lists_active_users_without_an_employee(client, db_session, nc):
    await add_user(db_session)
    await add_user(db_session, email="huang@example.com", code="000700")
    await add_user(db_session, email="gone@example.com", code="000701", is_active=False)
    await do_import(client, roster(row()))
    items = (await client.get("/mdm/v1/staff/unlinked-users")).json()["items"]
    assert [i["email"] for i in items] == ["huang@example.com"]


# ── NC to-dos and read-back ──────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_new_todo_supersedes_open_one_of_same_type(client, db_session, nc):
    a, b = await dept(db_session), await dept(db_session, "0110", "Sales")
    nc.append(person(dept="0999"))
    await do_import(client, roster(row()))
    e = await employee(db_session)
    await client.patch(f"/mdm/v1/staff/{e.id}", json={"department_id": str(a.id)})
    await client.patch(f"/mdm/v1/staff/{e.id}", json={"department_id": str(b.id)})
    [todo] = await todos(db_session)
    assert todo.target["nc_department_code"] == "0110"


@pytest.mark.asyncio
async def test_not_in_nc_is_a_notice_until_nc_shows_them(client, db_session, nc):
    """NCT-006: listed for HR, but no to-do and no task; HR adds people as needed."""
    await do_import(client, roster(row()))
    await client.post("/mdm/v1/staff/nc/check")
    assert not await todos(db_session) and not await open_tasks(db_session)
    report = (await client.get("/mdm/v1/staff/nc/report")).json()
    assert [e["employee_number"] for e in report["not_in_nc"]] == ["000052"]
    assert report["differences"] == [] and report["summary"]["not_in_nc"] == 1
    nc.append(person())
    await client.post("/mdm/v1/staff/nc/check")
    assert (await employee(db_session)).nc_state == "in_step"
    assert (await client.get("/mdm/v1/staff/nc/report")).json()["not_in_nc"] == []


@pytest.mark.asyncio
async def test_nobody_is_asked_to_change_a_person_nc_never_had(client, db_session, nc):
    await do_import(client, roster(row()))
    e = await employee(db_session)
    await client.post(f"/mdm/v1/staff/{e.id}/correct-number", json={"new_number": "000995", "reason": "typo"})
    await client.post(f"/mdm/v1/staff/{e.id}/leave", json={"left_on": "2026-11-02"})
    await client.post(f"/mdm/v1/staff/{e.id}/reinstate")
    assert not await todos(db_session)


@pytest.mark.asyncio
async def test_todo_raised_before_nc_was_read_closes_if_nc_never_had_them(client, db_session, nc, monkeypatch):
    from app.services.erp_client import ErpError

    async def _down(client=None):
        raise ErpError(None, "network error")
    with monkeypatch.context() as m:
        m.setattr(nc_readback, "fetch_persons", _down)
        await do_import(client, roster(row()))
    e = await employee(db_session)
    await client.post(f"/mdm/v1/staff/{e.id}/correct-number", json={"new_number": "000995", "reason": "typo"})
    assert (await todos(db_session))[0].type == "change_code"
    await client.post("/mdm/v1/staff/nc/check")
    assert not await todos(db_session)


@pytest.mark.asyncio
async def test_terminate_closes_when_the_person_disappears_from_nc(client, db_session, nc):
    nc.append(person())
    await do_import(client, roster(row()))
    e = await employee(db_session)
    await client.post(f"/mdm/v1/staff/{e.id}/leave", json={"left_on": "2026-11-02"})
    await client.post("/mdm/v1/staff/nc/check")
    assert (await employee(db_session)).nc_state == "different_status"
    nc.clear()
    await client.post("/mdm/v1/staff/nc/check")
    assert (await employee(db_session)).nc_state == "in_step"
    assert not await todos(db_session)


@pytest.mark.asyncio
async def test_person_recoded_in_nc_stays_the_same_person(client, db_session, nc):
    nc.append(person("000052", pk="pkA"))
    await do_import(client, roster(row()))
    nc[:] = [person("000099", pk="pkA")]
    await client.post("/mdm/v1/staff/nc/check")
    e = await employee(db_session)
    assert e.nc_person_code == "000099" and e.nc_state == "different_code"
    report = (await client.get("/mdm/v1/staff/nc/report")).json()
    assert report["not_in_uniops"] == []
    [todo] = await todos(db_session)
    assert todo.type == "fix_mismatch" and todo.target["new_code"] == "000052"


@pytest.mark.asyncio
async def test_mismatch_raises_one_todo_and_a_department_nc_lacks_cannot_differ(client, db_session, nc):
    finance, bd = await dept(db_session), await dept(db_session, "0112", "Business Development")
    await map_dept(db_session, "Finance", finance)
    nc[:] = [person(dept="0104"), person("000300", dept="0103")]
    await do_import(client, roster(row()))
    await client.post("/mdm/v1/staff/nc/check")
    await client.post("/mdm/v1/staff/nc/check")
    assert [t.type for t in await todos(db_session)] == ["fix_mismatch"]
    assert (await employee(db_session)).nc_state == "different_department"

    nc[0] = person(dept="0103")
    await client.post("/mdm/v1/staff/nc/check")
    assert not await todos(db_session)
    e = await employee(db_session)
    await client.patch(f"/mdm/v1/staff/{e.id}", json={"department_id": str(bd.id)})
    await client.post("/mdm/v1/staff/nc/check")
    assert (await employee(db_session)).nc_state == "in_step"


@pytest.mark.asyncio
async def test_a_todo_closed_by_hand_does_not_come_back(client, db_session, nc):
    nc.append(person())
    await do_import(client, roster(row()))
    e = await employee(db_session)
    await client.post(f"/mdm/v1/staff/{e.id}/leave", json={"left_on": "2026-11-02"})
    [todo] = await todos(db_session)
    assert (await client.post("/mdm/v1/staff/nc-todos/close", json={"ids": [str(todo.id)], "reason": ""})).status_code == 422
    r = await client.post("/mdm/v1/staff/nc-todos/close",
                          json={"ids": [str(todo.id)], "reason": "kept in NC for open claims"})
    assert r.json()["closed"] == 1
    await client.post("/mdm/v1/staff/nc/check")
    assert not await todos(db_session)
    assert (await employee(db_session)).nc_state == "different_status"


@pytest.mark.asyncio
async def test_report_lists_nc_persons_with_no_employee(client, db_session, nc):
    nc[:] = [person(), person("000300")]
    await do_import(client, roster(row()))
    report = (await client.get("/mdm/v1/staff/nc/report")).json()
    assert [p["code"] for p in report["not_in_uniops"]] == ["000300"]
    assert report["summary"]["not_in_uniops"] == 1


@pytest.mark.asyncio
async def test_import_works_when_nc_is_down(client, db_session, monkeypatch):
    from app.services.erp_client import ErpError

    async def _down(client=None):
        raise ErpError(None, "network error")
    monkeypatch.setattr(nc_readback, "fetch_persons", _down)
    result = await do_import(client, roster(row()))
    assert result["nc_checked"] is False
    assert (await employee(db_session)).nc_state == "unknown"


async def open_tasks(db):
    await db.flush()
    return list((await db.execute(select(Task).where(Task.is_completed.is_(False)).execution_options(populate_existing=True))).scalars())


@pytest.mark.asyncio
async def test_one_task_while_anything_is_open_and_it_closes_itself(client, db_session, nc):
    nc[:] = [person(), person("000053")]
    await do_import(client, roster(row(), row("000053", email="b@example.com")))
    for number in ("000052", "000053"):
        e = await employee(db_session, number)
        await client.post(f"/mdm/v1/staff/{e.id}/leave", json={"left_on": "2026-11-02"})
    [task] = await open_tasks(db_session)
    assert task.title == "NC person updates pending: 2"
    assert task.assigned_role == "system_admin" and task.document_type == "nc_person"
    nc[:] = [person()]
    await client.post("/mdm/v1/staff/nc/check")
    [task] = await open_tasks(db_session)
    assert task.title == "NC person updates pending: 1"
    nc.clear()
    await client.post("/mdm/v1/staff/nc/check")
    assert not await open_tasks(db_session)


@pytest.mark.asyncio
async def test_old_todo_is_flagged_overdue(client, db_session, nc):
    nc.append(person())
    await do_import(client, roster(row()))
    e = await employee(db_session)
    await client.post(f"/mdm/v1/staff/{e.id}/leave", json={"left_on": "2026-11-02"})
    [todo] = await todos(db_session)
    todo.created_at = datetime.now(timezone.utc) - timedelta(days=15)
    await db_session.flush()
    await staff_sync(db_session)
    [task] = await open_tasks(db_session)
    assert "over 14 days" in task.title and task.priority == "urgent"
    assert (await client.get("/mdm/v1/staff/nc-todos?status=open")).json()["items"][0]["overdue"] is True


async def staff_sync(db):
    await nc_readback.sync_task(db)
    await db.flush()


# ── Permissions ──────────────────────────────────────────────────────────────

@pytest.mark.parametrize("key,method,path", [
    ("employee.view", "get", "/mdm/v1/staff"),
    ("employee.view", "get", "/mdm/v1/staff/nc/report"),
    ("employee.import", "get", "/mdm/v1/staff/imports"),
])
@pytest.mark.asyncio
async def test_employee_data_needs_the_key_and_the_key_admits(client, monkeypatch, key, method, path):
    from app.main import app
    from app.core.deps import get_token_payload
    import uniops_authz.core as core

    held: set[str] = set()

    async def _roles(db, user_id, base_role):
        return {base_role}

    async def _matrix(db):
        return {"requester": held}
    monkeypatch.setattr(core, "user_role_codes", _roles)
    monkeypatch.setattr(core, "_effective_matrix", _matrix)
    app.dependency_overrides[get_token_payload] = lambda: {"sub": str(uuid.uuid4()), "role": "requester"}

    denied = await getattr(client, method)(path)
    assert denied.status_code == 403
    held.add(key)
    allowed = await getattr(client, method)(path)
    assert allowed.status_code == 200


@pytest.mark.asyncio
async def test_correct_number_is_system_admin_only(client, db_session, nc):
    from app.main import app
    from app.core.deps import get_token_payload
    await do_import(client, roster(row()))
    e = await employee(db_session)
    app.dependency_overrides[get_token_payload] = lambda: {"sub": str(uuid.uuid4()), "role": "requester"}
    r = await client.post(f"/mdm/v1/staff/{e.id}/correct-number", json={"new_number": "000995", "reason": "x"})
    assert r.status_code == 403
