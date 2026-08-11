"""排期行的落库与查询。周期日期算法在 app/services/agreement_schedule.py。"""
import uuid
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.agreement import PurchaseAgreement
from app.models.agreement_schedule import AgreementPaymentSchedule
from app.schemas.agreement import MilestoneRowIn


def _resolve_milestone_amounts(
    row: MilestoneRowIn, ceiling: Decimal | None
) -> tuple[Decimal | None, Decimal | None]:
    """金额与百分比二选一录入,另一个推算。基数是 not_to_exceed —— 协议上唯一的
    总额字段。两个都给了就都存,不去纠正用户。"""
    amount, pct = row.expected_amount, row.amount_pct
    if amount is None and pct is not None and ceiling:
        amount = (ceiling * pct / Decimal("100")).quantize(Decimal("0.01"))
    elif pct is None and amount is not None and ceiling:
        pct = (amount / ceiling * Decimal("100")).quantize(Decimal("0.01"))
    return amount, pct


async def replace_milestone_rows(
    db: AsyncSession, agr: PurchaseAgreement, rows: list[MilestoneRowIn]
) -> None:
    """整体替换阶段行。只在 draft/returned 调用 —— 已认领的阶段不能被抹掉。"""
    existing = (await db.execute(
        select(AgreementPaymentSchedule).where(
            AgreementPaymentSchedule.agreement_id == agr.id,
            AgreementPaymentSchedule.schedule_type == "milestone",
        )
    )).scalars().all()
    claimed = [r for r in existing if r.invoice_id is not None]
    if claimed:
        raise ValueError(
            f"{len(claimed)} milestone stage(s) already have an invoice matched to them "
            "and cannot be re-entered; detach the invoice first")
    for row in existing:
        await db.delete(row)
    await db.flush()

    for i, row in enumerate(rows, start=1):
        amount, pct = _resolve_milestone_amounts(row, agr.not_to_exceed)
        db.add(AgreementPaymentSchedule(
            agreement_id=agr.id, schedule_type="milestone", sequence=i,
            milestone_name=row.milestone_name, expected_timing=row.expected_timing,
            expected_amount=amount, amount_pct=pct, status="pending",
        ))
    await db.flush()


async def list_rows(
    db: AsyncSession, agreement_id: uuid.UUID
) -> list[AgreementPaymentSchedule]:
    return list((await db.execute(
        select(AgreementPaymentSchedule)
        .where(AgreementPaymentSchedule.agreement_id == agreement_id)
        .order_by(AgreementPaymentSchedule.schedule_type,
                  AgreementPaymentSchedule.sequence)
    )).scalars().all())
