"""Journal voucher lifecycle — 制单→审核→过账, 弃审, 反过账, 红冲.

Vouchers are created `draft` by services/journal_voucher.generate_from_event.
This module drives the human/controlled transitions. Only `posted` vouchers
reach the GL (Plan 3 switches GL reads to posted JV lines).
"""
import uuid
from datetime import datetime, timezone
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.fiscal_period import OPEN, FiscalPeriod
from app.models.journal_voucher import (
    DRAFT, POSTED, REVERSED, REVIEWED, JournalVoucher, JournalVoucherLine, JvLineDimension,
)
from app.models.mirrors import SodRule
from app.services.journal_voucher import next_jv_number

# Finance authority to review/post vouchers. role_management-assignment gating
# (finance_bp / finance_manager) can layer on later; JWT role is the base gate.
_JV_ROLES = {"finance_manager", "finance_bp", "system_admin"}


class JvStateError(ValueError):
    """Illegal state transition (wrong current status)."""


class JvPermissionError(Exception):
    """Caller lacks the role, or SoD forbids the action."""


def _require_role(user: dict) -> None:
    if user.get("role") not in _JV_ROLES:
        raise JvPermissionError("Insufficient role for journal-voucher action")


async def _sod_self_review_enabled(db: AsyncSession) -> bool:
    rule = (await db.execute(
        select(SodRule).where(SodRule.rule_code == "jv_self_review")
    )).scalar_one_or_none()
    return bool(rule and rule.enabled)


async def get(db: AsyncSession, jv_id: uuid.UUID) -> JournalVoucher | None:
    return (await db.execute(
        select(JournalVoucher).where(JournalVoucher.id == jv_id))).scalar_one_or_none()


async def _require(db: AsyncSession, jv_id: uuid.UUID, expect_status: str) -> JournalVoucher:
    jv = await get(db, jv_id)
    if jv is None:
        raise JvStateError("Journal voucher not found")
    if jv.status != expect_status:
        raise JvStateError(f"Voucher is '{jv.status}', expected '{expect_status}'")
    return jv


async def review(db: AsyncSession, jv_id: uuid.UUID, user: dict) -> JournalVoucher:
    jv = await _require(db, jv_id, DRAFT)
    _require_role(user)
    if await _sod_self_review_enabled(db) and str(user["sub"]) == str(jv.prepared_by):
        raise JvPermissionError("SoD (jv_self_review): reviewer cannot be the preparer")
    jv.status = REVIEWED
    jv.reviewed_by = uuid.UUID(user["sub"])
    jv.reviewed_at = datetime.now(timezone.utc)
    await db.flush()
    return jv


async def unreview(db: AsyncSession, jv_id: uuid.UUID, user: dict) -> JournalVoucher:
    jv = await _require(db, jv_id, REVIEWED)
    _require_role(user)
    jv.status = DRAFT
    jv.reviewed_by = None
    jv.reviewed_at = None
    await db.flush()
    return jv


async def _require_period_open(db: AsyncSession, period: str) -> None:
    row = (await db.execute(
        select(FiscalPeriod).where(FiscalPeriod.period == period)
    )).scalar_one_or_none()
    if row is not None and row.status != OPEN:
        raise JvStateError(f"Fiscal period {period} is closed ({row.status})")


async def post(db: AsyncSession, jv_id: uuid.UUID, user: dict) -> JournalVoucher:
    jv = await _require(db, jv_id, REVIEWED)
    _require_role(user)
    await _require_period_open(db, jv.fiscal_period)
    jv.status = POSTED
    jv.posted_by = uuid.UUID(user["sub"])
    jv.posted_at = datetime.now(timezone.utc)
    await db.flush()
    return jv


async def unpost(db: AsyncSession, jv_id: uuid.UUID, user: dict) -> JournalVoucher:
    jv = await _require(db, jv_id, POSTED)
    _require_role(user)
    await _require_period_open(db, jv.fiscal_period)
    jv.status = REVIEWED
    jv.posted_by = None
    jv.posted_at = None
    await db.flush()
    return jv


def _neg(v: Decimal | None) -> Decimal | None:
    return None if v is None else -v


async def reverse(db: AsyncSession, jv_id: uuid.UUID, user: dict) -> JournalVoucher:
    """红冲: create a posted red (negated) voucher that offsets the original, and
    mark the original `reversed`. Both stay for audit. Period must be open."""
    jv = await _require(db, jv_id, POSTED)
    _require_role(user)
    await _require_period_open(db, jv.fiscal_period)

    now = datetime.now(timezone.utc)
    red = JournalVoucher(
        jv_number=await next_jv_number(db, jv.fiscal_period, jv.voucher_word),
        voucher_word=jv.voucher_word,
        voucher_date=jv.voucher_date,
        fiscal_period=jv.fiscal_period,
        summary=f"红冲: {jv.summary or jv.jv_number}",
        status=POSTED,
        source_service=jv.source_service, source_doc_type=jv.source_doc_type,
        source_doc_id=jv.source_doc_id, source_doc_number=jv.source_doc_number,
        prepared_by=uuid.UUID(user["sub"]), prepared_at=now,
        reviewed_by=uuid.UUID(user["sub"]), reviewed_at=now,
        posted_by=uuid.UUID(user["sub"]), posted_at=now,
        reverses_jv_id=jv.id,
        total_debit=_neg(jv.total_debit), total_credit=_neg(jv.total_credit),
        total_local_debit=_neg(jv.total_local_debit),
        total_local_credit=_neg(jv.total_local_credit),
        entity_id=jv.entity_id,
    )
    db.add(red)
    await db.flush()

    src_lines = (await db.execute(
        select(JournalVoucherLine).where(JournalVoucherLine.jv_id == jv.id)
        .order_by(JournalVoucherLine.line_no))).scalars().all()
    line_map: list[tuple[JournalVoucherLine, uuid.UUID]] = []
    for sl in src_lines:
        rl = JournalVoucherLine(
            jv_id=red.id, line_no=sl.line_no, account_code=sl.account_code,
            summary=sl.summary,
            orig_debit=_neg(sl.orig_debit), orig_credit=_neg(sl.orig_credit),
            local_debit=_neg(sl.local_debit), local_credit=_neg(sl.local_credit),
            currency=sl.currency, fx_rate=sl.fx_rate,
            quantity=_neg(sl.quantity), unit=sl.unit, price=sl.price,
            cost_center_id=sl.cost_center_id, department_id=sl.department_id,
            partner_id=sl.partner_id, partner_name=sl.partner_name,
            tax_code=sl.tax_code, project_id=sl.project_id, item_id=sl.item_id,
        )
        db.add(rl)
        line_map.append((rl, sl.id))
    await db.flush()

    for rl, src_id in line_map:
        dims = (await db.execute(
            select(JvLineDimension).where(JvLineDimension.jv_line_id == src_id)
        )).scalars().all()
        for d in dims:
            db.add(JvLineDimension(jv_line_id=rl.id, dim_code=d.dim_code,
                                   value_id=d.value_id, value_text=d.value_text))

    jv.status = REVERSED
    jv.reversed_by_jv_id = red.id
    await db.flush()
    return red


async def _batch(db, ids, user, fn) -> list[dict]:
    """Apply a single-voucher transition to each id, collecting per-id outcome.
    Each failure is caught and reported; successes stay in the shared txn."""
    out: list[dict] = []
    for jid in ids:
        try:
            await fn(db, jid, user)
            out.append({"id": str(jid), "ok": True, "error": None})
        except (JvStateError, JvPermissionError) as e:
            out.append({"id": str(jid), "ok": False, "error": str(e)})
    return out


async def review_batch(db: AsyncSession, ids: list[uuid.UUID], user: dict) -> list[dict]:
    return await _batch(db, ids, user, review)


async def post_batch(db: AsyncSession, ids: list[uuid.UUID], user: dict) -> list[dict]:
    return await _batch(db, ids, user, post)
