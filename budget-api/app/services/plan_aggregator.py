"""Plan grid serialization with Q/Y aggregates."""
import uuid
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.catalog import BudgetAccount, BudgetL1
from app.models.plan import BudgetPlan, BudgetPlanBreakdown, BudgetPlanLine
from app.schemas.plan import (
    AccountPlanGrid, BreakdownResponse, PlanGridResponse, PlanResponse,
)


def _quarter_sums(months: dict[int, Decimal]) -> tuple[Decimal, Decimal, Decimal, Decimal, Decimal]:
    """Return (Q1, Q2, Q3, Q4, year_total)."""
    q1 = months.get(1, Decimal("0")) + months.get(2, Decimal("0")) + months.get(3, Decimal("0"))
    q2 = months.get(4, Decimal("0")) + months.get(5, Decimal("0")) + months.get(6, Decimal("0"))
    q3 = months.get(7, Decimal("0")) + months.get(8, Decimal("0")) + months.get(9, Decimal("0"))
    q4 = months.get(10, Decimal("0")) + months.get(11, Decimal("0")) + months.get(12, Decimal("0"))
    return q1, q2, q3, q4, q1 + q2 + q3 + q4


async def build_plan_grid(db: AsyncSession, plan: BudgetPlan) -> PlanGridResponse:
    """Assemble the full plan grid response with Q/Y aggregates per account."""

    # 1. Fetch all lines for this plan
    lines_q = await db.execute(
        select(BudgetPlanLine).where(BudgetPlanLine.plan_id == plan.id)
    )
    lines = list(lines_q.scalars().all())

    # 2. Fetch all breakdowns for this plan's lines
    line_ids = [l.id for l in lines]
    breakdowns: list[BudgetPlanBreakdown] = []
    if line_ids:
        bd_q = await db.execute(
            select(BudgetPlanBreakdown).where(BudgetPlanBreakdown.plan_line_id.in_(line_ids))
        )
        breakdowns = list(bd_q.scalars().all())
    bd_by_line: dict[uuid.UUID, list[BudgetPlanBreakdown]] = {}
    for bd in breakdowns:
        bd_by_line.setdefault(bd.plan_line_id, []).append(bd)

    # 3. Group lines by account
    by_account: dict[uuid.UUID, dict[int, BudgetPlanLine]] = {}
    for line in lines:
        by_account.setdefault(line.account_id, {})[line.month] = line

    # 4. Fetch account + l1 meta
    account_ids = list(by_account.keys())
    accts_meta = {}
    if account_ids:
        meta_q = await db.execute(
            select(BudgetAccount, BudgetL1)
            .join(BudgetL1, BudgetAccount.l1_id == BudgetL1.id)
            .where(BudgetAccount.id.in_(account_ids))
        )
        for acct, l1 in meta_q.all():
            accts_meta[acct.id] = (acct, l1)

    # 5. Build rows
    rows: list[AccountPlanGrid] = []
    grand_total = Decimal("0")
    for account_id, by_month in by_account.items():
        acct_l1 = accts_meta.get(account_id)
        if acct_l1 is None:
            continue
        acct, l1 = acct_l1
        months = {m: line.amount for m, line in by_month.items()}
        breakdowns_by_month: dict[int, list[BreakdownResponse]] = {}
        for m, line in by_month.items():
            bds = bd_by_line.get(line.id, [])
            if bds:
                breakdowns_by_month[m] = [BreakdownResponse.model_validate(b) for b in bds]
        q1, q2, q3, q4, year_total = _quarter_sums(months)
        grand_total += year_total
        rows.append(AccountPlanGrid(
            account_id=acct.id, account_code=acct.code, account_name=acct.name,
            l1_id=l1.id, l1_code=l1.code, l1_name=l1.name,
            decomposition_enabled=acct.decomposition_enabled,
            months=months, breakdowns_by_month=breakdowns_by_month,
            q1=q1, q2=q2, q3=q3, q4=q4, year_total=year_total,
        ))

    rows.sort(key=lambda r: (r.l1_code, r.account_code))
    return PlanGridResponse(
        plan=PlanResponse.model_validate(plan),
        rows=rows,
        grand_total=grand_total,
    )
