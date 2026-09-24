"""Who can see the parts of a permit they are allowed to see the whole of.

A permit is readable by whoever raised it, and by anybody holding the register
permission. Its gas readings and its signatures are part of that document and
follow the same rule — they did not, and the gap looked to the person in front
of it like their signature had not saved.
"""
import uuid
from datetime import datetime, timedelta, timezone

import pytest

from app.models.permit import PermitType


@pytest.fixture
async def permit_type(db_session) -> PermitType:
    """Seeded here: the test database comes from `create_all`, so it has the
    permit tables but none of the types the migration inserts."""
    row = PermitType(
        id=uuid.uuid4(), code=f"hot-work-{uuid.uuid4().hex[:6]}", name="Hot work",
        precautions=[{"key": "extinguisher", "label": "Extinguisher to hand"}],
        requires_gas_test=False, required_signatures=["issuer", "receiver"],
        max_duration_hours=8,
    )
    db_session.add(row)
    await db_session.commit()
    return row


async def _a_permit(client, permit_type) -> dict:
    permit = await client.post("/api/v1/permits", json={
        "type_id": str(permit_type.id),
        "work_description": "Clean the balance tank",
        "valid_from": datetime.now(timezone.utc).isoformat(),
        "valid_to": (datetime.now(timezone.utc) + timedelta(hours=4)).isoformat(),
    })
    assert permit.status_code in (200, 201), permit.text
    return permit.json()


async def test_the_person_who_signed_can_see_their_own_signature(worker, permit_type):
    """★ What the third round reported as "signatures are not saved when worker
    is logged in but show when supervisor is logged in".

    They were saved. Signing needs `ehs.permit.request`, which this person has;
    reading the signatures needed `ehs.permit.read`, which they do not — so the
    list came back 403 and the screen had nothing to draw. A write you are
    allowed to make and then not allowed to see is indistinguishable from a
    write that failed.
    """
    signer, client = worker
    permit = await _a_permit(client, permit_type)

    signed = await client.post(f"/api/v1/permits/{permit['id']}/signatures", json={
        "capacity": "receiver",
        "user_id": str(signer.id),
        "person_name": signer.full_name or "Test Requester",
        "signature": "data:image/png;base64,iVBORw0KGgo=",
    })
    assert signed.status_code in (200, 201), signed.text

    back = await client.get(f"/api/v1/permits/{permit['id']}/signatures")
    assert back.status_code == 200, back.text
    assert [s["capacity"] for s in back.json()] == ["receiver"]

    # The gas readings sit behind the same rule, and were behind the same wall.
    assert (await client.get(f"/api/v1/permits/{permit['id']}/gas")).status_code == 200


async def test_somebody_elses_permit_is_still_not_yours_to_read(
        worker, permit_type, test_engine, db_session):
    """The admission above must not have been bought by opening it to everyone.

    A permit raised by one requester stays closed to another: the rule is "your
    own, or the register", and only the first half was relaxed.
    """
    from tests.conftest import authed_client, make_token, make_user

    _, mine = worker
    permit = await _a_permit(mine, permit_type)

    stranger = await make_user(test_engine, role="requester")
    async with authed_client(make_token(stranger.id, stranger.role), db_session) as theirs:
        assert (await theirs.get(
            f"/api/v1/permits/{permit['id']}/signatures")).status_code == 403
        assert (await theirs.get(
            f"/api/v1/permits/{permit['id']}/gas")).status_code == 403


async def test_the_register_sees_everybodys(worker, hse_manager, permit_type):
    """And somebody holding the register permission reads anyone's — otherwise
    the denial above would be satisfied by refusing everybody."""
    _, mine = worker
    permit = await _a_permit(mine, permit_type)
    _, hse = hse_manager
    assert (await hse.get(f"/api/v1/permits/{permit['id']}/signatures")).status_code == 200
    assert (await hse.get(f"/api/v1/permits/{permit['id']}/gas")).status_code == 200
