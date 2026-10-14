"""Supervisors edit reports; an anonymous one stays anonymous to everyone but HSE.

Round 6, answer box 4: "Supervisors shall have edit rights to all incidents,
near misses and hazard assessments to help manage them except an anonymous
report, which will stay that way when other supervisors view it. only the HSE
role can see what user ID was used to submit the incident to allow for further
private discussions."
"""
from datetime import datetime, timedelta, timezone

from app.core.actor import set_actor
from tests.conftest import authed_client, make_token, make_user
from tests.test_worker_reach import risk_matrix  # noqa: F401  (fixture)

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


class TestWhatTheyMadeOnTheirOwnReport:
    """★ Round-6 review (uniops-93): the report was masked, and what hangs on it
    was not — a photo the reporter took, the risk assessment they made on the
    report screen. Each signed it with their name."""

    async def _photo(self, db, iid, who):
        import uuid as _uuid
        from app.models.incident import IncidentAttachment
        # As the upload endpoint would: the uploader is the actor on the trail.
        set_actor(who.id, who.full_name)
        db.add(IncidentAttachment(
            id=_uuid.uuid4(), incident_id=_uuid.UUID(iid), storage_key=_uuid.uuid4(),
            filename="scene.jpg", content_type="image/jpeg", file_size=1,
            uploaded_by=who.id, uploaded_by_name=who.full_name))
        await db.commit()
        set_actor(None)

    async def test_their_photo_does_not_name_them(self, test_engine, db_session, hse_manager):
        reporter, iid = await _report(test_engine, db_session, anonymous=True)
        await self._photo(db_session, iid, reporter)
        helper, _ = await _client_as(test_engine, db_session, "area_supervisor")
        await self._photo(db_session, iid, helper)

        _, sup = await _client_as(test_engine, db_session, "area_supervisor")
        async with sup as c:
            listed = (await c.get(f"/api/v1/incidents/{iid}/attachments")).json()
        mine = [a for a in listed if a["by_anonymous_reporter"]]
        assert len(mine) == 1 and mine[0]["uploaded_by_name"] is None
        # The admission: somebody else's upload is still named.
        assert any(a["uploaded_by_name"] == helper.full_name for a in listed)

        _, hse = hse_manager
        named = (await hse.get(f"/api/v1/incidents/{iid}/attachments")).json()
        assert any(a["uploaded_by_name"] == "Dana Reporter" for a in named)

        # The photo's own trail names the uploader; masked too.
        photo_id = next(a["id"] for a in listed if a["by_anonymous_reporter"])
        params = {"table": "ehs_incident_attachments", "row_id": photo_id}
        _, sup2 = await _client_as(test_engine, db_session, "area_supervisor")
        async with sup2 as c:
            trail = (await c.get("/api/v1/change-log", params=params)).json()
        assert trail, "the photo's trail must be there"
        assert all(e["changed_by_name"] != "Dana Reporter" for e in trail)
        raw = (await hse.get("/api/v1/change-log", params=params)).json()
        assert any(e["changed_by_name"] == "Dana Reporter" for e in raw)

    async def test_their_risk_assessment_does_not_name_them(
            self, test_engine, db_session, hse_manager, risk_matrix):
        reporter, iid = await _report(test_engine, db_session, anonymous=True)
        async with authed_client(make_token(reporter.id, reporter.role), db_session) as c:
            made = await c.post("/api/v1/risk/assessments", json={
                "source_type": "incident", "source_id": iid,
                "hazard_label": "Wet floor", "consequence_type": "people",
                "likelihood_code": "C", "severity_level": 3})
        assert made.status_code in (200, 201), made.text
        aid = made.json()["id"]
        url = f"/api/v1/risk/assessments?source_type=incident&source_id={iid}"

        _, sup = await _client_as(test_engine, db_session, "area_supervisor")
        async with sup as c:
            seen = (await c.get(url)).json()
            trail = (await c.get("/api/v1/change-log", params={
                "table": "ehs_risk_assessments", "row_id": aid})).json()
        assert seen[0]["assessed_by_name"] is None and seen[0]["by_anonymous_reporter"]
        assert trail and all(e["changed_by_name"] != "Dana Reporter" for e in trail)
        assert all(str(reporter.id) not in (e["new_value"] or "") for e in trail)
        assert all("Dana Reporter" not in (e["new_value"] or "") for e in trail)

        _, hse = hse_manager
        assert (await hse.get(url)).json()[0]["assessed_by_name"] == "Dana Reporter"

    async def test_on_a_named_report_the_assessment_is_signed(
            self, test_engine, db_session, risk_matrix):
        reporter, iid = await _report(test_engine, db_session, anonymous=False)
        async with authed_client(make_token(reporter.id, reporter.role), db_session) as c:
            assert (await c.post("/api/v1/risk/assessments", json={
                "source_type": "incident", "source_id": iid,
                "hazard_label": "Wet floor", "consequence_type": "people",
                "likelihood_code": "C", "severity_level": 3})).status_code in (200, 201)
        _, sup = await _client_as(test_engine, db_session, "area_supervisor")
        async with sup as c:
            seen = (await c.get(
                f"/api/v1/risk/assessments?source_type=incident&source_id={iid}")).json()
        assert seen[0]["assessed_by_name"] == "Dana Reporter"
        assert not seen[0]["by_anonymous_reporter"]


class TestWhenItHappenedOnceAClockRunsFromIt:
    """★ Round-6 review (uniops-93): editing the time left the MOL 48 hours
    counted from a time the record no longer said — and refusing it outright
    left a wrong time with no way to correct it. HSE corrects it; the clocks
    still owed move with it; anybody else is told why not."""

    async def _clocked(self, test_engine, db, hse):
        _, iid = await _report(test_engine, db, anonymous=False)
        got = await hse.post(f"/api/v1/incidents/{iid}/classify", json={
            "injury_class": "medical_aid", "mol_reportable": True,
            "mol_reportable_reason": "critical_injury"})
        assert got.status_code == 200, got.text
        deadlines = (await hse.get(f"/api/v1/incidents/{iid}")).json()["deadlines"]
        assert {d["kind"] for d in deadlines} >= {"mol_48h"}
        return iid, deadlines

    async def test_a_supervisor_is_told_only_hse_can(self, test_engine, db_session, hse_manager):
        _, hse = hse_manager
        iid, _ = await self._clocked(test_engine, db_session, hse)
        _, sup = await _client_as(test_engine, db_session, "area_supervisor")
        later = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
        async with sup as c:
            refused = await c.patch(f"/api/v1/incidents/{iid}", json={"occurred_at": later})
            assert refused.status_code == 409 and "Only HSE" in refused.text
            # The rest of the account is still theirs to correct.
            assert (await c.patch(f"/api/v1/incidents/{iid}",
                                  json={"title": "Fell from the mezzanine"})).status_code == 200

    async def test_hse_moves_it_and_the_owed_clocks_follow(
            self, test_engine, db_session, hse_manager):
        _, hse = hse_manager
        iid, before = await self._clocked(test_engine, db_session, hse)
        mol = next(d for d in before if d["kind"] == "mol_48h")
        new_time = datetime.now(timezone.utc).replace(microsecond=0) - timedelta(minutes=30)

        moved = await hse.patch(f"/api/v1/incidents/{iid}", json={"occurred_at": new_time.isoformat()})
        assert moved.status_code == 200, moved.text
        after = next(d for d in moved.json()["deadlines"] if d["kind"] == "mol_48h")
        assert after["id"] == mol["id"], "the row is reused, so its history stays"
        assert datetime.fromisoformat(after["starts_at"]) == new_time
        assert datetime.fromisoformat(after["due_at"]) == new_time + timedelta(hours=48)

    async def test_a_satisfied_clock_stays_as_recorded(
            self, test_engine, db_session, hse_manager):
        import uuid as _uuid
        from app.models.statutory import StatutoryDeadline
        _, hse = hse_manager
        iid, before = await self._clocked(test_engine, db_session, hse)
        mol = next(d for d in before if d["kind"] == "mol_48h")
        row = await db_session.get(StatutoryDeadline, _uuid.UUID(mol["id"]))
        row.satisfied_at = datetime.now(timezone.utc)
        await db_session.commit()

        new_time = datetime.now(timezone.utc).replace(microsecond=0) - timedelta(minutes=30)
        moved = await hse.patch(f"/api/v1/incidents/{iid}", json={"occurred_at": new_time.isoformat()})
        assert moved.status_code == 200, moved.text
        kept = next(d for d in moved.json()["deadlines"] if d["kind"] == "mol_48h")
        assert kept["starts_at"] == mol["starts_at"] and kept["due_at"] == mol["due_at"]

    async def test_without_a_clock_a_supervisor_corrects_the_time(self, test_engine, db_session):
        """The admission: the lock is on the clock, not on the field."""
        _, iid = await _report(test_engine, db_session, anonymous=False)
        _, sup = await _client_as(test_engine, db_session, "area_supervisor")
        later = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
        async with sup as c:
            assert (await c.patch(f"/api/v1/incidents/{iid}",
                                  json={"occurred_at": later})).status_code == 200


class TestNoNewActionOnAClosedIncident:
    async def test_refused_once_closed_and_taken_while_open(
            self, test_engine, db_session, hse_manager):
        _, hse = hse_manager
        owner = await make_user(test_engine, role="area_supervisor")
        _, iid = await _report(test_engine, db_session, anonymous=False, kind="near_miss")
        body = {"source_type": "incident", "source_id": iid, "title": "Mop the floor",
                "hierarchy_of_control": "administrative", "owner_id": str(owner.id),
                "due_date": (datetime.now(timezone.utc) + timedelta(days=7)).date().isoformat()}
        opened = await hse.post("/api/v1/actions", json=body)
        assert opened.status_code == 201, opened.text
        # Closing waits for open actions; take this one out of the way.
        import uuid as _uuid
        from app.models.action import Action
        row = await db_session.get(Action, _uuid.UUID(opened.json()["id"]))
        row.status = "cancelled"
        await db_session.commit()
        assert (await hse.post(f"/api/v1/incidents/{iid}/close")).status_code == 200, "closed"

        refused = await hse.post("/api/v1/actions", json={**body, "source_type": "observation"})
        assert refused.status_code == 409 and "Reopen it first" in refused.text
