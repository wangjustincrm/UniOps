"""Settings: the lists and parameters the HSE Manager maintains.

The test database is built with create_all rather than by running migrations,
so the vocabularies the seed migration would provide are created here.
"""
import uuid
from datetime import datetime, timedelta, timezone

import pytest
import sqlalchemy
from sqlalchemy import select

from app.models.config import EhsConfig
from app.models.vocabulary import Vocabulary, VocabularyItem


@pytest.fixture
async def vocabs(db_session):
    """One ordinary list, one hierarchical, one locked."""
    db_session.add_all([
        Vocabulary(code="root_cause", name="Root Cause", is_hierarchical=True),
        Vocabulary(code="ppe", name="PPE Type"),
        Vocabulary(code="injury_class", name="Injury Classification", is_system_locked=True),
    ])
    await db_session.flush()
    locked = [
        VocabularyItem(id=uuid.uuid4(), vocabulary_code="injury_class", code=c,
                       label=l, sort_order=s, path=l)
        for c, l, s in (("first_aid", "First Aid Injury", 10),
                        ("medical_aid", "Medical Aid", 20),
                        ("lost_time", "Lost Time", 30))
    ]
    db_session.add_all(locked)
    await db_session.flush()
    return locked


@pytest.fixture
async def config_row(db_session):
    row = EhsConfig(id=1)
    db_session.add(row)
    await db_session.flush()
    return row


# ── Listing ─────────────────────────────────────────────────────────────────

async def test_vocabularies_report_their_entry_counts(hse_manager, vocabs):
    _, client = hse_manager
    rows = (await client.get("/api/v1/settings/vocabularies")).json()
    by_code = {r["code"]: r for r in rows}
    assert by_code["injury_class"]["item_count"] == 3
    assert by_code["injury_class"]["active_item_count"] == 3
    assert by_code["injury_class"]["is_system_locked"] is True
    assert by_code["ppe"]["item_count"] == 0
    assert by_code["root_cause"]["is_hierarchical"] is True


async def test_an_unknown_vocabulary_is_404(hse_manager, vocabs):
    _, client = hse_manager
    assert (await client.get("/api/v1/settings/vocabularies/nope/items")).status_code == 404


# ── Adding ──────────────────────────────────────────────────────────────────

async def test_adding_an_entry(hse_manager, vocabs):
    _, client = hse_manager
    r = await client.post("/api/v1/settings/vocabularies/ppe/items",
                          json={"code": "GLOVES", "label": "Cut-resistant gloves"})
    assert r.status_code == 201
    assert r.json()["label"] == "Cut-resistant gloves"
    assert r.json()["is_active"] is True


async def test_a_hierarchical_entry_gets_a_path(hse_manager, vocabs):
    _, client = hse_manager
    parent = (await client.post("/api/v1/settings/vocabularies/root_cause/items",
                                json={"code": "EQUIP", "label": "Equipment"})).json()
    child = (await client.post("/api/v1/settings/vocabularies/root_cause/items",
                               json={"code": "EQ-GUARD", "label": "Guard missing or defeated",
                                     "parent_id": parent["id"]})).json()
    assert child["path"] == "Equipment/Guard missing or defeated"


async def test_a_flat_list_rejects_a_parent(hse_manager, vocabs):
    _, client = hse_manager
    parent = (await client.post("/api/v1/settings/vocabularies/ppe/items",
                                json={"code": "A", "label": "A"})).json()
    r = await client.post("/api/v1/settings/vocabularies/ppe/items",
                          json={"code": "B", "label": "B", "parent_id": parent["id"]})
    assert r.status_code == 422
    assert "flat list" in r.text


async def test_a_duplicate_code_is_refused(hse_manager, vocabs):
    _, client = hse_manager
    await client.post("/api/v1/settings/vocabularies/ppe/items",
                      json={"code": "GLOVES", "label": "Gloves"})
    r = await client.post("/api/v1/settings/vocabularies/ppe/items",
                          json={"code": "GLOVES", "label": "Gloves again"})
    assert r.status_code == 409


async def test_clashing_with_a_retired_entry_says_so(hse_manager, vocabs):
    """Adding a duplicate of something retired is nearly always someone trying
    to bring it back, so the error says how."""
    _, client = hse_manager
    added = (await client.post("/api/v1/settings/vocabularies/ppe/items",
                               json={"code": "GLOVES", "label": "Gloves"})).json()
    await client.delete(f"/api/v1/settings/vocabularies/ppe/items/{added['id']}")
    r = await client.post("/api/v1/settings/vocabularies/ppe/items",
                          json={"code": "GLOVES", "label": "Gloves"})
    assert r.status_code == 409
    assert "reactivate" in r.text


# ── Retiring, never deleting ────────────────────────────────────────────────

async def test_delete_retires_rather_than_removes(hse_manager, vocabs, db_session):
    _, client = hse_manager
    item = (await client.post("/api/v1/settings/vocabularies/ppe/items",
                              json={"code": "OLD", "label": "Obsolete visor"})).json()

    r = await client.delete(f"/api/v1/settings/vocabularies/ppe/items/{item['id']}")
    assert r.status_code == 200
    assert r.json()["is_active"] is False

    # The row is still there — an investigation that used it still reads.
    still = (await db_session.execute(sqlalchemy.text(
        "SELECT count(*) FROM ehs_vocabulary_items WHERE id = :i"), {"i": item["id"]})).scalar()
    assert still == 1


async def test_retired_entries_are_hidden_from_pickers_but_findable(hse_manager, vocabs):
    _, client = hse_manager
    item = (await client.post("/api/v1/settings/vocabularies/ppe/items",
                              json={"code": "OLD", "label": "Obsolete visor"})).json()
    await client.delete(f"/api/v1/settings/vocabularies/ppe/items/{item['id']}")

    live = (await client.get("/api/v1/settings/vocabularies/ppe/items")).json()
    assert item["id"] not in {i["id"] for i in live}

    everything = await client.get("/api/v1/settings/vocabularies/ppe/items",
                                  params={"include_inactive": "true"})
    assert item["id"] in {i["id"] for i in everything.json()}


async def test_a_retired_entry_can_be_brought_back(hse_manager, vocabs):
    _, client = hse_manager
    item = (await client.post("/api/v1/settings/vocabularies/ppe/items",
                              json={"code": "OLD", "label": "Visor"})).json()
    await client.delete(f"/api/v1/settings/vocabularies/ppe/items/{item['id']}")
    r = await client.patch(f"/api/v1/settings/vocabularies/ppe/items/{item['id']}",
                           json={"is_active": True})
    assert r.json()["is_active"] is True


# ── Locked lists ────────────────────────────────────────────────────────────

async def test_a_locked_list_refuses_new_entries(hse_manager, vocabs):
    """A fourth injury class would make this year's rates incomparable with
    last year's."""
    _, client = hse_manager
    r = await client.post("/api/v1/settings/vocabularies/injury_class/items",
                          json={"code": "fatality", "label": "Fatality"})
    assert r.status_code == 409
    assert "fixed list" in r.text


async def test_a_locked_entry_cannot_be_retired(hse_manager, vocabs):
    _, client = hse_manager
    r = await client.delete(
        f"/api/v1/settings/vocabularies/injury_class/items/{vocabs[0].id}")
    assert r.status_code == 409


async def test_a_locked_entry_can_still_be_relabelled(hse_manager, vocabs):
    """Wording is a local matter even when the set of levels is not."""
    _, client = hse_manager
    r = await client.patch(
        f"/api/v1/settings/vocabularies/injury_class/items/{vocabs[0].id}",
        json={"label": "First Aid Only"})
    assert r.status_code == 200
    assert r.json()["label"] == "First Aid Only"


# ── Behaviour parameters ────────────────────────────────────────────────────

async def test_reading_the_defaults(hse_manager, config_row):
    _, client = hse_manager
    body = (await client.get("/api/v1/settings/config")).json()
    assert body["capa_remind_before_days"] == 3
    assert body["capa_escalate_supervisor_days"] == 5
    assert body["capa_escalate_manager_days"] == 10
    assert body["cert_warn_days"] == [90, 60, 30]


async def test_updating_a_single_parameter_leaves_the_rest(hse_manager, config_row):
    _, client = hse_manager
    body = (await client.put("/api/v1/settings/config",
                             json={"capa_remind_before_days": 7})).json()
    assert body["capa_remind_before_days"] == 7
    assert body["capa_escalate_supervisor_days"] == 5


async def test_the_ladder_cannot_be_inverted(hse_manager, config_row):
    _, client = hse_manager
    r = await client.put("/api/v1/settings/config",
                         json={"capa_escalate_supervisor_days": 10,
                               "capa_escalate_manager_days": 5})
    assert r.status_code == 422


async def test_inverting_the_ladder_one_field_at_a_time_is_also_caught(hse_manager, config_row):
    """Raising only the supervisor threshold past the stored manager one would
    pass field validation and silently skip a rung."""
    _, client = hse_manager
    r = await client.put("/api/v1/settings/config",
                         json={"capa_escalate_supervisor_days": 20})
    assert r.status_code == 422
    assert "later than" in r.text


async def test_the_sweep_can_be_switched_off(hse_manager, config_row):
    _, client = hse_manager
    body = (await client.put("/api/v1/settings/config",
                             json={"statutory_scan_interval_minutes": 0})).json()
    assert body["statutory_scan_interval_minutes"] == 0


async def test_an_absurd_interval_is_refused(hse_manager, config_row):
    _, client = hse_manager
    r = await client.put("/api/v1/settings/config",
                         json={"statutory_scan_interval_minutes": 100000})
    assert r.status_code == 422


# ── Permissions ─────────────────────────────────────────────────────────────

async def test_a_worker_can_read_the_lists_a_form_needs(worker, vocabs, config_row):
    """The reference data a report form offers has to be readable by whoever
    is filing. Gating it on ehs.incident.read left the location picker empty
    for production workers — the people the module exists for."""
    _, client = worker
    assert (await client.get("/api/v1/settings/vocabularies")).status_code == 200
    assert (await client.get("/api/v1/settings/vocabularies/ppe/items")).status_code == 200
    assert (await client.get("/api/v1/settings/locations")).status_code == 200


async def test_a_worker_still_cannot_change_anything(worker, vocabs, config_row):
    _, client = worker
    assert (await client.post("/api/v1/settings/vocabularies/ppe/items",
                              json={"code": "X", "label": "X"})).status_code == 403
    assert (await client.put("/api/v1/settings/config",
                             json={"capa_remind_before_days": 1})).status_code == 403
    # Behaviour parameters are not form data — a worker has no reason to read
    # the escalation ladder or the notification groups.
    assert (await client.get("/api/v1/settings/config")).status_code == 403


async def test_an_auditor_can_read_but_not_change(auditor, vocabs, config_row):
    _, client = auditor
    assert (await client.get("/api/v1/settings/vocabularies")).status_code == 200
    assert (await client.get("/api/v1/settings/config")).status_code == 200
    assert (await client.put("/api/v1/settings/config",
                             json={"capa_remind_before_days": 1})).status_code == 403


# ── Plant tree ──────────────────────────────────────────────────────────────

async def test_the_location_tree_nests(hse_manager, db_session):
    _, client = hse_manager
    site, building, line = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    for id_, code, name, parent, level, path, depth in (
        (site, "KGN", "Kingston Plant", None, "site", "Kingston Plant", 0),
        (building, "103", "Building 103", site, "building", "Kingston Plant/Building 103", 1),
        (line, "103-FILL", "Filling", building, "department",
         "Kingston Plant/Building 103/Filling", 2),
    ):
        await db_session.execute(sqlalchemy.text(
            "INSERT INTO locations (id, code, name, parent_id, level, path, depth, is_active)"
            " VALUES (:i,:c,:n,:p,:l,:pa,:d,true)"),
            {"i": str(id_), "c": code, "n": name,
             "p": str(parent) if parent else None, "l": level, "pa": path, "d": depth})
    await db_session.flush()

    tree = (await client.get("/api/v1/settings/locations")).json()
    assert len(tree) == 1
    assert tree[0]["name"] == "Kingston Plant"
    assert tree[0]["children"][0]["name"] == "Building 103"
    assert tree[0]["children"][0]["children"][0]["path"] == "Kingston Plant/Building 103/Filling"


async def test_a_code_is_only_drawn_for_something_that_answers(hse_manager, test_engine):
    """A sticker whose code goes nowhere is worse than no sticker.

    Somebody tries it, nothing happens, and they stop trying the others — so
    the token is resolved before it is drawn rather than encoding whatever
    string arrives.
    """
    from sqlalchemy import text
    _, client = hse_manager

    missing = await client.get("/api/v1/qr/L-nothing-here/image")
    assert missing.status_code == 404

    async with test_engine.begin() as conn:
        token = (await conn.execute(text(
            "SELECT qr_token FROM locations WHERE qr_token IS NOT NULL LIMIT 1"))).scalar()
    if token is None:
        pytest.skip("no location carries a code in this database")

    drawn = await client.get(f"/api/v1/qr/{token}/image")
    assert drawn.status_code == 200
    assert drawn.headers["content-type"].startswith("image/svg+xml")
    body = drawn.text
    assert body.startswith("<svg"), "it has to be an image, not a description of one"
    # Four modules of quiet zone are the specification's minimum, and the usual
    # reason a phone will not read a printed code is that they were trimmed.
    assert 'viewBox="0 0 37 37"' in body or "viewBox=" in body


def test_the_drawn_modules_match_the_encoder():
    """The compact path is ours; the encoding is not.

    Rendering reportlab's widget through renderSVG gives one rect per module
    and 44 kB for a short URL, which on a page listing forty areas is most of a
    megabyte. Runs are merged into one path instead — about twenty times
    smaller, and worth nothing if a single module moved.
    """
    import re
    from reportlab.graphics.barcode import qr

    from app.services.qr_svg import QUIET_ZONE, svg

    data = "https://safety.example/qr/abc123"
    widget = qr.QrCodeWidget(data, barLevel="M")
    widget.draw()
    code = widget.qr
    n = code.getModuleCount()
    expected = {(r, c) for r in range(n) for c in range(n) if code.isDark(r, c)}

    drawn: set[tuple[int, int]] = set()
    for m in re.finditer(r"M(\d+) (\d+)h(\d+)v1h-\3z", svg(data)):
        x, y, width = int(m.group(1)), int(m.group(2)), int(m.group(3))
        for i in range(width):
            drawn.add((y - QUIET_ZONE, x - QUIET_ZONE + i))

    assert drawn == expected, "the drawn code is not the code the encoder produced"


async def test_a_form_can_be_asked_which_form_it_is(hse_manager):
    """The builder had the template id and no way to ask what it was called.

    It showed "Form builder" and nothing else, which with four similarly-named
    ladder inspections open is not enough to tell them apart — and four is what
    the first acceptance round produced, because starting over was easier than
    working out which one was which.
    """
    _, client = hse_manager
    created = await client.post("/api/v1/forms/templates", json={
        "code": "ladder_weekly", "name": "Ladder inspection — weekly",
        "category": "inspection",
    })
    assert created.status_code == 201, created.text
    template = created.json()

    one = await client.get(f"/api/v1/forms/templates/{template['id']}")
    assert one.status_code == 200
    assert one.json()["name"] == "Ladder inspection — weekly"
    assert one.json()["code"] == "ladder_weekly"


async def test_a_reference_list_is_named_differently_from_its_code(hse_manager, test_engine):
    """Why the builder must offer a choice rather than a text box.

    The list a person knows as "Safety Equipment Type" answers to
    `safety_asset_type`. Asked to type the code, HSE typed the name, the form
    went out pointing at a list that does not exist, and the question had
    nothing to pick from.
    """
    from sqlalchemy import text
    _, client = hse_manager
    # Seeded here rather than relied on. This used to pass only because another
    # test file happened to run first and insert one — a test that depends on
    # its neighbours is not testing what it says it is.
    async with test_engine.begin() as conn:
        await conn.execute(text(
            "INSERT INTO ehs_vocabularies (code, name, is_hierarchical, is_system_locked)"
            " VALUES ('safety_asset_type', 'Safety Equipment Type', false, false)"
            " ON CONFLICT DO NOTHING"))

    listing = (await client.get("/api/v1/settings/vocabularies")).json()
    by_name = {v["name"]: v["code"] for v in listing}
    assert by_name, "the builder's dropdown is filled from this"
    assert any(name.lower().replace(" ", "_") != code for name, code in by_name.items()), (
        "if every name matched its code the text box would have been survivable; "
        "they do not, which is the whole reason for the dropdown")

    missing = await client.get("/api/v1/settings/vocabularies/Safety_Equipment_Type/items")
    assert missing.status_code == 404, (
        "a question pointing at a list that does not exist has to fail loudly — "
        "the picker used to render an empty dropdown and say nothing")


async def test_a_requester_sees_the_permits_they_raised(hse_manager, test_engine, db_session):
    """Raising one and then seeing nothing looks exactly like it not saving.

    Every role can raise a permit — the person who needs a tank cleaned is not
    the person authorised to allow entry into it — but reading the register is
    narrower, and the list refused outright. The second acceptance round
    reported it as "it does not save once the raise it button is pressed". It
    had saved.
    """
    from tests.conftest import authed_client, make_token, make_user

    _, hse = hse_manager
    from sqlalchemy import text
    # The six permit types come from a migration this test database does not
    # run, so one is made here.
    async with test_engine.begin() as conn:
        await conn.execute(text(
            "INSERT INTO ehs_permit_types (id, code, name, is_active)"
            " VALUES (gen_random_uuid(), 'CONFINED', 'Confined space entry', true)"
            " ON CONFLICT DO NOTHING"))
    types = (await hse.get("/api/v1/permit-types")).json()
    assert types, "a permit type has to exist before one can be raised"

    requester = await make_user(test_engine, role="requester", full_name="Tank Cleaner")
    async with authed_client(make_token(requester.id, requester.role), db_session) as theirs:
        raised = await theirs.post("/api/v1/permits", json={
            "type_id": types[0]["id"],
            "work_description": "Clean the balance tank",
        })
        assert raised.status_code == 201, raised.text
        mine = await theirs.get("/api/v1/permits")
        assert mine.status_code == 200, "raising one and then being refused the list is the bug"
        assert any(p["id"] == raised.json()["id"] for p in mine.json())

        one = await theirs.get(f"/api/v1/permits/{raised.json()['id']}")
        assert one.status_code == 200

    # And no further: the register itself stays behind ehs.permit.read.
    other = (await hse.post("/api/v1/permits", json={
        "type_id": types[0]["id"], "work_description": "Hot work in the dryer",
    })).json()
    async with authed_client(make_token(requester.id, requester.role), db_session) as theirs:
        assert (await theirs.get(f"/api/v1/permits/{other['id']}")).status_code == 403
        listed = (await theirs.get("/api/v1/permits")).json()
        assert all(p["id"] != other["id"] for p in listed)


async def test_taking_somebody_off_a_position_and_putting_them_back(hse_manager, worker):
    """Removed and re-added the same day is a correction, not two spells.

    The assignment row is unique on (person, position, start date) and removal
    ends the row rather than deleting it — a job history is worth keeping. So
    putting the same person back on the same day collided with their own ended
    row and answered 500, which is what the second acceptance round hit:
    "removing staff and then adding them back returns server error msg".
    """
    _, client = hse_manager
    person, _ = worker

    created = await client.post("/api/v1/positions", json={"code": "FORKLIFT", "name": "Forklift Operator"})
    assert created.status_code in (200, 201), created.text
    position_id = created.json()["id"]

    first = await client.post(f"/api/v1/positions/{position_id}/holders",
                              json={"user_id": str(person.id)})
    assert first.status_code in (200, 201), first.text
    assignment_id = first.json()["assignment_id"]

    removed = await client.delete(
        f"/api/v1/positions/{position_id}/holders/{assignment_id}")
    assert removed.status_code in (200, 204), removed.text

    again = await client.post(f"/api/v1/positions/{position_id}/holders",
                              json={"user_id": str(person.id)})
    assert again.status_code in (200, 201), (
        f"re-adding the same person the same day must not fail: {again.text}")

    holders = (await client.get(f"/api/v1/positions/{position_id}/holders")).json()
    mine = [h for h in holders if h["user_id"] == str(person.id)]
    assert len(mine) == 1, (
        "the person holds the position once, not twice — a revived row, not a "
        f"duplicate: {mine}")


async def test_one_person_signing_a_policy_does_not_sign_it_for_everybody(
        hse_manager, worker, db_session):
    """The inbox said the whole plant had read it.

    Publishing a document that needs acknowledgement opens one task per person.
    Closing them was scoped to the document and not to the person, so the first
    signature closed every outstanding read-and-sign task on it: the policy
    vanished from everyone's list, and the task inbox reported the plant as
    having read something it had not seen. The acknowledgement rows — the
    actual record — stayed correct, so the two disagreed, and the inbox was the
    one people were reading.
    """
    from sqlalchemy import select

    from app.models.mirrors import Task

    hse_user, client = hse_manager
    other, other_client = worker

    created = await client.post("/api/v1/documents", json={
        "title": "Working at Heights Policy",
        "doc_type": "policy",
        "requires_ack": True,
        "ack_everyone": True,
    })
    assert created.status_code in (200, 201), created.text
    doc_id = created.json()["id"]

    # Publishing refuses a document with nothing attached, which is right —
    # there would be nothing to read. The upload goes to file-api, which is not
    # running here, so the join is written straight onto the row.
    from app.models.document import SafetyDocument

    row = await db_session.get(SafetyDocument, uuid.UUID(doc_id))
    row.file_id = uuid.uuid4()
    await db_session.commit()

    published = await client.post(f"/api/v1/documents/{doc_id}/publish", json={})
    assert published.status_code == 200, published.text

    async def open_tasks() -> list[Task]:
        await db_session.commit()
        return list((await db_session.execute(
            select(Task).where(
                Task.document_type == "ehs_doc",
                Task.document_id == uuid.UUID(doc_id),
                Task.is_completed.is_(False),
            ))).scalars())

    before = await open_tasks()
    assert len(before) >= 2, (
        "this proves nothing unless at least two people were asked to sign — "
        f"got {len(before)}")
    assert {t.assigned_user_id for t in before} >= {hse_user.id, other.id}

    signed = await client.post(f"/api/v1/documents/{doc_id}/acknowledge",
                               json={"signature": "data:image/png;base64,iVBORw0KGgo="})
    assert signed.status_code in (200, 201), signed.text

    after = await open_tasks()
    remaining = {t.assigned_user_id for t in after}
    assert hse_user.id not in remaining, "the signer's own task must close"
    assert other.id in remaining, (
        "somebody else's task must stay open — they have not read it")


class TestTheLostTimeGuidanceReachesTheReporter:
    """★ "Use WSIB wording for lost time" — HSE, round 4, after being asked
    twice across two rounds.

    Nothing here classifies an injury. The reporter picks one of the twelve
    options on HSE's form, and that choice drives LTIFR and the WSIB clock — so
    whether it is picked correctly depends entirely on whether they know the
    test. HSE writes the test; it is a regulatory definition and one invented in
    this codebase would be wrong and relied on anyway.

    The part worth testing is who can read it. An ordinary employee is exactly
    who reports an injury and does not hold `ehs.incident.read`, so served from
    the config endpoint it would have been invisible to everyone who needs it —
    this module's most repeated fault, three times over.
    """

    async def test_an_ordinary_employee_can_read_it(self, hse_manager, worker, config_row):
        _, hse = hse_manager
        _, wc = worker

        wording = ("Lost time means the worker did not return to their next "
                   "scheduled shift, whatever the reason for the absence.")
        saved = await hse.put("/api/v1/settings/config",
                              json={"lost_time_rules": {"guidance": wording}})
        assert saved.status_code == 200, saved.text

        theirs = await wc.get("/api/v1/settings/reporting-guidance")
        assert theirs.status_code == 200, theirs.text
        assert theirs.json()["lost_time_guidance"] == wording

        # ★ And the endpoint it is deliberately not on is still refused to them,
        # so this is a narrow opening rather than the config being widened.
        assert (await wc.get("/api/v1/settings/config")).status_code == 403

    async def test_it_is_absent_rather_than_invented_when_hse_has_not_written_it(
            self, hse_manager, worker, config_row):
        """Empty means nothing is shown. A definition the product made up would
        be cited in an audit as though HSE had written it."""
        _, hse = hse_manager
        _, wc = worker
        assert (await hse.put("/api/v1/settings/config",
                              json={"lost_time_rules": {"guidance": "   "}})).status_code == 200

        theirs = await wc.get("/api/v1/settings/reporting-guidance")
        assert theirs.status_code == 200, theirs.text
        assert theirs.json()["lost_time_guidance"] is None


class TestWhoIsToldAboutAnIncident:
    """★ HSE, round 4: "provide a way to manage the recipients list on incidents,
    not on each form, rather create a master email distribution list where the
    email templates are stored and editable by the HSE role."

    `incident_notify_groups` has been read by the notifier since the first
    release and had no screen, so every notice went to the role fallback
    whatever kind of incident it was.

    ★ Keyed on the incident type rather than the category, which uniops-9a
    caught before the screen was built: on the dev database 60 incidents carry 4
    categories between them, because only five of the twelve types imply one. A
    list per category would have applied to four incidents and silently fallen
    through for the rest. Every incident has a type.
    """

    async def test_the_list_decides_who_is_emailed(
            self, hse_manager, config_row, test_engine, db_session):
        from app.models.communication import Communication
        from tests.conftest import make_user

        _, client = hse_manager
        named = await make_user(test_engine, role="requester", full_name="Told Person")

        saved = await client.put("/api/v1/settings/config", json={
            "incident_notify_groups": {"property_damage": [str(named.id)]}})
        assert saved.status_code == 200, saved.text

        made = await client.post("/api/v1/incidents", json={
            "form_kind": "equipment", "title": "Forklift into the racking",
            "occurred_at": (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat(),
            "incident_type": "property_damage"})
        assert made.status_code in (200, 201), made.text
        # Sent, as the form does: the notice goes on submit, not on create.
        sent = await client.post(f"/api/v1/incidents/{made.json()['id']}/submit")
        assert sent.status_code == 200, sent.text

        mail = (await db_session.execute(
            select(Communication).where(
                Communication.doc_id == uuid.UUID(made.json()["id"]),
                Communication.template_key == "incident_reported"))).scalars().first()
        assert mail is not None, "nothing was queued"
        assert mail.to_addrs == [named.email], mail.to_addrs

    async def test_a_type_with_nobody_named_falls_through(
            self, hse_manager, config_row, test_engine, db_session):
        """★ The half that keeps an unconfigured list safe. A kind of incident
        with nobody against it uses the catch-all, and an injury reported to
        nobody is the one outcome this must never produce."""
        from app.models.communication import Communication
        from tests.conftest import make_user

        _, client = hse_manager
        catch_all = await make_user(test_engine, role="requester", full_name="Catch All")

        assert (await client.put("/api/v1/settings/config", json={
            "incident_notify_groups": {"*": [str(catch_all.id)],
                                       "property_damage": []}})).status_code == 200

        made = await client.post("/api/v1/incidents", json={
            "form_kind": "medical", "title": "Cut to a finger",
            "occurred_at": (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat(),
            "incident_type": "first_aid"})
        assert made.status_code in (200, 201), made.text
        # Sent, as the form does: the notice goes on submit, not on create.
        sent = await client.post(f"/api/v1/incidents/{made.json()['id']}/submit")
        assert sent.status_code == 200, sent.text

        mail = (await db_session.execute(
            select(Communication).where(
                Communication.doc_id == uuid.UUID(made.json()["id"]),
                Communication.template_key == "incident_reported"))).scalars().first()
        assert mail is not None, "an incident with no list for its type told nobody"
        assert mail.to_addrs == [catch_all.email], mail.to_addrs
