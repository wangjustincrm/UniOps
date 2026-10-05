"""What an ordinary employee can open, and what they cannot.

Round 4, TC-19: "worker is able to open items not related to them." The round
before had gone the other way — screens were turning refusals into empty lists,
so refusals were made visible — and making them visible showed that some of
them were not refusals at all.

Three holes, all of the same shape: a record was gated and something *about*
that record was not.

Every denial here is paired with an admission in the same test. "Nobody can
read it" satisfies a denial assertion perfectly well, and a permission change
that locks out the people who need the screen is the other way this module has
failed — three times, by building something an HSE manager could reach and a
worker could not.
"""
import uuid
from datetime import datetime, timedelta, timezone

import pytest

from app.models.incident import Incident
from app.models.risk import RiskLikelihood, RiskSeverity


@pytest.fixture
async def risk_matrix(db_session):
    """Two rows of HSE's grid.

    The real matrix arrives by migration and this database is built from the
    models, so it is empty here — an assessment against it answers "Unknown
    likelihood". Only the two bands these tests score against are seeded; the
    grid's contents are HSE's document and are not what is under test.
    """
    db_session.add_all([
        RiskLikelihood(code="B", label="Only Slightly Possible", sort_order=2,
                       ratings={"2": "acceptable", "3": "acceptable"}),
        RiskLikelihood(code="C", label="Possible", sort_order=3,
                       ratings={"2": "acceptable", "3": "moderate"}),
        RiskSeverity(level=2, people="Medical aid and/or modified work"),
        RiskSeverity(level=3, people="Lost time illness"),
    ])
    await db_session.commit()


async def _an_incident_by_somebody_else(client, db_session) -> Incident:
    """Reported by HSE, with the worker neither reporting nor involved."""
    r = await client.post("/api/v1/incidents", json={
        "form_kind": "medical",
        "title": "Somebody else's incident",
        # ★ Relative to now, never a fixed hour on today's date: an incident
        # cannot be dated in the future, and "today at 09:00" is in the future
        # for the first nine hours of every UTC day.
        "occurred_at": (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat(),
        "description": "Caught a finger at the capping head",
    })
    assert r.status_code in (200, 201), r.text
    return await db_session.get(Incident, uuid.UUID(r.json()["id"]))


class TestRiskAssessmentsFollowTheirRecord:
    """★ The incident answered 403 and its assessments answered 200.

    The assessment states what could happen, how bad, and who judged it. Served
    on the reporting permission it is the incident, read around the incident's
    own gate.
    """

    async def test_a_worker_cannot_read_the_assessments_of_an_incident_they_cannot_open(
            self, hse_manager, worker, db_session, risk_matrix):
        _, hse = hse_manager
        _, wc = worker
        incident = await _an_incident_by_somebody_else(hse, db_session)

        made = await hse.post("/api/v1/risk/assessments", json={
            "source_type": "incident", "source_id": str(incident.id),
            "hazard_label": "Unguarded nip point",
            "consequence_type": "people",
            "likelihood_code": "C", "severity_level": 3,
        })
        assert made.status_code in (200, 201), made.text

        # The incident itself is already refused — that is the rule being followed.
        assert (await wc.get(f"/api/v1/incidents/{incident.id}")).status_code == 403

        refused = await wc.get(
            f"/api/v1/risk/assessments?source_type=incident&source_id={incident.id}")
        assert refused.status_code == 403, refused.text

        # ★ The admission: HSE reads the same thing.
        allowed = await hse.get(
            f"/api/v1/risk/assessments?source_type=incident&source_id={incident.id}")
        assert allowed.status_code == 200
        assert len(allowed.json()) == 1

    async def test_a_worker_reads_the_assessments_of_their_own_incident(
            self, worker, hse_manager, db_session, risk_matrix):
        """The narrowing follows the incident's rule, which includes "your own"."""
        wu, wc = worker
        _, hse = hse_manager

        mine = await wc.post("/api/v1/incidents", json={
            "form_kind": "near_miss", "title": "I reported this one",
            "occurred_at": (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat(),
            "description": "Nearly walked into a reversing forklift",
        })
        assert mine.status_code in (200, 201), mine.text
        incident_id = mine.json()["id"]

        scored = await hse.post("/api/v1/risk/assessments", json={
            "source_type": "incident", "source_id": incident_id,
            "consequence_type": "people", "likelihood_code": "B", "severity_level": 2,
        })
        assert scored.status_code in (200, 201), scored.text

        r = await wc.get(
            f"/api/v1/risk/assessments?source_type=incident&source_id={incident_id}")
        assert r.status_code == 200, r.text
        assert len(r.json()) == 1, "the reporter cannot see the assessment of their own report"

    async def test_hazard_assessments_stay_open_to_everybody(self, worker, hse_manager, risk_matrix):
        """Deliberate, and the reason the narrowing is per source type.

        A hazard is plant-wide safety information — a worker is meant to consult
        it before starting work, and the hazard register is readable on the same
        permission. Locking the scorings while leaving the register open would
        be theatre.
        """
        _, hse = hse_manager
        _, wc = worker
        made = await hse.post("/api/v1/hazards", json={
            "title": "Chemical store door sticks", "description": "Jams shut",
        })
        assert made.status_code in (200, 201), made.text
        hazard_id = made.json()["id"]

        scored = await hse.post(f"/api/v1/hazards/{hazard_id}/assess", json={
            "consequence_type": "people", "likelihood_code": "C", "severity_level": 2,
        })
        assert scored.status_code in (200, 201), scored.text

        r = await wc.get(
            f"/api/v1/risk/assessments?source_type=hazard&source_id={hazard_id}")
        assert r.status_code == 200, r.text
        assert len(r.json()) >= 1

    async def test_listing_without_naming_a_record_needs_the_register(
            self, worker, hse_manager):
        """★ Otherwise the filter is the gate, and omitting it is the bypass.

        A caller refused one incident's assessments must not get every
        incident's by leaving the question vaguer.
        """
        _, wc = worker
        _, hse = hse_manager
        assert (await wc.get("/api/v1/risk/assessments")).status_code == 403
        assert (await hse.get("/api/v1/risk/assessments")).status_code == 200

    async def test_naming_the_record_without_its_type_is_still_refused(
            self, hse_manager, worker, db_session, risk_matrix):
        """★ The round-3 fix was half a fix, found in review by uniops-9a.

        `_may_see` only checked anything when the query said "incident", so
        asking for the same record with no type at all passed the gate and the
        filter returned that incident's assessments anyway. The type now comes
        from the record, never from the caller — and neither does claiming it is
        something else.
        """
        _, hse = hse_manager
        _, wc = worker
        incident = await _an_incident_by_somebody_else(hse, db_session)
        made = await hse.post("/api/v1/risk/assessments", json={
            "source_type": "incident", "source_id": str(incident.id),
            "consequence_type": "people", "likelihood_code": "C", "severity_level": 3,
        })
        assert made.status_code in (200, 201), made.text

        # No type at all — the shape that got through.
        bare = await wc.get(f"/api/v1/risk/assessments?source_id={incident.id}")
        assert bare.status_code == 403, bare.text

        # And calling it something else does not help.
        lied = await wc.get(
            f"/api/v1/risk/assessments?source_type=hazard&source_id={incident.id}")
        assert lied.status_code == 403, lied.text

        # ★ The admission: HSE reads it either way.
        assert (await hse.get(
            f"/api/v1/risk/assessments?source_id={incident.id}")).status_code == 200

    async def test_a_worker_cannot_assess_an_incident_they_cannot_open(
            self, hse_manager, worker, db_session, risk_matrix):
        _, hse = hse_manager
        _, wc = worker
        incident = await _an_incident_by_somebody_else(hse, db_session)

        refused = await wc.post("/api/v1/risk/assessments", json={
            "source_type": "incident", "source_id": str(incident.id),
            "consequence_type": "people", "likelihood_code": "C", "severity_level": 3,
        })
        assert refused.status_code == 403, refused.text


class TestTheSigningRosterIsForWhoeverManagesDocuments:
    """Who has *not* signed the harassment policy is a management fact.

    It was readable by anyone who could read the document, which is everybody by
    design — Read & Sign cannot work behind a 403.
    """

    @pytest.fixture
    async def a_published_policy(self, hse_manager):
        _, hse = hse_manager
        made = await hse.post("/api/v1/documents", json={
            "title": "Workplace harassment policy", "category": "policy",
            "version": "1.0", "requires_ack": True,
            "ack_roles": ["requester", "ehs_manager"],
            # Publishing refuses a document with nothing to read, and the real
            # upload needs the file server these tests do not run.
            "file_id": str(uuid.uuid4()), "filename": "harassment-policy.pdf",
        })
        assert made.status_code in (200, 201), made.text
        doc_id = made.json()["id"]
        published = await hse.post(f"/api/v1/documents/{doc_id}/publish", json={})
        assert published.status_code in (200, 201), published.text
        return doc_id

    async def test_a_worker_cannot_see_who_else_has_signed(
            self, worker, hse_manager, a_published_policy):
        _, wc = worker
        _, hse = hse_manager

        assert (await wc.get(
            f"/api/v1/documents/{a_published_policy}/acknowledgements")).status_code == 403
        assert (await wc.get(
            f"/api/v1/documents/{a_published_policy}/signing-status")).status_code == 403

        # ★ The admissions, without which "nobody can see it" would pass.
        assert (await hse.get(
            f"/api/v1/documents/{a_published_policy}/acknowledgements")).status_code == 200
        assert (await hse.get(
            f"/api/v1/documents/{a_published_policy}/signing-status")).status_code == 200

    async def test_a_supervisor_can_see_who_has_signed(
            self, hse_manager, a_published_policy, test_engine, db_session):
        """★ Round 5, box 5: "give supervisors the whole roster as they may have
        staff fro other departments with them."

        Round 5 narrowed this to whoever manages documents, which took it away
        from the supervisors who do the chasing. It is `ehs.worker.read` now —
        the permission for facts about people, held by HSE, the coordinator and
        area supervisors, and by nobody else.

        The whole roster, not their own department: HSE's reason is that a
        supervisor runs crews from more than one.
        """
        from tests.conftest import authed_client, make_token, make_user

        sup = await make_user(test_engine, role="area_supervisor",
                              full_name="Crew Supervisor")
        async with authed_client(make_token(sup.id, sup.role), db_session) as sc:
            roster = await sc.get(
                f"/api/v1/documents/{a_published_policy}/acknowledgements")
            assert roster.status_code == 200, roster.text
            status_ = await sc.get(
                f"/api/v1/documents/{a_published_policy}/signing-status")
            assert status_.status_code == 200, status_.text
            assert "outstanding" in status_.json() or "audience" in status_.json()

            # And it is still not a licence to publish or revise.
            assert (await sc.post(
                f"/api/v1/documents/{a_published_policy}/retire", json={})
            ).status_code == 403

    async def test_a_worker_can_still_read_and_sign_the_policy_itself(
            self, worker, a_published_policy):
        """★ The point of the whole feature, and what the narrowing must not break.

        A policy nobody can open is a policy nobody can acknowledge, and the
        signature is the compliance record.
        """
        _, wc = worker
        assert (await wc.get(f"/api/v1/documents/{a_published_policy}")).status_code == 200

        signed = await wc.post(
            f"/api/v1/documents/{a_published_policy}/acknowledge", json={"signature": None})
        assert signed.status_code in (200, 201), signed.text


class TestSubmittingSomebodyElsesDraft:
    """★ Checked nothing at all.

    Filing a statutory record in somebody else's name, from the one state where
    they have not finished deciding what it says.
    """

    async def test_a_worker_cannot_submit_a_draft_that_is_not_theirs(
            self, hse_manager, worker, db_session):
        _, hse = hse_manager
        _, wc = worker
        incident = await _an_incident_by_somebody_else(hse, db_session)

        refused = await wc.post(f"/api/v1/incidents/{incident.id}/submit")
        assert refused.status_code == 403, refused.text

    async def test_a_worker_submits_their_own(self, worker):
        _, wc = worker
        mine = await wc.post("/api/v1/incidents", json={
            "form_kind": "near_miss", "title": "Mine to file",
            "occurred_at": (datetime.now(timezone.utc) - timedelta(hours=3)).isoformat(),
            "description": "Pallet stacked too high in the aisle",
        })
        assert mine.status_code in (200, 201), mine.text
        submitted = await wc.post(f"/api/v1/incidents/{mine.json()['id']}/submit")
        assert submitted.status_code == 200, submitted.text
