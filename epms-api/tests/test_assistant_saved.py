"""Marked questions: per person, capped at ten, never someone else's."""
import pytest

pytestmark = pytest.mark.asyncio

URL = "/api/v1/assistant/saved"


async def test_mark_list_and_unmark(admin_client):
    r = await admin_client.post(URL, json={"question": "  Type 1 POs not fully received  "})
    assert r.status_code == 200, r.text
    items = r.json()["items"]
    assert [i["question"] for i in items] == ["Type 1 POs not fully received"]
    assert r.json()["limit"] == 10

    # Marking it again is a no-op, not a second copy.
    r = await admin_client.post(URL, json={"question": "Type 1 POs not fully received"})
    assert len(r.json()["items"]) == 1

    r = await admin_client.delete(f"{URL}/{items[0]['id']}")
    assert r.status_code == 200
    assert r.json()["items"] == []


async def test_the_eleventh_is_refused_not_rotated_in(admin_client):
    for i in range(10):
        r = await admin_client.post(URL, json={"question": f"report {i}"})
        assert r.status_code == 200, r.text
    r = await admin_client.post(URL, json={"question": "report 10"})
    assert r.status_code == 409
    assert "10" in r.json()["detail"]
    # Nothing that was kept got pushed out to make room.
    kept = {i["question"] for i in (await admin_client.get(URL)).json()["items"]}
    assert kept == {f"report {i}" for i in range(10)}


async def test_lists_are_per_person(admin_client, requester_client):
    r = await admin_client.post(URL, json={"question": "admin's report"})
    admin_id = next(i["id"] for i in r.json()["items"] if i["question"] == "admin's report")
    r = await requester_client.post(URL, json={"question": "requester's report"})
    assert r.status_code == 200

    # Each sees their own, and only their own.
    mine = [i["question"] for i in (await requester_client.get(URL)).json()["items"]]
    assert mine == ["requester's report"]
    theirs = [i["question"] for i in (await admin_client.get(URL)).json()["items"]]
    assert "admin's report" in theirs and "requester's report" not in theirs

    # Deleting by someone else's id removes nothing.
    await requester_client.delete(f"{URL}/{admin_id}")
    theirs = [i["question"] for i in (await admin_client.get(URL)).json()["items"]]
    assert "admin's report" in theirs


async def test_history_carries_the_query_that_ran():
    from app.api.v1.assistant_chat import ChatTurn, _turn_content
    t = ChatTurn(role="assistant", text="44 orders",
                 query={"entity": "po_line", "where": [{"field": "outstanding_qty"}]})
    out = _turn_content(t)
    assert out.startswith("44 orders") and "outstanding_qty" in out
    # A user turn, or an answer that ran no query, is passed through untouched.
    assert _turn_content(ChatTurn(role="user", text="hi")) == "hi"
    assert _turn_content(ChatTurn(role="assistant", text="ok")) == "ok"
