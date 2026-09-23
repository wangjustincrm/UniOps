"""Balance and actuals aggregation queries."""
import logging
import uuid
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.crud.ledger import get_actual_spent, get_committed
from app.models.catalog import BudgetAccount, BudgetL1
from app.models.ledger import ACTUAL_OPS, COMMIT_ADDS, COMMIT_RELEASES, BudgetLedger
from app.models.plan import BudgetPlan, BudgetPlanLine
from app.schemas.actual import (
    AccountSummary,
    ActualsSummaryResponse,
    MonthlyAccountSummary,
    MonthlyActualRow,
    MonthlyActualsSummaryResponse,
)
from app.schemas.balance import BalanceResponse
from app.services import finance_client

logger = logging.getLogger(__name__)


async def get_annual_budget(
    db: AsyncSession,
    cost_center_id: uuid.UUID,
    account_id: uuid.UUID,
    fiscal_year: int,
) -> Decimal:
    """annual_budget = SUM(plan_line.amount) for the *current approved* version
    (plan.status='approved' AND plan.is_current=True)."""
    q = (
        select(func.coalesce(func.sum(BudgetPlanLine.amount), 0))
        .join(BudgetPlan, BudgetPlanLine.plan_id == BudgetPlan.id)
        .where(
            BudgetPlan.cost_center_id == cost_center_id,
            BudgetPlan.fiscal_year == fiscal_year,
            BudgetPlan.status == "approved",
            BudgetPlan.is_current.is_(True),
            BudgetPlanLine.account_id == account_id,
        )
    )
    result = await db.execute(q)
    return Decimal(str(result.scalar_one()))


async def get_balance(
    db: AsyncSession,
    cost_center_id: uuid.UUID,
    account_id: uuid.UUID,
    fiscal_year: int,
    *,
    bearer_token: str | None = None,
) -> BalanceResponse:
    """Remaining balance for one (cost center × account × year).

    `actual_spent` is NC's posted actual, read from finance-api — the same
    figure the Budget Dashboard shows. It is NOT this service's `budget_ledger`:
    nothing ever writes the purchase chain into that table (commit / release /
    actualize have no callers anywhere in the repo), so it holds only the
    2026-07-07 opening import, and serving that back made a PR's over-budget
    test compare this year's request against last year's opening balance.

    `committed` stays on the ledger and stays 0 — `commit` has no writers
    either. Until in-flight commitment is derived from the documents
    themselves, the same budget can pass this test for any number of PRs while
    NC has yet to post them. That is a known gap, not an oversight, and it is
    no worse than the behaviour this replaces.

    Falls back to the ledger figure when finance-api can't be reached, which
    preserves the old (wrong, but non-zero) number rather than silently
    treating "unknown" as "nothing spent" — the latter would report the full
    annual budget as available and wave every PR through.
    """
    acct = await db.get(BudgetAccount, account_id)
    if acct is None:
        # Surface 404 to caller via API layer
        raise LookupError(f"Account {account_id} not found")
    annual = await get_annual_budget(db, cost_center_id, account_id, fiscal_year)
    committed = await get_committed(db, cost_center_id, account_id, fiscal_year)
    actual = await finance_client.nc_actual_for_budget_check(
        bearer_token=bearer_token,
        cost_center_id=cost_center_id,
        account_id=account_id,
        fiscal_year=fiscal_year,
    )
    if actual is None:
        # ★ TO WHOEVER DELETES budget_ledger: this line is one of its consumers,
        # and the only one that is not a display. The table was declared dead on
        # 2026-09-20 (the purchase chain is never being wired to it); the
        # consumers counted at the time — the Total Committed card, the export's
        # actual(docs) row, lineage's operation mix — all merely show a number,
        # so removing the table makes something go blank. This one decides
        # whether a PR needs two more approvals, so removing it without a
        # decision makes get_actual_spent() raise or return a constant 0, and
        # the over-budget gate changes behaviour with nobody having touched it.
        #
        # Know what this fallback is before you remove what it falls back to.
        # It is not the safe default it resembles: where a (cc, account) has no
        # opening row the ledger is already 0, so available comes back as the
        # whole annual budget and the gate passes everything. It is strict where
        # an opening import happens to exist and absent where it does not.
        #
        # ★ That it stays that way is a decision, not an oversight. Asked
        # directly on 2026-09-23 what should happen to a PR when this figure
        # cannot be had, the user chose to keep passing it. The alternative was
        # priced honestly: an outage would otherwise put two extra approvals on
        # every PR raised during it, and approvals cannot be withdrawn once the
        # workflow has them — somebody has to go clear them by hand. Raising
        # from here would not even buy fail-closed, since epms-api's
        # budget_client fails open by design (crud/pr.py — `if data is None:
        # return False, None`); it would let the PR through AND blank the budget
        # panel. Making the gate genuinely fail-closed means changing epms-api,
        # and that is the change the user declined. Do not quietly "fix" this
        # into raising or into a hard zero.
        actual = await get_actual_spent(db, cost_center_id, account_id, fiscal_year)
        logger.warning(
            "balance cc=%s account=%s fy=%s: finance-api unavailable, actual_spent "
            "fell back to the budget_ledger figure (opening import only)",
            cost_center_id, account_id, fiscal_year,
        )
    available = annual - committed - actual
    return BalanceResponse(
        cost_center_id=cost_center_id,
        account_id=account_id,
        account_code=acct.code,
        fiscal_year=fiscal_year,
        annual_budget=annual,
        committed=committed,
        actual_spent=actual,
        available=available,
    )


async def list_monthly_actuals(
    db: AsyncSession,
    *, cost_center_id: uuid.UUID | None = None,
    fiscal_year: int | None = None,
    account_id: uuid.UUID | None = None,
    month: int | None = None,
    cc_ids: list[uuid.UUID] | None = None,
) -> list[MonthlyActualRow]:
    """Returns per (cc, account, year, month) aggregated rows."""
    if cc_ids is not None and len(cc_ids) == 0:
        return []
    q = (
        select(
            BudgetLedger.cost_center_id,
            BudgetLedger.account_id,
            BudgetLedger.fiscal_year,
            BudgetLedger.month,
            BudgetLedger.operation,
            func.coalesce(func.sum(BudgetLedger.amount), 0).label("total"),
        )
        .group_by(
            BudgetLedger.cost_center_id, BudgetLedger.account_id,
            BudgetLedger.fiscal_year, BudgetLedger.month, BudgetLedger.operation,
        )
    )
    if cost_center_id is not None:
        q = q.where(BudgetLedger.cost_center_id == cost_center_id)
    elif cc_ids:
        q = q.where(BudgetLedger.cost_center_id.in_(cc_ids))
    if fiscal_year is not None:
        q = q.where(BudgetLedger.fiscal_year == fiscal_year)
    if account_id is not None:
        q = q.where(BudgetLedger.account_id == account_id)
    if month is not None:
        q = q.where(BudgetLedger.month == month)

    rows = (await db.execute(q)).all()
    bucket: dict[tuple, dict] = {}
    for cc, aid, fy, m, op, total in rows:
        key = (cc, aid, fy, m)
        b = bucket.setdefault(key, {"committed": Decimal("0"), "actual_spent": Decimal("0")})
        amount = Decimal(str(total))
        # One statement of the rule, not a second copy of it: actualize belongs
        # to both buckets, which is why the actual test is not an elif.
        if op in COMMIT_ADDS:
            b["committed"] += amount
        elif op in COMMIT_RELEASES:
            b["committed"] -= amount
        if op in ACTUAL_OPS:
            b["actual_spent"] += amount

    # Resolve account codes
    account_ids = {k[1] for k in bucket}
    code_q = select(BudgetAccount.id, BudgetAccount.code).where(BudgetAccount.id.in_(account_ids))
    codes = {row.id: row.code for row in (await db.execute(code_q)).all()} if account_ids else {}

    items: list[MonthlyActualRow] = []
    for (cc, aid, fy, m), b in bucket.items():
        committed = b["committed"] if b["committed"] > 0 else Decimal("0")
        items.append(MonthlyActualRow(
            cost_center_id=cc, account_id=aid,
            account_code=codes.get(aid, ""),
            fiscal_year=fy, month=m,
            committed=committed, actual_spent=b["actual_spent"],
        ))
    items.sort(key=lambda r: (r.fiscal_year, r.month, r.account_code))
    return items


async def get_actuals_summary(
    db: AsyncSession,
    *, cost_center_id: uuid.UUID | None = None, fiscal_year: int,
    cc_ids: list[uuid.UUID] | None = None,
) -> ActualsSummaryResponse:
    """Per-account summary: plan vs actual for the full year."""
    if cc_ids is not None and len(cc_ids) == 0:
        return ActualsSummaryResponse(
            cost_center_id=cost_center_id, fiscal_year=fiscal_year, accounts=[])
    accts_q = (
        select(BudgetAccount, BudgetL1)
        .join(BudgetL1, BudgetAccount.l1_id == BudgetL1.id)
        .where(BudgetAccount.is_active.is_(True))
        .order_by(BudgetL1.sort_order, BudgetAccount.sort_order)
    )
    rows = (await db.execute(accts_q)).all()

    summaries: list[AccountSummary] = []
    for acct, l1 in rows:
        if cost_center_id is None:
            # Aggregate across all CCs that have a current approved plan for this year
            annual_q = (
                select(func.coalesce(func.sum(BudgetPlanLine.amount), 0))
                .join(BudgetPlan, BudgetPlanLine.plan_id == BudgetPlan.id)
                .where(
                    BudgetPlan.fiscal_year == fiscal_year,
                    BudgetPlan.status == "approved",
                    BudgetPlan.is_current.is_(True),
                    BudgetPlanLine.account_id == acct.id,
                )
            )
            committed_q = select(func.coalesce(func.sum(BudgetLedger.amount), 0)).where(
                BudgetLedger.account_id == acct.id,
                BudgetLedger.fiscal_year == fiscal_year,
                BudgetLedger.operation == "commit",
            )
            release_q = select(func.coalesce(func.sum(BudgetLedger.amount), 0)).where(
                BudgetLedger.account_id == acct.id,
                BudgetLedger.fiscal_year == fiscal_year,
                BudgetLedger.operation.in_(["release", "actualize"]),
            )
            actual_q = select(func.coalesce(func.sum(BudgetLedger.amount), 0)).where(
                BudgetLedger.account_id == acct.id,
                BudgetLedger.fiscal_year == fiscal_year,
                BudgetLedger.operation.in_(ACTUAL_OPS),
            )
            if cc_ids:
                annual_q = annual_q.where(BudgetPlan.cost_center_id.in_(cc_ids))
                committed_q = committed_q.where(BudgetLedger.cost_center_id.in_(cc_ids))
                release_q = release_q.where(BudgetLedger.cost_center_id.in_(cc_ids))
                actual_q = actual_q.where(BudgetLedger.cost_center_id.in_(cc_ids))
            annual = Decimal(str((await db.execute(annual_q)).scalar_one()))
            committed = Decimal(str((await db.execute(committed_q)).scalar_one())) \
                       - Decimal(str((await db.execute(release_q)).scalar_one()))
            if committed < 0:
                committed = Decimal("0")
            actual = Decimal(str((await db.execute(actual_q)).scalar_one()))
        else:
            annual = await get_annual_budget(db, cost_center_id, acct.id, fiscal_year)
            committed = await get_committed(db, cost_center_id, acct.id, fiscal_year)
            actual = await get_actual_spent(db, cost_center_id, acct.id, fiscal_year)
        available = annual - committed - actual
        utilisation = float((committed + actual) / annual * 100) if annual > 0 else 0.0
        summaries.append(AccountSummary(
            account_id=acct.id, account_code=acct.code, account_name=acct.name,
            l1_id=l1.id, l1_code=l1.code,
            annual_budget=annual, committed=committed, actual_spent=actual,
            available=available, utilisation_pct=round(utilisation, 2),
        ))

    return ActualsSummaryResponse(
        cost_center_id=cost_center_id, fiscal_year=fiscal_year, accounts=summaries,
    )


async def get_monthly_actuals_summary(
    db: AsyncSession,
    *, cost_center_id: uuid.UUID | None = None, fiscal_year: int,
    cc_ids: list[uuid.UUID] | None = None,
) -> MonthlyActualsSummaryResponse:
    """Per-account plan vs actual broken down by month (Jan..Dec) + year totals.

    plan_by_month[m]  = SUM(plan_line.amount) for the current approved plan(s)
                        grouped by month.
    actual_by_month[m]= SUM(ledger.amount) where operation in
                        (actualize, book_expense, opening) grouped by month.
    When cost_center_id is None, aggregates across all CCs that have a current
    approved plan for the year (mirrors get_actuals_summary).
    """
    accts_q = (
        select(BudgetAccount, BudgetL1)
        .join(BudgetL1, BudgetAccount.l1_id == BudgetL1.id)
        .where(BudgetAccount.is_active.is_(True))
        .order_by(BudgetL1.sort_order, BudgetAccount.sort_order)
    )
    acct_rows = (await db.execute(accts_q)).all()

    if cc_ids is not None and len(cc_ids) == 0:
        return MonthlyActualsSummaryResponse(
            cost_center_id=cost_center_id, fiscal_year=fiscal_year, accounts=[])

    # ── Plan amounts by (account, month) for current approved plans ──────────
    plan_q = (
        select(
            BudgetPlanLine.account_id,
            BudgetPlanLine.month,
            func.coalesce(func.sum(BudgetPlanLine.amount), 0).label("total"),
        )
        .join(BudgetPlan, BudgetPlanLine.plan_id == BudgetPlan.id)
        .where(
            BudgetPlan.fiscal_year == fiscal_year,
            BudgetPlan.status == "approved",
            BudgetPlan.is_current.is_(True),
        )
        .group_by(BudgetPlanLine.account_id, BudgetPlanLine.month)
    )
    if cost_center_id is not None:
        plan_q = plan_q.where(BudgetPlan.cost_center_id == cost_center_id)
    elif cc_ids:
        plan_q = plan_q.where(BudgetPlan.cost_center_id.in_(cc_ids))
    plan_map: dict[tuple[uuid.UUID, int], Decimal] = {
        (aid, m): Decimal(str(total)) for aid, m, total in (await db.execute(plan_q)).all()
    }

    # ── Actual amounts by (account, month) from the ledger ───────────────────
    actual_q = (
        select(
            BudgetLedger.account_id,
            BudgetLedger.month,
            func.coalesce(func.sum(BudgetLedger.amount), 0).label("total"),
        )
        .where(
            BudgetLedger.fiscal_year == fiscal_year,
            BudgetLedger.operation.in_(ACTUAL_OPS),
        )
        .group_by(BudgetLedger.account_id, BudgetLedger.month)
    )
    if cost_center_id is not None:
        actual_q = actual_q.where(BudgetLedger.cost_center_id == cost_center_id)
    elif cc_ids:
        actual_q = actual_q.where(BudgetLedger.cost_center_id.in_(cc_ids))
    actual_map: dict[tuple[uuid.UUID, int], Decimal] = {
        (aid, m): Decimal(str(total)) for aid, m, total in (await db.execute(actual_q)).all()
    }

    summaries: list[MonthlyAccountSummary] = []
    for acct, l1 in acct_rows:
        plan_by_month = {
            m: plan_map.get((acct.id, m), Decimal("0")) for m in range(1, 13)
        }
        actual_by_month = {
            m: actual_map.get((acct.id, m), Decimal("0")) for m in range(1, 13)
        }
        summaries.append(MonthlyAccountSummary(
            account_id=acct.id, account_code=acct.code, account_name=acct.name,
            l1_id=l1.id, l1_code=l1.code,
            plan_by_month=plan_by_month, actual_by_month=actual_by_month,
            plan_year=sum(plan_by_month.values(), Decimal("0")),
            actual_year=sum(actual_by_month.values(), Decimal("0")),
        ))

    return MonthlyActualsSummaryResponse(
        cost_center_id=cost_center_id, fiscal_year=fiscal_year, accounts=summaries,
    )
