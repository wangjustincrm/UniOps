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
