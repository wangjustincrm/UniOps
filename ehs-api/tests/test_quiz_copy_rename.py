"""UAT 14: "Create a way to copy quizzes and rename them." """
from tests.conftest import make_user
from tests.test_quiz_roster import _answer, _course, _quiz


async def test_rename_changes_the_title_only(hse_manager, worker, db_session):
    _, hse = hse_manager
    _, emp = worker
    quiz = await _quiz(db_session, None)
    url = f"/api/v1/quizzes/{quiz.id}"
    assert (await emp.patch(url, json={"title": "Mine"})).status_code == 403
    assert (await hse.patch(url, json={"title": "   "})).status_code == 422
    got = await hse.patch(url, json={"title": "  LOTO — end of session  "})
    assert got.status_code == 200, got.text
    assert got.json()["title"] == "LOTO — end of session"
    assert got.json()["pass_mark_percent"] == 100 and got.json()["question_count"] == 1


async def test_a_copy_has_the_questions_and_answers_and_none_of_the_attempts(
        hse_manager, test_engine, db_session):
    """Copied with its answer key, for another course; the original — already
    answered, so its questions are frozen — is untouched, and the copy's can be
    changed."""
    _, hse = hse_manager
    quiz = await _quiz(db_session, await _course(db_session))
    quiz.intro = "Ten minutes, on your phone."
    someone = await make_user(test_engine)
    await _answer(db_session, quiz, someone, right=True, delivery=None)
    other = await _course(db_session)

    made = await hse.post(f"/api/v1/quizzes/{quiz.id}/copy", json={
        "title": "LOTO check — contractors", "course_id": str(other.id), "pass_mark_percent": 80})
    assert made.status_code == 201, made.text
    copy = made.json()
    assert copy["id"] != str(quiz.id)
    assert (copy["title"], copy["course_id"], copy["pass_mark_percent"], copy["question_count"],
            copy["intro"]) == ("LOTO check — contractors", str(other.id), 80, 1,
                               "Ten minutes, on your phone.")
    assert copy["qr_token"] and copy["qr_token"] != quiz.qr_token

    key = (await hse.get(f"/api/v1/quizzes/{copy['id']}/answer-key")).json()
    assert [(q["prompt"], q["correct"]) for q in key] == [("Lock before work?", ["t"])]
    assert (await hse.get(f"/api/v1/quizzes/{copy['id']}/attempts")).json() == []

    # The copy's questions can change; the original's still cannot.
    new_q = {"questions": [{"prompt": "Try the start button?", "kind": "true_false",
                            "options": [{"key": "t", "label": "True"}, {"key": "f", "label": "False"}],
                            "correct": ["t"]}]}
    assert (await hse.put(f"/api/v1/quizzes/{copy['id']}/questions", json=new_q)).status_code == 200
    frozen = await hse.put(f"/api/v1/quizzes/{quiz.id}/questions", json=new_q)
    assert frozen.status_code == 409 and "copy it" in frozen.json()["detail"]
    original = (await hse.get(f"/api/v1/quizzes/{quiz.id}/answer-key")).json()
    assert [q["prompt"] for q in original] == ["Lock before work?"]


async def test_copying_needs_the_training_permission(worker, db_session):
    _, emp = worker
    quiz = await _quiz(db_session, None)
    got = await emp.post(f"/api/v1/quizzes/{quiz.id}/copy", json={"title": "Mine"})
    assert got.status_code == 403
