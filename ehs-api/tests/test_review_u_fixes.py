"""Review of UAT 14/15 by uniops-9e: U1, U2, U3, U4, U6."""
import uuid
from datetime import date, datetime, timedelta, timezone

import pytest
from sqlalchemy import text

from app.models.config import EhsConfig
from app.models.incident import Incident
from tests.conftest import authed_client, make_token, make_user
from tests.test_incident_types_r12 import _near_miss

SIGNATURE = "data:image/png;base64,iVBORw0KGgo="


@pytest.fixture(autouse=True)
async def _config(db_session):
    if await db_session.get(EhsConfig, 1) is None:
        db_session.add(EhsConfig(id=1))
        await db_session.commit()


# ── U1 ──────────────────────────────────────────────────────────────────────
async def test_u1_a_signed_and_reviewed_investigation_keeps_both_when_only_the_batch_code_changes(
        hse_manager, db_session):
    me, hse = hse_manager
    made = (await hse.post("/api/v1/incidents", json=_near_miss("near_miss"))).json()
    iid = made["id"]
    await hse.post(f"/api/v1/incidents/{iid}/submit")
    inv = {"sequence_of_events": "Lid left off the blender", "investigator_id": str(me.id)}
    causes = {"causes": [{"cause_type": "immediate", "label": "No changeover check"}]}
    assert (await hse.put(f"/api/v1/incidents/{iid}/investigation", json=inv)).status_code == 200
    await hse.put(f"/api/v1/incidents/{iid}/causes", json=causes)
    signed = await hse.post(f"/api/v1/incidents/{iid}/investigation/sign", json={"signature": SIGNATURE})
    assert signed.status_code == 200, signed.text
    row = await db_session.get(Incident, uuid.UUID(iid))
    row.hse_reviewed_at = datetime.now(timezone.utc)
    await db_session.commit()

    async def state():
        await db_session.commit()
        i = (await hse.get(f"/api/v1/incidents/{iid}")).json()
        v = (await hse.get(f"/api/v1/incidents/{iid}/investigation")).json()
        return i["hse_reviewed_at"] is not None, v["completed_at"] is not None

    # The investigation page's save, with only the batch code changed.
    await hse.patch(f"/api/v1/incidents/{iid}", json={"batch_code": "B-77"})
    assert (await hse.put(f"/api/v1/incidents/{iid}/investigation", json=inv)).status_code == 200
    await hse.put(f"/api/v1/incidents/{iid}/causes", json=causes)
    assert await state() == (True, True)
    # The investigator renamed since: not a change to the investigation.
    from app.models.mirrors import User
    person = await db_session.get(User, me.id)
    person.full_name = "Renamed Investigator"
    await db_session.commit()
    await hse.put(f"/api/v1/incidents/{iid}/investigation", json=inv)
    assert await state() == (True, True)
    # What it says changes: un-signed, and the review withdrawn.
    await hse.put(f"/api/v1/incidents/{iid}/investigation", json={**inv, "sequence_of_events": "Other"})
    assert await state() == (False, False)


# ── U2 ──────────────────────────────────────────────────────────────────────
async def test_u2_the_monthly_list_and_audit_pack_count_a_talk_held_again_when_held(
        hse_manager, test_engine, db_session):
    from app.services import audit, monthly
    _, hse = hse_manager
    sup = await make_user(test_engine, role="area_supervisor")
    talk = (await hse.post("/api/v1/toolbox-talks", json={"title": "Ladders",
                                                          "period_month": "2042-08-01"})).json()
    await hse.post(f"/api/v1/toolbox-talks/{talk['id']}/publish", json={"supervisor_ids": [str(sup.id)]})
    async with authed_client(make_token(sup.id, sup.role), db_session) as k:
        held = await k.post(f"/api/v1/toolbox-talks/{talk['id']}/hold-again", json={
            "signatures": [{"person_name": "P", "signature": SIGNATURE}], "held_at": "2042-11-12T15:00:00Z"})
        assert held.status_code == 201, held.text
    aug = await monthly._toolbox(db_session, sup.id, date(2042, 8, 1))
    nov = await monthly._toolbox(db_session, sup.id, date(2042, 11, 1))
    assert (aug.done, nov.done) == (False, True)   # August's hand-out is still to do
    pack_aug = await audit.toolbox(db_session, audit.Period(date(2042, 8, 1), date(2042, 9, 1)))
    assert (pack_aug["deliveries"], pack_aug["delivered"], pack_aug["completion_rate"] in (0, 0.0)) == (1, 0, True)
    pack_nov = await audit.toolbox(db_session, audit.Period(date(2042, 11, 1), date(2042, 12, 1)))
    assert pack_nov["held_again"] == 1


# ── U3 ──────────────────────────────────────────────────────────────────────
async def test_u3_a_feedback_only_quiz_passes_and_a_marked_question_is_worth_a_point(
        hse_manager, test_engine, db_session):
    from tests.test_quiz_roster import _quiz
    _, hse = hse_manager
    quiz = await _quiz(db_session, None)
    zero = await hse.put(f"/api/v1/quizzes/{quiz.id}/questions", json={"questions": [
        {"prompt": "x", "kind": "true_false", "points": 0, "correct": ["t"],
         "options": [{"key": "t", "label": "True"}, {"key": "f", "label": "False"}]}]})
    assert zero.status_code == 422
    put = await hse.put(f"/api/v1/quizzes/{quiz.id}/questions", json={"questions": [
        {"prompt": "Rate it", "kind": "rating"}, {"prompt": "Anything else?", "kind": "comment"}]})
    assert put.status_code == 200, put.text
    person = await make_user(test_engine)
    async with authed_client(make_token(person.id, person.role), db_session) as c:
        got = (await c.post(f"/api/v1/quizzes/{quiz.id}/attempts", json={"answers": {}})).json()
    assert got["passed"] is True and got["points_possible"] == 0


# ── U4 ──────────────────────────────────────────────────────────────────────
async def test_u4_a_deadline_escalated_before_gets_its_coordinator_task(db_session):
    from app.services import tasks as task_service
    from app.tasks import scheduler
    from tests.test_scheduler import _deadline
    now = datetime(2026, 11, 17, 12, tzinfo=timezone.utc)
    d = await _deadline(db_session, due_at=now + timedelta(hours=20), level=1)
    await task_service.open_task(
        db_session, task_type=task_service.TASK_STATUTORY, doc_type=task_service.DOC_INCIDENT,
        doc_id=d.source_id, doc_number="INC-T", title="mol 48h — due", assigned_role="ehs_manager",
        description="x", priority="normal")
    await db_session.flush()
    assert await scheduler.sweep_statutory(db_session, now) == 0     # not a new escalation
    await db_session.flush()
    roles = (await db_session.execute(text(
        "select assigned_role from tasks where document_id = :d and is_completed = false"),
        {"d": str(d.source_id)})).scalars().all()
    assert sorted(roles) == ["ehs_coordinator", "ehs_manager"]
    await scheduler.sweep_statutory(db_session, now)                  # and only once
    await db_session.flush()
    assert (await db_session.execute(text(
        "select count(*) from tasks where document_id = :d"), {"d": str(d.source_id)})).scalar() == 2


# ── U6 ──────────────────────────────────────────────────────────────────────
async def test_u6_feedback_from_any_attempt_and_comments_in_no_telling_order(
        hse_manager, test_engine, db_session):
    from tests.test_quiz_roster import _quiz
    _, hse = hse_manager
    quiz = await _quiz(db_session, None)
    qs = (await hse.put(f"/api/v1/quizzes/{quiz.id}/questions", json={"questions": [
        {"prompt": "Rate it", "kind": "rating"}, {"prompt": "Anything else?", "kind": "comment"}]})).json()
    rate, comment = qs[0]["id"], qs[1]["id"]
    zed, amy = await make_user(test_engine), await make_user(test_engine)
    for who, answers in ((zed, {rate: ["4"], comment: ["zebra crossing"]}),
                         (amy, {comment: ["apples"]}),
                         (zed, {})):                     # Zed retakes and skips the feedback
        async with authed_client(make_token(who.id, who.role), db_session) as c:
            assert (await c.post(f"/api/v1/quizzes/{quiz.id}/attempts",
                                 json={"answers": answers})).status_code == 201
    fb = {f["kind"]: f for f in (await hse.get(f"/api/v1/quizzes/{quiz.id}/results")).json()["feedback"]}
    assert fb["rating"]["answered"] == 1 and fb["rating"]["average"] == 4.0
    assert fb["comment"]["comments"] == ["apples", "zebra crossing"]
