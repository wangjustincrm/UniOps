"""Click-to-sort headers on the Booking admin lists — /admin/bookings and
/admin/notifications: ?sort=<key>&dir=asc|desc sorted in SQL before
limit/offset, NULLs last both ways, unknown key → 422, no sort → the old
default (newest first). Everything runs inside the savepoint-isolated
db_session, so nothing leaks."""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import delete

from app.models.booking import Booking
from app.models.notification import NotificationLog
from app.models.room import MeetingRoom
from app.models.user_mirror import User
from tests.conftest import authed_client, make_token, make_user

T0 = datetime(2031, 5, 5, 13, 0, tzinfo=timezone.utc)


def _items(resp):
    assert resp.status_code == 200, resp.text
    return resp.json()["items"]


@pytest.mark.asyncio
async def test_admin_bookings_sort(admin, db_session):
    _, c = admin
    room_a = MeetingRoom(id=uuid.uuid4(), name="beta room", code=f"B-{uuid.uuid4().hex[:6]}", capacity=4)
    room_b = MeetingRoom(id=uuid.uuid4(), name="Alpha Room", code=f"A-{uuid.uuid4().hex[:6]}", capacity=4)
    org1 = User(id=uuid.uuid4(), full_name="zed Org", email=f"z{uuid.uuid4().hex[:6]}@t.com",
                role="requester", is_active=True)
    org2 = User(id=uuid.uuid4(), full_name="Amy Org", email=f"a{uuid.uuid4().hex[:6]}@t.com",
                role="requester", is_active=True)
    db_session.add_all([room_a, room_b, org1, org2])
    await db_session.flush()
    await db_session.execute(delete(NotificationLog))   # FK to bookings
    await db_session.execute(delete(Booking))
    # (title, room, organizer_id, hour offset, status, sync)
    spec = [
        ("Budget", room_a, org1.id, 1, "confirmed", "compensating"),
        ("all hands", room_b, org2.id, 2, "no_show", "sent"),
        ("Cleanup", room_a, uuid.uuid4(), 3, "cancelled", "failed"),   # organizer unknown
    ]
    for title, room, org, h, st, sync in spec:
        db_session.add(Booking(id=uuid.uuid4(), room_id=room.id, title=title, organizer_id=org,
                               starts_at=T0 + timedelta(hours=h), ends_at=T0 + timedelta(hours=h, minutes=30),
                               status=st, sync_status=sync, calendar_uid=f"uid-{uuid.uuid4().hex}"))
    await db_session.flush()

    async def titles(qs):
        return [i["title"] for i in _items(await c.get(f"/api/v1/admin/bookings?{qs}"))]

    assert await titles("") == ["Cleanup", "all hands", "Budget"]                # starts_at desc
    assert await titles("sort=title") == ["all hands", "Budget", "Cleanup"]       # case-insensitive
    assert await titles("sort=title&dir=desc") == ["Cleanup", "Budget", "all hands"]
    assert (await titles("sort=room"))[0] == "all hands"                         # by room NAME
    # organizer by NAME; the unknown organizer (no user row → NULL) last both ways
    assert await titles("sort=organizer") == ["all hands", "Budget", "Cleanup"]
    assert await titles("sort=organizer&dir=desc") == ["Budget", "all hands", "Cleanup"]
    assert await titles("sort=starts_at") == ["Budget", "all hands", "Cleanup"]
    # status as shown: Cancelled < Confirmed < No show
    assert await titles("sort=status") == ["Cleanup", "Budget", "all hands"]
    # sync label: Failed < Sent < Updating (compensating)
    assert await titles("sort=sync_status") == ["Cleanup", "all hands", "Budget"]
    # offset page 2 continues page 1; total unaffected
    r1 = await c.get("/api/v1/admin/bookings?sort=organizer&dir=desc&limit=2&offset=0")
    r2 = await c.get("/api/v1/admin/bookings?sort=organizer&dir=desc&limit=2&offset=2")
    assert [i["title"] for i in _items(r1) + _items(r2)] == ["Budget", "all hands", "Cleanup"]
    assert r1.json()["total"] == 3
    assert (await c.get("/api/v1/admin/bookings?sort=rrule")).status_code == 422
    assert (await c.get("/api/v1/admin/bookings?sort=title&dir=up")).status_code == 422


@pytest.mark.asyncio
async def test_admin_notifications_sort(admin, db_session):
    _, c = admin
    await db_session.execute(delete(NotificationLog))
    # (type, recipients, status, retries, error, sent minutes or None, created minutes)
    spec = [
        ("created", ["b@x.com"], "sent", 0, None, 5, 1),
        ("cancelled_occ", [], "failed", 3, "Timeout", None, 2),
        ("updated", ["A@x.com", "c@x.com"], "pending", 1, "auth", 9, 3),
    ]
    for t, rec, st, rc, err, sent, cr in spec:
        db_session.add(NotificationLog(
            id=uuid.uuid4(), notif_type=t, recipients=rec, status=st, retry_count=rc, error=err,
            sent_at=(T0 + timedelta(minutes=sent)) if sent is not None else None,
            created_at=T0 + timedelta(minutes=cr)))
    await db_session.flush()

    async def types(qs):
        return [i["notif_type"] for i in _items(await c.get(f"/api/v1/admin/notifications?{qs}"))]

    assert await types("") == ["updated", "cancelled_occ", "created"]            # created_at desc
    assert await types("sort=created_at") == ["created", "cancelled_occ", "updated"]
    # type as shown: "cancelled occurrence" < "created" < "updated"
    assert await types("sort=notif_type") == ["cancelled_occ", "created", "updated"]
    # recipients by text; the empty list (shown "—") last both ways
    assert await types("sort=recipients") == ["updated", "created", "cancelled_occ"]
    assert await types("sort=recipients&dir=desc") == ["created", "updated", "cancelled_occ"]
    assert await types("sort=retry_count&dir=desc") == ["cancelled_occ", "updated", "created"]
    # error: case-insensitive, NULL last both ways
    assert await types("sort=error") == ["updated", "cancelled_occ", "created"]
    assert await types("sort=error&dir=desc") == ["cancelled_occ", "updated", "created"]
    # sent_at NULL last both ways
    assert await types("sort=sent_at") == ["created", "updated", "cancelled_occ"]
    assert await types("sort=sent_at&dir=desc") == ["updated", "created", "cancelled_occ"]
    assert await types("sort=status") == ["cancelled_occ", "updated", "created"]
    r1 = await c.get("/api/v1/admin/notifications?sort=sent_at&dir=desc&limit=2&offset=0")
    r2 = await c.get("/api/v1/admin/notifications?sort=sent_at&dir=desc&limit=2&offset=2")
    assert [i["notif_type"] for i in _items(r1) + _items(r2)] == ["updated", "created", "cancelled_occ"]
    assert r1.json()["total"] == 3
    assert (await c.get("/api/v1/admin/notifications?sort=booking_id")).status_code == 422


@pytest.mark.asyncio
async def test_admin_bookings_organizer_filter_is_server_side(admin, db_session):
    """The "Organizer name…" box used to filter only the 50 rows on screen; it
    is now a server filter, so total and paging cover every match."""
    _, c = admin
    await db_session.execute(delete(NotificationLog))
    await db_session.execute(delete(Booking))
    room = MeetingRoom(id=uuid.uuid4(), name="Org Room", code=f"O-{uuid.uuid4().hex[:6]}", capacity=4)
    pat = User(id=uuid.uuid4(), full_name="Patricia Stone", email=f"p{uuid.uuid4().hex[:6]}@t.com",
               role="requester", is_active=True)
    bob = User(id=uuid.uuid4(), full_name="Bob Ray", email=f"b{uuid.uuid4().hex[:6]}@t.com",
               role="requester", is_active=True)
    db_session.add_all([room, pat, bob])
    await db_session.flush()
    for i, org in enumerate([pat, bob, pat, pat]):
        db_session.add(Booking(id=uuid.uuid4(), room_id=room.id, title=f"m{i}", organizer_id=org.id,
                               starts_at=T0 + timedelta(hours=i), ends_at=T0 + timedelta(hours=i, minutes=30),
                               status="confirmed", sync_status="sent",
                               calendar_uid=f"uid-{uuid.uuid4().hex}"))
    await db_session.flush()
    r = await c.get("/api/v1/admin/bookings?organizer=sTONe&sort=starts_at&limit=2&offset=0")
    r2 = await c.get("/api/v1/admin/bookings?organizer=sTONe&sort=starts_at&limit=2&offset=2")
    assert r.json()["total"] == 3 and r2.json()["total"] == 3
    assert [i["title"] for i in _items(r) + _items(r2)] == ["m0", "m2", "m3"]
    assert {i["organizer_name"] for i in _items(r) + _items(r2)} == {"Patricia Stone"}
    assert (await c.get("/api/v1/admin/bookings")).json()["total"] == 4
    # the CSV export honours the same filter
    csv = (await c.get("/api/v1/admin/bookings/export?organizer=ray")).text
    assert csv.count("\n") == 2 and "Bob Ray" in csv


@pytest.mark.asyncio
async def test_my_bookings_paged(requester, db_session):
    """GET /bookings/mine/paged: server paging + total + tab scope + sort, so a
    long series can no longer push near-term or past bookings off the page."""
    user, c = requester
    room = MeetingRoom(id=uuid.uuid4(), name="Mine Room", code=f"M-{uuid.uuid4().hex[:6]}", capacity=4)
    db_session.add(room)
    await db_session.flush()
    now = datetime.now(timezone.utc)
    # (title, start offset days, status, attendee count)
    spec = [("zeta", 400, "confirmed", 0), ("Alpha", 1, "confirmed", 3), ("beta", 2, "confirmed", 1),
            ("old", -5, "confirmed", 2), ("gone", 3, "cancelled", 0)]
    for title, d, st, n in spec:
        s = now + timedelta(days=d)
        db_session.add(Booking(id=uuid.uuid4(), room_id=room.id, title=title, organizer_id=user.id,
                               starts_at=s, ends_at=s + timedelta(hours=1), status=st,
                               attendee_ids=[str(uuid.uuid4()) for _ in range(n)],
                               sync_status="sent", calendar_uid=f"uid-{uuid.uuid4().hex}"))
    await db_session.flush()

    async def titles(qs):
        r = await c.get(f"/api/v1/bookings/mine/paged?{qs}")
        assert r.status_code == 200, r.text
        return [i["title"] for i in r.json()["items"]], r.json()["total"]

    assert await titles("") == (["zeta", "gone", "beta", "Alpha", "old"], 5)   # starts_at desc, as /mine
    assert await titles("scope=upcoming") == (["zeta", "beta", "Alpha"], 3)
    assert await titles("scope=past") == (["gone", "old"], 2)
    assert (await titles("scope=upcoming&sort=starts_at"))[0] == ["Alpha", "beta", "zeta"]
    assert (await titles("scope=upcoming&sort=title&dir=desc"))[0] == ["zeta", "beta", "Alpha"]
    assert (await titles("sort=attendees&dir=desc"))[0][:3] == ["Alpha", "old", "beta"]
    # page 2 continues page 1 under a sort; total is the full count
    p1, t1 = await titles("scope=upcoming&sort=starts_at&limit=2&offset=0")
    p2, t2 = await titles("scope=upcoming&sort=starts_at&limit=2&offset=2")
    assert p1 + p2 == ["Alpha", "beta", "zeta"] and t1 == t2 == 3
    assert (await c.get("/api/v1/bookings/mine/paged?sort=rrule")).status_code == 422
    assert (await c.get("/api/v1/bookings/mine/paged?scope=soon")).status_code == 422


@pytest.mark.asyncio
async def test_get_booking_by_id(requester, admin, test_engine, db_session):
    """GET /bookings/{id}: the edit page's fallback when opened without router
    state. Same visibility as PATCH: organizer or booking admin; anyone else
    gets 404 (no existence probing); unknown id 404."""
    owner, oc = requester
    _, ac = admin
    room = MeetingRoom(id=uuid.uuid4(), name="Get Room", code=f"G-{uuid.uuid4().hex[:6]}", capacity=4)
    db_session.add(room)
    await db_session.flush()
    b = Booking(id=uuid.uuid4(), room_id=room.id, title="Old one", organizer_id=owner.id,
                starts_at=T0 - timedelta(days=900), ends_at=T0 - timedelta(days=900, hours=-1),
                status="confirmed", sync_status="sent", calendar_uid=f"uid-{uuid.uuid4().hex}")
    db_session.add(b)
    await db_session.flush()

    r = await oc.get(f"/api/v1/bookings/{b.id}")
    assert r.status_code == 200, r.text
    assert r.json()["title"] == "Old one" and r.json()["room_name"] == "Get Room"
    assert r.json()["organizer_name"] == owner.full_name
    assert (await ac.get(f"/api/v1/bookings/{b.id}")).status_code == 200
    other = await make_user(test_engine, role="requester")
    async with authed_client(make_token(other.id, "requester"), session=db_session) as xc:
        r = await xc.get(f"/api/v1/bookings/{b.id}")
        assert r.status_code == 404
        assert (await xc.get(f"/api/v1/bookings/{uuid.uuid4()}")).status_code == 404
    assert (await oc.get(f"/api/v1/bookings/{uuid.uuid4()}")).status_code == 404
    # the static GET paths are not shadowed by /{booking_id}
    assert (await oc.get("/api/v1/bookings/mine")).status_code == 200
    assert (await oc.get("/api/v1/bookings/mine/paged")).status_code == 200
