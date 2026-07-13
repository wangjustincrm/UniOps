"""GST/HST return worksheet (Phase a A5, FIN-TAX-004).

ITC (input tax credits) come from posting_lines with line_role='sales_tax'
(debit) — emitted by AP accrual (vendor invoices) and expense_paid (employee
expenses). Output tax (sales) comes from line_role='output_tax' (credit),
emitted by AR revenue recognition. Both are grouped by tax_code within a fiscal
period. Net tax = output − ITC (negative = refund/credit). Uncoded lines
(tax_code NULL) are listed as exceptions to be coded before filing.

Basis note (Plan 5 GL switchover): this worksheet reads the BUSINESS SPINE
(posting_lines — all events, including ones whose JV is still draft), while
the GL 2200 balance reflects POSTED journal vouchers only, and a JV-level
red-flush (红冲) writes no posting_lines. The two can therefore diverge by
design; follow-up planned to move ITC/output tax onto posted-JV lines.
"""
import csv
import io
import re
from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import CurrentUser
from app.db.base import get_db
from app.models.posting import PostingEvent, PostingLine

router = APIRouter(prefix="/tax", tags=["tax-return"])

_PERIOD_RE = re.compile(r"^\d{4}-(0[1-9]|1[0-2])$")


async def _tax_rows(db: AsyncSession, period: str, line_role: str, side):
    """(tax_code, summed amount) for `line_role` lines in the period. `side` is
    PostingLine.debit (ITC) or PostingLine.credit (output tax)."""
    q = (
        select(PostingLine.tax_code, func.coalesce(func.sum(side), 0))
        .join(PostingEvent, PostingLine.event_id == PostingEvent.id)
        .where(PostingEvent.fiscal_period == period,
               PostingLine.line_role == line_role)
        .group_by(PostingLine.tax_code)
    )
    return (await db.execute(q)).all()


async def _itc_rows(db: AsyncSession, period: str):
    return await _tax_rows(db, period, "sales_tax", PostingLine.debit)


async def _output_rows(db: AsyncSession, period: str):
    return await _tax_rows(db, period, "output_tax", PostingLine.credit)


def _build(period: str, itc_rows, output_rows) -> dict:
    def coded(rows, key):
        out = [{"tax_code": code or "(uncoded)", key: str(amt)}
               for code, amt in rows if code is not None]
        out.sort(key=lambda r: r["tax_code"])
        return out

    itc_by_code = coded(itc_rows, "itc")
    output_by_code = coded(output_rows, "output_tax")
    uncoded_itc = sum((amt for code, amt in itc_rows if code is None), Decimal("0"))
    uncoded_output = sum((amt for code, amt in output_rows if code is None), Decimal("0"))
    itc_total = sum((amt for _, amt in itc_rows), Decimal("0"))
    output_total = sum((amt for _, amt in output_rows), Decimal("0"))
    return {
        "period": period,
        "itc_by_code": itc_by_code,
        "output_tax_by_code": output_by_code,
        "uncoded_itc": str(uncoded_itc),
        "uncoded_output_tax": str(uncoded_output),
        "itc_total": str(itc_total),
        "output_tax_total": str(output_total),
        "net_tax": str(output_total - itc_total),   # negative = refund/credit
        "uncoded_note": "Uncoded lines must be assigned a tax code before filing "
                        "(set line tax codes on the source invoice/expense).",
    }


@router.get("/gst-hst-return")
async def gst_hst_return(
    _: CurrentUser,
    db: AsyncSession = Depends(get_db),
    period: str = Query(..., description="YYYY-MM"),
):
    if not _PERIOD_RE.match(period):
        raise HTTPException(status_code=422, detail="period must be 'YYYY-MM'")
    return _build(period, await _itc_rows(db, period), await _output_rows(db, period))


@router.get("/gst-hst-return/export")
async def gst_hst_return_export(
    _: CurrentUser,
    db: AsyncSession = Depends(get_db),
    period: str = Query(..., description="YYYY-MM"),
):
    if not _PERIOD_RE.match(period):
        raise HTTPException(status_code=422, detail="period must be 'YYYY-MM'")
    data = _build(period, await _itc_rows(db, period), await _output_rows(db, period))
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow([f"GST/HST Return Worksheet — {period}"])
    w.writerow([])
    w.writerow(["Output tax (sales) by code", ""])
    for r in data["output_tax_by_code"]:
        w.writerow([r["tax_code"], r["output_tax"]])
    if Decimal(data["uncoded_output_tax"]) != 0:
        w.writerow(["(uncoded — assign codes before filing)", data["uncoded_output_tax"]])
    w.writerow([])
    w.writerow(["Input tax credits (ITC) by code", ""])
    for r in data["itc_by_code"]:
        w.writerow([r["tax_code"], r["itc"]])
    if Decimal(data["uncoded_itc"]) != 0:
        w.writerow(["(uncoded — assign codes before filing)", data["uncoded_itc"]])
    w.writerow([])
    w.writerow(["Total output tax", data["output_tax_total"]])
    w.writerow(["Total ITC", data["itc_total"]])
    w.writerow(["Net tax (output - ITC)", data["net_tax"]])
    return Response(
        content="﻿" + buf.getvalue(), media_type="text/csv",
        headers={"Content-Disposition": f"attachment; filename=gst-hst-return-{period}.csv"},
    )
