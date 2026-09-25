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
