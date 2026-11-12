"""While Sales runs beside Zoho: retailer invoices Zoho already sent, completed in Sales.

Given lines "<PO> <invoice date>" (YYYY/MM/DD or YYYY-MM-DD), each order waiting for its
delivery confirmation is confirmed on that date through Sales' own step (the invoice and
its lines are built as they would be live), its 810 is built and kept with the invoice —
not sent: Zoho sent the partner's — and the invoice is Sent, "invoiced by Zoho". The
invoice's problems (a price missing, a part case) are reported; an invoice with one is
left Ready for a person. Nothing goes to TrueCommerce or by email.

    python -m app.scripts.complete_invoices <file>            # dry run: each invoice and its checks, nothing kept
    python -m app.scripts.complete_invoices <file> --apply
"""
from __future__ import annotations

import json
import re
from datetime import date
from pathlib import Path

from sqlalchemy import select

from app.core.deps import uow_factory
from app.domain.status import INVOICE, OUTBOUND_FILE, OutboundFile
from app.domain.status import Invoice as Inv
from app.models.invoices import Invoice, InvoiceLine
from app.models.orders import Order
from app.platform.actors import SYSTEM_ACTOR
from app.scripts.base import ScriptArgs, script_main
from app.services import oplog
from app.services.invoicing import Invoicing
from app.services.problems import task_service

LINE = re.compile(r"^\s*(\S+)\s+(\d{4})[/-](\d{2})[/-](\d{2})\s*$")


def parse(text: str) -> list[tuple[str, date]]:
    out = []
    for raw in text.splitlines():
        m = LINE.match(raw)
        if m:
            out.append((m.group(1), date(int(m.group(2)), int(m.group(3)), int(m.group(4)))))
    return out


def checks(inv: Invoice, lines: list[InvoiceLine], data: bytes | None, mailbox: str | None) -> list[str]:
    """What a person should look at before such an invoice goes out."""
    out = []
    if not lines:
        out.append("no line invoiced")
    for ln in lines:
        if not ln.unit_price:
            out.append(f"line {ln.line_no}: no price")
    if inv.total is None or inv.total <= 0:
        out.append("total is not above zero")
    if data is not None:
        body = json.loads(data)["Invoice"][0]
        if mailbox and body.get("EDIMailbox") != mailbox:
            out.append("the 810 does not carry the order's EDI mailbox")
        if not (body.get("ShipTo") or {}).get("ShipToCode"):
            out.append("the 810 ship-to has no code (GLN)")
        if sum(i["QuantityShipped"] for i in body["Items"]) <= 0:
            out.append("the 810 ships nothing")
    return out


async def main(args: ScriptArgs) -> dict:
    wanted = parse(Path(args.positional[0]).read_text())
    invoicing = Invoicing(uow_factory=uow_factory, tasks=task_service(lambda: False), sftp=None, email=None)
    rows = []
    for po, when in wanted:
        row: dict = {"po": po, "invoice_date": when.isoformat()}
        rows.append(row)
        async with uow_factory(SYSTEM_ACTOR) as u:
            s = u.session
            found = list((await s.execute(select(Order).where(Order.source_type == "EDI_850", Order.source_order_number == po))).scalars())
            if len(found) != 1:
                row["result"] = f"{'no' if not found else 'several'} retailer order with this PO"
                continue
            o = found[0]
            row["order"] = o.label
            if o.invoice_status != Inv.WAITING_FOR_DELIVERY_CONFIRMATION.value:
                row["result"] = f"left: invoice is {o.invoice_status}"
                continue
            inv = await invoicing.confirm(u, o.id, confirmed_date=when, note="invoiced by Zoho (parallel run)")
            lines = list((await s.execute(select(InvoiceLine).where(InvoiceLine.invoice_id == inv.id).order_by(InvoiceLine.line_no))).scalars())
            data = await invoicing._810(s, inv, o, lines) if inv.method == "810_JSON" else None
            problems = checks(inv, lines, data, o.edi_mailbox)
            row.update(invoice=inv.invoice_number, method=inv.method, lines=len(lines), total=str(inv.total),
                       status_after_confirm=o.invoice_status, checks=problems or "ok")
            if o.invoice_status != Inv.READY.value or problems:
                row["result"] = "confirmed; left for a person (see checks)" if args.apply else "would be left for a person"
                if args.apply:
                    await u.commit()
                continue
            if data is not None:
                name = (f"810-{o.partner_code}-{o.order_number}-{o.source_order_number}-zoho-parallel.json")
                doc = await invoicing._document(u, inv, o, data, name, system="TRUECOMMERCE", kind="810_JSON",
                                                conn_id=None)
                OUTBOUND_FILE.transition(doc, OutboundFile.SUPPRESSED, actor=u.actor, events=u.events,
                                         reason="parallel run: Zoho sent the partner's 810; this one was not sent")
                doc.error = "not sent: Zoho sent the 810 (parallel run)"
            INVOICE.transition(o, Inv.SENT, actor=u.actor, events=u.events, reason=f"invoiced by Zoho on {when.isoformat()}")
            row["result"] = "completed: Sent (by Zoho)" if args.apply else "would be completed: Sent (by Zoho)"
            if args.apply:
                await oplog.write(s, "INFO", "repair", f"order {o.label}: invoice {inv.invoice_number} completed, "
                                  f"invoiced by Zoho on {when.isoformat()}", order_id=o.id)
                await u.commit()
    return {"orders": len(rows), "detail": rows}


if __name__ == "__main__":
    script_main(main)
