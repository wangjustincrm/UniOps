"""Once (user 2026-11-03): link the rows of the shadow-run 945s stored before
their rows were linked to the order lines. Linking only — no status, no
quantity, no task, no Shopify write (Shipments.link_shadow_rows). Idempotent.

    python -m app.scripts.link_shadow_945            # dry run: counts, nothing kept
    python -m app.scripts.link_shadow_945 --apply    # link, and log it to sales_log

The mapping rule (FR-OB-25a) on rows already linked; order numbers and quantities only:

    python -m app.scripts.link_shadow_945 --relink            # dry run: which rows would move
    python -m app.scripts.link_shadow_945 --relink --apply    # move them, and log it
"""
from __future__ import annotations

import asyncio
import json
import sys

from app.core.deps import uow_factory
from app.platform.actors import SYSTEM_ACTOR
from app.scripts.redact_card_documents import quiet_sql
from app.services.shipments import Shipments


async def main(apply: bool, relink: bool = False) -> dict:
    ships = Shipments(uow_factory=uow_factory, tasks=None, shopify=None, outbound=None)
    async with uow_factory(SYSTEM_ACTOR) as u:
        counts = await (ships.relink_shadow_rows if relink else ships.link_shadow_rows)(u, dry_run=not apply)
        if apply:
            await u.commit()
    return counts


if __name__ == "__main__":
    quiet_sql()
    print(json.dumps({"apply": "--apply" in sys.argv, **asyncio.run(main("--apply" in sys.argv, "--relink" in sys.argv))}))
