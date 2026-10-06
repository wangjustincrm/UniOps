"""Submitting a form that has nothing in it.

Round 4, TC-11: "press the open the minutes returns a new form not the same form
back."

It *was* the same form. Three sets of committee minutes in the dev database were
marked submitted with not one answer between them, so opening them showed an
empty form — indistinguishable from a fresh one, and the only way to tell was to
compare reference numbers across two screens. Two things were wrong and neither
was the idempotency:

- a template whose questions are all optional, which HSE's minutes form is, let
  a completely blank form be submitted, and a submitted form is immutable;
- the form did not say what it was for.

A blank statutory record is worse than a missing one, because it looks done.
"""
import uuid
from datetime import date

import pytest

from app.models.config import EhsConfig


@pytest.fixture
async def optional_template(hse_manager):
    """A published form on which nothing is required.

    This is the shape that made the hole — not a contrived one. HSE's minutes
    form has no required questions, because what a committee minutes varies by
    meeting.
    """
    _, client = hse_manager
    created = await client.post("/api/v1/forms/templates", json={
        "code": f"optional-{uuid.uuid4().hex[:5]}",
        "name": "Nothing is required here",
        "category": "jhsc",
    })
    assert created.status_code in (200, 201), created.text
    template = created.json()

    saved = await client.put(f"/api/v1/forms/templates/{template['id']}/schema", json={
        "schema": [
            {"key": "present", "type": "text", "label": "Who was there", "required": False},
            {"key": "notes", "type": "textarea", "label": "What was discussed",
             "required": False},
        ],
    })
    assert saved.status_code in (200, 201), saved.text
    published = await client.post(
        f"/api/v1/forms/templates/{template['id']}/publish",
        json={"change_note": "First version"})
    assert published.status_code in (200, 201), published.text
    return template


async def _a_submission(client, template_id: str) -> dict:
    r = await client.post(f"/api/v1/forms/templates/{template_id}/submissions", json={})
    assert r.status_code in (200, 201), r.text
    return r.json()


class TestABlankFormCannotBeFiled:
    async def test_submitting_an_empty_form_is_refused(self, hse_manager, optional_template):
        _, client = hse_manager
        sub = await _a_submission(client, optional_template["id"])

        refused = await client.post(f"/api/v1/forms/submissions/{sub['id']}/submit")
        assert refused.status_code == 422, refused.text
        assert "nothing has been filled in" in refused.json()["detail"].lower()

        # ★ And it is still editable, which is the point of refusing rather than
        # accepting: a submitted form cannot be corrected.
        back = (await client.get(f"/api/v1/forms/submissions/{sub['id']}")).json()
        assert back["status"] == "draft"

    async def test_one_answer_is_enough_to_file_it(self, hse_manager, optional_template):
        """★ The admission. Refusing everything would satisfy the test above.

        One answer, on a form where nothing is required, still submits — the
        guard is against *nothing at all*, not a judgement about completeness.
        """
        _, client = hse_manager
        sub = await _a_submission(client, optional_template["id"])

        saved = await client.put(f"/api/v1/forms/submissions/{sub['id']}/answers",
                                 json={"values": {"present": "Eight of twenty-three"}})
        assert saved.status_code == 200, saved.text

        submitted = await client.post(f"/api/v1/forms/submissions/{sub['id']}/submit")
        assert submitted.status_code == 200, submitted.text
        assert submitted.json()["status"] == "submitted"

    async def test_an_answer_that_was_cleared_does_not_count(
            self, hse_manager, optional_template):
        """Blank means blank, whether nothing was ever typed or it was deleted.

        An answer row exists once a question has been touched, so counting rows
        rather than values would let a form that was filled in and then emptied
        be filed as if it said something.
        """
        _, client = hse_manager
        sub = await _a_submission(client, optional_template["id"])

        assert (await client.put(f"/api/v1/forms/submissions/{sub['id']}/answers",
                                 json={"values": {"present": "Everyone"}})).status_code == 200
        assert (await client.put(f"/api/v1/forms/submissions/{sub['id']}/answers",
                                 json={"values": {"present": ""}})).status_code == 200

        refused = await client.post(f"/api/v1/forms/submissions/{sub['id']}/submit")
        assert refused.status_code == 422, refused.text


class TestTheMinutesSayWhatTheyAreFor:
    @pytest.fixture
    async def minutes_form_configured(self, hse_manager, db_session, optional_template):
        config = await db_session.get(EhsConfig, 1)
        if config is None:
            config = EhsConfig(id=1)
            db_session.add(config)
        config.jhsc_minutes_template_code = optional_template["code"]
        await db_session.commit()
        return optional_template

    async def test_a_started_minutes_form_names_its_meeting(
            self, hse_manager, minutes_form_configured):
        """★ Both directions. The meeting knew its minutes; the minutes knew
        nothing, so an unfilled one looked like a fresh blank form."""
        _, client = hse_manager
        meeting = (await client.post("/api/v1/jhsc/meetings", json={
            "held_on": date.today().isoformat(), "title": "Monthly meeting"})).json()

        started = await client.post(
            f"/api/v1/jhsc/meetings/{meeting['id']}/minutes-form/start")
        assert started.status_code in (200, 201), started.text
        submission_id = started.json()["submission_id"]

        sub = (await client.get(f"/api/v1/forms/submissions/{submission_id}")).json()
        assert sub["source_type"] == "jhsc"
        assert sub["source_id"] == meeting["id"]
        assert sub["source_ref"] == meeting["meeting_no"], \
            "the form cannot say which meeting it minutes"

    async def test_the_second_press_returns_the_same_form_still_naming_the_meeting(
            self, hse_manager, minutes_form_configured):
        _, client = hse_manager
        meeting = (await client.post("/api/v1/jhsc/meetings", json={
            "held_on": date.today().isoformat()})).json()

        first = await client.post(f"/api/v1/jhsc/meetings/{meeting['id']}/minutes-form/start")
        second = await client.post(f"/api/v1/jhsc/meetings/{meeting['id']}/minutes-form/start")
        assert first.json()["submission_id"] == second.json()["submission_id"]

        sub = (await client.get(
            f"/api/v1/forms/submissions/{second.json()['submission_id']}")).json()
        assert sub["source_ref"] == meeting["meeting_no"]


class TestPublishingMinutesWrittenOnTheForm:
    """★ "publish the minutes button can not be pressed by the creator of the
    meeting" — which read like a permissions fault and was not one.

    Publishing tested the free-text minutes box. Where HSE has configured a
    minutes form that box is not rendered at all, so the test could never pass
    and Publish was permanently refused. Adding the form path two rounds ago
    without moving the guard that protects publishing is what caused it.
    """

    @pytest.fixture
    async def minutes_form_configured(self, hse_manager, db_session, optional_template):
        config = await db_session.get(EhsConfig, 1)
        if config is None:
            config = EhsConfig(id=1)
            db_session.add(config)
        config.jhsc_minutes_template_code = optional_template["code"]
        await db_session.commit()
        return optional_template

    async def test_a_meeting_minuted_on_the_form_can_be_published(
            self, hse_manager, minutes_form_configured):
        _, client = hse_manager
        meeting = (await client.post("/api/v1/jhsc/meetings", json={
            "held_on": date.today().isoformat()})).json()

        started = await client.post(
            f"/api/v1/jhsc/meetings/{meeting['id']}/minutes-form/start")
        submission_id = started.json()["submission_id"]

        # Refused while the form is still a draft — the noticeboard copy comes
        # from a finished document, not a half-written one.
        too_early = await client.post(f"/api/v1/jhsc/meetings/{meeting['id']}/publish")
        assert too_early.status_code == 422, too_early.text
        assert "minutes form" in too_early.json()["detail"]

        assert (await client.put(f"/api/v1/forms/submissions/{submission_id}/answers",
                                 json={"values": {"notes": "Racking inspection agreed"}}
                                 )).status_code == 200
        assert (await client.post(
            f"/api/v1/forms/submissions/{submission_id}/submit")).status_code == 200

        # ★ And now it publishes, with the box still empty.
        published = await client.post(f"/api/v1/jhsc/meetings/{meeting['id']}/publish")
        assert published.status_code == 200, published.text
        assert published.json()["status"] == "published"
        assert not (published.json()["minutes"] or "").strip(), \
            "this case is only meaningful while the free-text box is empty"

    async def test_a_meeting_with_neither_is_still_refused(
            self, hse_manager, minutes_form_configured):
        """The admission's opposite: widening the test must not remove it."""
        _, client = hse_manager
        meeting = (await client.post("/api/v1/jhsc/meetings", json={
            "held_on": date.today().isoformat()})).json()
        refused = await client.post(f"/api/v1/jhsc/meetings/{meeting['id']}/publish")
        assert refused.status_code == 422, refused.text


@pytest.fixture
async def dated_template(hse_manager):
    """A published form with a date on it — which is most of them.

    Every inspection HSE has shown us asks when it was carried out, and the form
    engine's own seed templates all carry one.
    """
    _, client = hse_manager
    created = await client.post("/api/v1/forms/templates", json={
        "code": f"dated-{uuid.uuid4().hex[:5]}",
        "name": "Something with a date on it",
        "category": "inspection",
    })
    assert created.status_code in (200, 201), created.text
    template = created.json()
    saved = await client.put(f"/api/v1/forms/templates/{template['id']}/schema", json={
        "schema": [
            {"key": "when", "type": "date", "label": "Date of inspection",
             "required": False},
            {"key": "notes", "type": "textarea", "label": "Notes", "required": False},
        ],
    })
    assert saved.status_code in (200, 201), saved.text
    published = await client.post(
        f"/api/v1/forms/templates/{template['id']}/publish",
        json={"change_note": "First version"})
    assert published.status_code in (200, 201), published.text
    return template


class TestADateAnswerCanBeSaved:
    """★★ Round 5, reported three times over as “What you typed has not been
    saved yet, so this has not been submitted” — on an equipment inspection, a
    ladder inspection and the committee minutes.

    One bug behind all three. `value_date` is a DATE column and was being handed
    the string: asyncpg asks the value for `toordinal()`, a str has none, so
    every save of a date answer answered 500. The front end queues today's date
    into every date question the moment a form is opened, so the first keystroke
    on any form with a date on it hit it — and the round-4 submit guard then
    correctly refused to submit, because the batch never cleared.

    It read as a connection problem because the message on screen was about the
    batch rather than about the answer.
    """

    async def test_saving_a_date_answer_stores_it(self, hse_manager, dated_template):
        _, client = hse_manager
        sub = await _a_submission(client, dated_template["id"])

        saved = await client.put(
            f"/api/v1/forms/submissions/{sub['id']}/answers",
            json={"values": {"when": "2026-10-05"}})
        assert saved.status_code == 200, saved.text
        row = next(a for a in saved.json() if a["question_key"] == "when")
        assert row["value_date"] == "2026-10-05"

    async def test_a_form_with_a_date_can_be_submitted(self, hse_manager, dated_template):
        """The whole path the three failures were on, end to end."""
        _, client = hse_manager
        sub = await _a_submission(client, dated_template["id"])
        first = await client.put(
            f"/api/v1/forms/submissions/{sub['id']}/answers",
            json={"values": {"when": date.today().isoformat(), "notes": "Checked."}})
        assert first.status_code == 200, first.text

        done = await client.post(f"/api/v1/forms/submissions/{sub['id']}/submit", json={})
        assert done.status_code == 200, done.text
        assert done.json()["status"] == "submitted"

    async def test_a_date_already_parsed_still_works(self, hse_manager, dated_template):
        """Belt and braces: the scheduler and the asset path pass a real date."""
        from app.services import form_engine

        row = form_engine.normalise(
            {"key": "when", "type": "date", "label": "Date"}, date(2026, 10, 5))
        assert row["value_date"] == date(2026, 10, 5)

    async def test_rubbish_is_refused_rather_than_crashing(
            self, hse_manager, dated_template):
        """★ 422, not 500, and not silently dropped either.

        Dropping it would leave a dated statutory record with no date on it and
        nothing on screen to say so — worse than a refusal the person can see.
        """
        _, client = hse_manager
        sub = await _a_submission(client, dated_template["id"])
        bad = await client.put(
            f"/api/v1/forms/submissions/{sub['id']}/answers",
            json={"values": {"when": "last Tuesday"}})
        assert bad.status_code == 422, bad.text
        assert "yyyy-mm-dd" in bad.text

    async def test_clearing_a_date_is_allowed(self, hse_manager, dated_template):
        """An emptied date is empty, not a parse failure."""
        _, client = hse_manager
        sub = await _a_submission(client, dated_template["id"])
        await client.put(f"/api/v1/forms/submissions/{sub['id']}/answers",
                         json={"values": {"when": "2026-10-05"}})
        cleared = await client.put(
            f"/api/v1/forms/submissions/{sub['id']}/answers",
            json={"values": {"when": ""}})
        assert cleared.status_code == 200, cleared.text
        row = next(a for a in cleared.json() if a["question_key"] == "when")
        assert row["value_date"] is None


class TestOnlySomeKindsOfFormHaveAVerdict:
    """★ Round 5, TC-09: "minutes are a documentation of a meeting, there is no
    pass or fail in the form nor in the minutes."

    He is right, and it was not only the minutes: the builder offered a fail
    condition on every yes/no question of every template, so a toolbox talk and
    a job analysis carried the apparatus of an inspection verdict too.
    """

    async def _template(self, client, category: str):
        created = await client.post("/api/v1/forms/templates", json={
            "code": f"k-{uuid.uuid4().hex[:5]}", "name": f"A {category} form",
            "category": category,
        })
        assert created.status_code in (200, 201), created.text
        return created.json()

    async def test_an_inspection_has_a_verdict(self, hse_manager):
        _, client = hse_manager
        t = await self._template(client, "inspection")
        assert t["can_fail"] is True

        saved = await client.put(f"/api/v1/forms/templates/{t['id']}/schema", json={
            "schema": [{"key": "ok", "type": "yes_no_na", "label": "Is it sound?",
                        "fail_on": {"equals": "no"}}],
        })
        assert saved.status_code in (200, 201), saved.text
        back = (await client.get(f"/api/v1/forms/templates/{t['id']}")).json()
        assert back["can_fail"] is True

    async def test_minutes_have_none_and_the_condition_is_refused(self, hse_manager):
        """★ Refused rather than quietly dropped.

        A fail condition saved and never fired is the shape this module has
        been caught by twice — a field built and never read.
        """
        _, client = hse_manager
        t = await self._template(client, "other")
        assert t["can_fail"] is False

        refused = await client.put(f"/api/v1/forms/templates/{t['id']}/schema", json={
            "schema": [{"key": "ok", "type": "yes_no_na", "label": "Agreed?",
                        "fail_on": {"equals": "no"}}],
        })
        assert refused.status_code == 422, refused.text
        assert "no pass or fail" in refused.text
        assert "ok" in refused.text, "it must name the question to fix"

    async def test_the_same_form_saves_fine_without_one(self, hse_manager):
        """The admission beside the denial: minutes are not blocked, only the
        verdict is."""
        _, client = hse_manager
        t = await self._template(client, "other")
        saved = await client.put(f"/api/v1/forms/templates/{t['id']}/schema", json={
            "schema": [{"key": "ok", "type": "yes_no_na", "label": "Agreed?"},
                       {"key": "notes", "type": "textarea", "label": "Discussion"}],
        })
        assert saved.status_code in (200, 201), saved.text

    async def test_which_kinds_have_one(self, hse_manager):
        """Stated once, read by every screen."""
        from app.services import form_engine

        for kind in ("inspection", "audit", "permit"):
            assert form_engine.kind_can_fail(kind), kind
        for kind in ("jsa", "orientation", "toolbox_talk", "other", None, ""):
            assert not form_engine.kind_can_fail(kind), kind


class TestArchivingAndDeletingATemplate:
    """Round 5, box 7: "none of the forms are finalized and specifically the ones
    mentioned they are scratch templates and can be deleted, in fact provide a
    way to delete or archive all the forms."

    There was neither. `is_active` had been on the template since the first
    migration with no endpoint to set it, so nothing could ever be taken out of
    the list.
    """

    async def _template(self, client, category: str = "inspection"):
        made = await client.post("/api/v1/forms/templates", json={
            "code": f"a-{uuid.uuid4().hex[:5]}", "name": "Scratch", "category": category,
        })
        assert made.status_code in (200, 201), made.text
        return made.json()

    async def test_a_scratch_template_can_be_deleted(self, hse_manager):
        _, client = hse_manager
        t = await self._template(client)

        gone = await client.delete(f"/api/v1/forms/templates/{t['id']}")
        assert gone.status_code == 204, gone.text
        assert (await client.get(f"/api/v1/forms/templates/{t['id']}")).status_code == 404

    async def test_a_template_somebody_has_filled_in_is_not_deleted(self, hse_manager):
        """★ Whatever is asked. The submission carries its own snapshot of the
        questions, but a statutory record whose form no longer exists is the kind
        of gap an auditor finds."""
        _, client = hse_manager
        t = await self._template(client)
        await client.put(f"/api/v1/forms/templates/{t['id']}/schema", json={
            "schema": [{"key": "ok", "type": "yes_no_na", "label": "Sound?"}]})
        await client.post(f"/api/v1/forms/templates/{t['id']}/publish",
                          json={"change_note": "v1"})
        await _a_submission(client, t["id"])

        refused = await client.delete(f"/api/v1/forms/templates/{t['id']}")
        assert refused.status_code == 409, refused.text
        assert "1 submissions" in refused.text
        assert "Archive it instead" in refused.text

        # And archiving, which is what it tells you to do, works.
        archived = await client.post(f"/api/v1/forms/templates/{t['id']}/archive")
        assert archived.status_code == 200, archived.text
        assert archived.json()["is_active"] is False

    async def test_an_archived_template_is_off_the_list_and_can_come_back(
            self, hse_manager):
        _, client = hse_manager
        t = await self._template(client)
        await client.post(f"/api/v1/forms/templates/{t['id']}/archive")

        listed = [x["id"] for x in (await client.get("/api/v1/forms/templates")).json()]
        assert t["id"] not in listed

        with_archived = [x["id"] for x in (await client.get(
            "/api/v1/forms/templates?include_inactive=true")).json()]
        assert t["id"] in with_archived

        back = await client.post(f"/api/v1/forms/templates/{t['id']}/restore")
        assert back.status_code == 200, back.text
        assert t["id"] in [x["id"] for x in
                           (await client.get("/api/v1/forms/templates")).json()]

    async def test_archiving_is_refused_while_equipment_scans_to_it(
            self, hse_manager, db_session):
        """★ Counted, not assumed. Archiving a form an eyewash station opens by
        QR code would leave somebody scanning it and landing on a form nobody
        maintains."""
        import uuid as _uuid

        from app.models.asset import SafetyAsset

        _, client = hse_manager
        t = await self._template(client)
        db_session.add(SafetyAsset(
            id=_uuid.uuid4(), asset_no="EQ-TEST-1", name="An eyewash station",
            asset_type_label="Eye Wash", status="in_service",
            inspection_template_id=_uuid.UUID(t["id"])))
        await db_session.flush()

        refused = await client.post(f"/api/v1/forms/templates/{t['id']}/archive")
        assert refused.status_code == 409, refused.text
        assert "scanned" in refused.text

    async def test_references_are_reported_before_anything_is_asked(self, hse_manager):
        """The screen can say what will happen rather than finding out."""
        _, client = hse_manager
        t = await self._template(client)
        refs = (await client.get(
            f"/api/v1/forms/templates/{t['id']}/references")).json()
        # Every kind of reference reported, each at zero. Asserted key by key so
        # adding a fourth kind does not silently stop being checked.
        assert refs["submissions"] == 0
        assert refs["schedules"] == 0
        assert refs["equipment"] == 0
        assert refs["settings"] == 0


class TestAKindWithNoVerdictRaisesNoFailures:
    """★ Round 5 review (uniops-41): the two halves disagreed.

    `fail_criteria` on the template list reads the kind of form and reported 0
    for a minutes form. `form_engine.normalise` reads the question's own
    `fail_on` and knows nothing about the template, so a leftover condition on
    such a form still produced failures — raising corrective actions, and on an
    equipment form taking the item out of service — while the list said there
    were no criteria at all.

    Saving a condition on a kind with no verdict is refused now, so the only way
    to hold one is a template whose kind was changed afterwards. That is exactly
    the case this covers.
    """

    async def test_a_leftover_condition_on_a_minutes_form_fails_nothing(
            self, hse_manager, db_session):
        from sqlalchemy import select

        from app.models.form import FormTemplate, FormVersion

        _, client = hse_manager
        # Built as an inspection, with a real fail condition…
        made = await client.post("/api/v1/forms/templates", json={
            "code": f"turned-{uuid.uuid4().hex[:5]}", "name": "Was an inspection",
            "category": "inspection",
        })
        template = made.json()
        await client.put(f"/api/v1/forms/templates/{template['id']}/schema", json={
            "schema": [{"key": "ok", "type": "yes_no_na", "label": "Is it sound?",
                        "fail_on": {"equals": "no"}}]})
        await client.post(f"/api/v1/forms/templates/{template['id']}/publish",
                          json={"change_note": "v1"})

        # …then changed to a kind that has no verdict, which is how a condition
        # can still be in a published schema.
        row = (await db_session.execute(select(FormTemplate).where(
            FormTemplate.id == uuid.UUID(template["id"])))).scalar_one()
        row.category = "other"
        await db_session.commit()

        version = (await db_session.execute(select(FormVersion).where(
            FormVersion.template_id == uuid.UUID(template["id"])))).scalars().first()
        assert any(q.get("fail_on") for q in version.schema), (
            "the condition must still be in the schema, or this proves nothing")

        sub = await _a_submission(client, template["id"])
        saved = await client.put(f"/api/v1/forms/submissions/{sub['id']}/answers",
                                 json={"values": {"ok": "no"}})
        assert saved.status_code == 200, saved.text
        # ★ The column itself, not just what the submit does with it. Settled
        # where the answer is written, so all three readers agree.
        assert saved.json()[0]["is_failure"] is False

        done = await client.post(f"/api/v1/forms/submissions/{sub['id']}/submit", json={})
        assert done.status_code == 200, done.text
        assert done.json()["failed_count"] == 0, (
            "a form with no verdict cannot record a failure")

        # No corrective action raised off it…
        actions = (await client.get("/api/v1/actions?source_type=inspection")).json()
        rows = actions if isinstance(actions, list) else actions.get("items", [])
        assert not [a for a in rows if a.get("source_id") == sub["id"]]

        # …and nothing for the "which checks fail most often" report to count,
        # which is the reader my first attempt at this missed.
        from app.crud import form as form_crud

        counts = await form_crud.failure_counts(db_session, uuid.UUID(template["id"]))
        assert counts == []

    async def test_an_inspection_still_fails_and_raises(self, hse_manager):
        """★ The admission. Narrowing this must not stop an inspection failing —
        that is the whole of TC-07's "there should be a pass of fail criteria"."""
        _, client = hse_manager
        made = await client.post("/api/v1/forms/templates", json={
            "code": f"insp-{uuid.uuid4().hex[:5]}", "name": "A real inspection",
            "category": "inspection",
        })
        template = made.json()
        await client.put(f"/api/v1/forms/templates/{template['id']}/schema", json={
            "schema": [{"key": "ok", "type": "yes_no_na", "label": "Is it sound?",
                        "fail_on": {"equals": "no"}}]})
        await client.post(f"/api/v1/forms/templates/{template['id']}/publish",
                          json={"change_note": "v1"})

        sub = await _a_submission(client, template["id"])
        await client.put(f"/api/v1/forms/submissions/{sub['id']}/answers",
                         json={"values": {"ok": "no"}})
        done = await client.post(f"/api/v1/forms/submissions/{sub['id']}/submit", json={})
        assert done.status_code == 200, done.text
        assert done.json()["failed_count"] == 1


class TestArchivingFinishesTheJob:
    """★ Round 5 review (uniops-41): switching a schedule off stopped new rounds
    and left the pending ones, their inbox tasks, and the way in.

    Each of the three is the same fault in a different place — the thing outlives
    the template, and invisibly, because the template has left the list where
    somebody would have noticed.
    """

    async def _published(self, client, category: str = "inspection"):
        made = await client.post("/api/v1/forms/templates", json={
            "code": f"arch-{uuid.uuid4().hex[:5]}", "name": "Monthly something",
            "category": category,
        })
        template = made.json()
        await client.put(f"/api/v1/forms/templates/{template['id']}/schema", json={
            "schema": [{"key": "ok", "type": "yes_no_na", "label": "Sound?"}]})
        await client.post(f"/api/v1/forms/templates/{template['id']}/publish",
                          json={"change_note": "v1"})
        return template

    async def test_pending_rounds_and_their_tasks_are_cancelled(
            self, hse_manager, db_session):
        from sqlalchemy import select

        from app.models.mirrors import Task
        from app.models.schedule import FormScheduleRun
        from app.services import schedule_sweep, tasks as task_service

        _, client = hse_manager
        template = await self._published(client)
        made = await client.post("/api/v1/forms/schedules", json={
            "template_id": template["id"], "name": "Every month",
            "frequency": "monthly", "next_due_on": date.today().isoformat(),
        })
        assert made.status_code in (200, 201), made.text

        runs = await schedule_sweep.generate_due(db_session, today=date.today())
        await db_session.commit()
        assert runs, "the sweep must have raised a round, or this proves nothing"
        run_id = runs[0].id

        open_tasks = (await db_session.execute(select(Task).where(
            Task.document_type == task_service.DOC_RUN,
            Task.document_id == run_id,
            Task.is_completed.is_(False)))).scalars().all()
        assert open_tasks, "the round must be in somebody's list to begin with"

        archived = await client.post(f"/api/v1/forms/templates/{template['id']}/archive")
        assert archived.status_code == 200, archived.text

        run = await db_session.get(FormScheduleRun, run_id)
        await db_session.refresh(run)
        assert run.status == "cancelled", (
            "a pending round on an archived form is nobody's work")

        still_open = (await db_session.execute(select(Task).where(
            Task.document_type == task_service.DOC_RUN,
            Task.document_id == run_id,
            Task.is_completed.is_(False)))).scalars().all()
        assert still_open == [], "and nobody should still be chased for it"

    async def test_a_missed_round_is_cancelled_too(self, hse_manager, db_session):
        """★ Round 5 review (uniops-41): `missed` means it was chased and not
        done. Once the form is archived there is nothing left to chase it for —
        and left alone it stayed on the forms list as overdue work that answers
        "this form has been archived" when you press Start.
        """
        from sqlalchemy import select

        from app.models.schedule import FormScheduleRun
        from app.services import schedule_sweep

        _, client = hse_manager
        template = await self._published(client)
        made = await client.post("/api/v1/forms/schedules", json={
            "template_id": template["id"], "name": "Every month",
            "frequency": "monthly", "next_due_on": date.today().isoformat(),
        })
        assert made.status_code in (200, 201), made.text
        runs = await schedule_sweep.generate_due(db_session, today=date.today())
        await db_session.commit()
        assert runs

        run = await db_session.get(FormScheduleRun, runs[0].id)
        run.status = "missed"
        await db_session.commit()

        archived = await client.post(f"/api/v1/forms/templates/{template['id']}/archive")
        assert archived.status_code == 200, archived.text

        await db_session.refresh(run)
        assert run.status == "cancelled"

        # ★ And off the default list. Asking with no filter used to return
        # cancelled rounds as well — the screen asks for pending and missed, so
        # nothing exercised the default.
        listed = (await client.get("/api/v1/forms/runs")).json()
        rows = listed if isinstance(listed, list) else listed.get("items", [])
        assert str(run.id) not in [str(r.get("id")) for r in rows]

        # Still reachable when asked for by name, because the round happened.
        explicit = (await client.get("/api/v1/forms/runs?status=cancelled")).json()
        explicit_rows = explicit if isinstance(explicit, list) else explicit.get("items", [])
        assert str(run.id) in [str(r.get("id")) for r in explicit_rows]

    async def test_restoring_leaves_the_schedule_off(self, hse_manager, db_session):
        """★ Deliberate, and stated on screen. A monthly form archived six months
        ago and restored today would otherwise raise six overdue rounds the moment
        it came back — work nobody decided to ask for."""
        from sqlalchemy import select

        from app.models.schedule import FormSchedule

        _, client = hse_manager
        template = await self._published(client)
        await client.post("/api/v1/forms/schedules", json={
            "template_id": template["id"], "name": "Every month",
            "frequency": "monthly", "next_due_on": date.today().isoformat(),
        })
        await client.post(f"/api/v1/forms/templates/{template['id']}/archive")
        await client.post(f"/api/v1/forms/templates/{template['id']}/restore")

        sched = (await db_session.execute(select(FormSchedule).where(
            FormSchedule.template_id == uuid.UUID(template["id"])))).scalars().first()
        await db_session.refresh(sched)
        assert sched.is_active is False, (
            "restoring must not quietly resume a schedule")

    async def test_an_archived_form_cannot_be_started(self, hse_manager):
        """★ Archiving took it off every list; a link, a bookmark or a QR code
        could still start one."""
        _, client = hse_manager
        template = await self._published(client)
        await client.post(f"/api/v1/forms/templates/{template['id']}/archive")

        refused = await client.post(
            f"/api/v1/forms/templates/{template['id']}/submissions", json={})
        assert refused.status_code == 409, refused.text
        assert "archived" in refused.text

        # The admission: put it back and it works again.
        await client.post(f"/api/v1/forms/templates/{template['id']}/restore")
        started = await client.post(
            f"/api/v1/forms/templates/{template['id']}/submissions", json={})
        assert started.status_code in (200, 201), started.text

    async def test_the_minutes_form_cannot_be_archived_from_under_the_committee(
            self, hse_manager, db_session):
        """★ Settings names the committee's minutes form by its *code*, so it is
        not a foreign key and nothing would have stopped it being archived — the
        next meeting would simply have found no minutes form."""
        from sqlalchemy import select

        from app.models.config import EhsConfig

        _, client = hse_manager
        template = await self._published(client, category="other")
        # The single config row is seeded by a migration, and the test database
        # is built with `create_all` — so it may not be here.
        config = (await db_session.execute(
            select(EhsConfig).where(EhsConfig.id == 1))).scalar_one_or_none()
        if config is None:
            config = EhsConfig(id=1)
            db_session.add(config)
        config.jhsc_minutes_template_code = template["code"]
        await db_session.commit()

        refs = (await client.get(
            f"/api/v1/forms/templates/{template['id']}/references")).json()
        assert refs["settings"] == 1, "the reference has to be visible before it is hit"

        refused = await client.post(f"/api/v1/forms/templates/{template['id']}/archive")
        assert refused.status_code == 409, refused.text
        assert "minutes" in refused.text
