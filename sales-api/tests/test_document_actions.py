"""The file register's actions (FR-OB-24 / 54 / 62): read again, ignore, send again."""
import hashlib
import json
from types import SimpleNamespace

import pytest

from app.models.documents import Document
from app.platform.actors import SYSTEM_ACTOR
from app.platform.errors import BusinessError
from app.services.edi_intake import EdiIntake
from app.services.operations import DocumentActions
from tests.test_intake import PO_CONTROL_CHARS
from tests.test_orders import OP, env, po  # noqa: F401 — env is a fixture
from tests.test_outbound import edi  # noqa: F401 — a fixture


async def _stored(env, data: bytes, kind: str, status: str) -> int:
    async with env.factory(SYSTEM_ACTOR) as u:
        d = Document(direction="IN", origin="PARTNER", system="TRUECOMMERCE", kind=kind, remote_name="po.json", size=len(data),
                     sha256=hashlib.sha256(data).hexdigest(), content=data, processing_status=status, error="not valid JSON")
        u.session.add(d)
        await u.session.flush()
        await u.commit()
        return d.id


async def test_a_file_once_unreadable_is_read_again_and_makes_its_order(env):  # noqa: F811
    body = json.dumps({"PurchaseOrder": [po("LBL", "PO-R", [{"Line": 1, "BuyerPart": "L-555", "Quantity": 1, "UOM": "CA"}])]}).encode()
    did = await _stored(env, body, "OTHER", "Pending")   # as an older reader left it
    async with env.factory(OP) as u:
        doc = await DocumentActions(u).read_again(did, env.tasks)
        await u.commit()
    assert (doc.kind, doc.processing_status, doc.error) == ("850_JSON", "Pending", None)
    r = await EdiIntake(uow_factory=env.factory, tasks=env.tasks).run_pass()
    assert r["orders"] == 1


async def test_a_failed_file_is_ignored_with_a_reason_and_only_then(env):  # noqa: F811
    did = await _stored(env, PO_CONTROL_CHARS, "850_JSON", "Failed")
    async with env.factory(OP) as u:
        with pytest.raises(BusinessError):
            await DocumentActions(u).ignore(did, "x", env.tasks)
        doc = await DocumentActions(u).ignore(did, "a test file from the partner", env.tasks)
        await u.commit()
    assert doc.processing_status == "Skipped" and "a test file" in doc.error
    async with env.factory(OP) as u:
        with pytest.raises(BusinessError) as e:
            await DocumentActions(u).read_again(did, env.tasks)
    assert e.value.code == "document.not_failed"


async def test_send_again_refuses_a_received_file(env):  # noqa: F811
    did = await _stored(env, b"{}", "850_JSON", "Failed")
    async with env.factory(OP) as u:
        with pytest.raises(BusinessError) as e:
            await DocumentActions(u).send_again(did, SimpleNamespace())
    assert e.value.code == "document.not_outbound"


async def test_a_refused_940_is_sent_again_from_the_register(edi):  # noqa: F811
    from app.models.config import Connection
    from tests.test_outbound import a_paid_order, docs, status
    from app.domain.status import Fulfillment as F
    o = await a_paid_order(edi)
    async with edi.env.factory(SYSTEM_ACTOR) as u:
        c = await u.session.get(Connection, edi.conn_id)
        c.can_write = False
        await u.commit()
    await edi.out.queue_due()
    await edi.worker.run_once()
    [d] = await docs(edi)
    assert d["file_status"] == "Suppressed" and await status(edi, o.id) == F.FAILED_940
    async with edi.env.factory(SYSTEM_ACTOR) as u:
        (await u.session.get(Connection, edi.conn_id)).can_write = True
        await u.commit()
    async with edi.env.factory(OP) as u:
        text = await DocumentActions(u).send_again(d["id"], SimpleNamespace(outbound=edi.out))
        await u.commit()
    assert text.startswith("940 ") and (await edi.worker.run_once()).done == 1
    assert await status(edi, o.id) == F.SENT_940
