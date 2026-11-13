"""Once per database, after sales31: each Radiant report's "picked" (its Allocation column: CRM|<order>) read
again from the report file kept with it. Lines are matched by item and lot; nothing else changes.

    python -m app.scripts.backfill_count_picked            # dry run: how many lines would be set
    python -m app.scripts.backfill_count_picked --apply
"""
from __future__ import annotations

import collections

from sqlalchemy import select

from app.core.deps import uow_factory
from app.domain.radiant_stock import StockFileError, parse_stock_file
from app.models.documents import Document
from app.models.stock import StockCount, StockCountLine
from app.platform.actors import SYSTEM_ACTOR
from app.scripts.base import ScriptArgs, script_main
from app.services import oplog


async def main(args: ScriptArgs) -> dict:
    n: collections.Counter = collections.Counter()
    async with uow_factory(SYSTEM_ACTOR) as u:
        s = u.session
        for c in list((await s.execute(select(StockCount).order_by(StockCount.id))).scalars()):
            content = (await s.execute(select(Document.content).where(Document.id == c.document_id))).scalar() if c.document_id else None
            if content is None:
                n["count with no file"] += 1
                continue
            try:
                picked = {(r.item_code, r.lot): r.picked for r in parse_stock_file(c.file_name or "report", bytes(content))}
            except StockFileError:
                n["count file not read"] += 1
                continue
            for ln in (await s.execute(select(StockCountLine).where(StockCountLine.count_id == c.id).with_for_update())).scalars():
                value = picked.get((ln.item_code, ln.lot)) or {}
                if value == (ln.picked or {}):
                    n["line unchanged"] += 1
                    continue
                n["line set"] += 1
                if args.apply:
                    ln.picked = value
        if args.apply and n["line set"]:
            await oplog.write(s, "INFO", "repair", "Radiant reports: picked pallets read again from their files", context=dict(n))
            await u.commit()
    return {"outcome": dict(n)}


if __name__ == "__main__":
    script_main(main)
