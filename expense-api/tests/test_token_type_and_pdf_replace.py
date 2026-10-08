"""Two small hardening fixes.

L1 — token type. identity-api signs access AND refresh tokens with the same
secret and separates them by a `type` claim. epms-api and file-api check it;
expense-api did not, so a refresh token authenticated every OA endpoint.

M8 — Regenerate PDF appended. Each press left another <claim_number>.pdf in
the attachments card and another blob in file-api, with nothing to say which
one was current.
"""
import uuid
from datetime import date

import pytest

from app.models.expense import ExpenseClaim
from tests.conftest import _client, _make_token


# ── L1: refresh tokens are not session tokens ─────────────────────────────────

async def test_a_refresh_token_is_refused():
    async with _client(_make_token("system_admin", token_type="refresh")) as c:
        r = await c.get("/api/v1/expenses")
    assert r.status_code == 401


async def test_a_token_with_no_type_claim_is_refused():
    """Belt and braces — anything not explicitly an access token is out."""
    async with _client(_make_token("system_admin", token_type="")) as c:
        r = await c.get("/api/v1/expenses")
    assert r.status_code == 401


async def test_an_access_token_still_works(admin_client):
    r = await admin_client.get("/api/v1/expenses")
    assert r.status_code == 200


# ── M8: regenerating the TRA PDF replaces, it does not pile up ────────────────

async def _approved_tra(db_session) -> ExpenseClaim:
    tra = ExpenseClaim(
        claim_number=f"TRA-PDF-{uuid.uuid4().hex[:8]}", claim_type="TRA",
        employee_id=uuid.uuid4(), employee_name="Traveler",
        department_name="Ops", submission_date=date(2026, 9, 10),
        status="approved", created_by=uuid.uuid4(),
    )
    db_session.add(tra)
    await db_session.commit()
    return tra


@pytest.fixture
def fake_file_server(mocker):
    """file-api stands in: each upload returns a fresh storage key, and deletes
    are recorded so the test can see the superseded blob being cleaned up."""
    keys, deleted = [], []

    async def _upload(data, filename, mime, kind, doc_id, token):
        key = uuid.uuid4()
        keys.append(key)
        return key

    async def _delete(storage_key, token):
        deleted.append(storage_key)

    mocker.patch("app.api.v1.travel.upload_to_file_server", side_effect=_upload)
    mocker.patch("app.api.v1.travel.delete_from_file_server", side_effect=_delete)
    return {"keys": keys, "deleted": deleted}


async def _attachments(db_session, claim_id, filename):
    """Read back as plain columns. The endpoint writes from its own session, so
    an ORM query here would hand back this session's cached objects and a
    refresh of them mid-comprehension trips MissingGreenlet."""
    from sqlalchemy import text
    await db_session.rollback()          # end this session's stale snapshot
    rows = (await db_session.execute(text(
        "SELECT file_id FROM expense_attachments "
        "WHERE claim_id = :c AND file_name = :n"),
        {"c": str(claim_id), "n": filename})).all()
    return [r[0] for r in rows]


async def test_regenerating_twice_leaves_one_attachment(admin_client, db_session,
                                                        fake_file_server):
    tra = await _approved_tra(db_session)
    url = f"/api/v1/travel-applications/{tra.id}/pdf"

    first = await admin_client.post(url)
    assert first.status_code == 200, first.text
    second = await admin_client.post(url)
    assert second.status_code == 200, second.text

    file_ids = await _attachments(db_session, tra.id, f"{tra.claim_number}.pdf")
    assert len(file_ids) == 1
    assert file_ids[0] == str(fake_file_server["keys"][-1])
    assert second.json()["replaced"] == 1


async def test_the_superseded_blob_is_deleted_from_file_api(admin_client, db_session,
                                                            fake_file_server):
    tra = await _approved_tra(db_session)
    url = f"/api/v1/travel-applications/{tra.id}/pdf"

    await admin_client.post(url)
    await admin_client.post(url)

    assert fake_file_server["deleted"] == [fake_file_server["keys"][0]]


async def test_the_first_generation_replaces_nothing(admin_client, db_session,
                                                     fake_file_server):
    tra = await _approved_tra(db_session)

    r = await admin_client.post(f"/api/v1/travel-applications/{tra.id}/pdf")

    assert r.status_code == 200
    assert r.json()["replaced"] == 0
    assert fake_file_server["deleted"] == []
