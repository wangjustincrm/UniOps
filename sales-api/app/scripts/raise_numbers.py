"""Cutover (FR-OB-22): Sales' own numbers start above Zoho's, so no number means two things.

    python -m app.scripts.raise_numbers --order 1009000 --control 1009000 --transfer 107            # dry run
    python -m app.scripts.raise_numbers --order 1009000 --control 1009000 --transfer 107 --apply

- order:    the internal order number (W05-02 of a retailer 940, and what a 945 echoes). Below it, a
            945 that carries a Shopify order number could also match an internal number.
- control:  the X12 interchange / group / set control of every 940 and 943 (one sequence, A.2).
- transfer: the 943 transfer number (W06-02).

Give the next number each should take: above the highest Zoho used. A sequence is only ever
raised, never lowered (a number already given out is never given again). Prints the before and after.
"""
from __future__ import annotations

import asyncio
import json
import sys
from typing import Any

import sqlalchemy as sa

from app.platform.actors import SYSTEM_ACTOR
from app.services import oplog

STEP = "cutover"
SEQUENCES = {"order": "sales_order_number_seq", "control": "sales_x12_control_seq", "transfer": "sales_transfer_number_seq"}


async def _next(s, seq: str) -> int:
    """The number the sequence gives next."""
    last, called = (await s.execute(sa.text(f"SELECT last_value, is_called FROM {seq}"))).one()
    return int(last) + 1 if called else int(last)


async def run(uow_factory, wanted: dict[str, int], *, apply: bool) -> dict[str, Any]:
    unknown = set(wanted) - set(SEQUENCES)
    if unknown:
        raise ValueError(f"unknown: {sorted(unknown)}; give any of {sorted(SEQUENCES)}")
    report: dict[str, Any] = {"apply": apply}
    async with uow_factory(SYSTEM_ACTOR) as u:
        s = u.session
        for name, seq in SEQUENCES.items():
            before = await _next(s, seq)
            want = wanted.get(name)
            after = max(before, want) if want is not None else before
            report[name] = {"next_before": before, "next_after": after}
            if apply and after > before:
                await s.execute(sa.text("SELECT setval(CAST(:seq AS regclass), :v, false)"), {"seq": seq, "v": after})
        if apply:
            await oplog.write(s, "INFO", STEP, "Sales numbers raised above Zoho's", context=report)
            await u.commit()
    return report


def _args(argv: list[str]) -> tuple[dict[str, int], bool]:
    wanted, i = {}, 0
    while i < len(argv):
        a = argv[i]
        if a.startswith("--") and a[2:] in SEQUENCES:
            wanted[a[2:]] = int(argv[i + 1])
            i += 2
            continue
        i += 1
    return wanted, "--apply" in argv


if __name__ == "__main__":
    from app.core.deps import uow_factory
    from app.scripts.redact_card_documents import quiet_sql
    quiet_sql()
    wanted, apply = _args(sys.argv[1:])
    print(json.dumps(asyncio.run(run(uow_factory, wanted, apply=apply))))
