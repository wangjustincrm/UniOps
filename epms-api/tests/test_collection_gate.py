"""ISS-0146 ②: Admin → Collection Config "Collection Confirmation Required".

Off (default): a physical PO is ready for a PA as soon as a goods receipt is
recorded (unchanged). On: not until goods on it are collected — create_pa is
raised at collection instead of at GR creation. Service POs are not affected.
The switch is collection_config.collection_gate_enabled; the older
collection_required key (stored true for years, never read) must not turn it on.
"""
import pytest
from sqlalchemy.orm.attributes import flag_modified

import app.db.session as sm
from app.crud import gr as gr_crud
from app.crud import po as po_crud
from app.crud.config import get_or_create as get_config
from app.schemas.gr import GrActionRequest
from tests.test_gr_create_raises_create_pa import (
    _create_gr, _match_invoice_no_gr, _open_tasks, _setup_po,
)

pytestmark = pytest.mark.asyncio


@pytest.fixture
async def gate():
    """Set the switch for one test, restore the shared config row after."""
    async with sm.AsyncSessionLocal() as db:
        cfg = await get_config(db)
        before = dict(cfg.collection_config or {})
        await db.commit()

    async def _set(**values):
        async with sm.AsyncSessionLocal() as db:
            cfg = await get_config(db)
            cfg.collection_config = {**before, **values}
            flag_modified(cfg, "collection_config")
            await db.commit()
    yield _set
    await _set()


async def test_off_by_default_even_with_the_legacy_key_true(gate):
    await gate(collection_required=True)     # the never-read legacy key
    async with sm.AsyncSessionLocal() as db:
        po, line, v, req = await _setup_po(db, po_type=3, po_status="issued")
        await _match_invoice_no_gr(db, po, line, v, req)
        await _create_gr(db, po, line, req)          # pending_ack, nothing collected
        await db.flush()
        assert await po_crud.po_ready_for_pa(db, po) is True
        assert len(await _open_tasks(db, po.id, "create_pa")) == 1
        await db.rollback()


async def test_on_waits_for_collection_then_raises_create_pa(gate):
    await gate(collection_gate_enabled=True)
    async with sm.AsyncSessionLocal() as db:
        po, line, v, req = await _setup_po(db, po_type=3, po_status="issued")
        await _match_invoice_no_gr(db, po, line, v, req)
        gr = await _create_gr(db, po, line, req)
        await db.flush()
        assert await po_crud.po_has_three_way_matched_invoice(db, po.id) is True
        assert await po_crud.po_ready_for_pa(db, po) is False
        assert await _open_tasks(db, po.id, "create_pa") == []
        # Received, so no "confirm the receipt" nudge either.
        assert await _open_tasks(db, po.id, "confirm_receipt") == []

        await gr_crud.action(db, gr, GrActionRequest(action="acknowledge"), actor_id=req.id)
        assert await _open_tasks(db, po.id, "create_pa") == []
        await gr_crud.action(db, gr, GrActionRequest(action="collect"), actor_id=req.id)
        assert await po_crud.po_ready_for_pa(db, po) is True
        tasks = await _open_tasks(db, po.id, "create_pa")
        assert len(tasks) == 1 and tasks[0].assigned_user_id == req.id
        await db.rollback()


async def test_on_does_not_hold_back_service_pos(gate):
    await gate(collection_gate_enabled=True)
    async with sm.AsyncSessionLocal() as db:
        po, line, v, req = await _setup_po(db, po_type=4, po_status="issued")
        await _match_invoice_no_gr(db, po, line, v, req)
        await _create_gr(db, po, line, req)
        await db.flush()
        assert await po_crud.po_ready_for_pa(db, po) is True
        assert len(await _open_tasks(db, po.id, "create_pa")) == 1
        await db.rollback()
