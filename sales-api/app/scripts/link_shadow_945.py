"""Once (user 2026-11-03): link the rows of the shadow-run 945s stored before
their rows were linked to the order lines. Linking only — no status, no
quantity, no task, no Shopify write (Shipments.link_shadow_rows). Idempotent.

    python -m app.scripts.link_shadow_945            # dry run: counts, nothing kept
    python -m app.scripts.link_shadow_945 --apply    # link, and log it to sales_log
"""
from __future__ import annotations

import asyncio
import json
import sys

from app.core.deps import uow_factory
from app.platform.actors import SYSTEM_ACTOR
from app.services.shipments import Shipments


async def main(apply: bool) -> dict[str, int]:
    ships = Shipments(uow_factory=uow_factory, tasks=None, shopify=None, outbound=None)
    async with uow_factory(SYSTEM_ACTOR) as u:
        counts = await ships.link_shadow_rows(u, dry_run=not apply)
        if apply:
            await u.commit()
    return counts


if __name__ == "__main__":
    print(json.dumps({"apply": "--apply" in sys.argv, **asyncio.run(main("--apply" in sys.argv))}))
