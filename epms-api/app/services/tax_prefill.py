"""发票创建时按有效税率反推税码,自动预填单条 tax line(fail-open)。

规则(spec 2026-07-09):有效率 = tax_amount/amount,在激活税码中找
|rate - 有效率| ≤ 0.005(±0.5 个百分点)的候选;唯一命中才预填,
零命中/并列一律跳过(照旧进 ITC uncoded 清单,宁缺勿错)。
"""
from __future__ import annotations

import logging
from decimal import Decimal

from app.services.mdm_client import MdmClient  # monkeypatch anchor

logger = logging.getLogger(__name__)

_RATE_TOLERANCE = Decimal("0.005")


def pick_tax_code(amount: Decimal, tax_amount: Decimal, codes: list[dict]) -> dict | None:
    if amount is None or tax_amount is None or amount <= 0 or tax_amount <= 0:
        return None
    effective = Decimal(tax_amount) / Decimal(amount)
    hits = [
        c for c in codes
        if abs(Decimal(str(c.get("rate", "0"))) - effective) <= _RATE_TOLERANCE
    ]
    return hits[0] if len(hits) == 1 else None


async def apply_tax_prefill(db, invoice, bearer_token: str) -> None:
    """创建后自动预填 — 任何失败只记 warning,绝不影响发票创建。"""
    from app.models.invoice_tax_line import InvoiceTaxLine
    try:
        if not invoice.tax_amount or invoice.tax_amount <= 0 or not invoice.amount or invoice.amount <= 0:
            return
        async with MdmClient(bearer_token) as mdm:
            codes = await mdm.get_tax_codes()
        pick = pick_tax_code(invoice.amount, invoice.tax_amount, codes or [])
        if pick is None:
            logger.info("tax prefill: no unique rate match for invoice %s", invoice.id)
            return
        async with db.begin_nested():
            db.add(InvoiceTaxLine(
                invoice_id=invoice.id, line_no=1,
                tax_code=pick["code"], taxable_amount=invoice.amount,
                tax_amount=invoice.tax_amount,
                recoverable=bool(pick.get("recoverable", True)),
            ))
            await db.flush()
    except Exception as exc:  # noqa: BLE001 — fail-open by design
        logger.warning("tax prefill skipped for invoice %s: %s", getattr(invoice, "id", "?"), exc)
