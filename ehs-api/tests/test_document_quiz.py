"""A quiz on a document: signing that you have read it waits for a pass.

Round 6, answer box 1: "add a quiz to a document".
"""
import uuid

from app.models.document import SafetyDocument

SIG = {"signature": "data:image/png;base64,iVBORw0KGgo="}


async def _published(client, db, *, title="Working at Heights Policy", review_months=None) -> str:
    made = await client.post("/api/v1/documents", json={
        "title": title, "doc_type": "policy", "requires_ack": True,
        **({"review_months": review_months} if review_months else {})})
    assert made.status_code in (200, 201), made.text
    doc_id = made.json()["id"]
    # file-api is not running here; the join is written straight onto the row,
    # as the document tests do.
    row = await db.get(SafetyDocument, uuid.UUID(doc_id))
    row.file_id = uuid.uuid4()
    await db.commit()
    assert (await client.post(f"/api/v1/documents/{doc_id}/publish", json={})).status_code == 200
    return doc_id


async def _quiz_on(client, doc_id, *, max_attempts=None) -> tuple[str, str]:
    made = await client.post("/api/v1/quizzes", json={
        "title": "Heights policy check", "document_id": doc_id, "pass_mark_percent": 100,
        "max_attempts": max_attempts})
    assert made.status_code in (200, 201), made.text
    qid = made.json()["id"]
    put = await client.put(f"/api/v1/quizzes/{qid}/questions", json={"questions": [{
        "prompt": "Is a harness required above 3 m?", "kind": "true_false",
        "options": [{"key": "t", "label": "True"}, {"key": "f", "label": "False"}],
        "correct": ["t"]}]})
    assert put.status_code == 200, put.text
    question_id = put.json()[0]["id"]
    return qid, question_id


async def test_signing_waits_for_a_pass(hse_manager, worker, db_session):
    _, hse = hse_manager
    _, me = worker
    doc_id = await _published(hse, db_session)
    qid, question = await _quiz_on(hse, doc_id)

    page = (await me.get(f"/api/v1/documents/{doc_id}")).json()
    assert page["quiz_id"] == qid and page["quiz_passed"] is False

    refused = await me.post(f"/api/v1/documents/{doc_id}/acknowledge", json=SIG)
    assert refused.status_code == 409 and "Pass the quiz" in refused.text

    failed = await me.post(f"/api/v1/quizzes/{qid}/attempts", json={"answers": {question: ["f"]}})
    assert failed.status_code == 201 and not failed.json()["passed"]
    assert (await me.post(f"/api/v1/documents/{doc_id}/acknowledge", json=SIG)).status_code == 409

    passed = await me.post(f"/api/v1/quizzes/{qid}/attempts", json={"answers": {question: ["t"]}})
    assert passed.json()["passed"]
    assert (await me.get(f"/api/v1/documents/{doc_id}")).json()["quiz_passed"] is True
    signed = await me.post(f"/api/v1/documents/{doc_id}/acknowledge", json=SIG)
    assert signed.status_code in (200, 201), signed.text


async def test_a_document_without_a_quiz_signs_as_before(hse_manager, worker, db_session):
    """The admission beside the refusal."""
    _, hse = hse_manager
    _, me = worker
    doc_id = await _published(hse, db_session, title="Lockout policy")
    assert (await me.post(f"/api/v1/documents/{doc_id}/acknowledge", json=SIG)).status_code in (200, 201)


async def test_the_quiz_moves_to_the_new_version(hse_manager, db_session):
    """Left on the retired version, the new one would be signable with no quiz."""
    _, hse = hse_manager
    doc_id = await _published(hse, db_session, title="Confined space policy")
    qid, _ = await _quiz_on(hse, doc_id)

    v2 = await hse.post(f"/api/v1/documents/{doc_id}/new-version", json={"version": "2"})
    assert v2.status_code in (200, 201), v2.text
    v2_id = v2.json()["id"]
    row = await db_session.get(SafetyDocument, uuid.UUID(v2_id))
    row.file_id = uuid.uuid4()
    await db_session.commit()
    assert (await hse.post(f"/api/v1/documents/{v2_id}/publish", json={})).status_code == 200

    assert (await hse.get(f"/api/v1/documents/{v2_id}")).json()["quiz_id"] == qid
    assert (await hse.get(f"/api/v1/documents/{doc_id}")).json()["quiz_id"] is None


async def test_a_quiz_tests_one_thing(hse_manager, db_session):
    _, hse = hse_manager
    doc_id = await _published(hse, db_session, title="Hot work policy")
    course = (await hse.post("/api/v1/training/courses", json={
        "code": f"C-{uuid.uuid4().hex[:5]}", "name": "Hot work"})).json()
    both = await hse.post("/api/v1/quizzes", json={
        "title": "Both", "course_id": course["id"], "document_id": doc_id})
    assert both.status_code == 422


async def test_a_quiz_with_no_questions_holds_nothing(hse_manager, worker, db_session):
    """★ Round-6 review: made and not yet written, it held signing shut."""
    _, hse = hse_manager
    _, me = worker
    doc_id = await _published(hse, db_session, title="Ladder policy")
    made = await hse.post("/api/v1/quizzes", json={"title": "Not written yet", "document_id": doc_id})
    assert made.status_code in (200, 201), made.text
    assert (await me.get(f"/api/v1/documents/{doc_id}")).json()["quiz_id"] is None
    assert (await me.post(f"/api/v1/documents/{doc_id}/acknowledge", json=SIG)).status_code in (200, 201)


async def test_retiring_the_quiz_lets_the_document_be_signed(hse_manager, worker, db_session):
    _, hse = hse_manager
    _, me = worker
    doc_id = await _published(hse, db_session, title="Forklift policy")
    qid, _ = await _quiz_on(hse, doc_id)
    assert (await me.post(f"/api/v1/documents/{doc_id}/acknowledge", json=SIG)).status_code == 409

    assert (await me.post(f"/api/v1/quizzes/{qid}/retire")).status_code == 403
    retired = await hse.post(f"/api/v1/quizzes/{qid}/retire")
    assert retired.status_code == 200 and retired.json()["is_active"] is False
    assert (await me.post(f"/api/v1/documents/{doc_id}/acknowledge", json=SIG)).status_code in (200, 201)


async def test_the_code_by_the_door_sends_them_to_the_quiz(hse_manager, worker, db_session):
    """★ Round-6 review: it offered "I have read this", which was then refused."""
    _, hse = hse_manager
    _, me = worker
    doc_id = await _published(hse, db_session, title="PPE policy")
    qid, question = await _quiz_on(hse, doc_id)
    token = (await hse.get(f"/api/v1/documents/{doc_id}")).json()["qr_token"]

    before = (await me.get(f"/api/v1/qr/{token}")).json()
    assert before["quiz_id"] == qid and before["quiz_passed"] is False
    await me.post(f"/api/v1/quizzes/{qid}/attempts", json={"answers": {question: ["t"]}})
    assert (await me.get(f"/api/v1/qr/{token}")).json()["quiz_passed"] is True


async def test_a_document_with_no_quiz_says_so_on_its_code(hse_manager, worker, db_session):
    _, hse = hse_manager
    _, me = worker
    doc_id = await _published(hse, db_session, title="Visitor policy")
    token = (await hse.get(f"/api/v1/documents/{doc_id}")).json()["qr_token"]
    got = (await me.get(f"/api/v1/qr/{token}")).json()
    assert got["quiz_id"] is None and "acknowledge" in got["actions"]


async def test_a_new_version_asks_for_the_quiz_again(hse_manager, worker, db_session):
    """★ Round 7, question 2: "a revised document requires a quiz to be
    answered again. a previous pass on a quiz does not automatically pass a
    revised document." """
    _, hse = hse_manager
    _, me = worker
    doc_id = await _published(hse, db_session, title="Respirator policy")
    qid, question = await _quiz_on(hse, doc_id)
    assert (await me.post(f"/api/v1/quizzes/{qid}/attempts",
                          json={"answers": {question: ["t"]}})).json()["passed"]
    assert (await me.get(f"/api/v1/documents/{doc_id}")).json()["quiz_passed"] is True

    v2 = await hse.post(f"/api/v1/documents/{doc_id}/new-version", json={"version": "2"})
    v2_id = v2.json()["id"]
    row = await db_session.get(SafetyDocument, uuid.UUID(v2_id))
    row.file_id = uuid.uuid4()
    await db_session.commit()
    assert (await hse.post(f"/api/v1/documents/{v2_id}/publish", json={})).status_code == 200

    page = (await me.get(f"/api/v1/documents/{v2_id}")).json()
    assert page["quiz_id"] == qid and page["quiz_passed"] is False
    assert (await me.post(f"/api/v1/documents/{v2_id}/acknowledge", json=SIG)).status_code == 409

    await me.post(f"/api/v1/quizzes/{qid}/attempts", json={"answers": {question: ["t"]}})
    assert (await me.get(f"/api/v1/documents/{v2_id}")).json()["quiz_passed"] is True
    assert (await me.post(f"/api/v1/documents/{v2_id}/acknowledge", json=SIG)).status_code in (200, 201)


async def test_attempts_are_counted_per_version(hse_manager, worker, db_session):
    """★ Raised in review by uniops-55: with a limit, somebody who used their
    attempts passing v1 could never pass — and so never sign — v2."""
    _, hse = hse_manager
    _, me = worker
    doc_id = await _published(hse, db_session, title="Lifting policy")
    qid, question = await _quiz_on(hse, doc_id, max_attempts=2)
    await me.post(f"/api/v1/quizzes/{qid}/attempts", json={"answers": {question: ["f"]}})
    assert (await me.post(f"/api/v1/quizzes/{qid}/attempts",
                          json={"answers": {question: ["t"]}})).json()["passed"]
    # On v1 the limit holds.
    assert (await me.post(f"/api/v1/quizzes/{qid}/attempts",
                          json={"answers": {question: ["t"]}})).status_code == 409

    v2_id = (await hse.post(f"/api/v1/documents/{doc_id}/new-version", json={"version": "2"})).json()["id"]
    row = await db_session.get(SafetyDocument, uuid.UUID(v2_id))
    row.file_id = uuid.uuid4()
    await db_session.commit()
    assert (await hse.post(f"/api/v1/documents/{v2_id}/publish", json={})).status_code == 200

    again = await me.post(f"/api/v1/quizzes/{qid}/attempts", json={"answers": {question: ["t"]}})
    assert again.status_code == 201 and again.json()["passed"], again.text
    assert (await me.post(f"/api/v1/documents/{v2_id}/acknowledge", json=SIG)).status_code in (200, 201)


async def test_archiving_takes_a_document_out_of_force(hse_manager, worker, db_session):
    """★ Round 7, TC-11: "add an archive option on all documents." Archiving
    asks nobody to sign it any more and closes its review deadline — which
    retiring never did, for a replaced version either."""
    from sqlalchemy import select
    from app.models.statutory import StatutoryDeadline
    _, hse = hse_manager
    _, me = worker
    doc_id = await _published(hse, db_session, title="Old visitor policy", review_months=12)

    async def open_reviews(doc):
        return (await db_session.execute(select(StatutoryDeadline).where(
            StatutoryDeadline.source_type == "document",
            StatutoryDeadline.source_id == uuid.UUID(doc),
            StatutoryDeadline.satisfied_at.is_(None)))).scalars().all()

    assert await open_reviews(doc_id), "a published policy has a review date"
    assert (await me.post(f"/api/v1/documents/{doc_id}/retire")).status_code == 403
    archived = await hse.post(f"/api/v1/documents/{doc_id}/retire")
    assert archived.status_code == 200 and archived.json()["status"] == "retired"
    db_session.expire_all()
    assert not await open_reviews(doc_id)
    assert (await me.post(f"/api/v1/documents/{doc_id}/acknowledge", json=SIG)).status_code != 201


async def test_a_replaced_version_stops_being_reviewed(hse_manager, db_session):
    from sqlalchemy import select
    from app.models.statutory import StatutoryDeadline
    _, hse = hse_manager
    doc_id = await _published(hse, db_session, title="Noise policy", review_months=12)
    v2_id = (await hse.post(f"/api/v1/documents/{doc_id}/new-version", json={"version": "2"})).json()["id"]
    row = await db_session.get(SafetyDocument, uuid.UUID(v2_id))
    row.file_id = uuid.uuid4()
    await db_session.commit()
    assert (await hse.post(f"/api/v1/documents/{v2_id}/publish", json={})).status_code == 200
    db_session.expire_all()
    left = (await db_session.execute(select(StatutoryDeadline).where(
        StatutoryDeadline.source_type == "document",
        StatutoryDeadline.source_id == uuid.UUID(doc_id),
        StatutoryDeadline.satisfied_at.is_(None)))).scalars().all()
    assert not left, "v1 is not in force; it has nothing to review"
