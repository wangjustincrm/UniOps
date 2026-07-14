"""Journal voucher generation service.

generate_from_event() turns one just-emitted posting_event (+ its lines and
long-tail dimensions) into a balanced `draft` JournalVoucher, 1:1 idempotent on
posting_event_id. Called at the tail of emit_event() inside the same txn.
"""
import uuid
from datetime import datetime, timezone
from decimal import Decimal

from sqlalchemy import Integer, cast, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.journal_voucher import DRAFT, JournalVoucher, JournalVoucherLine, JvLineDimension
from app.models.posting import PostingEvent, PostingLine, PostingLineDimension

_ZERO = Decimal("0")

# event_type/source_doc_type → summary template (Chinese; user-facing UI strings
# are handled elsewhere — this is a stored memo, editable later).
_SUMMARY = {
    ("ap_invoice", "accrual"): "应付计提",
    ("pa", "payment"): "付款",
    ("pa_dir", "payment"): "付款",
    ("exp", "expense_paid"): "报销付款",
    ("trv", "expense_paid"): "报销付款",
    ("cfm", "expense_paid"): "报销付款",
    ("mil", "expense_paid"): "报销付款",
}


def _q(v) -> Decimal:
    return Decimal(str(v)).quantize(Decimal("0.01"))


def build_summary(source_doc_type: str, event_type: str,
                  doc_number: str | None, partner_name: str | None) -> str:
    prefix = _SUMMARY.get((source_doc_type, event_type))
    if not prefix:
        return doc_number or ""
    parts = [prefix, doc_number or ""]
    if partner_name:
        parts.append(partner_name)
    return " · ".join(p for p in parts if p)


async def next_jv_number(db: AsyncSession, fiscal_period: str, voucher_word: str = "JV") -> str:
    """Max-suffix+1, not count: NC-imported history shares the JV- namespace and
    can have numbering gaps — a count would reissue a taken number."""
    yyyymm = fiscal_period.replace("-", "")
    prefix = f"{voucher_word}-{yyyymm}-"
    n = (await db.execute(
        select(func.coalesce(func.max(
            cast(func.split_part(JournalVoucher.jv_number, "-", 3), Integer)), 0))
        .where(JournalVoucher.jv_number.like(prefix + "%"))
    )).scalar_one()
    return f"{prefix}{n + 1:04d}"


async def generate_from_event(db: AsyncSession, event_id: uuid.UUID,
                              prepared_by: uuid.UUID | None) -> JournalVoucher | None:
    """Create a draft JV from a posting_event. Idempotent on posting_event_id —
    if a JV already exists for this event, returns it without creating a second."""
    existing = (await db.execute(
        select(JournalVoucher).where(JournalVoucher.posting_event_id == event_id)
    )).scalar_one_or_none()
    if existing is not None:
        return existing

    ev = (await db.execute(
        select(PostingEvent).where(PostingEvent.id == event_id)
    )).scalar_one_or_none()
    if ev is None:
        return None

    plines = (await db.execute(
        select(PostingLine).where(PostingLine.event_id == event_id)
        .order_by(PostingLine.line_no)
    )).scalars().all()

    jv = JournalVoucher(
        jv_number=await next_jv_number(db, ev.fiscal_period or ev.occurred_at.strftime("%Y-%m")),
        voucher_word="JV",
        voucher_date=ev.occurred_at.date(),
        fiscal_period=ev.fiscal_period or ev.occurred_at.strftime("%Y-%m"),
        summary=build_summary(ev.source_doc_type, ev.event_type, ev.source_doc_number,
                              next((p.partner_name for p in plines if p.partner_name), None)),
        status=DRAFT,
        posting_event_id=ev.id,
        source_service=ev.source_service, source_doc_type=ev.source_doc_type,
        source_doc_id=ev.source_doc_id, source_doc_number=ev.source_doc_number,
        prepared_by=prepared_by, prepared_at=datetime.now(timezone.utc),
        entity_id=ev.entity_id,
    )
    db.add(jv)
    await db.flush()

    tot_d = tot_c = tot_ld = tot_lc = _ZERO
    line_map: list[tuple[JournalVoucherLine, uuid.UUID]] = []
    for pl in plines:
        rate = pl.fx_rate if pl.fx_rate is not None else Decimal("1")
        ld = _q(pl.debit * rate)
        lc = _q(pl.credit * rate)
        jl = JournalVoucherLine(
            jv_id=jv.id, line_no=pl.line_no, account_code=pl.account_code,
            summary=pl.memo,
            orig_debit=pl.debit, orig_credit=pl.credit,
            local_debit=ld, local_credit=lc,
            currency=pl.currency, fx_rate=rate,
            cost_center_id=pl.cost_center_id, department_id=pl.department_id,
            partner_id=pl.partner_id, partner_name=pl.partner_name,
            tax_code=pl.tax_code, project_id=pl.project_id, item_id=pl.item_id,
        )
        db.add(jl)
        tot_d += pl.debit; tot_c += pl.credit; tot_ld += ld; tot_lc += lc
        line_map.append((jl, pl.id))
    await db.flush()

    # copy long-tail dimensions; income_expense_item is ALSO promoted onto the
    # line column (queries read the column; the KV row keeps the code text).
    for jl, pl_id in line_map:
        dims = (await db.execute(
            select(PostingLineDimension).where(PostingLineDimension.posting_line_id == pl_id)
        )).scalars().all()
        for d in dims:
            db.add(JvLineDimension(jv_line_id=jl.id, dim_code=d.dim_code,
                                   value_id=d.value_id, value_text=d.value_text))
            if d.dim_code == "income_expense_item" and d.value_id is not None:
                jl.income_expense_item_id = d.value_id

    jv.total_debit = _q(tot_d); jv.total_credit = _q(tot_c)
    jv.total_local_debit = _q(tot_ld); jv.total_local_credit = _q(tot_lc)
    await db.flush()
    return jv
