"""Chemicals, their data sheets, and the workplace labels printed from them."""




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
