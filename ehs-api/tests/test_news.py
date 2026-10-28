"""★ Round 10 (open box): HSE posts news — date, time, importance, message —
and everybody sees it on My Actions."""


async def test_hse_posts_everyone_reads_and_hse_takes_it_down(hse_manager, worker):
    user, hse = hse_manager
    _, someone = worker
    made = await hse.post("/api/v1/news", json={
        "importance": "high", "message": "  Forklift lane 3 closed for repair today  "})
    assert made.status_code == 201, made.text
    item = made.json()
    assert item["message"] == "Forklift lane 3 closed for repair today"
    assert item["posted_by_name"] == user.full_name and item["posted_at"]

    seen = (await someone.get("/api/v1/news")).json()
    assert item["id"] in {n["id"] for n in seen}
    # Only HSE posts and takes down.
    assert (await someone.post("/api/v1/news", json={"importance": "low",
                                                    "message": "x"})).status_code == 403
    assert (await someone.post(f"/api/v1/news/{item['id']}/archive")).status_code == 403
    assert (await hse.post(f"/api/v1/news/{item['id']}/archive")).status_code == 204
    assert item["id"] not in {n["id"] for n in (await someone.get("/api/v1/news")).json()}


async def test_importance_is_one_of_three_and_a_message_is_needed(hse_manager):
    _, hse = hse_manager
    assert (await hse.post("/api/v1/news", json={"importance": "urgent",
                                                "message": "x"})).status_code == 422
    assert (await hse.post("/api/v1/news", json={"importance": "low",
                                                "message": "   "})).status_code == 422
