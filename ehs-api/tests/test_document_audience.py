"""Who a document is sent to.

The audience was roles only. A procedure that concerns four people on one line
corresponds to no role, and sending it to whoever holds the nearest one teaches
thirty others to ignore the next thing they are sent.
"""
import uuid

import pytest


async def _published(client, **kw) -> dict:
    body = {
        "title": "Line 2 changeover procedure",
        "category": "procedure",
        "requires_ack": True,
        # Publishing refuses a document with nothing to read, and the real
        # upload needs the file server, which these tests do not run. The id is
        # the only part of that the document itself holds, so this goes through
        # the ordinary create path and fakes only the far side.
        "file_id": str(uuid.uuid4()),
        "filename": "changeover.pdf",
    }
    body.update(kw)
    created = await client.post("/api/v1/documents", json=body)
    assert created.status_code in (200, 201), created.text
    doc = created.json()
    published = await client.post(f"/api/v1/documents/{doc['id']}/publish")
    assert published.status_code in (200, 201), published.text
    return published.json()


async def test_a_document_can_be_sent_to_named_people(hse_manager, worker, test_engine):
    """★ What the third round asked for: specific individuals, not only roles."""
    from tests.conftest import make_user

    _, client = hse_manager
    named, _ = worker
    other = await make_user(test_engine, role="requester")

    doc = await _published(client, ack_roles=[], ack_user_ids=[str(named.id)])
    assert doc["ack_user_ids"] == [str(named.id)]

    status_ = (await client.get(f"/api/v1/documents/{doc['id']}/signing-status")).json()
    assert status_["audience"] == 1, (
        f"only the person named is in the audience, got {status_['audience']}")

    # And it is that person, not merely somebody.
    outstanding = (await client.get(
        f"/api/v1/documents/{doc['id']}/signing-status")).json()
    assert str(other.id) not in str(outstanding)


async def test_naming_somebody_adds_to_a_role_rather_than_narrowing_it(
        hse_manager, worker, test_engine):
    """★ The union, not the intersection.

    The same mistake was made once on the toolbox talk, where naming a
    supervisor filtered the role's list instead of adding to it — so naming
    somebody outside the role sent the talk to nobody at all.
    """
    from tests.conftest import make_user

    manager, client = hse_manager
    outsider = await make_user(test_engine, role="requester")

    doc = await _published(client, ack_roles=["ehs_manager"],
                           ack_user_ids=[str(outsider.id)])
    counted = (await client.get(f"/api/v1/documents/{doc['id']}/signing-status")).json()

    # Both the role's holder and the named outsider. The exact number depends on
    # how many hold the role in this database, so the assertion is the shape:
    # more than the named person alone, and not zero.
    assert counted["audience"] >= 2, (
        f"naming somebody must not narrow the role's audience, got {counted}")
    assert manager.role == "ehs_manager"


async def test_a_document_with_no_audience_still_means_everybody(hse_manager):
    """Unqualified means the plant, which is what it meant before."""
    _, client = hse_manager
    doc = await _published(client, ack_roles=[], ack_user_ids=[])
    counted = (await client.get(f"/api/v1/documents/{doc['id']}/signing-status")).json()
    assert counted["audience"] >= 2


async def test_a_revision_keeps_the_people_it_was_sent_to(hse_manager, worker):
    """★ A correction must not turn three people into the whole plant.

    The audience of a document with no roles listed is everybody — that is what
    an unqualified policy means. So a document sent to three named people and
    to no role, revised once, went out to four hundred: the revision copied the
    roles and not the names, leaving it with no audience at all, which reads as
    all of them.
    """
    _, client = hse_manager
    named, _ = worker

    first = await _published(client, ack_roles=[], ack_user_ids=[str(named.id)])
    before = (await client.get(
        f"/api/v1/documents/{first['id']}/signing-status")).json()["audience"]
    assert before == 1

    revised = await client.post(f"/api/v1/documents/{first['id']}/new-version",
                                json={"version": "2"})
    assert revised.status_code in (200, 201), revised.text
    draft = revised.json()
    assert draft["ack_user_ids"] == [str(named.id)], (
        "the people it was sent to come across with it")

    # Through to publication, because that is when the lists go out.
    await client.patch(f"/api/v1/documents/{draft['id']}",
                       json={"file_id": first["file_id"], "filename": "changeover-v2.pdf"})
    published = await client.post(f"/api/v1/documents/{draft['id']}/publish")
    assert published.status_code in (200, 201), published.text

    after = (await client.get(
        f"/api/v1/documents/{draft['id']}/signing-status")).json()["audience"]
    assert after == 1, f"still the one person it concerns, got {after}"


async def test_the_code_stops_asking_somebody_who_has_already_signed(
        hse_manager, db_session):
    """★ The code by the door should not ask for a signature it will discard.

    `acknowledge` is idempotent and returns the original record, so pressing
    the button after signing in August reported success for something that had
    not just happened.
    """
    manager, client = hse_manager
    doc = await _published(client)

    token = (await client.get(f"/api/v1/documents/{doc['id']}")).json().get("qr_token")
    assert token, "a published document carries a code"

    before = (await client.get(f"/api/v1/qr/{token}")).json()
    assert "acknowledge" in before["actions"]
    assert before["signed_at"] is None

    signed = await client.post(f"/api/v1/documents/{doc['id']}/acknowledge",
                               json={"signature": None})
    assert signed.status_code in (200, 201), signed.text

    after = (await client.get(f"/api/v1/qr/{token}")).json()
    assert after["signed_at"], "the screen has to know it was already signed"
    assert "acknowledge" not in after["actions"], (
        "and stop offering to take it again")


async def test_retiring_a_version_takes_its_signature_task_away(
        hse_manager, worker, db_session):
    """★★ Round 5, TC-22: "policy documents that are Superseded by a later
    version still show up in the My actions list to sign and yet when it is
    opened there is no way to sign the superseeded document."

    Two places retired a document and only one did the whole job: `retire()`
    closed the outstanding read-and-sign tasks, and publishing a new version set
    the status column directly. So everybody who had not yet signed v1 kept a
    task pointing at a version they are not allowed to sign — the sign panel
    only ever appears on the published one — *and* got a second task for v2.
    """
    from sqlalchemy import select

    from app.models.mirrors import Task
    from app.services import tasks as task_service

    _, client = hse_manager
    named, _ = worker

    first = await _published(client, ack_roles=[], ack_user_ids=[str(named.id)])

    async def open_tasks(doc_id: str) -> list[Task]:
        rows = (await db_session.execute(
            select(Task).where(
                Task.document_type == task_service.DOC_DOCUMENT,
                Task.document_id == uuid.UUID(doc_id),
                Task.type == task_service.TASK_READ_SIGN,
                Task.is_completed.is_(False),
            ))).scalars().all()
        return list(rows)

    # It is asked for to begin with, or the assertion below is satisfied by
    # there never having been a task.
    assert len(await open_tasks(first["id"])) == 1, "v1 must ask for a signature"

    revised = (await client.post(f"/api/v1/documents/{first['id']}/new-version",
                                 json={"version": "2"})).json()
    await client.patch(f"/api/v1/documents/{revised['id']}",
                       json={"file_id": first["file_id"], "filename": "v2.pdf"})
    published = await client.post(f"/api/v1/documents/{revised['id']}/publish")
    assert published.status_code in (200, 201), published.text

    assert await open_tasks(first["id"]) == [], (
        "the retired version must stop being chased")
    assert len(await open_tasks(revised["id"])) == 1, (
        "and the version in force must be asked for instead")

    # Not in the list of what this person owes, either.
    mine = (await client.get("/api/v1/documents/mine")).json()
    assert first["id"] not in [d["id"] for d in mine]


async def test_a_retired_version_says_what_replaced_it(hse_manager, worker):
    """★ The other half of TC-22: "when it is opened there is no way to sign".

    There should not be — a signature on a retired version would record that
    somebody had read the wrong document. But old links and bookmarks keep
    arriving, so the page has to say why and where to go.
    """
    _, client = hse_manager
    named, _ = worker

    first = await _published(client, ack_roles=[], ack_user_ids=[str(named.id)])
    revised = (await client.post(f"/api/v1/documents/{first['id']}/new-version",
                                 json={"version": "7"})).json()
    await client.patch(f"/api/v1/documents/{revised['id']}",
                       json={"file_id": first["file_id"], "filename": "v7.pdf"})
    await client.post(f"/api/v1/documents/{revised['id']}/publish")

    old = (await client.get(f"/api/v1/documents/{first['id']}")).json()
    assert old["status"] == "retired"
    assert old["superseded_by_id"] == revised["id"]
    assert old["superseded_by_version"] == "7"

    # And the current one does not claim to have been replaced.
    current = (await client.get(f"/api/v1/documents/{revised['id']}")).json()
    assert current["superseded_by_id"] is None
