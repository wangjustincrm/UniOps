"""Chemicals, their data sheets, and the workplace labels printed from them."""
import sqlalchemy as sa




async def test_correcting_the_name_withdraws_an_approved_label(hse_manager):
    """WHMIS wants the identifier on the label to match the product.

    The label keeps its own copy — correctly, it is the record of what was
    printed — so a name corrected on the product used to leave an approved
    label printing the old wording with nothing on screen to say it had gone
    stale. The approval is withdrawn instead: somebody approved that wording,
    not this one, and approval before printing is the whole of the control.
    """
    _, client = hse_manager
    created = await client.post("/api/v1/chemicals", json={
        "name": "Caustic soada 50%",            # the typo this is all about
        "signal_word": "Danger",
    })
    assert created.status_code in (200, 201), created.text
    chem = created.json()
    chem_id = chem["id"]

    label = (await client.post(f"/api/v1/chemicals/{chem_id}/labels", json={})).json()
    await client.patch(f"/api/v1/labels/{label['id']}", json={
        "hazard_statements": [{"code": "H314", "text": "Causes severe skin burns."}],
    })
    approved = (await client.post(f"/api/v1/labels/{label['id']}/approve", json={})).json()
    assert approved["status"] == "approved"
    assert approved["product_identifier"] == "Caustic soada 50%"

    await client.patch(f"/api/v1/chemicals/{chem_id}", json={"name": "Caustic soda 50%"})

    after = (await client.get(f"/api/v1/labels/{label['id']}")).json()
    assert after["product_identifier"] == "Caustic soda 50%", "the label must carry the correction"
    assert after["status"] == "draft", "and must be approved again before it prints"
    assert after["approved_at"] is None


async def test_a_label_cannot_be_approved_without_its_pictogram(hse_manager, test_engine):
    """The artwork is the point of the gate, not a nice-to-have.

    A label claiming a hazard class prints the words and, without artwork, not
    the symbol — and WHMIS requires the symbol. It would go on a drum looking
    complete while being non-compliant, which is the failure the approval gate
    exists to prevent, so a missing pictogram blocks approval rather than
    merely being listed.
    """
    from sqlalchemy import text
    _, client = hse_manager

    async with test_engine.begin() as conn:
        # The vocabulary itself is seeded by a migration this test database does
        # not run, so the parent row has to exist before the entry can.
        await conn.execute(text(
            "INSERT INTO ehs_vocabularies (code, name, is_hierarchical, is_system_locked)"
            " VALUES ('ghs_hazard_class', 'GHS hazard classes', false, true)"
            " ON CONFLICT DO NOTHING"))
        await conn.execute(text(
            "INSERT INTO ehs_vocabulary_items"
            " (id, vocabulary_code, code, label, sort_order, is_active, attrs)"
            " VALUES (gen_random_uuid(), 'ghs_hazard_class', 'CORROSIVE', 'Corrosive',"
            " 50, true, '{}'::jsonb) ON CONFLICT DO NOTHING"))

    chem = (await client.post("/api/v1/chemicals", json={"name": "Sodium hydroxide 50%"})).json()
    label = (await client.post(f"/api/v1/chemicals/{chem['id']}/labels", json={})).json()
    await client.patch(f"/api/v1/labels/{label['id']}", json={
        "pictograms": ["CORROSIVE"],
        "hazard_statements": [{"code": "H314", "text": "Causes severe skin burns."}],
    })

    blocked = await client.post(f"/api/v1/labels/{label['id']}/approve", json={})
    assert blocked.status_code == 422
    assert "pictogram for Corrosive" in blocked.text

    # The same class written as the vocabulary label, not its code, must resolve
    # to the same artwork — both shapes are on file from before this existed.
    await client.patch(f"/api/v1/labels/{label['id']}", json={"pictograms": ["Corrosive"]})
    still = await client.post(f"/api/v1/labels/{label['id']}/approve", json={})
    assert still.status_code == 422, "the class name must resolve like its code does"


async def test_the_label_wording_can_be_corrected_by_hand(hse_manager):
    """Reading it off the data sheet is the fast path, not the only one.

    A scanned sheet gives the reader nothing, a statement comes back
    truncated, a hazard class is missed. Until the wording could be corrected
    the only buttons were read, approve and print, so a label the reader got
    wrong stayed wrong — and WHMIS is a standard about what the container
    actually says.
    """
    _, client = hse_manager
    chem = (await client.post("/api/v1/chemicals", json={
        "name": "Caustic soda 50%", "signal_word": "Danger",
    })).json()
    label = (await client.post(f"/api/v1/chemicals/{chem['id']}/labels", json={})).json()

    fixed = await client.patch(f"/api/v1/labels/{label['id']}", json={
        "supplier": "Univar Solutions",
        "signal_word": "Danger",
        "hazard_statements": [
            {"code": "H314", "text": "Causes severe skin burns and eye damage."}],
        "precautionary_statements": [
            {"code": "P280", "text": "Wear protective gloves and eye protection."}],
        "first_aid": "Rinse with water for 15 minutes.",
        "ppe_note": "Face shield and gauntlets.",
    })
    assert fixed.status_code == 200, fixed.text

    # Read back, because the editor is filled from this response — a field the
    # endpoint kept but did not return would open blank and be saved blank
    # over what was there.
    back = (await client.get(f"/api/v1/labels/{label['id']}")).json()
    assert back["supplier"] == "Univar Solutions"
    assert back["first_aid"] == "Rinse with water for 15 minutes."
    assert back["ppe_note"] == "Face shield and gauntlets."
    assert back["hazard_statements"][0]["code"] == "H314"
    assert back["precautionary_statements"][0]["text"].startswith("Wear protective")

    # Correcting one field leaves the others alone.
    await client.patch(f"/api/v1/labels/{label['id']}",
                       json={"supplier": "Brenntag Canada"})
    after = (await client.get(f"/api/v1/labels/{label['id']}")).json()
    assert after["supplier"] == "Brenntag Canada"
    assert after["first_aid"] == "Rinse with water for 15 minutes.", (
        "changing the supplier must not clear the first aid wording")


async def test_retiring_a_product_stops_its_review_clock(hse_manager, db_session):
    """★ Two screens disagreed about the same obligation.

    WHMIS's three-year review is owed on a product kept on site. "Needs
    attention" counted only products on site; the compliance calendar listed
    retired ones as overdue reviews, because retiring never discharged the
    clock. Two of the overdue reviews in the dev database were products nobody
    keeps any more.
    """
    _, client = hse_manager
    chem = (await client.post("/api/v1/chemicals", json={
        "name": "Sample solvent, no longer stocked",
    })).json()
    filed = await client.post(f"/api/v1/chemicals/{chem['id']}/sds", json={
        "revision": "1", "revision_date": "2023-01-10",
    })
    assert filed.status_code in (200, 201), filed.text
    sheet_id = filed.json()["id"]

    # Running to begin with — otherwise the assertion below is satisfied by
    # there being no clock at all.
    rows = (await client.get("/api/v1/deadlines?limit=500")).json()
    mine = [d for d in rows if d["source_id"] == sheet_id]
    assert mine, "filing a data sheet must start its three-year review"
    assert mine[0]["satisfied_at"] is None
    # And reachable, which is the other half of this round's calendar work.
    assert mine[0]["path"] == f"/chemicals?sheet={sheet_id}"

    retired = await client.post(f"/api/v1/chemicals/{chem['id']}/retire")
    assert retired.status_code == 200, retired.text

    after = (await client.get("/api/v1/deadlines?limit=500")).json()
    assert not [d for d in after if d["source_id"] == sheet_id], (
        "a retired product's review must leave the calendar")
    listed = (await client.get(
        "/api/v1/deadlines?include_satisfied=true&limit=500")).json()
    closed = [d for d in listed if d["source_id"] == sheet_id]
    assert closed and closed[0]["satisfied_at"] is not None
    # The note is on the record rather than in the response: an auditor has to
    # be able to see the review was answered by the product leaving, not by a
    # review that never happened.
    note = (await db_session.execute(sa.text(
        "select evidence_note from ehs_statutory_deadlines where source_id = :s"),
        {"s": sheet_id})).scalar()
    assert note == "Product no longer kept on site"


async def test_the_manufacturer_and_their_contact_reach_the_label(hse_manager):
    """Round 6, TC-09: "the manufacturer's name contact must be shown." Kept on
    the chemical, carried onto a new label, editable there before approval."""
    _, client = hse_manager
    chem = (await client.post("/api/v1/chemicals", json={
        "name": "Bleach 12%", "manufacturer": "Acme Chemicals Ltd", "supplier": "A distributor",
        "manufacturer_contact": "1-800-555-0100"})).json()
    assert chem["manufacturer_contact"] == "1-800-555-0100"

    draft = await client.post(f"/api/v1/chemicals/{chem['id']}/labels")
    assert draft.status_code in (200, 201), draft.text
    label = draft.json()
    # The maker, not whoever sold it, and how to reach them.
    assert label["supplier"] == "Acme Chemicals Ltd"
    assert label["supplier_contact"] == "1-800-555-0100"

    fixed = await client.patch(f"/api/v1/labels/{label['id']}",
                               json={"supplier_contact": "1-800-555-0199"})
    assert fixed.status_code == 200, fixed.text
    assert fixed.json()["supplier_contact"] == "1-800-555-0199"


async def test_the_label_code_resolves_to_the_sheet_in_force_today(hse_manager, worker, db_session):
    """★ Round 7, TC-07: the code on a label opens the data sheet, and the one
    current when it is scanned — not the revision current when it was printed."""
    import uuid as _uuid
    from app.models.chemical import SafetyDataSheet
    _, client = hse_manager
    _, me = worker
    chem = (await client.post("/api/v1/chemicals", json={"name": "Peracetic acid 15%"})).json()
    url = f"/api/v1/chemicals/{chem['id']}/sds/current"

    none = (await me.get(url)).json()
    assert none["sds_id"] is None and none["has_file"] is False

    first = (await client.post(f"/api/v1/chemicals/{chem['id']}/sds", json={"revision": "1"})).json()
    row = await db_session.get(SafetyDataSheet, _uuid.UUID(first["id"]))
    row.file_id = _uuid.uuid4()
    await db_session.commit()
    got = (await me.get(url)).json()
    assert got["sds_id"] == first["id"] and got["has_file"] is True

    second = (await client.post(f"/api/v1/chemicals/{chem['id']}/sds", json={"revision": "2"})).json()
    assert (await me.get(url)).json()["sds_id"] == second["id"], "the revision in force, not the first"
