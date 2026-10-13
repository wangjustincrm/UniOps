"""Supervisors edit reports; an anonymous one stays anonymous to everyone but HSE.

Round 6, answer box 4: "Supervisors shall have edit rights to all incidents,
near misses and hazard assessments to help manage them except an anonymous
report, which will stay that way when other supervisors view it. only the HSE
role can see what user ID was used to submit the incident to allow for further
private discussions."
"""
from datetime import datetime, timedelta, timezone

from tests.conftest import authed_client, make_token, make_user

SIGNATURE = "data:image/png;base64,iVBORw0KGgo="


async def _report(test_engine, db, *, anonymous: bool, kind: str = "medical"):
    reporter = await make_user(test_engine, role="requester", full_name="Dana Reporter")
    async with authed_client(make_token(reporter.id, reporter.role), db) as c:
        made = await c.post("/api/v1/incidents", json={
            "form_kind": kind, "title": "Slipped by the dryer",
            "occurred_at": (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat(),
            "incident_type": "near_miss" if kind == "near_miss" else "first_aid",
            "is_anonymous": anonymous, "signature": SIGNATURE,
            "description": "Wet floor, no sign."})
        assert made.status_code in (200, 201), made.text
        iid = made.json()["id"]
        assert (await c.post(f"/api/v1/incidents/{iid}/submit")).status_code == 200
    return reporter, iid


async def _client_as(test_engine, db, role):
    who = await make_user(test_engine, role=role)
    return who, authed_client(make_token(who.id, who.role), db)


class TestAnAnonymousReportStaysAnonymous:
    async def test_a_supervisor_sees_nothing_that_leads_back(
            self, test_engine, db_session):
        _, iid = await _report(test_engine, db_session, anonymous=True)
        _, sup = await _client_as(test_engine, db_session, "area_supervisor")
        async with sup as c:
            got = (await c.get(f"/api/v1/incidents/{iid}")).json()
        assert got["is_anonymous"] is True
        assert got["reported_by"] is None and got["reported_by_name"] is None
        assert got["signature"] is None and got["anonymous_reporter_name"] is None

    async def test_hse_sees_who_it_was(self, test_engine, db_session, hse_manager):
        reporter, iid = await _report(test_engine, db_session, anonymous=True)
        _, hse = hse_manager
        got = (await hse.get(f"/api/v1/incidents/{iid}")).json()
        assert got["anonymous_reporter_name"] == "Dana Reporter"
        assert got["reported_by"] == str(reporter.id)

    async def test_a_named_report_is_unchanged(self, test_engine, db_session):
        """The admission beside the denial: only anonymous reports are masked."""
        reporter, iid = await _report(test_engine, db_session, anonymous=False)
        _, sup = await _client_as(test_engine, db_session, "area_supervisor")
        async with sup as c:
            got = (await c.get(f"/api/v1/incidents/{iid}")).json()
        assert got["reported_by"] == str(reporter.id)
        assert got["reported_by_name"] == "Dana Reporter"
        assert got["signature"] == SIGNATURE

    async def test_the_change_log_does_not_name_them_either(
            self, test_engine, db_session, hse_manager):
        """★ The trail named the reporter on the line that created the report,
        and carried their user id as the value of reported_by."""
        reporter, iid = await _report(test_engine, db_session, anonymous=True)
        _, sup = await _client_as(test_engine, db_session, "area_supervisor")
        params = {"table": "ehs_incidents", "row_id": iid}
        async with sup as c:
            trail = (await c.get("/api/v1/change-log", params=params)).json()
        assert trail, "the trail itself must still be there"
        assert all(e["changed_by_name"] != "Dana Reporter" for e in trail)
        assert all(str(reporter.id) not in (e["new_value"] or "") for e in trail)

        _, hse = hse_manager
        named = (await hse.get("/api/v1/change-log", params=params)).json()
        assert any(e["changed_by_name"] == "Dana Reporter" for e in named)


class TestASupervisorEditsTheReport:
    async def test_the_account_can_be_corrected(self, test_engine, db_session):
        _, iid = await _report(test_engine, db_session, anonymous=False)
        _, sup = await _client_as(test_engine, db_session, "area_supervisor")
        async with sup as c:
            got = await c.patch(f"/api/v1/incidents/{iid}", json={
                "title": "Slipped on condensate by dryer 2",
                "description": "Condensate from the dryer; no sign out.",
                "immediate_action_taken": "Coned off and mopped."})
        assert got.status_code == 200, got.text
        body = got.json()
        assert body["title"] == "Slipped on condensate by dryer 2"
        assert body["immediate_action_taken"] == "Coned off and mopped."

    async def test_a_near_miss_too(self, test_engine, db_session):
        _, iid = await _report(test_engine, db_session, anonymous=False, kind="near_miss")
        _, sup = await _client_as(test_engine, db_session, "area_supervisor")
        async with sup as c:
            got = await c.patch(f"/api/v1/incidents/{iid}", json={"title": "Pallet nearly fell"})
        assert got.status_code == 200, got.text

    async def test_an_anonymous_one_stays_anonymous_after_editing(
            self, test_engine, db_session):
        _, iid = await _report(test_engine, db_session, anonymous=True)
        _, sup = await _client_as(test_engine, db_session, "area_supervisor")
        async with sup as c:
            got = await c.patch(f"/api/v1/incidents/{iid}", json={"title": "Edited"})
        assert got.status_code == 200, got.text
        assert got.json()["is_anonymous"] and got.json()["reported_by"] is None

    async def test_an_employee_cannot(self, test_engine, db_session):
        _, iid = await _report(test_engine, db_session, anonymous=False)
        _, worker = await _client_as(test_engine, db_session, "requester")
        async with worker as c:
            got = await c.patch(f"/api/v1/incidents/{iid}", json={"title": "Mine now"})
        assert got.status_code == 403

    async def test_editing_withdraws_the_review_and_a_closed_one_is_refused(
            self, test_engine, db_session, hse_manager):
        _, iid = await _report(test_engine, db_session, anonymous=False)
        _, hse = hse_manager
        assert (await hse.post(f"/api/v1/incidents/{iid}/hse-review")).status_code == 200
        who, sup = await _client_as(test_engine, db_session, "area_supervisor")
        async with sup as c:
            edited = await c.patch(f"/api/v1/incidents/{iid}", json={"description": "More detail."})
            assert edited.status_code == 200, edited.text
            assert edited.json()["hse_reviewed_at"] is None, "what was reviewed has changed"

        assert (await hse.post(f"/api/v1/incidents/{iid}/hse-review")).status_code == 200
        assert (await hse.post(f"/api/v1/incidents/{iid}/close")).status_code == 200
        async with authed_client(make_token(who.id, who.role), db_session) as c:
            closed = await c.patch(f"/api/v1/incidents/{iid}", json={"title": "Too late"})
        assert closed.status_code == 409 and "Reopen it first" in closed.text
