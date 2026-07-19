"""CRUD for budget plans, lines, and breakdowns.

Key invariant (FR BPLAN-004): when account.decomposition_enabled = True,
  plan_line.amount = SUM(plan_line.breakdowns.amount)
Enforced by recalculating plan_line.amount after every breakdown write.

Exception — CSV import: import_plan_csv is allowed to write a direct month total
to a decomposition-enabled account. When it does, it clears that (account, month)
cell's breakdowns so the cell becomes an "undecomposed total" (amount set, no
breakdowns). The matrix editor shows it empty and the user can re-decompose later.

Revision model (Plan A): every (cost_center_id, fiscal_year) may have multiple
versions. Only the version with `is_current=True` is consulted by balance/actuals
queries. A revision creates a new draft (version+1, is_current=False) — current
flips on approval (see services.plan_aggregator or approval-api post-approve hook).
"""
import csv
import io
import uuid
from decimal import Decimal, InvalidOperation

from fastapi import HTTPException, status
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.catalog import BudgetAccount, BudgetL1
from app.models.plan import BudgetPlan, BudgetPlanBreakdown, BudgetPlanLine
from app.models.ledger import BudgetLedger
from app.schemas.plan import (
    BreakdownCreate, BreakdownUpdate, BulkBreakdownReplaceItem,
    PlanCreate, PlanImportResult, PlanLineUpdate, PlanUpdate,
)


_NON_TERMINAL_STATUSES = ("draft", "submitted", "in_review", "returned")


# ── Plan ──────────────────────────────────────────────────────────────────────

async def list_plans(
    db: AsyncSession, *,
    cost_center_id: uuid.UUID | None = None,
    fiscal_year: int | None = None,
    status: str | None = None,
    include_history: bool = False,
) -> list[BudgetPlan]:
    """List plans. Defaults to current versions only — pass include_history=True
    to also return superseded prior versions for audit/history views."""
    q = select(BudgetPlan).order_by(
        BudgetPlan.fiscal_year.desc(),
        BudgetPlan.cost_center_id,
        BudgetPlan.version.desc(),
    )
    if cost_center_id is not None:
        q = q.where(BudgetPlan.cost_center_id == cost_center_id)
    if fiscal_year is not None:
        q = q.where(BudgetPlan.fiscal_year == fiscal_year)
    if status is not None:
        q = q.where(BudgetPlan.status == status)
    if not include_history:
        q = q.where(BudgetPlan.is_current.is_(True))
    result = await db.execute(q)
    return list(result.scalars().all())


async def list_plan_versions(
    db: AsyncSession, cost_center_id: uuid.UUID, fiscal_year: int,
) -> list[BudgetPlan]:
    """Full version chain for one (cc, fy), newest first."""
    q = (
        select(BudgetPlan)
        .where(
            BudgetPlan.cost_center_id == cost_center_id,
            BudgetPlan.fiscal_year == fiscal_year,
        )
        .order_by(BudgetPlan.version.desc())
    )
    return list((await db.execute(q)).scalars().all())


async def get_plan(db: AsyncSession, plan_id: uuid.UUID) -> BudgetPlan | None:
    return await db.get(BudgetPlan, plan_id)


async def delete_plan(db: AsyncSession, plan: BudgetPlan) -> None:
    """Hard-delete a plan. Caller is responsible for the status guard.
    plan_lines + plan_breakdowns are removed via ON DELETE CASCADE."""
    await db.delete(plan)
    await db.flush()


async def get_current_plan(
    db: AsyncSession, cost_center_id: uuid.UUID, fiscal_year: int,
) -> BudgetPlan | None:
    """Return the version flagged `is_current=True` for (cc, fy), if any."""
    result = await db.execute(
        select(BudgetPlan).where(
            BudgetPlan.cost_center_id == cost_center_id,
            BudgetPlan.fiscal_year == fiscal_year,
            BudgetPlan.is_current.is_(True),
        )
    )
    return result.scalar_one_or_none()


# Backwards-compat alias for older call sites.
get_plan_by_cc_year = get_current_plan


async def current_plan_lines(
    db: AsyncSession, fiscal_year: int, month: int,
) -> list[tuple]:
    """(cost_center_id, account_id, amount) for every CURRENT APPROVED plan's
    lines in this (fiscal_year, month), across all cost centers. Feeds finance's
    predreal budget column (joined on cost_center_id + account_id = income-expense)."""
    q = (
        select(BudgetPlan.cost_center_id, BudgetPlanLine.account_id, BudgetPlanLine.amount)
        .join(BudgetPlanLine, BudgetPlanLine.plan_id == BudgetPlan.id)
        .where(
            BudgetPlan.fiscal_year == fiscal_year,
            BudgetPlan.is_current.is_(True),
            BudgetPlan.status == "approved",
            BudgetPlanLine.month == month,
        )
    )
    return list((await db.execute(q)).all())


async def get_approved_plan(
    db: AsyncSession, cost_center_id: uuid.UUID, fiscal_year: int,
) -> BudgetPlan | None:
    """The current approved plan (status='approved' AND is_current=True)."""
    result = await db.execute(
        select(BudgetPlan).where(
            BudgetPlan.cost_center_id == cost_center_id,
            BudgetPlan.fiscal_year == fiscal_year,
            BudgetPlan.status == "approved",
            BudgetPlan.is_current.is_(True),
        )
    )
    return result.scalar_one_or_none()


async def get_open_revision(
    db: AsyncSession, cost_center_id: uuid.UUID, fiscal_year: int,
) -> BudgetPlan | None:
    """Return any non-terminal (draft/submitted/in_review/returned) version for (cc, fy)."""
    result = await db.execute(
        select(BudgetPlan)
        .where(
            BudgetPlan.cost_center_id == cost_center_id,
            BudgetPlan.fiscal_year == fiscal_year,
            BudgetPlan.status.in_(_NON_TERMINAL_STATUSES),
        )
        .limit(1)
    )
    return result.scalars().first()


async def create_plan(
    db: AsyncSession, payload: PlanCreate, actor_id: uuid.UUID,
) -> BudgetPlan:
    existing = await get_current_plan(db, payload.cost_center_id, payload.fiscal_year)
    if existing:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            f"Plan already exists for cost_center={payload.cost_center_id} fiscal_year={payload.fiscal_year} (id={existing.id})",
        )
    plan = BudgetPlan(
        cost_center_id=payload.cost_center_id,
        fiscal_year=payload.fiscal_year,
        notes=payload.notes,
        created_by=actor_id,
        version=1,
        parent_plan_id=None,
        is_current=True,
    )
    db.add(plan)
    await db.flush()

    # Seed plan_lines = 0 for every active account × 12 months
    active_accts = await db.execute(
        select(BudgetAccount).where(BudgetAccount.is_active.is_(True))
    )
    for acct in active_accts.scalars().all():
        for m in range(1, 13):
            db.add(BudgetPlanLine(
                plan_id=plan.id, account_id=acct.id, month=m, amount=Decimal("0"),
            ))
    await db.flush()
    await db.refresh(plan)
    return plan


async def revise_plan(
    db: AsyncSession, parent: BudgetPlan, actor_id: uuid.UUID,
    revision_notes: str | None,
) -> BudgetPlan:
    """Create a new draft revision of an approved + currently-active plan.

    Guards:
      - parent must be status='approved' AND is_current=True
      - no other non-terminal version may exist for the same (cc, fy)

    The new draft inherits all lines + breakdowns from the parent. It starts
    is_current=False so balance / actuals queries continue to read the parent
    until the revision is approved (supersession happens in the post-approve hook).
    """
    if parent.status != "approved" or not parent.is_current:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "Only the current approved plan can be revised "
            f"(this plan is status='{parent.status}', is_current={parent.is_current}).",
        )
    open_rev = await get_open_revision(db, parent.cost_center_id, parent.fiscal_year)
    if open_rev is not None:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            f"A draft/submitted revision already exists (id={open_rev.id}, "
            f"version={open_rev.version}). Finish or cancel it before starting another.",
        )

    # Highest existing version (across all statuses) determines the next version.
    max_v_q = select(func.coalesce(func.max(BudgetPlan.version), 0)).where(
        BudgetPlan.cost_center_id == parent.cost_center_id,
        BudgetPlan.fiscal_year == parent.fiscal_year,
    )
    next_version = int((await db.execute(max_v_q)).scalar_one()) + 1

    revision = BudgetPlan(
        cost_center_id=parent.cost_center_id,
        fiscal_year=parent.fiscal_year,
        status="draft",
        notes=parent.notes,
        created_by=actor_id,
        version=next_version,
        parent_plan_id=parent.id,
        is_current=False,
        revision_notes=revision_notes,
    )
    db.add(revision)
    await db.flush()

    # Copy lines (and their breakdowns) from the parent.
    parent_lines_q = await db.execute(
        select(BudgetPlanLine).where(BudgetPlanLine.plan_id == parent.id)
    )
    parent_lines = list(parent_lines_q.scalars().all())
    new_lines_by_parent: dict[uuid.UUID, BudgetPlanLine] = {}
    for pl in parent_lines:
        new_line = BudgetPlanLine(
            plan_id=revision.id,
            account_id=pl.account_id,
            month=pl.month,
            amount=pl.amount,
            notes=pl.notes,
        )
        db.add(new_line)
        new_lines_by_parent[pl.id] = new_line
    await db.flush()

    if parent_lines:
        bd_q = await db.execute(
            select(BudgetPlanBreakdown).where(
                BudgetPlanBreakdown.plan_line_id.in_(list(new_lines_by_parent.keys()))
            )
        )
        for bd in bd_q.scalars().all():
            target_line = new_lines_by_parent.get(bd.plan_line_id)
            if target_line is None:
                continue
            db.add(BudgetPlanBreakdown(
                plan_line_id=target_line.id,
                factor_combo=bd.factor_combo,
                amount=bd.amount,
                notes=bd.notes,
            ))
        await db.flush()

    # Seed amount=0 lines for any currently-active accounts that the parent
    # plan didn't cover (e.g. new L2 accounts added to the catalog after the
    # original plan was created). Without this, those accounts wouldn't appear
    # in the revision grid and the user couldn't allocate budget to them.
    seeded: set[tuple[uuid.UUID, int]] = {
        (pl.account_id, pl.month) for pl in parent_lines
    }
    active_accts_q = await db.execute(
        select(BudgetAccount).where(BudgetAccount.is_active.is_(True))
    )
    added = 0
    for acct in active_accts_q.scalars().all():
        for m in range(1, 13):
            if (acct.id, m) in seeded:
                continue
            db.add(BudgetPlanLine(
                plan_id=revision.id, account_id=acct.id, month=m,
                amount=Decimal("0"),
            ))
            added += 1
    if added:
        await db.flush()

    await db.refresh(revision)
    return revision


async def supersede_parent_on_approval(
    db: AsyncSession, plan: BudgetPlan,
) -> None:
    """Flip is_current from parent → this plan when a revision is approved.

    Idempotent: if this plan has no parent, or already is_current, no-op.
    Called from the post-approval path (approval-api or budget-api action handler)
    after the plan transitions to status='approved'.
    """
    if plan.is_current:
        return
    if plan.parent_plan_id is None:
        # v1 reaching approved for the first time — just promote it.
        plan.is_current = True
        await db.flush()
        return
    parent = await db.get(BudgetPlan, plan.parent_plan_id)
    if parent is not None and parent.is_current:
        parent.is_current = False
    plan.is_current = True
    await db.flush()


async def update_plan(
    db: AsyncSession, plan: BudgetPlan, payload: PlanUpdate,
) -> BudgetPlan:
    if payload.notes is not None:
        plan.notes = payload.notes
    await db.flush()
    await db.refresh(plan)
    return plan


# ── Plan line ─────────────────────────────────────────────────────────────────

async def get_plan_line(
    db: AsyncSession, plan_id: uuid.UUID, account_id: uuid.UUID, month: int,
) -> BudgetPlanLine | None:
    result = await db.execute(
        select(BudgetPlanLine).where(
            BudgetPlanLine.plan_id == plan_id,
            BudgetPlanLine.account_id == account_id,
            BudgetPlanLine.month == month,
        )
    )
    return result.scalar_one_or_none()


async def update_plan_line(
    db: AsyncSession,
    plan: BudgetPlan,
    account_id: uuid.UUID,
    month: int,
    payload: PlanLineUpdate,
) -> BudgetPlanLine:
    if plan.status not in ("draft", "returned"):
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            f"Plan is in '{plan.status}' status — cannot edit lines",
        )
    acct = await db.get(BudgetAccount, account_id)
    if acct is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Account not found")
    if acct.decomposition_enabled:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "Account uses decomposition — edit breakdowns instead of line amount",
        )
    line = await get_plan_line(db, plan.id, account_id, month)
    if line is None:
        line = BudgetPlanLine(plan_id=plan.id, account_id=account_id, month=month, amount=payload.amount)
        db.add(line)
    else:
        line.amount = payload.amount
        if payload.notes is not None:
            line.notes = payload.notes
    await db.flush()
    await db.refresh(line)
    return line


# ── Breakdown ─────────────────────────────────────────────────────────────────

async def _ensure_plan_line(
    db: AsyncSession, plan_id: uuid.UUID, account_id: uuid.UUID, month: int,
) -> BudgetPlanLine:
    line = await get_plan_line(db, plan_id, account_id, month)
    if line is None:
        line = BudgetPlanLine(plan_id=plan_id, account_id=account_id, month=month, amount=Decimal("0"))
        db.add(line)
        await db.flush()
    return line


async def _recompute_line_amount(db: AsyncSession, line: BudgetPlanLine) -> None:
    """plan_line.amount = SUM(breakdowns.amount). Used after any breakdown write."""
    await db.refresh(line, attribute_names=["breakdowns"])
    line.amount = sum((b.amount for b in line.breakdowns), Decimal("0"))
    await db.flush()


async def list_breakdowns(
    db: AsyncSession, plan_line_id: uuid.UUID,
) -> list[BudgetPlanBreakdown]:
    result = await db.execute(
        select(BudgetPlanBreakdown)
        .where(BudgetPlanBreakdown.plan_line_id == plan_line_id)
        .order_by(BudgetPlanBreakdown.created_at)
    )
    return list(result.scalars().all())


async def get_breakdown(
    db: AsyncSession, breakdown_id: uuid.UUID,
) -> BudgetPlanBreakdown | None:
    return await db.get(BudgetPlanBreakdown, breakdown_id)


async def create_breakdown(
    db: AsyncSession,
    plan: BudgetPlan,
    account_id: uuid.UUID,
    month: int,
    payload: BreakdownCreate,
) -> BudgetPlanBreakdown:
    if plan.status not in ("draft", "returned"):
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            f"Plan is in '{plan.status}' status — cannot edit breakdowns",
        )
    acct = await db.get(BudgetAccount, account_id)
    if acct is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Account not found")
    if not acct.decomposition_enabled:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "Account does not have decomposition enabled — set plan_line.amount directly",
        )
    line = await _ensure_plan_line(db, plan.id, account_id, month)
    bd = BudgetPlanBreakdown(
        plan_line_id=line.id, factor_combo=payload.factor_combo,
        amount=payload.amount, notes=payload.notes,
    )
    db.add(bd)
    await db.flush()
    await _recompute_line_amount(db, line)
    await db.refresh(bd)
    return bd


async def update_breakdown(
    db: AsyncSession, plan: BudgetPlan, bd: BudgetPlanBreakdown, payload: BreakdownUpdate,
) -> BudgetPlanBreakdown:
    if plan.status not in ("draft", "returned"):
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            f"Plan is in '{plan.status}' status — cannot edit breakdowns",
        )
    if payload.factor_combo is not None:
        bd.factor_combo = payload.factor_combo
    if payload.amount is not None:
        bd.amount = payload.amount
    if payload.notes is not None:
        bd.notes = payload.notes
    await db.flush()
    line = await db.get(BudgetPlanLine, bd.plan_line_id)
    if line:
        await _recompute_line_amount(db, line)
    await db.refresh(bd)
    return bd


async def delete_breakdown(
    db: AsyncSession, plan: BudgetPlan, bd: BudgetPlanBreakdown,
) -> None:
    if plan.status not in ("draft", "returned"):
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            f"Plan is in '{plan.status}' status — cannot delete breakdowns",
        )
    line_id = bd.plan_line_id
    await db.delete(bd)
    await db.flush()
    line = await db.get(BudgetPlanLine, line_id)
    if line:
        await _recompute_line_amount(db, line)


async def replace_breakdowns(
    db: AsyncSession,
    plan: BudgetPlan,
    account_id: uuid.UUID,
    month: int,
    items: list[BulkBreakdownReplaceItem],
) -> tuple[BudgetPlanLine, list[BudgetPlanBreakdown]]:
    """Atomic bulk-replace of all breakdowns for one (plan_line) cell.

    Steps inside a single flush sequence:
      1. Guard plan status (draft / returned only)
      2. Verify Account exists AND has decomposition_enabled
      3. Upsert plan_line
      4. DELETE every existing breakdown for that line
      5. INSERT each item
      6. Recompute line.amount via _recompute_line_amount

    Zero-amount items are still inserted (preserves user intent — they
    explicitly listed a combo with value 0). Items with amount=0 effectively
    just keep the slot reserved; total stays unaffected.
    """
    if plan.status not in ("draft", "returned"):
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            f"Plan is in '{plan.status}' status — cannot edit breakdowns",
        )
    acct = await db.get(BudgetAccount, account_id)
    if acct is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Account not found")
    if not acct.decomposition_enabled:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "Account does not have decomposition enabled — set plan_line.amount directly",
        )

    line = await _ensure_plan_line(db, plan.id, account_id, month)

    # Wipe all existing breakdowns for this line.
    existing = (await db.execute(
        select(BudgetPlanBreakdown).where(BudgetPlanBreakdown.plan_line_id == line.id)
    )).scalars().all()
    for bd in existing:
        await db.delete(bd)
    await db.flush()

    # Insert the new rows.
    new_rows: list[BudgetPlanBreakdown] = []
    for item in items:
        bd = BudgetPlanBreakdown(
            plan_line_id=line.id,
            factor_combo=item.factor_combo,
            amount=item.amount,
            notes=item.notes,
        )
        db.add(bd)
        new_rows.append(bd)
    await db.flush()

    # Recompute line.amount from the new breakdowns.
    await _recompute_line_amount(db, line)

    # Refresh to get final state (amount, ids, created_at).
    await db.refresh(line)
    for bd in new_rows:
        await db.refresh(bd)
    return line, new_rows


# ── Baseline / historical reference (Matrix sidebar) ─────────────────────────

async def get_baseline(
    db: AsyncSession,
    plan: BudgetPlan,
    account_id: uuid.UUID,
    month: int,
) -> dict:
    """Return historical reference data for the (plan, account, month) cell.

    Used by the Matrix editor sidebar to help users plan reasonably:
      - last_year_same_month: total budget for (CC, account, fy-1, month)
        from the previous fiscal year's current approved plan
      - last_year_breakdowns: that plan's breakdowns for the same cell
        (so user can "Copy from Last Year")
      - current_month_actual: sum of ledger entries with operation
        in ('actualize', 'book_expense') for this (CC, account, fy, month)
      - ytd_actual: same as above, but month <= current month

    Any field can be None if no source data exists.
    """
    cc_id = plan.cost_center_id
    fy = plan.fiscal_year

    # ── Last year same month from the (cc, fy-1) approved + is_current plan ──
    last_year_total: Decimal | None = None
    last_year_breakdowns: list[BudgetPlanBreakdown] = []
    prev = await get_approved_plan(db, cc_id, fy - 1)
    if prev is not None:
        prev_line_q = await db.execute(
            select(BudgetPlanLine).where(
                BudgetPlanLine.plan_id == prev.id,
                BudgetPlanLine.account_id == account_id,
                BudgetPlanLine.month == month,
            )
        )
        prev_line = prev_line_q.scalar_one_or_none()
        if prev_line is not None:
            last_year_total = prev_line.amount
            bd_q = await db.execute(
                select(BudgetPlanBreakdown).where(
                    BudgetPlanBreakdown.plan_line_id == prev_line.id
                )
            )
            last_year_breakdowns = list(bd_q.scalars().all())

    # ── Current month actual: ledger actualize + book_expense for this cell ──
    actual_q = await db.execute(
        select(func.coalesce(func.sum(BudgetLedger.amount), 0)).where(
            BudgetLedger.cost_center_id == cc_id,
            BudgetLedger.account_id == account_id,
            BudgetLedger.fiscal_year == fy,
            BudgetLedger.month == month,
            BudgetLedger.operation.in_(("actualize", "book_expense")),
        )
    )
    current_month_actual = Decimal(str(actual_q.scalar_one()))

    # ── YTD actual: same but month <= the requested month ──
    ytd_q = await db.execute(
        select(func.coalesce(func.sum(BudgetLedger.amount), 0)).where(
            BudgetLedger.cost_center_id == cc_id,
            BudgetLedger.account_id == account_id,
            BudgetLedger.fiscal_year == fy,
            BudgetLedger.month <= month,
            BudgetLedger.operation.in_(("actualize", "book_expense")),
        )
    )
    ytd_actual = Decimal(str(ytd_q.scalar_one()))

    return {
        "last_year_same_month": last_year_total,
        "current_month_actual": current_month_actual if current_month_actual > 0 else None,
        "ytd_actual": ytd_actual if ytd_actual > 0 else None,
        "last_year_breakdowns": last_year_breakdowns,
    }


# ── Plan state transitions (called by api/plan.py action endpoint) ────────────

async def transition_plan(
    db: AsyncSession, plan: BudgetPlan, action: str, actor_id: uuid.UUID,  # noqa: ARG001
) -> BudgetPlan:
    """Naive state machine — full approval flow is delegated to approval-api in the
    API layer. This function records the local state transition only."""
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc)

    if action == "submit":
        if plan.status not in ("draft", "returned"):
            raise HTTPException(status.HTTP_409_CONFLICT, f"Cannot submit from status '{plan.status}'")
        plan.status = "submitted"
        plan.submitted_at = now
        plan.approval_step_idx = 0
    elif action == "approve":
        if plan.status not in ("submitted", "in_review"):
            raise HTTPException(status.HTTP_409_CONFLICT, f"Cannot approve from status '{plan.status}'")
        plan.status = "approved"
        plan.approved_at = now
        await supersede_parent_on_approval(db, plan)
    elif action == "return":
        if plan.status not in ("submitted", "in_review"):
            raise HTTPException(status.HTTP_409_CONFLICT, f"Cannot return from status '{plan.status}'")
        plan.status = "returned"
    elif action == "reject":
        if plan.status not in ("submitted", "in_review"):
            raise HTTPException(status.HTTP_409_CONFLICT, f"Cannot reject from status '{plan.status}'")
        plan.status = "rejected"
    elif action == "cancel":
        if plan.status in ("approved", "rejected", "cancelled"):
            raise HTTPException(status.HTTP_409_CONFLICT, f"Cannot cancel from terminal status '{plan.status}'")
        plan.status = "cancelled"
    else:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"Unknown action '{action}'")

    await db.flush()
    await db.refresh(plan)
    return plan


async def copy_from_prev_year(
    db: AsyncSession, plan: BudgetPlan, actor_id: uuid.UUID,  # noqa: ARG001
) -> int:
    """Copy approved plan from (cc, fiscal_year-1) into this plan. Returns count of lines copied."""
    prev = await get_approved_plan(db, plan.cost_center_id, plan.fiscal_year - 1)
    if prev is None:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND,
            f"No approved plan found for fiscal_year={plan.fiscal_year - 1}",
        )
    if plan.status not in ("draft",):
        raise HTTPException(status.HTTP_409_CONFLICT, "Target plan must be in draft status")

    # Build lookup of existing lines on this plan
    lines_q = await db.execute(
        select(BudgetPlanLine).where(BudgetPlanLine.plan_id == plan.id)
    )
    existing_by_key = {(l.account_id, l.month): l for l in lines_q.scalars().all()}

    prev_lines_q = await db.execute(
        select(BudgetPlanLine).where(BudgetPlanLine.plan_id == prev.id)
    )
    count = 0
    for pl in prev_lines_q.scalars().all():
        target = existing_by_key.get((pl.account_id, pl.month))
        if target:
            target.amount = pl.amount
            count += 1
    await db.flush()
    return count


# ── CSV Import / Export ───────────────────────────────────────────────────────

# Plan CSV is a "wide" sheet: one row per Account, 12 month columns plus Q/Y
# totals. Decomposed accounts export the line total (sum of breakdowns) but
# are skipped on import — those must be edited via the breakdown UI to keep
# the `line.amount = sum(breakdowns)` invariant intact.

_CSV_MONTH_HEADERS = [
    "Jan", "Feb", "Mar", "Apr", "May", "Jun",
    "Jul", "Aug", "Sep", "Oct", "Nov", "Dec",
]
_CSV_HEADERS = (
    ["L1 Code", "L1 Name", "Account Code", "Account Name", "Decomposed"]
    + _CSV_MONTH_HEADERS
    + ["Q1", "Q2", "Q3", "Q4", "Year"]
)


def _fmt_decimal(value: Decimal | None) -> str:
    if value is None:
        return "0"
    # Quantize to 2 dp, but keep "0" instead of "0.00" for cleanliness.
    if value == 0:
        return "0"
    return f"{value:.2f}"


async def export_plan_csv(db: AsyncSession, plan: BudgetPlan) -> str:
    """Render this plan as a wide CSV (one row per Account)."""
    # Pull all active accounts + their L1, plus the plan's lines.
    accts_q = (
        select(BudgetAccount, BudgetL1)
        .join(BudgetL1, BudgetAccount.l1_id == BudgetL1.id)
        .where(BudgetAccount.is_active.is_(True))
        .order_by(BudgetL1.sort_order, BudgetL1.code, BudgetAccount.sort_order, BudgetAccount.code)
    )
    accts_rows = list((await db.execute(accts_q)).all())

    lines_q = await db.execute(
        select(BudgetPlanLine).where(BudgetPlanLine.plan_id == plan.id)
    )
    lines = list(lines_q.scalars().all())
    # key: (account_id, month) -> amount
    amounts: dict[tuple[uuid.UUID, int], Decimal] = {
        (l.account_id, l.month): l.amount for l in lines
    }

    buf = io.StringIO()
    writer = csv.writer(buf, lineterminator="\n")
    writer.writerow(_CSV_HEADERS)

    for acct, l1 in accts_rows:
        months = [amounts.get((acct.id, m), Decimal(0)) for m in range(1, 13)]
        q1 = sum(months[0:3], Decimal(0))
        q2 = sum(months[3:6], Decimal(0))
        q3 = sum(months[6:9], Decimal(0))
        q4 = sum(months[9:12], Decimal(0))
        year = q1 + q2 + q3 + q4
        writer.writerow([
            l1.code, l1.name,
            acct.code, acct.name,
            "Yes" if acct.decomposition_enabled else "No",
            *[_fmt_decimal(v) for v in months],
            _fmt_decimal(q1), _fmt_decimal(q2),
            _fmt_decimal(q3), _fmt_decimal(q4),
            _fmt_decimal(year),
        ])
    return buf.getvalue()


def _parse_decimal_cell(raw: str) -> Decimal | None:
    """Parse a CSV cell into Decimal. Blank/whitespace → None (no change).
    Accepts thousand separators (1,234.56) but treats lone commas as bare decimal commas."""
    s = (raw or "").strip()
    if not s:
        return None
    # Strip currency symbols & thousands separators commonly seen in Excel exports.
    s = s.replace(",", "").replace("$", "").replace("CAD", "").strip()
    if s == "":
        return None
    try:
        return Decimal(s)
    except (InvalidOperation, ValueError):
        raise ValueError(f"invalid number '{raw}'")


async def import_plan_csv(
    db: AsyncSession, plan: BudgetPlan, csv_text: str,
) -> PlanImportResult:
    """Bulk-update plan_line amounts from a wide CSV.

    Format must match `export_plan_csv` headers (extra columns are ignored).
    Blank month cells leave the existing amount unchanged; explicit "0" sets it.
    Decomposition-enabled accounts ARE imported: a direct month total overrides
    (and clears) that (account, month) cell's factor breakdowns.
    Plan must be in 'draft' or 'returned' status.
    """
    if plan.status not in ("draft", "returned"):
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            f"Plan is in '{plan.status}' status — only draft or returned plans can be imported",
        )

    result = PlanImportResult()

    # Pre-build account-code -> account lookup.
    accts_q = await db.execute(select(BudgetAccount))
    acct_by_code: dict[str, BudgetAccount] = {a.code: a for a in accts_q.scalars().all()}

    # Existing line lookup so we upsert instead of duplicate.
    lines_q = await db.execute(
        select(BudgetPlanLine).where(BudgetPlanLine.plan_id == plan.id)
    )
    line_by_key: dict[tuple[uuid.UUID, int], BudgetPlanLine] = {
        (l.account_id, l.month): l for l in lines_q.scalars().all()
    }

    reader = csv.DictReader(io.StringIO(csv_text))
    if reader.fieldnames is None or "Account Code" not in reader.fieldnames:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "CSV missing required 'Account Code' column — did you upload the right file?",
        )

    touched: set[uuid.UUID] = set()
    for row_idx, row in enumerate(reader, start=2):  # row 1 is header
        code = (row.get("Account Code") or "").strip()
        if not code:
            continue
        acct = acct_by_code.get(code)
        if acct is None:
            result.errors.append(f"Row {row_idx}: unknown account code '{code}' — skipped")
            continue

        row_changed = False
        row_cleared = False
        for m_idx, col in enumerate(_CSV_MONTH_HEADERS, start=1):
            cell = row.get(col)
            try:
                amount = _parse_decimal_cell(cell or "")
            except ValueError as e:
                result.errors.append(f"Row {row_idx} {col}: {e}")
                continue
            if amount is None:
                continue  # blank → no change

            key = (acct.id, m_idx)
            line = line_by_key.get(key)
            changed = False
            if line is None:
                line = BudgetPlanLine(
                    plan_id=plan.id, account_id=acct.id, month=m_idx, amount=amount,
                )
                db.add(line)
                line_by_key[key] = line
                changed = True
            else:
                if line.amount != amount:
                    line.amount = amount
                    changed = True
                # Decomposed account: a direct total overrides factor breakdowns.
                # Clear them so the cell becomes an undecomposed total.
                if acct.decomposition_enabled:
                    res = await db.execute(
                        delete(BudgetPlanBreakdown).where(
                            BudgetPlanBreakdown.plan_line_id == line.id
                        )
                    )
                    cleared = res.rowcount or 0
                    if cleared:
                        result.breakdowns_cleared += cleared
                        changed = True
                        row_cleared = True

            if not changed:
                continue  # unchanged, nothing cleared
            result.lines_updated += 1
            row_changed = True

        if row_changed:
            touched.add(acct.id)
        if row_cleared:
            result.errors.append(
                f"Row {row_idx}: account '{code}' uses factor decomposition — "
                f"imported totals replaced its factor breakdowns"
            )

    result.accounts_touched = len(touched)
    await db.flush()
    return result
