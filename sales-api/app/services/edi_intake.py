"""Retailer 850s (FR-OB-13, design §4.4): every stored, pending 850 document
is parsed and each PO in it upserted as an order — one document per unit.
A file that is JSON but not an 850 the parser can read ends Failed with a
task; a file that is not JSON at all was already stored and tasked at intake.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone

from sqlalchemy import select

from app.domain.intake import decode
from app.domain.sources import SourceError, from_850
from app.domain.status import INBOUND_FILE, InboundFile
from app.models.documents import Document
from app.platform.actors import SYSTEM_ACTOR
from app.platform.pipeline import isolated
from app.services import oplog
from app.services.orders import OrderSync
from app.services.problems import ORDER_FILE_UNREADABLE, problem

STEP = "850"


class EdiIntake:
    def __init__(self, *, uow_factory, tasks, clock=lambda: datetime.now(timezone.utc)) -> None:
        self.uow_factory = uow_factory
        self.tasks = tasks
        self.clock = clock

    async def run_pass(self, *, run_id: int | None = None, connection_id: int | None = None) -> dict[str, int]:
        async with self.uow_factory(SYSTEM_ACTOR) as uow:
            ids = list((await uow.session.execute(select(Document.id).where(
                Document.kind == "850_JSON", Document.direction == "IN",
                Document.processing_status == InboundFile.PENDING.value,
                *([Document.connection_id == connection_id] if connection_id is not None else []))
                .order_by(Document.id))).scalars())
        out = {"files": len(ids), "processed": 0, "failed": 0, "orders": 0}
        for doc_id in ids:
            outcome = await isolated(f"850 document {doc_id}", lambda uow, d=doc_id: self._one(uow, d, run_id),
                                     uow_factory=self.uow_factory, log=self._failed(doc_id, run_id))
            if outcome.ok:
                out["processed"] += 1
                out["orders"] += outcome.result
            else:
                out["failed"] += 1
        return out

    async def _one(self, uow, doc_id: int, run_id) -> int:
        s = uow.session
        doc = await s.get(Document, doc_id, with_for_update=True)
        content = (await s.execute(select(Document.content).where(Document.id == doc_id))).scalar_one()
        INBOUND_FILE.transition(doc, InboundFile.PROCESSING, actor=uow.actor, events=uow.events, reason="parse 850")
        orders = from_850(json.loads(decode(content), strict=False))  # as classify() reads it
        sync = OrderSync(uow, self.tasks)
        for src in orders:
            await sync.upsert(src, connection_id=doc.connection_id, document_id=doc.id, received_at=self.clock())
        INBOUND_FILE.transition(doc, InboundFile.PROCESSED, actor=uow.actor, events=uow.events,
                                reason=f"{len(orders)} order(s)")
        await oplog.write(s, "INFO", STEP, f"{doc.remote_name}: {len(orders)} order(s) applied", run_id=run_id,
                          document_id=doc.id)
        return len(orders)

    def _failed(self, doc_id: int, run_id):
        async def log(outcome) -> None:
            why = outcome.message if isinstance(outcome.error, (SourceError, ValueError)) else \
                f"{outcome.error_type}: {outcome.message}"
            async with self.uow_factory(SYSTEM_ACTOR, reason="850 could not be applied") as uow:
                doc = await uow.session.get(Document, doc_id, with_for_update=True)
                if doc.processing_status == InboundFile.PENDING.value:
                    INBOUND_FILE.transition(doc, InboundFile.PROCESSING, actor=uow.actor, events=uow.events)
                if doc.processing_status == InboundFile.PROCESSING.value:
                    INBOUND_FILE.transition(doc, InboundFile.FAILED, actor=uow.actor, events=uow.events, reason=why[:200])
                    doc.error = why[:2000]
                await oplog.write(uow.session, "ERROR", STEP, f"{doc.remote_name}: {why}", run_id=run_id, document_id=doc_id)
                await self.tasks.raise_problem(uow.session, ORDER_FILE_UNREADABLE, doc_id, problem(
                    ORDER_FILE_UNREADABLE, f"850 could not be applied: {doc.remote_name}"[:255],
                    f"Document {doc_id} ({doc.remote_name}): {why}"))
                await uow.commit()
        return log
