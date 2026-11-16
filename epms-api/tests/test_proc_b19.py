"""Tracker round 3, batch 19 (goods receipts)."""
import pytest

from tests.test_gr import GR_URL, _gr_payload, _make_issued_po
from tests.test_gr_over_receipt import _line, _po_line_id

pytestmark = pytest.mark.asyncio


@pytest.fixture(autouse=True)
def _quiet_notification_email(monkeypatch):
    async def _noop(to, subject, html, **kwargs):
        return None

    monkeypatch.setattr("app.services.email.send_email", _noop)


# ── ISS-0179: a service GR's confirmation task is not an Acknowledge task ────

async def test_assistant_groups_a_service_gr_task_as_the_inbox_does():
    """The owner's task on a service GR is raised as acknowledge_gr titled
    "Confirm Service Completion: GR-…" (crud/gr.py _create_ack_task); the
    Task Inbox lists it under Confirm Service Receipt (taskTypes.ts
    taskGroupType), so the assistant's counts must too."""
    from app.services import guide_view

    def task(type_, title):
        return {"type": type_, "title": title, "document_type": "gr", "priority": "normal",
                "document_number": "GR-1", "assigned_to_me_personally": True}

    s = guide_view.task_summary([
        task("acknowledge_gr", "Confirm Service Completion: GR-20261105-0019 — Cleaning"),
        task("acknowledge_gr", "Acknowledge GR: GR-20261105-0020 — Tape"),
    ])
    assert {(g["type"], g["label"], g["count"]) for g in s["groups"]} == {
        ("confirm_service_gr", "Confirm Service Receipt", 1),
        ("acknowledge_gr", "Acknowledge Goods Receipt", 1)}


async def test_the_service_gr_task_title_is_the_one_the_inbox_looks_for():
    import inspect
    from app.crud import gr as gr_crud

    src = inspect.getsource(gr_crud._create_ack_task)
    assert 'f"Confirm Service Completion: {gr.number}' in src
