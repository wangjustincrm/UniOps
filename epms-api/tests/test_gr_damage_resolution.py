"""The damage-report task stays open until Procurement records a return or
credit on the GR, or the GR is cancelled (ISS-0099) — not just until Collect."""
import uuid as _uuid

import pytest
from sqlalchemy import select as _select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from tests.test_gr import GR_URL, _make_issued_po
from tests.test_gr_over_receipt import _ack_and_collect, _line, _po_line_id, _post_gr

pytestmark = pytest.mark.asyncio


@pytest.fixture(autouse=True)
def _quiet_notification_email(monkeypatch):
    async def _noop(to, subject, html, **kwargs):
        return None

    monkeypatch.setattr("app.services.email.send_email", _noop)


async def _damage_tasks(engine, gr_id):
    from app.models.task import Task
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as db:
        return (await db.execute(_select(Task).where(
            Task.type == "gr_damage_report",
            Task.document_id == _uuid.UUID(gr_id)))).scalars().all()


async def _damaged_gr(client, engine, code):
    po, _ = await _make_issued_po(client, engine, code)
    pl = await _po_line_id(client, po["id"])
    r = await _post_gr(client, po["id"], [_line(
        pl, 2, condition="damaged", discrepancy_notes="Housing cracked")])
    assert r.status_code == 201, r.text
    return r.json()


async def test_damage_task_survives_collect_and_closes_on_resolution(admin_client, test_engine):
    gr = await _damaged_gr(admin_client, test_engine, "VND-DMG-01")
    r = await _ack_and_collect(admin_client, gr["id"])
    assert r.status_code == 200, r.text

    tasks = await _damage_tasks(test_engine, gr["id"])
    assert len(tasks) == 1 and tasks[0].is_completed is False

    url = f"{GR_URL}/{gr['id']}/action"
    r = await admin_client.post(url, json={"action": "resolve_damage", "damage_resolution": "credit"})
    assert r.status_code == 409, r.text          # reference required
    r = await admin_client.post(url, json={
        "action": "resolve_damage", "damage_resolution": "refund", "damage_resolution_ref": "CN-1"})
    assert r.status_code == 409, r.text          # return | credit only

    r = await admin_client.post(url, json={
        "action": "resolve_damage", "damage_resolution": "credit",
        "damage_resolution_ref": " CN-77 ", "damage_resolution_note": "Vendor credits 2 units"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["damage_resolution"] == "credit"
    assert body["damage_resolution_ref"] == "CN-77"
    assert body["damage_resolved_at"] is not None
    assert body["status"] == "collected"          # the receipt itself is untouched

    tasks = await _damage_tasks(test_engine, gr["id"])
    assert tasks[0].is_completed is True

    r = await admin_client.post(url, json={
        "action": "resolve_damage", "damage_resolution": "return", "damage_resolution_ref": "RMA-1"})
    assert r.status_code == 409, r.text          # already recorded


async def test_cancelling_the_gr_closes_the_damage_task(admin_client, test_engine):
    gr = await _damaged_gr(admin_client, test_engine, "VND-DMG-02")
    r = await admin_client.post(f"{GR_URL}/{gr['id']}/action", json={"action": "cancel"})
    assert r.status_code == 200, r.text
    tasks = await _damage_tasks(test_engine, gr["id"])
    assert tasks[0].is_completed is True


async def test_resolve_damage_is_procurements_job(admin_client, requester_client, test_engine):
    gr = await _damaged_gr(admin_client, test_engine, "VND-DMG-03")
    payload = {"action": "resolve_damage", "damage_resolution": "return", "damage_resolution_ref": "RMA-9"}

    r = await requester_client.post(f"{GR_URL}/{gr['id']}/action", json=payload)
    assert r.status_code == 403, r.text
    r = await admin_client.post(f"{GR_URL}/{gr['id']}/action", json=payload)
    assert r.status_code == 200, r.text


async def test_resolve_damage_refused_on_a_clean_gr(admin_client, test_engine):
    po, _ = await _make_issued_po(admin_client, test_engine, "VND-DMG-04")
    pl = await _po_line_id(admin_client, po["id"])
    gr = (await _post_gr(admin_client, po["id"], [_line(pl, 2)])).json()
    r = await admin_client.post(f"{GR_URL}/{gr['id']}/action", json={
        "action": "resolve_damage", "damage_resolution": "return", "damage_resolution_ref": "RMA-2"})
    assert r.status_code == 409, r.text


async def test_replacement_and_other_close_the_task(admin_client, test_engine):
    """Follow-up to ISS-0099: a replacement (reference optional) or "other"
    (e.g. minor damage, no claim — note required) also settles it."""
    gr = await _damaged_gr(admin_client, test_engine, "VND-DMG-05")
    url = f"{GR_URL}/{gr['id']}/action"
    r = await admin_client.post(url, json={"action": "resolve_damage", "damage_resolution": "replacement"})
    assert r.status_code == 200, r.text
    assert r.json()["damage_resolution"] == "replacement" and r.json()["damage_resolution_ref"] is None
    assert (await _damage_tasks(test_engine, gr["id"]))[0].is_completed is True

    gr = await _damaged_gr(admin_client, test_engine, "VND-DMG-06")
    url = f"{GR_URL}/{gr['id']}/action"
    r = await admin_client.post(url, json={"action": "resolve_damage", "damage_resolution": "other"})
    assert r.status_code == 409, r.text          # "other" needs a note
    r = await admin_client.post(url, json={
        "action": "resolve_damage", "damage_resolution": "other",
        "damage_resolution_note": "Scuffed box only; goods fine, no claim"})
    assert r.status_code == 200, r.text
    assert r.json()["damage_resolution_note"] == "Scuffed box only; goods fine, no claim"
    assert (await _damage_tasks(test_engine, gr["id"]))[0].is_completed is True
