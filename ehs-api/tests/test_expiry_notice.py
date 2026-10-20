"""Round 8: a rule emails an address N days before certificates expire.

"This rule will allow notifications to be emailed to a specified email address
and have a field for the number of days notice. For example the notice would be
sent to an email address when every certificate is expiring in 32 days."
"""
import uuid
from datetime import date, timedelta

from sqlalchemy import select

from app.models.communication import Communication
from app.models.mirrors import User
from app.models.training import Course, TrainingRecord, WorkerCertification
from app.services import expiry_notice

TODAY = date(2026, 10, 20)


async def _notices(db) -> list[Communication]:
    return list((await db.execute(select(Communication).where(
        Communication.template_key == "cert_expiry_notice"))).scalars())


def _record(user, *, expires_in: int, course="Forklift", completed_ago: int = 1000, course_id=None):
    return TrainingRecord(
        id=uuid.uuid4(), user_id=user.id, user_name=user.full_name,
        course_id=course_id, course_label=course,
        completed_on=TODAY - timedelta(days=completed_ago),
        expires_on=TODAY + timedelta(days=expires_in), delivery="external")


async def _rule(client, days=32, email="hse-desk@example.com"):
    made = await client.post("/api/v1/training/expiry-rules",
                             json={"email": email, "days_before": days})
    assert made.status_code == 201, made.text
    return made.json()


async def test_hse_keeps_the_rules(hse_manager):
    _, client = hse_manager
    rule = await _rule(client)
    assert (rule["email"], rule["days_before"], rule["is_active"]) == ("hse-desk@example.com", 32, True)
    listed = (await client.get("/api/v1/training/expiry-rules")).json()
    assert [r["id"] for r in listed] == [rule["id"]]
    changed = await client.patch(f"/api/v1/training/expiry-rules/{rule['id']}",
                                 json={"days_before": 60, "is_active": False})
    assert changed.status_code == 200 and changed.json()["days_before"] == 60
    assert (await client.delete(f"/api/v1/training/expiry-rules/{rule['id']}")).status_code == 204
    assert (await client.get("/api/v1/training/expiry-rules")).json() == []


async def test_a_rule_needs_an_address_and_a_number_of_days(hse_manager):
    _, client = hse_manager
    for body in ({"email": "not an address", "days_before": 32},
                 {"email": "a@example.com", "days_before": 0}):
        assert (await client.post("/api/v1/training/expiry-rules", json=body)).status_code == 422


async def test_a_worker_cannot_set_where_certificates_are_emailed(worker):
    """The email names people and their tickets; it goes where HSE says."""
    _, client = worker
    made = await client.post("/api/v1/training/expiry-rules",
                             json={"email": "me@example.com", "days_before": 32})
    assert made.status_code == 403
    assert (await client.get("/api/v1/training/expiry-rules")).status_code == 403


async def test_what_is_inside_the_window_is_sent_once(hse_manager, worker, db_session):
    _, client = hse_manager
    person, _ = worker
    await _rule(client, days=32)
    inside = _record(person, expires_in=32, course="Forklift")
    outside = _record(person, expires_in=33, course="Confined space")
    licence = WorkerCertification(
        id=uuid.uuid4(), user_id=person.id, user_name=person.full_name,
        cert_type_label="Lift truck licence", expires_on=TODAY + timedelta(days=5))
    db_session.add_all([inside, outside, licence])
    await db_session.flush()

    assert await expiry_notice.send_due(db_session, today=TODAY) == 1
    [mail] = await _notices(db_session)
    assert mail.to_addrs == ["hse-desk@example.com"]
    assert mail.subject == "2 certificates expiring within 32 days"
    assert "Lift truck licence" in mail.body_text and "(in 5 days)" in mail.body_text
    assert "Forklift" in mail.body_text and "(in 32 days)" in mail.body_text
    assert "Confined space" not in mail.body_text
    # Listed soonest first.
    assert mail.body_text.index("Lift truck licence") < mail.body_text.index("Forklift")

    # The next tick has nothing new.
    assert await expiry_notice.send_due(db_session, today=TODAY) == 0
    # A day later the one that was 33 days out enters the window, alone.
    assert await expiry_notice.send_due(db_session, today=TODAY + timedelta(days=1)) == 1
    later = [m for m in await _notices(db_session) if m.id != mail.id]
    assert len(later) == 1 and "Confined space" in later[0].body_text
    assert "Forklift" not in later[0].body_text


async def test_each_rule_is_told_on_its_own_terms(hse_manager, worker, db_session):
    _, client = hse_manager
    person, _ = worker
    await _rule(client, days=32, email="hse-desk@example.com")
    await _rule(client, days=7, email="supervisors@example.com")
    db_session.add(_record(person, expires_in=20))
    await db_session.flush()

    assert await expiry_notice.send_due(db_session, today=TODAY) == 1
    assert [m.to_addrs for m in await _notices(db_session)] == [["hse-desk@example.com"]]
    assert await expiry_notice.send_due(db_session, today=TODAY + timedelta(days=13)) == 1
    assert sorted(m.to_addrs[0] for m in await _notices(db_session)) == [
        "hse-desk@example.com", "supervisors@example.com"]


async def test_a_switched_off_rule_sends_nothing(hse_manager, worker, db_session):
    _, client = hse_manager
    person, _ = worker
    rule = await _rule(client)
    await client.patch(f"/api/v1/training/expiry-rules/{rule['id']}", json={"is_active": False})
    db_session.add(_record(person, expires_in=3))
    await db_session.flush()
    assert await expiry_notice.send_due(db_session, today=TODAY) == 0


async def test_a_renewed_course_and_a_leaver_are_not_reported(hse_manager, worker, db_session):
    """Somebody who has already retaken the course has nothing to renew; somebody
    who has left has nothing to renew either."""
    _, client = hse_manager
    person, _ = worker
    await _rule(client)
    course_id = uuid.uuid4()
    db_session.add(Course(id=course_id, code=f"WHMIS-{course_id.hex[:6]}", name="WHMIS test"))
    await db_session.flush()
    db_session.add_all([
        _record(person, expires_in=10, course="WHMIS", course_id=course_id, completed_ago=1000),
        _record(person, expires_in=1000, course="WHMIS", course_id=course_id, completed_ago=5),
    ])
    gone = User(id=uuid.uuid4(), full_name="Left Last Year", email=f"gone-{uuid.uuid4().hex[:6]}@x.com",
                role="requester", is_active=False)
    db_session.add(gone)
    await db_session.flush()
    db_session.add(_record(gone, expires_in=10, course="Forklift"))
    await db_session.flush()

    assert await expiry_notice.send_due(db_session, today=TODAY) == 0


async def test_an_expiry_moved_to_a_new_date_is_told_again(hse_manager, worker, db_session):
    _, client = hse_manager
    person, _ = worker
    await _rule(client)
    licence = WorkerCertification(
        id=uuid.uuid4(), user_id=person.id, user_name=person.full_name,
        cert_type_label="Lift truck licence", expires_on=TODAY + timedelta(days=5))
    db_session.add(licence)
    await db_session.flush()
    assert await expiry_notice.send_due(db_session, today=TODAY) == 1
    licence.expires_on = TODAY + timedelta(days=20)
    await db_session.flush()
    assert await expiry_notice.send_due(db_session, today=TODAY) == 1
