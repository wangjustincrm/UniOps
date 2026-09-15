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
