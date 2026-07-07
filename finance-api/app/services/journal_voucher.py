"""Journal voucher generation service.

generate_from_event() turns one just-emitted posting_event (+ its lines and
long-tail dimensions) into a balanced `draft` JournalVoucher, 1:1 idempotent on
posting_event_id. Called at the tail of emit_event() inside the same txn.
"""
import uuid
from datetime import datetime, timezone
from decimal import Decimal

from sqlalchemy import func, select
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
    yyyymm = fiscal_period.replace("-", "")
    like = f"{voucher_word}-{yyyymm}-%"
    n = (await db.execute(
        select(func.count()).select_from(JournalVoucher).where(
            JournalVoucher.jv_number.like(like))
    )).scalar_one()
    return f"{voucher_word}-{yyyymm}-{n + 1:04d}"
