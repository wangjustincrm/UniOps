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
