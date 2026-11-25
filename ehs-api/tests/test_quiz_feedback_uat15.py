"""UAT 15: course feedback questions on a quiz — a 1–4 star rating, Excellent …
N/A with no right answer, and a comment box. Never marked."""
from tests.conftest import authed_client, make_token, make_user
from tests.test_quiz_roster import _quiz

TF = [{"key": "t", "label": "True"}, {"key": "f", "label": "False"}]


async def _with_feedback(hse, quiz):
    got = await hse.put(f"/api/v1/quizzes/{quiz.id}/questions", json={"questions": [
        {"prompt": "Lock before work?", "kind": "true_false", "options": TF, "correct": ["t"]},
        {"prompt": "How would you rate the trainer?", "kind": "rating"},
        {"prompt": "The course material was…", "kind": "likert",
         "options": [{"key": "x", "label": "ignored"}], "correct": ["x"]},
        {"prompt": "What did you like most about the training?", "kind": "comment"},
    ]})
    assert got.status_code == 200, got.text
    return {q["kind"]: q for q in got.json()}


async def test_feedback_questions_have_fixed_choices_and_no_answer_key(hse_manager, db_session):
    _, hse = hse_manager
    quiz = await _quiz(db_session, None)
    qs = await _with_feedback(hse, quiz)
    assert [o["label"] for o in qs["rating"]["options"]] == ["Below Average", "Average", "Good", "Excellent"]
    assert [o["label"] for o in qs["likert"]["options"]] == ["Excellent", "Good", "Average", "Poor", "N/A"]
    assert qs["comment"]["options"] == []
    assert all(qs[k]["correct"] == [] and qs[k]["points"] == 0 for k in ("rating", "likert", "comment"))
    # A marked question still needs options and a right answer.
    bad = await hse.put(f"/api/v1/quizzes/{quiz.id}/questions", json={"questions": [
        {"prompt": "x", "kind": "single", "options": TF[:1], "correct": ["t"]}]})
    assert bad.status_code == 422


async def test_feedback_is_not_marked_and_is_summed_up(hse_manager, test_engine, db_session):
    _, hse = hse_manager
    quiz = await _quiz(db_session, None)
    qs = await _with_feedback(hse, quiz)
    tf = qs["true_false"]["id"]
    people = [await make_user(test_engine) for _ in range(3)]
    sheets = [
        {tf: ["t"], qs["rating"]["id"]: ["4"], qs["likert"]["id"]: ["good"],
         qs["comment"]["id"]: ["The hands-on part"]},
        {tf: ["t"], qs["rating"]["id"]: ["2"], qs["likert"]["id"]: ["na"]},
        {tf: ["f"]},                                     # feedback skipped
    ]
    for person, answers in zip(people, sheets):
        async with authed_client(make_token(person.id, person.role), db_session) as c:
            got = await c.post(f"/api/v1/quizzes/{quiz.id}/attempts", json={"answers": answers})
            assert got.status_code == 201, got.text
            marked = got.json()
            # Only the true/false question is marked; the feedback is not in it.
            assert [m["question_id"] for m in marked["marked"]] == [tf]
            assert marked["points_possible"] == 1
            assert marked["passed"] is (answers[tf] == ["t"])

    res = (await hse.get(f"/api/v1/quizzes/{quiz.id}/results")).json()
    assert [q["question_id"] for q in res["by_question"]] == [tf]
    fb = {f["kind"]: f for f in res["feedback"]}
    assert fb["rating"]["average"] == 3.0 and fb["rating"]["answered"] == 2 and fb["rating"]["of"] == 3
    assert {c["label"]: c["count"] for c in fb["likert"]["counts"]} == {
        "Excellent": 0, "Good": 1, "Average": 0, "Poor": 0, "N/A": 1}
    assert fb["comment"]["comments"] == ["The hands-on part"]


async def test_a_feedback_answer_must_be_one_of_its_choices(hse_manager, test_engine, db_session):
    _, hse = hse_manager
    quiz = await _quiz(db_session, None)
    qs = await _with_feedback(hse, quiz)
    person = await make_user(test_engine)
    async with authed_client(make_token(person.id, person.role), db_session) as c:
        got = await c.post(f"/api/v1/quizzes/{quiz.id}/attempts", json={"answers": {
            qs["true_false"]["id"]: ["t"], qs["rating"]["id"]: ["5"]}})
        assert got.status_code == 422
        long = await c.post(f"/api/v1/quizzes/{quiz.id}/attempts", json={"answers": {
            qs["true_false"]["id"]: ["t"], qs["comment"]["id"]: ["x" * 2001]}})
        assert long.status_code == 422


async def test_a_feedback_only_quiz_says_nothing_was_marked(hse_manager, test_engine, db_session):
    """UAT 16 TC-09: "quizzes have to have a pass mark above 1 which can not
    happen on feedback" — the screens need to know there is no score to show."""
    _, hse = hse_manager
    quiz = await _quiz(db_session, None)
    assert (await hse.put(f"/api/v1/quizzes/{quiz.id}/questions", json={"questions": [
        {"prompt": "How was the course?", "kind": "rating"},
        {"prompt": "Comments", "kind": "comment"}]})).status_code == 200
    listed = {q["id"]: q for q in (await hse.get("/api/v1/quizzes")).json()}[str(quiz.id)]
    assert (listed["question_count"], listed["marked_count"]) == (2, 0)

    person = await make_user(test_engine)
    async with authed_client(make_token(person.id, person.role), db_session) as c:
        got = (await c.post(f"/api/v1/quizzes/{quiz.id}/attempts", json={"answers": {}})).json()
    assert got["passed"] is True and got["points_possible"] == 0
    rows = (await hse.get(f"/api/v1/quizzes/{quiz.id}/attempts")).json()
    assert [r["points_possible"] for r in rows] == [0]

    mixed = await _quiz(db_session, None)
    await _with_feedback(hse, mixed)
    listed = {q["id"]: q for q in (await hse.get("/api/v1/quizzes")).json()}[str(mixed.id)]
    assert (listed["question_count"], listed["marked_count"]) == (4, 1)
