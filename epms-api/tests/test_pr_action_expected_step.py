"""The PR action forwards the step the approver's decision is for (ISS-0079).

approval-api refuses an approve/return/reject whose expected_step the PR has
already left — that is what stops the second click of a double-click from
approving the next step. It only works if epms-api passes the value on.
"""
import pytest

from tests.test_pr_type_field_gates import URL, _submittable


@pytest.fixture
def delegations(monkeypatch):
    calls: list[dict] = []

    async def _fake(doc_type, doc_id, action, comment, token, expected_step=None):  # noqa: ANN001
        calls.append({"action": action, "expected_step": expected_step})

    import app.api.v1.pr as pr_api
    monkeypatch.setattr(pr_api, "delegate_action", _fake)
    return calls


@pytest.mark.asyncio
async def test_approve_forwards_expected_step(admin_client, test_engine, delegations):
    pr = await _submittable(admin_client, test_engine, 2)

    r = await admin_client.post(f"{URL}/{pr['id']}/action",
                                json={"action": "approve", "expected_step": 1})

    assert r.status_code == 200, r.text
    assert delegations == [{"action": "approve", "expected_step": 1}]


@pytest.mark.asyncio
async def test_action_without_expected_step_still_works(admin_client, test_engine, delegations):
    pr = await _submittable(admin_client, test_engine, 2)

    r = await admin_client.post(f"{URL}/{pr['id']}/action", json={"action": "approve"})

    assert r.status_code == 200, r.text
    assert delegations == [{"action": "approve", "expected_step": None}]
