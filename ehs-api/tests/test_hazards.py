"""The hazard register — what is known to be dangerous, and correcting it."""


async def test_correcting_a_hazard_leaves_the_rest_of_it_alone(hse_manager):
    """A register entry outlives whoever wrote it.

    The controls get improved, the owner changes job, the review date moves.
    None of that should cost the entry its kind, its location or its owner —
    and a form that reopens with a field blank will write that blank back.
    This is the shape of the bug that destroyed an investigation earlier in
    this module's life, so it is pinned here rather than trusted.
    """
    _, client = hse_manager

    created = await client.post("/api/v1/hazards", json={
        "title": "Pinch point at the case sealer infeed",
        "description": "Guard was removed for a changeover and not refitted.",
        "existing_controls": "Verbal instruction not to reach in.",
        "control_level": "administrative",
    })
    assert created.status_code in (200, 201), created.text
    before = created.json()

    # Only the controls changed — the real edit HSE asked to be able to make.
    patched = await client.patch(f"/api/v1/hazards/{before['id']}", json={
        "existing_controls": "Interlocked guard refitted and keyed.",
        "control_level": "engineering",
    })
    assert patched.status_code == 200, patched.text
    after = patched.json()

    assert after["existing_controls"] == "Interlocked guard refitted and keyed."
    assert after["control_level"] == "engineering"
    for kept in ("title", "description", "hazard_no", "location_id", "owner_id"):
        assert after[kept] == before[kept], f"correcting the controls erased {kept}"


async def test_the_register_hands_back_the_id_the_picker_needs(hse_manager):
    """Returning only the label is what made the edit form clear the kind.

    The label is the snapshot that keeps a three-year-old record readable after
    somebody renames the vocabulary entry; the id is what puts the picker back
    on the right row when the record is reopened. A screen given only the label
    has nothing to preselect, shows an empty dropdown, and saves that emptiness.
    """
    _, client = hse_manager
    created = await client.post("/api/v1/hazards", json={"title": "Unguarded conveyor nip"})
    body = created.json()
    assert "hazard_type_id" in body, "the edit form cannot restore a selection without it"


async def test_a_misspelled_field_is_refused_not_dropped(hse_manager):
    """Silence here is how good data gets overwritten with nothing.

    Pydantic drops keys it does not recognise. A caller that sends
    `existing_control` instead of `existing_controls` would get a 200 and no
    change — and if the same caller also sent the fields it did spell right,
    the record would be saved around the missing one.
    """
    _, client = hse_manager
    created = await client.post("/api/v1/hazards", json={"title": "Steam line unlagged"})
    resp = await client.patch(f"/api/v1/hazards/{created.json()['id']}", json={
        "existing_control": "Lagging fitted.",     # singular — wrong
    })
    assert resp.status_code == 422, (
        "an unknown field must be refused; accepting it silently is how the "
        "investigation data was lost")
