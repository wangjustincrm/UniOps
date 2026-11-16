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


# ── ISS-0185: line conditions at collection go into the collection notes ────

async def test_collect_keeps_the_line_conditions_in_the_notes_and_raises_no_task(admin_client, test_engine):
    """GR-C-002: Collect Goods sends the lines' conditions inside
    collection_notes (epms lib/grNotes collectionNotesWithLines); they are
    kept as written, the lines stay as recorded, and no task is raised."""
    import uuid as _uuid
    from sqlalchemy import select as _select
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
    from app.models.task import Task

    po, _ = await _make_issued_po(admin_client, test_engine, "VND-B19-02")
    pl = await _po_line_id(admin_client, po["id"])
    r = await admin_client.post(GR_URL, json=_gr_payload(po["id"], line_items=[_line(pl, 2)]))
    assert r.status_code == 201, r.text
    gr_id = r.json()["id"]
    r = await admin_client.post(f"{GR_URL}/{gr_id}/action", json={"action": "acknowledge"})
    assert r.status_code == 200, r.text

    notes = ("Picked up at dock 3 · Line conditions at collection: "
             "Hydraulic Pump: Discrepancy — 包装破损（测试）")
    r = await admin_client.post(f"{GR_URL}/{gr_id}/action",
                                json={"action": "collect", "collection_notes": notes})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status"] == "collected"
    assert body["collection_notes"] == notes
    assert body["line_items"][0]["condition"] == "good"

    async with async_sessionmaker(test_engine, class_=AsyncSession)() as db:
        open_tasks = (await db.execute(_select(Task).where(
            Task.document_id == _uuid.UUID(gr_id),
            Task.is_completed.is_(False)))).scalars().all()
    assert open_tasks == []
