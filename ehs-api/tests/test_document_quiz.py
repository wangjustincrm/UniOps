"""A quiz on a document: signing that you have read it waits for a pass.

Round 6, answer box 1: "add a quiz to a document".
"""
import uuid

from app.models.document import SafetyDocument

SIG = {"signature": "data:image/png;base64,iVBORw0KGgo="}


async def _published(client, db, *, title="Working at Heights Policy") -> str:
    made = await client.post("/api/v1/documents", json={
        "title": title, "doc_type": "policy", "requires_ack": True})
    assert made.status_code in (200, 201), made.text
    doc_id = made.json()["id"]
    # file-api is not running here; the join is written straight onto the row,
    # as the document tests do.
    row = await db.get(SafetyDocument, uuid.UUID(doc_id))
    row.file_id = uuid.uuid4()
    await db.commit()
    assert (await client.post(f"/api/v1/documents/{doc_id}/publish", json={})).status_code == 200
    return doc_id


async def _quiz_on(client, doc_id) -> tuple[str, str]:
    made = await client.post("/api/v1/quizzes", json={
        "title": "Heights policy check", "document_id": doc_id, "pass_mark_percent": 100})
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
