"""Once per database, after sales29: each retailer order's "Agreement ID" read from the 850 it was made from
(the order's own stored file), so its next 810 carries it (user 2026-11-13). An order that already has one, or
whose 850 names none, is left. No status, no file, no send. Idempotent. (As backfill_edi_mailbox.)

    python -m app.scripts.backfill_agreement_id            # dry run: how many would be set
    python -m app.scripts.backfill_agreement_id --apply
"""
from __future__ import annotations

import collections
import json

from sqlalchemy import select

from app.core.deps import uow_factory
from app.domain.sources import SourceError, from_850
from app.models.documents import Document, DocumentLink
from app.models.orders import Order
from app.platform.actors import SYSTEM_ACTOR
from app.scripts.base import ScriptArgs, script_main
from app.services import oplog


async def main(args: ScriptArgs) -> dict:
    n: collections.Counter = collections.Counter()
    async with uow_factory(SYSTEM_ACTOR) as u:
        s = u.session
        orders = list((await s.execute(select(Order).where(Order.source_type == "EDI_850", Order.agreement_id.is_(None))
                                       .with_for_update())).scalars())
        for o in orders:
            contents = (await s.execute(select(Document.content).join(DocumentLink, DocumentLink.document_id == Document.id).where(
                DocumentLink.entity_type == "order", DocumentLink.entity_id == o.id, Document.kind == "850_JSON")
                .order_by(Document.id.desc()))).scalars().all()
            value = None
            for c in contents:  # the latest 850 of the order that names one
                try:
                    body = json.loads(c.decode("utf-8-sig") if isinstance(c, bytes) else c, strict=False)
                    srcs = [x for x in from_850(body) if x.source_order_id == o.source_order_id] or from_850(body)
                except (ValueError, SourceError):
                    continue
                value = next((x.header.get("agreement_id") for x in srcs if x.header.get("agreement_id")), None)
                if value:
                    break
            if not value:
                n["no Agreement ID in its 850"] += 1
                continue
            n[f"set ({o.partner_code})"] += 1
            if args.apply:
                o.agreement_id = value
        if args.apply:
            await oplog.write(s, "INFO", "repair", "Agreement ID read from each retailer order's 850", context=dict(n))
            await u.commit()
    return {"orders": sum(n.values()), "outcome": dict(n)}


if __name__ == "__main__":
    script_main(main)
