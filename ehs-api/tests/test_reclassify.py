"""The investigator changes what kind of incident it was, before it closes.

Round 7 (answer box): "a medical incident may become a lost time injury after
the initial report is created. allow for the investigator to reclassify the
injury by selecting a different What kind of incident pick list before the
incident is closed."
"""
from datetime import datetime, timedelta, timezone

from tests.conftest import authed_client, make_token, make_user

SIGNATURE = "data:image/png;base64,iVBORw0KGgo="


async def _reported(test_engine, db, kind: str, form_kind: str = "medical"):
    reporter = await make_user(test_engine, role="requester")
    async with authed_client(make_token(reporter.id, reporter.role), db) as c:
        made = await c.post("/api/v1/incidents", json={
            "form_kind": form_kind, "title": "Hurt on the line",
            "occurred_at": (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat(),
            "incident_type": kind, "signature": SIGNATURE, "description": "Hurt."})
        assert made.status_code in (200, 201), made.text
        iid = made.json()["id"]
        assert (await c.post(f"/api/v1/incidents/{iid}/submit")).status_code == 200
    return reporter, iid


async def test_first_aid_becomes_lost_time(test_engine, db_session, hse_manager):
    _, hse = hse_manager
    _, iid = await _reported(test_engine, db_session, "first_aid")
    assert (await hse.post(f"/api/v1/incidents/{iid}/hse-review")).status_code == 200
    got = await hse.post(f"/api/v1/incidents/{iid}/reclassify", json={"incident_type": "lost_time"})
    assert got.status_code == 200, got.text
    body = got.json()
    assert body["incident_type_code"] == "lost_time" and body["injury_class"] == "lost_time"
    assert body["hse_reviewed_at"] is None, "what was reviewed has changed"
    assert any(d["kind"] == "wsib_form7" and d["satisfied_at"] is None for d in body["deadlines"])


async def test_a_clock_it_no_longer_owes_closes_and_does_not_hold_it(
        test_engine, db_session, hse_manager):
    """★ uniops-55: with closing waiting for running clocks, a clock left
    behind by a change of kind would have held the incident open for ever."""
    _, hse = hse_manager
    _, iid = await _reported(test_engine, db_session, "unconscious")
    before = (await hse.get(f"/api/v1/incidents/{iid}")).json()["deadlines"]
    assert any(d["kind"] == "mol_48h" for d in before)

    got = (await hse.post(f"/api/v1/incidents/{iid}/reclassify",
                          json={"incident_type": "first_aid"})).json()
    mol = next(d for d in got["deadlines"] if d["kind"] == "mol_48h")
    assert mol["satisfied_at"] is not None
    assert "no longer applies — incident type changed from Unconscious to First Aid Injury" in mol["evidence_note"]
    assert got["mol_reportable"] is False
    assert (await hse.post(f"/api/v1/incidents/{iid}/hse-review")).status_code == 200
    assert (await hse.post(f"/api/v1/incidents/{iid}/close")).status_code == 200


async def test_who_and_when(test_engine, db_session, hse_manager):
    _, hse = hse_manager
    _, iid = await _reported(test_engine, db_session, "first_aid")
    other_form = await hse.post(f"/api/v1/incidents/{iid}/reclassify",
                                json={"incident_type": "property_damage"})
    assert other_form.status_code == 422 and "different form" in other_form.text

    worker = await make_user(test_engine, role="requester")
    async with authed_client(make_token(worker.id, worker.role), db_session) as c:
        assert (await c.post(f"/api/v1/incidents/{iid}/reclassify",
                             json={"incident_type": "medical_aid"})).status_code == 403
    # Report Only starts no clock, so the review and the close below are free
    # (medical aid would start WSIB's, which holds them — by design).
    sup = await make_user(test_engine, role="area_supervisor")
    async with authed_client(make_token(sup.id, sup.role), db_session) as c:
        assert (await c.post(f"/api/v1/incidents/{iid}/reclassify",
                             json={"incident_type": "report_only"})).status_code == 200

    assert (await hse.post(f"/api/v1/incidents/{iid}/hse-review")).status_code == 200
    assert (await hse.post(f"/api/v1/incidents/{iid}/close")).status_code == 200
    closed = await hse.post(f"/api/v1/incidents/{iid}/reclassify", json={"incident_type": "lost_time"})
    assert closed.status_code == 409 and "Reopen it first" in closed.text


async def test_a_change_of_kind_is_told_and_filed_on_the_incident(test_engine, db_session, hse_manager):
    """★ Round 8, TC-13: "add a notification that the incident class has changed
    and email it. log the communication on the form." """
    import uuid as _uuid
    from sqlalchemy import select
    from app.models.communication import Communication
    from app.models.config import EhsConfig

    cfg = await db_session.get(EhsConfig, 1)
    if cfg is None:
        cfg = EhsConfig(id=1)
        db_session.add(cfg)
    cfg.incident_status_notify = {}
    cfg.incident_notify_include = {"status:kind_changed": {"reporter": True, "supervisor": False}}
    await db_session.commit()

    _, hse = hse_manager
    reporter, iid = await _reported(test_engine, db_session, "first_aid")

    async def told():
        db_session.expire_all()
        return list((await db_session.execute(select(Communication).where(
            Communication.doc_id == _uuid.UUID(iid),
            Communication.template_key == "incident_kind_changed"))).scalars())

    assert (await hse.post(f"/api/v1/incidents/{iid}/reclassify",
                           json={"incident_type": "lost_time"})).status_code == 200
    sent = await told()
    assert len(sent) == 1 and sent[0].to_addrs == [reporter.email]
    assert "From First Aid Injury to Lost Time" in sent[0].body_text

    # The same kind again changes nothing and tells nobody.
    assert (await hse.post(f"/api/v1/incidents/{iid}/reclassify",
                           json={"incident_type": "lost_time"})).status_code == 200
    assert len(await told()) == 1


async def test_a_change_of_kind_is_on_the_timeline(test_engine, db_session, hse_manager):
    """★ Round 9, TC-04: "Log who changed it, and the date in the timeline." """
    user, hse = hse_manager
    _, iid = await _reported(test_engine, db_session, "first_aid")
    body = (await hse.post(f"/api/v1/incidents/{iid}/reclassify",
                           json={"incident_type": "lost_time"})).json()
    [event] = [e for e in body["events"] if e["kind"] == "kind_changed"]
    assert event["by_name"] == user.full_name
    assert event["detail"].startswith("From First Aid Injury to Lost Time")
    assert event["at"]
    # Choosing the same kind again is not a change, and is not on the timeline.
    again = (await hse.post(f"/api/v1/incidents/{iid}/reclassify",
                            json={"incident_type": "lost_time"})).json()
    assert len([e for e in again["events"] if e["kind"] == "kind_changed"]) == 1
