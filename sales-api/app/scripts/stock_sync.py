"""The Radiant inventory ledger brought up to date now (the system job does the same every run).

    python -m app.scripts.stock_sync            # dry run: the moves that would be added, by source
    python -m app.scripts.stock_sync --apply
"""
from __future__ import annotations

from app.core.deps import uow_factory
from app.platform.actors import SYSTEM_ACTOR
from app.scripts.base import ScriptArgs, script_main
from app.services import oplog
from app.services.stock import sync


async def main(args: ScriptArgs) -> dict:
    async with uow_factory(SYSTEM_ACTOR) as u:
        out = await sync(u)
        if args.apply:
            await oplog.write(u.session, "INFO", "stock", f"inventory: {sum(out.values())} moves added (script)", context=out)
            await u.commit()
    return {"moves": out}


if __name__ == "__main__":
    script_main(main)
