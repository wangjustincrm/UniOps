"""Tests for GET /{doc}/{id}/workflow-steps proxy endpoints (PR / PO / PA).

These tests monkeypatch approval_client.get_workflow_steps at the module level
so they run without a live approval-api.
"""
import pytest

_STEPS = [{"id": "dept_manager", "role": "dept_manager", "label": "Department Manager"}]

_PR_URL = "/api/v1/pr/00000000-0000-0000-0000-000000000000/workflow-steps"
_PO_URL = "/api/v1/po/00000000-0000-0000-0000-000000000000/workflow-steps"
_PA_URL = "/api/v1/pa/00000000-0000-0000-0000-000000000000/workflow-steps"


# ── PR ────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_pr_workflow_steps_proxies(admin_client, monkeypatch):
    """Happy path: engine returns steps → endpoint forwards them unchanged."""
    async def fake(doc_type, doc_id, token):
        assert doc_type == "pr"
        return _STEPS

    monkeypatch.setattr("app.api.v1.pr.approval_client.get_workflow_steps", fake)
    resp = await admin_client.get(_PR_URL)
    assert resp.status_code == 200
    assert resp.json()[0]["id"] == "dept_manager"


@pytest.mark.asyncio
async def test_pr_workflow_steps_404(admin_client, monkeypatch):
    """Engine 404 (LookupError) → endpoint returns 404."""
    async def fake(doc_type, doc_id, token):
        raise LookupError("PR not found in engine")

    monkeypatch.setattr("app.api.v1.pr.approval_client.get_workflow_steps", fake)
    resp = await admin_client.get(_PR_URL)
    assert resp.status_code == 404
    assert "not found" in resp.json()["detail"].lower()


@pytest.mark.asyncio
async def test_pr_workflow_steps_502(admin_client, monkeypatch):
    """Engine unreachable (RuntimeError) → endpoint returns 502."""
    async def fake(doc_type, doc_id, token):
        raise RuntimeError("Approval Engine unreachable")

    monkeypatch.setattr("app.api.v1.pr.approval_client.get_workflow_steps", fake)
    resp = await admin_client.get(_PR_URL)
    assert resp.status_code == 502


# ── PO ────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_po_workflow_steps_proxies(admin_client, monkeypatch):
    """Happy path: PO endpoint forwards doc_type='po' to engine."""
    async def fake(doc_type, doc_id, token):
        assert doc_type == "po"
        return _STEPS

    monkeypatch.setattr("app.api.v1.po.approval_client.get_workflow_steps", fake)
    resp = await admin_client.get(_PO_URL)
    assert resp.status_code == 200
    assert resp.json()[0]["id"] == "dept_manager"


# ── PA ────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_pa_workflow_steps_proxies(admin_client, monkeypatch):
    """Happy path: PA endpoint forwards doc_type='pa' to engine."""
    async def fake(doc_type, doc_id, token):
        assert doc_type == "pa"
        return _STEPS

    monkeypatch.setattr("app.api.v1.pa.approval_client.get_workflow_steps", fake)
    resp = await admin_client.get(_PA_URL)
    assert resp.status_code == 200
    assert resp.json()[0]["id"] == "dept_manager"


# ── Module import smoke test ──────────────────────────────────────────────────


def test_pr_po_pa_modules_import():
    """PR / PO / PA router modules import cleanly after the patch."""
    import app.api.v1.pr  # noqa: F401
    import app.api.v1.po  # noqa: F401
    import app.api.v1.pa  # noqa: F401
