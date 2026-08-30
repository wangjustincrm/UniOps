"""Settings: the lists and parameters the HSE Manager maintains.

The test database is built with create_all rather than by running migrations,
so the vocabularies the seed migration would provide are created here.
"""
import uuid

import pytest
import sqlalchemy

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

async def test_a_worker_can_read_lists_but_not_change_them(worker, vocabs, config_row):
    _, client = worker
    # Reading is gated on incident.read, which a worker does not hold either.
    assert (await client.get("/api/v1/settings/vocabularies")).status_code == 403
    assert (await client.post("/api/v1/settings/vocabularies/ppe/items",
                              json={"code": "X", "label": "X"})).status_code == 403
    assert (await client.put("/api/v1/settings/config",
                             json={"capa_remind_before_days": 1})).status_code == 403


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
