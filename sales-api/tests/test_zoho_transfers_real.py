"""Zoho's real transfers (943s 00100 … 00106, user 2026-11-12) and Radiant's real 944s for them:
Sales, given the same lines, builds Zoho's 943 (only the date and the file name in the NTE differ),
and reads each 944 as the warehouse meant it — Received, or the two real discrepancies (a
different item came: 00104 CF0103 for CF0093, 00106 CF0092 for CF0102)."""
from decimal import Decimal
from pathlib import Path

import pytest
import sqlalchemy as sa

from app.domain.shadow import compare
from app.domain.x12 import segments
from app.models.config import Item
from app.platform.actors import SYSTEM_ACTOR
from tests.test_orders import env  # noqa: F401 — a fixture (p7 needs it)
from tests.test_outbound import edi as p4_edi  # noqa: F401 — a fixture
from tests.test_transfers import p7, sent_transfer, status, store  # noqa: F401 — p7 is a fixture

FILES = Path(__file__).parent / "fixtures" / "zoho_transfers"
# The item master's gross weights (dev2, 2026-11-12): Zoho's W03 is these × the quantities.
WEIGHTS = {"CF0092": "2.03", "CF0093": "2.48", "CF0102": "2.03", "CF0103": "2.03", "CF0110": "2.03", "S0156": "2.48"}
OUTCOME = {"00100": None, "00101": "Received", "00102": "Received", "00103": "Received", "00104": "Discrepancy",
           "00105": "Received", "00106": "Discrepancy"}


@pytest.mark.parametrize("number", sorted(OUTCOME))
async def test_sales_builds_zohos_943_and_reads_radiants_944(p7, number):  # noqa: F811
    zoho = next(FILES.glob(f"CRM-943_*_TRID_{number}_.EDI")).read_bytes()
    lines = tuple((x[5], int(float(x[1]))) for x in segments(zoho) if x[0] == "W04")
    async with p7.factory(SYSTEM_ACTOR) as u:
        s = u.session
        for code, w in WEIGHTS.items():
            item = (await s.execute(sa.select(Item).where(Item.item_code == code))).scalar_one_or_none()
            if item is None:
                s.add(Item(item_code=code, sku=f"{code}-SKU", warehouse_held=True, gross_weight_lb_per_ea=Decimal(w), version=1))
            else:
                item.gross_weight_lb_per_ea = Decimal(w)
        await s.execute(sa.text(f"ALTER SEQUENCE sales_transfer_number_seq RESTART WITH {int(number)}"))
        await u.commit()
    x = await sent_transfer(p7, lines)
    assert x.transfer_number == number
    [(path, built)] = p7.store.files.items()
    assert path.endswith(f"_TRID_{number}_.EDI")
    c = compare(zoho, built)
    assert sorted({r["zoho"].split("*")[0] for r in c.rows if not r["same"]}) == ["NTE", "W06"], c.rows  # the weights too (W03)
    f944 = FILES / f"944-Radiant-{number}.edi"
    if OUTCOME[number] is None:
        assert not f944.exists()
        return
    await store(p7, "944_EDI", f944.read_bytes(), f944.name)
    r = await p7.tr.process_944s()
    assert r.receipts == 1 and await status(p7, x.id) == OUTCOME[number]
    if number == "00106":
        [t] = [x["description"] for x in await p7.q("SELECT description FROM tasks WHERE type = 'sales_transfer_discrepancy'")]
        assert "CF0092 3600 EA received but not on the transfer" in t and "line 3 CF0102: 0 of 3600 EA received" in t
