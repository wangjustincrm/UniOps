"""★ Review (uniops-8b): a message filed and never sent was only listed; nothing
ever tried again. HSE sends it again by hand (Settings → Undelivered email)."""
import uuid

from app.models.communication import Communication
from app.services import mail


async def _unsent(db) -> Communication:
    row = Communication(id=uuid.uuid4(), direction="out", from_addr="safety@example.com",
                        to_addrs=["someone@example.com"], subject="An expiry notice",
                        body_text="Body")
    db.add(row)
    await db.flush()
    return row


async def test_hse_sends_it_again(hse_manager, worker, db_session, monkeypatch):
    tried: list = []

    async def fake_deliver(cid):
        tried.append(cid)
        return True
    monkeypatch.setattr(mail, "deliver", fake_deliver)
    row = await _unsent(db_session)
    _, hse = hse_manager
    _, someone = worker
    listed = (await hse.get("/api/v1/communications/undelivered")).json()
    assert str(row.id) in {r["id"] for r in listed}
    assert (await someone.post(f"/api/v1/communications/{row.id}/send-again")).status_code == 403
    got = await hse.post(f"/api/v1/communications/{row.id}/send-again")
    assert got.status_code == 200 and got.json() == {"sent": True, "message": "Sent."}
    assert tried == [row.id]


async def test_still_failing_says_where_to_look(hse_manager, db_session, monkeypatch):
    async def fake_deliver(cid):
        return False
    monkeypatch.setattr(mail, "deliver", fake_deliver)
    row = await _unsent(db_session)
    _, hse = hse_manager
    body = (await hse.post(f"/api/v1/communications/{row.id}/send-again")).json()
    assert body["sent"] is False and "Company Settings" in body["message"]


async def test_one_already_sent_is_not_sent_twice(hse_manager, db_session, monkeypatch):
    from datetime import datetime, timezone
    monkeypatch.setattr(mail, "deliver", lambda cid: (_ for _ in ()).throw(AssertionError("sent")))
    row = await _unsent(db_session)
    row.sent_at = datetime.now(timezone.utc)
    await db_session.flush()
    _, hse = hse_manager
    assert (await hse.post(f"/api/v1/communications/{row.id}/send-again")).status_code == 409
