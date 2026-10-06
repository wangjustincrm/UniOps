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


class TestCausesOnTheHazardRegister:
    """★ Round 5, answer box 11: "the what was found, immediate causes and root
    cause, and corrective actions all need to be added ot the hazard register
    form."

    The same apparatus as an incident investigation, on a register entry — and
    the same rules, because they come from the same place in `crud/cause.py`.
    """

    async def _a_hazard(self, client) -> dict:
        made = await client.post("/api/v1/hazards", json={
            "title": "Unguarded nip point on the case sealer",
            "description": "Reachable from the walkway side.",
        })
        assert made.status_code in (200, 201), made.text
        return made.json()

    async def test_what_was_found_is_recorded(self, hse_manager):
        _, client = hse_manager
        hazard = await self._a_hazard(client)
        patched = await client.patch(f"/api/v1/hazards/{hazard['id']}", json={
            "findings": "Guard removed for a changeover and never put back.",
        })
        assert patched.status_code == 200, patched.text
        assert patched.json()["findings"].startswith("Guard removed")

    async def test_causes_are_recorded_and_split_by_kind(self, hse_manager):
        _, client = hse_manager
        hazard = await self._a_hazard(client)
        saved = await client.put(f"/api/v1/hazards/{hazard['id']}/causes", json={
            "causes": [
                {"cause_type": "immediate", "label": "Guard was off"},
                {"cause_type": "root", "label": "No check after a changeover"},
            ],
        })
        assert saved.status_code == 200, saved.text
        tree = saved.json()
        assert tree["hazard_no"] == hazard["hazard_no"]
        assert [c["label"] for c in tree["immediate"]] == ["Guard was off"]
        assert [c["label"] for c in tree["root"]] == ["No check after a changeover"]

    async def test_an_action_is_raised_with_its_root_cause(self, hse_manager, worker):
        """★ In the same request. An action points at a cause and a cause has no
        id until it is saved, so doing it from the client means guessing which id
        belongs to which row just typed."""
        from datetime import date, timedelta

        _, client = hse_manager
        owner, _ = worker
        hazard = await self._a_hazard(client)

        saved = await client.put(f"/api/v1/hazards/{hazard['id']}/causes", json={
            "causes": [{
                "cause_type": "root",
                "label": "No check after a changeover",
                "action": {
                    "title": "Add a guard check to the changeover sheet",
                    "description": "Signed off by the line lead before restart.",
                    "owner_id": str(owner.id),
                    "due_date": (date.today() + timedelta(days=14)).isoformat(),
                },
            }],
        })
        assert saved.status_code == 200, saved.text
        tree = saved.json()
        cause_id = tree["root"][0]["id"]
        assert cause_id in tree["actions"], "the action must hang off its cause"
        action = tree["actions"][cause_id][0]
        assert action["title"].startswith("Add a guard check")
        # Filed against the hazard, so it reaches the register's own list too.
        assert action["source_type"] == "hazard"
        assert action["source_id"] == hazard["id"]

    async def test_saving_twice_does_not_raise_a_second_action(
            self, hse_manager, worker):
        """★ The same guard as the investigation. The screen saves the whole set
        every time, so a payload still carrying last time's action must not raise
        it again — one root cause would otherwise collect an identical action per
        save, each with its own inbox task and escalation ladder."""
        from datetime import date, timedelta

        _, client = hse_manager
        owner, _ = worker
        hazard = await self._a_hazard(client)
        body = {
            "causes": [{
                "cause_type": "root", "label": "No check after a changeover",
                "action": {
                    "title": "Add a guard check",
                    "owner_id": str(owner.id),
                    "due_date": (date.today() + timedelta(days=14)).isoformat(),
                },
            }],
        }
        first = await client.put(f"/api/v1/hazards/{hazard['id']}/causes", json=body)
        assert first.status_code == 200, first.text
        again = await client.put(f"/api/v1/hazards/{hazard['id']}/causes", json=body)
        assert again.status_code == 200, again.text

        tree = again.json()
        cause_id = tree["root"][0]["id"]
        assert len(tree["actions"][cause_id]) == 1, (
            "saving again must not raise the action a second time")

    async def test_a_cause_with_an_action_survives_being_dropped(
            self, hse_manager, worker):
        """★ Deleting it would orphan the action and lose the link between a root
        cause and what is being done about it."""
        from datetime import date, timedelta

        _, client = hse_manager
        owner, _ = worker
        hazard = await self._a_hazard(client)
        await client.put(f"/api/v1/hazards/{hazard['id']}/causes", json={
            "causes": [{
                "cause_type": "root", "label": "Kept because it has work on it",
                "action": {"title": "Do the thing", "owner_id": str(owner.id),
                           "due_date": (date.today() + timedelta(days=7)).isoformat()},
            }],
        })
        after = await client.put(f"/api/v1/hazards/{hazard['id']}/causes", json={
            "causes": [{"cause_type": "root", "label": "A different cause"}],
        })
        assert after.status_code == 200, after.text
        labels = [c["label"] for c in after.json()["root"]]
        assert "Kept because it has work on it" in labels
        assert "A different cause" in labels

    async def test_an_incident_and_a_hazard_do_not_see_each_others_causes(
            self, hse_manager):
        """★ One table, two owners. The CHECK says a row has exactly one — this
        is the half that says the queries agree."""
        from datetime import datetime, timedelta, timezone

        _, client = hse_manager
        hazard = await self._a_hazard(client)
        await client.put(f"/api/v1/hazards/{hazard['id']}/causes", json={
            "causes": [{"cause_type": "root", "label": "The hazard's own"}]})

        incident = (await client.post("/api/v1/incidents", json={
            "form_kind": "medical", "title": "Something else",
            "occurred_at": (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat(),
        })).json()
        await client.post(f"/api/v1/incidents/{incident['id']}/submit")
        await client.put(f"/api/v1/incidents/{incident['id']}/causes", json={
            "causes": [{"cause_type": "root", "label": "The incident's own"}]})

        haz_read = await client.get(f"/api/v1/hazards/{hazard['id']}/causes")
        assert haz_read.status_code == 200, haz_read.text
        assert [c["label"] for c in haz_read.json()["root"]] == ["The hazard's own"]

        # ★ `cause-tree`, not `causes` — the latter is the PUT only. My first
        # version of this test asked for a GET that does not exist, and
        # `.get("root", [])` turned the 404 into "no causes", so it read as the
        # incident having lost them. The status is asserted now and the default
        # is gone: a missing key here is a broken test, not an empty result.
        inc_read = await client.get(f"/api/v1/incidents/{incident['id']}/cause-tree")
        assert inc_read.status_code == 200, inc_read.text
        assert [c["label"] for c in inc_read.json()["root"]] == ["The incident's own"]
